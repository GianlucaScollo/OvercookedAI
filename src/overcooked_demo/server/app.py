import os
import sys
import json, os, uuid
from uuid import uuid4
from flask import render_template, request, abort, redirect, url_for
from urllib.parse import quote

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

import subprocess

import  csv, hashlib
from flask import send_file

###########################
# Adding users to buckets #
###########################
BUCKET_FILE = os.path.join(os.path.dirname(__file__), "buckets.json")

def _load_buckets():
    if not os.path.exists(BUCKET_FILE):
        return {"cramped_first": 0, "forced_first": 0}
    with open(BUCKET_FILE, "r") as f:
        try:
            return json.load(f)
        except Exception:
            return {"cramped_first": 0, "forced_first": 0}

def _save_buckets(b):
    tmp = BUCKET_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(b, f)
    os.replace(tmp, BUCKET_FILE)

def assign_order_smaller_bucket():
    buckets = _load_buckets()
    # pick the smaller count (tie-break: cramped_first)
    if buckets["cramped_first"] <= buckets["forced_first"]:
        chosen = "cramped_first"
    else:
        chosen = "forced_first"
    buckets[chosen] += 1
    _save_buckets(buckets)
    return chosen

#################
# Global Config #
#################

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


RESULTS_CSV = CONFIG.get("results_csv", "data/game_results.csv")
_results_lock = Lock()
_active_games = {}  # room_id -> metadata

# Ensure folder exists
os.makedirs(os.path.dirname(RESULTS_CSV), exist_ok=True)

def _ensure_results_header():
    """Create CSV file with header if missing/empty."""
    if not os.path.exists(RESULTS_CSV) or os.path.getsize(RESULTS_CSV) == 0:
        with open(RESULTS_CSV, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "timestamp_utc",   # ISO8601
                "room_id",         # your room/session id
                "layout",          # selected layout name
                "score",           # final score
                "duration_sec",    # seconds
            ])

def _append_result(row):
    _ensure_results_header()
    with _results_lock, open(RESULTS_CSV, "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(row)


CHAT_CSV = CONFIG.get("chat_csv", "data/chat_logs.csv")
_chat_lock = Lock()

# Ensure folder exists for both results and chat (results folder likely already created)
os.makedirs(os.path.dirname(CHAT_CSV), exist_ok=True)

def _ensure_chat_header():
    """Create chat CSV file with header if missing/empty."""
    if not os.path.exists(CHAT_CSV) or os.path.getsize(CHAT_CSV) == 0:
        with open(CHAT_CSV, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "timestamp_utc",   # ISO8601 UTC time when the server received the message
                "room_id",         # the game room / session id
                "sender",          # 'user' | 'agent' | other
                "message"          # raw message text
            ])

