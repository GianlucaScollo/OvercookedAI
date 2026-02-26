import os
import sys

# If FLASK_ENV=production, we use eventlet for concurrency and patch
# standard Python libraries so they work well with green threads.
if os.getenv("FLASK_ENV", "production") == "production":
    import eventlet
    eventlet.monkey_patch()

import atexit
import json
import logging

# All other imports must come after patch to ensure eventlet compatibility
# Standard Python libraries for storing data or concurrency
import pickle
import queue
from datetime import datetime
from threading import Lock
import time

# Import custom modules from local files
import game
from flask import Flask, jsonify, render_template, request
from flask_socketio import SocketIO, emit, join_room, leave_room
from game import Game, OvercookedGame, OvercookedTutorial
from utils import ThreadSafeDict, ThreadSafeSet

###################
# Global Config   #
###################

# We load the configuration JSON. By default, we read from "config.json",
# but this is configurable using the CONF_PATH env var.
CONF_PATH = os.getenv("CONF_PATH", "config.json")
with open(CONF_PATH, "r") as f:
    CONFIG = json.load(f)

# Some important fields from config.json:
LOGFILE = CONFIG["logfile"]                # Path to the file where errors are logged
LAYOUTS = CONFIG["layouts"]                # List of layout names (like "you_shall_not_pass")
LAYOUT_GLOBALS = CONFIG["layout_globals"]  # Shared parameters for onion/tomato times/values
MAX_GAME_LENGTH = CONFIG["MAX_GAME_LENGTH"] # Global limit on each game’s length (in seconds)
MAX_GAMES = CONFIG["MAX_GAMES"]            # Maximum # of games that can exist at once
MAX_FPS = CONFIG["MAX_FPS"]                # The server’s frames-per-second for broadcasting states
PREDEFINED_CONFIG = json.dumps(CONFIG["predefined"])  # JSON that configures the /predefined page
TUTORIAL_CONFIG = json.dumps(CONFIG["tutorial"])       # JSON that configures the /tutorial page

# We keep track of "free" game IDs in a queue. Each game has a unique ID from 0..(MAX_GAMES-1).
FREE_IDS = queue.Queue(maxsize=MAX_GAMES)

FREE_MAP = ThreadSafeDict() # FREE_MAP[id] = True means "that ID is available"

# Initialise our ID tracking data (15 IDs available since max num of games is 15)
for i in range(MAX_GAMES):
    FREE_IDS.put(i)
    FREE_MAP[i] = True

# GAMES: a mapping { game_id -> Game object }, stored in a thread-safe dict
GAMES = ThreadSafeDict()

# ACTIVE_GAMES: A ThreadSafeSet of game_ids that are currently active (not waiting or ended).
ACTIVE_GAMES = ThreadSafeSet()

# WAITING_GAMES: A standard queue of game_ids that are waiting for players to join
WAITING_GAMES = queue.Queue()

# USERS: { user_id -> Lock() }, ensures we can lock user operations (like joining a game). Enforces user-level serialization
USERS = ThreadSafeDict()

# USER_ROOMS: { user_id -> game_id }, tracks which game the user is in. 
USER_ROOMS = ThreadSafeDict()

# We also define a mapping from "game_name" strings to the actual Python classes.
GAME_NAME_TO_CLS = {
    "overcooked": OvercookedGame,
    "tutorial": OvercookedTutorial,
}

# We tell our local "game.py" to store global references to MAX_GAME_LENGTH 
game._configure(MAX_GAME_LENGTH)


########################
# Flask Configuration  #
########################

# We create a Flask app with 'static/templates' as the directory for HTML.
app = Flask(__name__, template_folder=os.path.join("static", "templates"))
app.config["DEBUG"] = os.getenv("FLASK_ENV", "production") == "development"

# We wrap the Flask app with SocketIO for real-time communication.
# cors_allowed_origins="*" means we don’t restrict cross-domain requests.
socketio = SocketIO(app, cors_allowed_origins="*", logger=app.config["DEBUG"])


# We attach a FileHandler to log errors to LOGFILE
handler = logging.FileHandler(LOGFILE)
handler.setLevel(logging.ERROR)
app.logger.addHandler(handler)


#####################################
# Global Coordination / Helper Funcs #
#####################################

def try_create_game(game_name, **kwargs):
    """
    Attempts to create a brand new Game object (e.g., an OvercookedGame) with the given parameters.

    Returns (game_obj, error):
      - game_obj is a pointer to the newly created Game object, or None on failure
      - error is None on success, or an Exception if there was an error
    """
    try:
        # Grab a free ID from the FREE_IDS queue (non-blocking).
        curr_id = FREE_IDS.get(block=False)
        assert FREE_MAP[curr_id], "Current id is already in use"

        # Get the class from the dictionary, default to OvercookedGame if not found.
        game_cls = GAME_NAME_TO_CLS.get(game_name, OvercookedGame)
        # Instantiate the Game object with that ID
        game = game_cls(id=curr_id, **kwargs)

    except queue.Empty:
        # Means there are no free game IDs => server at max capacity
        err = RuntimeError("Server at max capacity")
        return None, err
    except Exception as e:
        # Any other error that might happen in the constructor
        return None, e
    else:
        # On success, store the new Game object in GAMES
        GAMES[game.id] = game
        FREE_MAP[game.id] = False
        return game, None


def cleanup_game(game: OvercookedGame):
    """
    Safely remove a game from memory (when it's ended or everyone left).
    This frees up the game ID so it can be reused for a new game.
    """
    if FREE_MAP[game.id]:
        raise ValueError("Double free on a game")

    # For each user in that game, make them leave the room
    for user_id in game.players:
        leave_curr_room(user_id)

    # Close the socket room, free the ID, remove from GAMES
    socketio.close_room(game.id)
    # Game tracking
    FREE_MAP[game.id] = True
    FREE_IDS.put(game.id)
    del GAMES[game.id]

    if game.id in ACTIVE_GAMES:
        ACTIVE_GAMES.remove(game.id)


def get_game(game_id):
    return GAMES.get(game_id, None)


def get_curr_game(user_id):
    return get_game(get_curr_room(user_id))


def get_curr_room(user_id):
    return USER_ROOMS.get(user_id, None)


def set_curr_room(user_id, room_id):
    USER_ROOMS[user_id] = room_id


def leave_curr_room(user_id):
    # Remove the user from the dict altogether
    del USER_ROOMS[user_id]


def get_waiting_game():
    """
    Return a pointer to a waiting game, if one exists

    Note: The use of a queue ensures that no two threads will ever receive the same pointer, unless
    the waiting game's ID is re-added to the WAITING_GAMES queue
    """
    try:
        waiting_id = WAITING_GAMES.get(block=False)
        while FREE_MAP[waiting_id]:
            waiting_id = WAITING_GAMES.get(block=False)
    except queue.Empty:
        return None
    else:
        return get_game(waiting_id)


##################################
# Socket Handler Helper Functions#
##################################

def _leave_game(user_id):
    """
    Removes `user_id` from its current game (if any). If it was an active game with multiple players,
    that might cause the game to end for everyone. If it was a waiting game with no one else, 
    we cleanup the game entirely.
    """
    # Get pointer to current game if it exists
    game = get_curr_game(user_id)

    if not game:
        # Cannot leave a game if not currently in one (user was not in any game)
        return False

    # Acquire this game's lock to ensure all global state updates are atomic
    with game.lock:
        # The user leaves the socket room
        leave_room(game.id)
        # Remove them from the user->room mapping
        leave_curr_room(user_id)

        # Remove them from the game’s player/spectator data
        if user_id in game.players:
            game.remove_player(user_id)
        else:
            game.remove_spectator(user_id)

        # Whether the game was active before the user left
        was_active = game.id in ACTIVE_GAMES

        # Rebroadcast data and handle cleanup based on the transition caused by leaving
        if was_active and game.is_empty():
            # The last player left an active game => deactivate + cleanup
            game.deactivate()
        elif game.is_empty():
            # If it was a waiting game with 1 user => just cleanup
            cleanup_game(game)
        elif not was_active:
            # Still in waiting -> broadcast "waiting"
            emit("waiting", {"in_game": True}, room=game.id)
        elif was_active and game.is_ready():
            # The game remains active with other players
            pass
        elif was_active and not game.is_empty():
            # The game transitions from active to waiting
            game.deactivate()

    return was_active