def _append_chat_row(row):
    _ensure_chat_header()
    with _chat_lock, open(CHAT_CSV, "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(row)

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


############################
# Subprocess SymbolicAgent #
############################

# global flags
_agent_plans_ready = False
_agent_process = None
_AGENT_DIR = os.environ.get("SYMBOLIC_AGENT_DIR", "/app/SymbolicAIAgent")


def start_symbolic_agent():
    '''
    Start gradle run
    '''
    global _agent_process
    if _agent_process is not None and _agent_process.poll() is None:
        print("[agent] Already active, skip.")
        return

    agent_dir = os.path.abspath(_AGENT_DIR)
    print(f"[agent] Starting symbolic agent in: {agent_dir}")
    
    debug_kqml = os.getenv('DEBUG_KQML', 'false').lower() == 'true'
    print(f"[agent] DEBUG_KQML={debug_kqml}")

    env = os.environ.copy()
    env['DEBUG_KQML'] = str(debug_kqml).lower()
    env['ORG_GRADLE_PROJECT_debugKqml'] = str(debug_kqml).lower()
    
    try:
        _agent_process = subprocess.Popen(
            ["gradle", "run"],
            cwd=agent_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env
        )
        print(f"[agent] Agent started (PID {_agent_process.pid})")

        # ── DEBUG ───────────────────────────────────────────────────────────────────
        import threading
        def log_output(proc):
            for line in iter(proc.stdout.readline, b''):
                print(f"[gradle] {line.decode('utf-8', errors='replace').rstrip()}")
        t = threading.Thread(target=log_output, args=(_agent_process,), daemon=True)
        t.start()
        # ────────────────────────────────────────────────────────────────────────────

    except Exception as e:
        print(f"[agent] Agent start error: {e}")

def shutdown_symbolic_agent():
    """
    Sends the agent_shutdown signal via socket (the agent does System.exit(0))
    and then waits for the process to die as a fallback.
    """
    global _agent_process
    socketio.emit("agent_shutdown", {}, broadcast=True)
    print("[agent] agent_shutdown emitted.")
    # Wait for the process to finish (max 8s), then force kill
    if _agent_process and _agent_process.poll() is None:
        try:
            _agent_process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            print("[agent] Timeout — SIGKILL.")
            _agent_process.kill()
    _agent_process = None


#######################
# Flask Configuration #
#######################

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


######################################
# Global Coordination / Helper Funcs #
######################################

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


###################################
# Socket Handler Helper Functions #
###################################

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
            socketio.emit("waiting", {"in_game": True}, broadcast=True)


#####################
# Flask HTTP Routes #
#####################

# Read the route from environment (route1 or route2)
ROUTE = os.getenv('ROUTE', 'route1')

# Define the flow for each route
ROUTE_FLOWS = {
    'route1': [
        {'type': 'page', 'route': 'pre_questionnaire', 'params': {}},
        {'type': 'page', 'route': 'instructions', 'params': {}},
        {'type': 'game', 'layout': 'cramped_room', 'time': 60},
        {'type': 'page', 'route': 'survey_n', 'params': {'n': 1}},
        {'type': 'chat', 'params': {}},
        {'type': 'game', 'layout': 'cramped_room', 'time': 60},
        {'type': 'chat', 'params': {}},
        {'type': 'page', 'route': 'survey_n', 'params': {'n': 2}},
        {'type': 'game', 'layout': 'forced_coordination', 'time': 60},
        {'type': 'page', 'route': 'survey_n', 'params': {'n': 3}},
        {'type': 'chat', 'params': {}},
        {'type': 'game', 'layout': 'forced_coordination', 'time': 60},
        {'type': 'chat', 'params': {}},
        {'type': 'page', 'route': 'survey_n', 'params': {'n': 4}},
        {'type': 'page', 'route': 'post_questionnaire', 'params': {}},
        {'type': 'page', 'route': 'finish', 'params': {}},
    ],
    'route2': [
        {'type': 'page', 'route': 'pre_questionnaire', 'params': {}},
        {'type': 'page', 'route': 'instructions', 'params': {}},
        {'type': 'chat', 'params': {}},
        {'type': 'game', 'layout': 'cramped_room', 'time': 60},
        {'type': 'chat', 'params': {}},
        {'type': 'page', 'route': 'survey_n', 'params': {'n': 1}},
        {'type': 'game', 'layout': 'cramped_room', 'time': 60},
        {'type': 'page', 'route': 'survey_n', 'params': {'n': 2}},
        {'type': 'chat', 'params': {}},
        {'type': 'game', 'layout': 'forced_coordination', 'time': 60},
        {'type': 'chat', 'params': {}},
        {'type': 'page', 'route': 'survey_n', 'params': {'n': 3}},
        {'type': 'game', 'layout': 'forced_coordination', 'time': 60},
        {'type': 'page', 'route': 'survey_n', 'params': {'n': 4}},
        {'type': 'page', 'route': 'post_questionnaire', 'params': {}},
        {'type': 'page', 'route': 'finish', 'params': {}},
    ]
}

def get_next_step(order, step):
    """
    Get the next step in the route flow.
    Returns the next route and its parameters.
    """
    # Determine which flow to use (route1 or route2)
    flow = ROUTE_FLOWS.get(order, ROUTE_FLOWS['route1'])
    
    # Get the next step
    if step >= len(flow):
        return None, None
    
    current = flow[step]
    return current, step + 1


@app.route("/next-step")
def next_step():
    """
    Main router that decides the next page based on route flow.
    Query params: pid, order, step
    """
    pid = request.args.get('pid', '')
    order = request.args.get('order', 'route1')  # ← route1 or route2
    step = int(request.args.get('step', '0'))
    
    current, next_step_num = get_next_step(order, step)
    
    if current is None:
        # Flow ended, go to finish
        return redirect(f'/finish')
    
    # Build the next redirect URL
    def build_next_url(step_num):
        if step_num is None:
            return f'/finish'
        return f'/next-step?pid={pid}&order={order}&step={step_num}'
    
    next_url = build_next_url(next_step_num)
    next_url_encoded = quote(next_url, safe='')

    # Route based on type
    if current['type'] == 'page':
        route_name = current['route']
        
        if route_name == 'home':
            return redirect('/')
        elif route_name == 'pre_questionnaire':
            return redirect(url_for('pre_questionnaire') + f'?pid={pid}&order={order}&next={next_url_encoded}')
        elif route_name == 'instructions':
            return redirect(url_for('instructions') + f'?pid={pid}&order={order}&next={next_url_encoded}')
        elif route_name == 'post_questionnaire':
            return redirect(url_for('post_questionnaire') + f'?pid={pid}&order={order}&next={next_url_encoded}')
        elif route_name == 'finish':
            return redirect('/finish')
        elif route_name == 'survey_n':
            n = current['params'].get('n', 1)
            return redirect(url_for('survey_n', n=n) + f'?pid={pid}&order={order}&next={next_url_encoded}')
    
    elif current['type'] == 'game':
        layout = current['layout']
        time = current['time']
        return redirect(f'/play/{layout}?time={time}&next={next_url_encoded}&pid={pid}&order={order}')
    
    elif current['type'] == 'chat':
        return redirect(f'/chat-room?next={next_url_encoded}&pid={pid}&order={order}')
    
    return redirect('/')


# ── HOME PAGE ─────────────────────────────────────────────────────────────

@app.route("/")
def home():
    return render_template("home.html")

# ── ENTRY POINT ───────────────────────────────────────────────────────────

@app.route("/enter")
def enter():
    """
    Entry point: create a new participant and start the experiment.
    """
    pid = uuid.uuid4().hex[:8]  # short, human-friendly
    # Determine which route based on ROUTE env var
    order = ROUTE
    return redirect(f'/next-step?pid={pid}&order={order}&step=0')

# ── DOWNLOAD RESULTS/CHAT ─────────────────────────────────────────────────

@app.route("/results.csv")
def download_results():
    _ensure_results_header()
    return send_file(RESULTS_CSV, as_attachment=True, download_name="game_results.csv")

@app.route("/chat.csv")
def download_chat():
    _ensure_chat_header()
    return send_file(CHAT_CSV, as_attachment=True, download_name="chat_logs.csv")

# ── PRE/POST QUESTIONNAIRE ────────────────────────────────────────────────

@app.route("/pre-questionnaire")
def pre_questionnaire():
    pid = request.args.get('pid', '')
    order = request.args.get('order', 'route1')
    next_url = request.args.get('next', '/finish')
    
    return render_template(
        "pre_questionnaire.html", 
        pid=pid,
        order=order,
        next_url=next_url
    )

@app.route("/post-questionnaire")
def post_questionnaire():
    pid = request.args.get('pid', '')
    order = request.args.get('order', 'route1')
    next_url = request.args.get('next', '/finish')
    
    return render_template(
        "post_questionnaire.html",
        pid=pid,
        order=order,
        next_url=next_url
    )

# ── FINISH ───────────────────────────────────────────────────────────────

@app.route("/finish")
def finish():
    # thank you page
    return render_template("finish.html")

# ── INSTRUCTIONS ─────────────────────────────────────────────────────────

@app.route("/instructions")
def instructions():
    pid = request.args.get('pid', '')
    order = request.args.get('order', 'route1')
    next_url = request.args.get('next', '/finish')
    
    return render_template(
        "instructions.html",
        layout_conf=LAYOUT_GLOBALS,
        pid=pid,
        order=order,
        next_url=next_url
    )

# ── PLAY LAYOUT ──────────────────────────────────────────────────────────

@app.route("/play/<layout>")
def play_locked(layout):
    pid = request.args.get('pid', '')
    order = request.args.get('order', 'route1')
    
    # default time from config, but overridable via ?time=XX
    default_time = CONFIG.get("predefined", {}).get("experimentParams", {}).get("gameTime", 60)
    locked_time = int(request.args.get("time", default_time))
    next_url = request.args.get("next", "/post-questionnaire")

    # Reuse index.html but pass "lock" flags so the page hides controls and auto-joins
    return render_template(
        "index.html",
        layouts=LAYOUTS,
        locked_layout=layout,
        locked_time=locked_time,
        auto_join=False,
        next_url=next_url,
        pid=pid,
        order=order
    )

# ── SURVEY ──────────────────────────────────────────────────────────────

@app.route("/survey/<int:n>")
def survey_n(n):
    pid = request.args.get('pid', '')
    order = request.args.get('order', 'route1')
    next_url = request.args.get('next')
    
    tmpl = f"survey{n}.html"
    if not os.path.exists(os.path.join(app.template_folder or "templates", tmpl)):
        abort(404)
    
    return render_template(
        tmpl, 
        pid=pid,
        order=order,
        next_url=next_url
    )

# ── CHAT-ROOM ───────────────────────────────────────────────────────────

@app.route("/chat-room")
def chat_room():
    next_url = request.args.get("next", "/")
    pid = request.args.get('pid', '')
    order = request.args.get('order', 'route1')
    
    return render_template(
        "chat_room.html", 
        next_url=next_url, 
        pid=pid, 
        order=order
    )

# ── AGENT STATUS ────────────────────────────────────────────────────────

@app.route("/agent/status")
def agent_status():
    global _agent_process
    is_running = _agent_process is not None and _agent_process.poll() is None
    return jsonify({"running": is_running})



############################
# Socket.IO Event Handlers #
############################
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

        # ---- NEW: capture layout + real room_id after creation ----
        params = data.get("params", {})
        layout = params.get("layout")
        if not layout:
            layouts = params.get("layouts", [])
            layout = layouts[0] if layouts else None

        room_id = get_curr_room(user_id)  # <- use the real room the server put us in
        _active_games[room_id] = {
            "layout": layout or "",
            "start_ts": datetime.utcnow()
        }


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
        params = data.get("params", {})  # <-- define params once, used below

        # Retrieve current game if one exists
        curr_game = get_curr_game(user_id)
        if curr_game:
            # Cannot join if currently in a game
            return

        # Try to get a waiting game from the queue
        game = get_waiting_game()

        if not game and create_if_not_found:
            # If no waiting game is found, create a new one
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
                    # ---- Re-seed layouts if empty so activate() can pop safely ----
                    layout_from_client = None
                    try:
                        layout_from_client = (
                            (params.get('layouts') or [None])[0]  # prefer array if present
                            or params.get('layout')
                            or params.get('layout_name')
                        )
                    except Exception:
                        layout_from_client = None

                    if not getattr(game, 'layouts', None) or len(game.layouts) == 0:
                        if layout_from_client:
                            game.layouts = [layout_from_client]
                        elif getattr(game, 'curr_layout', None):
                            game.layouts = [game.curr_layout]
                        else:
                            game.layouts = ['cramped_room']  # safe fallback
                    # ----------------------------------------------------------------

                    game.activate()
                    ACTIVE_GAMES.add(game.id)
                    
                    layout_info = {
                        "layout_name": game.curr_layout,       # e.g. 'cramped_room'
                        "terrain": game.mdp.terrain_mtx,       # 2D list
                        "state": game.get_state()
                    }
                    socketio.emit("java_layout", layout_info, broadcast=True)

                    # Background task that wait the plans and after send start_game
                    socketio.start_background_task(
                        wait_plans_then_start, game, spectating=False
                    )
                else:
                    # Still need to keep waiting for players
                    WAITING_GAMES.put(game.id)
                    emit("waiting", {"in_game": True}, room=game.id)
                    socketio.emit("waiting", {"in_game": True}, broadcast=True)


def wait_plans_then_start(game: OvercookedGame, spectating=False, timeout=40):
    """
    Waits for agent:plans_ready, then send start_game and runs play_game().
    The browser only sees the game when the agent is actually ready.
    """
    global _agent_plans_ready
    _agent_plans_ready = False

    waited = 0
    while not _agent_plans_ready and waited < timeout:
        socketio.sleep(0.5)
        waited += 0.5

    if not _agent_plans_ready:
        print(f"[server] Timeout {timeout}s — forced start_game", file=sys.stderr)
    else:
        print(f"[server] Plans ready after {waited}s → start_game", file=sys.stderr)

    socketio.emit(
        "start_game",
        {"spectating": spectating, "start_info": game.to_json()},
        room=game.id,
    )
    play_game(game)


#@socketio.on("leave")
#def on_leave(data):
#    """
#    If the user clicks "Leave" or otherwise triggers a leave event, we remove them from the game,
#    possibly ending the game for all players if it's active.
#    """
#    user_id = request.sid
#    with USERS[user_id]:
#        was_active = _leave_game(user_id)
#
#        if was_active:
#            emit("end_game", {"status": Game.Status.DONE, "data": {}})
#        else:
#            emit("end_lobby")


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


@socketio.on("chat:join")
def on_chat_join(data):
    user_id = request.sid

    if user_id not in USERS:
        USERS[user_id] = Lock()

    try:
        with USERS[user_id]:
            room = (data or {}).get("room", "lobby")
            join_room(room)
            print(f"[CHAT] {user_id} joined room: {room}", file=sys.stderr)
            emit("chat:joined", {"room": room})
    except Exception as e:
        print(f"[CHAT] on_chat_join fallback (no lock): {e}", file=sys.stderr)


@socketio.on("chat:send")
def handle_chat_send(data):
    message = (data or {}).get("message", "").strip()
    if not message:
        return

    # Fallback: if not in a room, use their own session id (still logs, won't crash)
    user_id = request.sid
    room_id = get_curr_room(user_id)
    if not room_id:
        room_id = user_id

    sender = (data or {}).get("sender", "").strip()
    payload = {"sender": sender, "message": message}
    
    # Broadcast of the message recevied
    emit("chat:message", payload, broadcast=True, include_self=False)

    # Log it
    _append_chat_row([
        datetime.utcnow().isoformat(timespec="seconds") + "Z",
        room_id,
        sender,
        message
    ])


########################
# Agent Event Handlers #
########################

@socketio.on("agent:start")
def on_agent_start(data):
    print(f"[server] agent:start (context={data.get('context','?')})")
    start_symbolic_agent()

@socketio.on("agent:shutdown")
def on_agent_shutdown(data):
    print(f"[server] agent:shutdown (context={data.get('context','?')})")
    shutdown_symbolic_agent()

@socketio.on("agent:plans_ready")
def on_agent_plans_ready(data):
    global _agent_plans_ready
    _agent_plans_ready = True
    print("[server] agent:plans_ready")
    socketio.emit("agent:plans_ready", {}, broadcast=True)

@socketio.on("agent:chat_ready")
def on_agent_chat_ready(data):
    print("[server] agent:chat_ready")
    # Broadcast to browser + Interpreter
    socketio.emit("agent:chat_ready", {}, broadcast=True)


########################
# Java side connection #
########################

@socketio.on("java_connected")
def handle_java_connected(data):
    print("Got java_connected event from Java with data:", data)
    # Report the browser that the agent is ready (enables _agentReady and Continue)
    socketio.emit("agent:ready", {}, broadcast=True)
    # Report to index.js (show UI message)
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
        try:
        # Prefer the engine’s own fields for accuracy
            layout_name = getattr(game, "curr_layout", _active_games.get(game.id, {}).get("layout", ""))
        # game.start_time is set in OvercookedGame.activate()
            duration_sec = 0
            try:
                import time as _t
                duration_sec = int(max(0, _t.time() - getattr(game, "start_time", _t.time())))
            except Exception:
                pass

            final_score = getattr(game, "score", "")
            _append_result([
                datetime.utcnow().isoformat(timespec="seconds") + "Z",
                game.id,
                layout_name,
                final_score,
                duration_sec,
                ])
        except Exception as e:
            app.logger.error(f"Failed to append game result for game {game.id}: {e}")

        if status != Game.Status.INACTIVE:
            game.deactivate()
        cleanup_game(game)

#######################
# Run the app if main #
#######################
if __name__ == "__main__":
    # Dynamically parse host and port from environment variables (set by docker build)
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", 80))

    # Attach exit handler to ensure graceful shutdown
    atexit.register(on_exit)

    # https://localhost:80 is external facing address regardless of build environment
    socketio.run(app, host=host, port=port, log_output=app.config["DEBUG"])