def _create_game(user_id, game_name, params={}):
    """
    Helper used by the on_create() or on_join() socket events:
      - actually attempt to create the game
      - add the user to it as either a player or spectator
      - if the game is 'ready', start it, else put it in WAITING_GAMES
    """
    game, err = try_create_game(game_name, **params)
    if not game:
        emit("creation_failed", {"error": err.__repr__()})
        return
    
    # By default we treat the user as "spectating" if the game is full
    spectating = True


    with game.lock:
        if not game.is_full():
            # If the game isn’t full, this user can be a player
            spectating = False
            game.add_player(user_id)
        else:
            # Otherwise, user is only spectating
            spectating = True
            game.add_spectator(user_id)

        # Make the user join the socket.io room for that game    
        join_room(game.id)
        set_curr_room(user_id, game.id)

        # If the game is ready to start, we do so
        if game.is_ready():
            game.activate()
            ACTIVE_GAMES.add(game.id)
            emit(
                "start_game",
                {"spectating": spectating, "start_info": game.to_json()},
                broadcast=True,
            )

            # We spin up a background task that calls play_game() 6 times per second by default
            socketio.start_background_task(play_game, game, fps=6)
        else:
            # If not ready, we put it in the WAITING_GAMES queue
            WAITING_GAMES.put(game.id)
            emit("waiting", {"in_game": True}, room=game.id)


#####################
# Debugging Helpers #
#####################





######################
# Flask HTTP Routes  #
######################
# Hitting each of these endpoints creates a brand new socket that is closed
# at after the server response is received. Standard HTTP protocol

@app.route("/")
def index():
    # The homepage. We pass in the list of agent_names and layouts so that
    # the user can pick them in the drop-down <select>.
    return render_template(
        "index.html", layouts=LAYOUTS
    )


@app.route("/predefined")
def predefined():
    # The "predefined" page, which runs multiple layouts in a row
    uid = request.args.get("UID")
    num_layouts = len(CONFIG["predefined"]["experimentParams"]["layouts"])
    return render_template(
        "predefined.html",
        uid=uid,
        config=PREDEFINED_CONFIG,
        num_layouts=num_layouts,
    )


@app.route("/instructions")
def instructions():
    # Shows the instructions page. We pass layout_conf to define onion/tomato times, etc.
    return render_template("instructions.html", layout_conf=LAYOUT_GLOBALS)

@app.route("/tutorial")
def tutorial():
    # The tutorial page, which loads TUTORIAL_CONFIG from config.json
    return render_template("tutorial.html", config=TUTORIAL_CONFIG)


@app.route("/debug")
def debug():
    """
    A debug endpoint that returns a JSON of all relevant server state:
      - active games
      - waiting games
      - free IDs
      - user->room mappings
    """
    resp = {}
    games = []
    active_games = []
    waiting_games = []
    users = []
    free_ids = []
    free_map = {}

    for game_id in ACTIVE_GAMES:
        game = get_game(game_id)
        active_games.append({"id": game_id, "state": game.to_json()})

    for game_id in list(WAITING_GAMES.queue):
        game = get_game(game_id)
        game_state = None if FREE_MAP[game_id] else game.to_json()
        waiting_games.append({"id": game_id, "state": game_state})

    for game_id in GAMES:
        games.append(game_id)

    for user_id in USER_ROOMS:
        users.append({user_id: get_curr_room(user_id)})

    for game_id in list(FREE_IDS.queue):
        free_ids.append(game_id)

    for game_id in FREE_MAP:
        free_map[game_id] = FREE_MAP[game_id]

    resp["active_games"] = active_games
    resp["waiting_games"] = waiting_games
    resp["all_games"] = games
    resp["users"] = users
    resp["free_ids"] = free_ids
    resp["free_map"] = free_map

    return jsonify(resp)


###########################
# Socket.IO Event Handlers#
###########################
# Asynchronous handling of client-side socket events. Note that the socket persists even after the
# event has been handled. This allows for more rapid data communication, as a handshake only has to
# happen once at the beginning. Thus, socket events are used for all game updates, where more rapid
# communication is needed


def creation_params(params):
    """
    This function extracts the dataCollection from the input and
    processes it before sending it to game creation
    """
    # this params file should be a dictionary that can have these keys:
    # playerZero: human
    # playerOne: Jason Agent (registered as human as we handle the logic of the Agent in another repository)
    # layout: one of the layouts in the config file
    # gameTime: time in seconds
    # dataCollection: on/off
    # layouts: [layout in the config file], this one determines which layout to use, and if there is more than one layout, a series of game is run back to back

    
    
    
    

    if "dataCollection" in params and params["dataCollection"] == "on":
        # We'll store trajectory data if dataCollection=on
        params["dataCollection"] = True
        mapping = {"human": "H"}
        # gameType is either HH, HA, AH, AA depending on the config
        gameType = "{}{}".format(
            mapping.get(params["playerZero"], "A"),
            mapping.get(params["playerOne"], "A"),
        )
        params["collection_config"] = {
            "time": datetime.today().strftime("%Y-%m-%d_%H-%M-%S"),
            "type": gameType,
        }
        
        params["collection_config"]["old_dynamics"] = "New"

    else:
        params["dataCollection"] = False


@socketio.on("create")
def on_create(data):
    """
    The client emits "create" when they explicitly want to create a new game.
    We parse the user’s 'params' (like layout, gameTime, etc.) and 
    then call _create_game(...) if they aren’t already in one.
    """
    user_id = request.sid
    with USERS[user_id]:
        # Retrieve current game if one exists
        curr_game = get_curr_game(user_id)
        if curr_game:
            # If user is already in a game, do nothing
            return

        params = data.get("params", {})
        creation_params(params)

        game_name = data.get("game_name", "overcooked")
        _create_game(user_id, game_name, params)


@socketio.on("join")
def on_join(data):
    """
    The client emits "join" to either:
      1) Join an existing waiting game
      2) If none is found, create a new game if create_if_not_found=True
      3) If none found and create_if_not_found=False, they get put in 'waiting' state
    """
    user_id = request.sid
    with USERS[user_id]:
        create_if_not_found = data.get("create_if_not_found", True)

        # Retrieve current game if one exists
        curr_game = get_curr_game(user_id)
        if curr_game:
            # Cannot join if currently in a game
            return

        # Try to get a waiting game from the queue
        game = get_waiting_game()

        if not game and create_if_not_found:
            # If no waiting game is found, create a new one
            params = data.get("params", {})
            creation_params(params)
            game_name = data.get("game_name", "overcooked")
            _create_game(user_id, game_name, params)
            return

        elif not game:
            # If no waiting game found and we’re not allowed to create => waiting
            emit("waiting", {"in_game": False})
        else:
            # We found a waiting game, so join it
            with game.lock:
                join_room(game.id)
                set_curr_room(user_id, game.id)
                game.add_player(user_id)

                if game.is_ready():
                    # Game is ready to begin play
                    game.activate()
                    ACTIVE_GAMES.add(game.id)
                    emit(
                        "start_game",
                        {"spectating": False, "start_info": game.to_json()},
                        room=game.id,
                    )
                    layout_info = {
                        "layout_name": game.curr_layout, # name of layout e.g. cramped_room
                        "terrain": game.mdp.terrain_mtx, # 2d list of the terrain
                        "state": game.get_state()
                    }
                    socketio.emit("java_layout", layout_info, broadcast=True)
                    socketio.start_background_task(play_game, game)
                else:
                    # Still need to keep waiting for players
                    WAITING_GAMES.put(game.id)
                    emit("waiting", {"in_game": True}, room=game.id)



@socketio.on("leave")
def on_leave(data):
    """
    If the user clicks "Leave" or otherwise triggers a leave event, we remove them from the game,
    possibly ending the game for all players if it's active.
    """
    user_id = request.sid
    with USERS[user_id]:
        was_active = _leave_game(user_id)

        if was_active:
            emit("end_game", {"status": Game.Status.DONE, "data": {}})
        else:
            emit("end_lobby")


@socketio.on("action")
def on_action(data):
    """
    Fired when a human user presses an arrow key or spacebar. We queue that action into the game’s 
    "pending actions" for that player. The game logic is advanced in play_game(...) background task.
    """
    user_id = request.sid
    action = data["action"]

    game = get_curr_game(user_id)
    if not game:
        return


    # 1) Enqueue the action in the Overcooked environment
    game.enqueue_action(user_id, action)

    # 2) Also broadcast "human_action",
    #    so that the Java agent can see it too.
    #    We use broadcast=True so *all* connected sockets get it (including Java).
    socketio.emit("human_action", {
        "user_id": user_id,
        "action": action
    }, broadcast=True)

    
    

@socketio.on("connect")
def on_connect():
    """
    When a new WebSocket connection is established, we add that user to USERS with a lock.
    """
    user_id = request.sid
    if user_id in USERS:
        return
    USERS[user_id] = Lock()

#######################
# Java side connection #
#######################

@socketio.on("java_connected")
def handle_java_connected(data):
    print("Got java_connected event from Java with data:", data)
    # Broadcast this event to ALL connected clients (including browsers)
    socketio.emit("java_connected", data, broadcast=True)

@socketio.on("thought")
def handle_java_connected(data):
    # Broadcast this event to ALL connected clients (including browsers)
    socketio.emit("thought", data, broadcast=True)

@socketio.on("disconnect")
def on_disconnect():
    """
    If the client disconnects unexpectedly, we treat it the same as "leave".
    This ensures the server doesn’t keep them stuck in a game.
    """
    print("disonnect triggered", file=sys.stderr)
    # Ensure game data is properly cleaned-up in case of unexpected disconnect
    user_id = request.sid
    if user_id not in USERS:
        return
    with USERS[user_id]:
        _leave_game(user_id)
    del USERS[user_id]


################
# on_exit hook #
################

def on_exit():
    """
    Called at server shutdown. We forcibly end every active game so it doesn’t hang.
    """
    for game_id in GAMES:
        socketio.emit(
            "end_game",
            {
                "status": Game.Status.INACTIVE,
                "data": get_game(game_id).get_data(),
            },
            room=game_id,
        )


#############
# Game Loop #
#############

def play_game(game: OvercookedGame, fps=6):
    """
    This function runs in a background thread for each active game. 
    Every 1/fps second, it:
      1) Locks the game
      2) Calls game.tick() to apply pending actions
      3) Emits "state_pong" to all clients with the new state
      4) If the game resets or ends, we broadcast that event and do the relevant cleanup
    
    Also sending "java_state_update" once every second so that the agent  can get an up to date information.
    """
    status = Game.Status.ACTIVE

    last_java_update = time.time()
    
    while status != Game.Status.DONE and status != Game.Status.INACTIVE:
        with game.lock:
            status = game.tick()
        if status == Game.Status.RESET:
            # If the game signals a "reset", we emit "reset_game", sleep for reset_timeout
            with game.lock:
                data = game.get_data()
            socketio.emit(
                "reset_game",
                {
                    "state": game.to_json(),
                    "timeout": game.reset_timeout,
                    "data": data,
                },
                room=game.id,
            )
            socketio.sleep(game.reset_timeout / 1000)
        else:
            # Otherwise, just a normal "state update"
            socketio.emit(
                "state_pong", {"state": game.get_state()}, room=game.id
            )
        # Send updates to the Java server side once per second
        now = time.time() # check the time now
        if now - last_java_update >= 0.2:
            # At 0.2 second, lock the game to read the state safely
            with game.lock:
                game_state = game.get_state()
            # Send the update and update the last time it has been sent so we can track it
            terrain = game.mdp.terrain_mtx
            socketio.emit("java_state_update", game_state, broadcast=True)
            socketio.emit("terrain_update", terrain, broadcast=True)
            last_java_update = now
        socketio.sleep(1 / fps)

    # Once we break out of the loop, it means the game is done or inactive
    with game.lock:
        data = game.get_data()
        socketio.emit(
            "end_game", {"status": status, "data": data}, room=game.id
        )
        
        if status != Game.Status.INACTIVE:
            game.deactivate()
        cleanup_game(game)

#############################
# Run the app if main       #
#############################
if __name__ == "__main__":
    # Dynamically parse host and port from environment variables (set by docker build)
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", 80))

    # Attach exit handler to ensure graceful shutdown
    atexit.register(on_exit)

    # https://localhost:80 is external facing address regardless of build environment
    socketio.run(app, host=host, port=port, log_output=app.config["DEBUG"])
