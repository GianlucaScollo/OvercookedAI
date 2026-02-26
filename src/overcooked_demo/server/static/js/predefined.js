// -----------------------------------------------------------------------------
// predefined.js
// -----------------------------------------------------------------------------
// Visit index.js first since some of the functions are similar/same and therefore
// not as extensively commented.
//
// This file is used for the "predefined.html" page, which runs a series of
// "predefined" Overcooked layouts back-to-back. It handles waiting rooms,
// timeouts, and transitions from one layout to the next.
//
// Like index.js, it uses Socket.IO to talk to the server. The server
// coordinates when each new layout starts, sends state updates, etc.
//
// -----------------------------------------------------------------------------

// Create a persistent Socket.IO connection.
var socket = io();

// A global config object that will parsed from the page.
var config;

// Example default experiment params (possibly overridden by server config).
var experimentParams = {
    layouts : ["cramped_room", "counter_circuit"],
    gameTime : 30,
    playerZero : "DummyAI"
};

// How long to wait in lobby before giving up
var lobbyWaitTime = 300000;

// Interval/timeout IDs so they can be cleared when needed
window.intervalID = -1;
window.ellipses = -1;
window.lobbyTimeout = -1;

/* -----------------------------------------------------------------------------
 * One click handler: if the user manually leaves the lobby, go back to "/"
which is index.html page.
 * -----------------------------------------------------------------------------
 */
$(function() {
    $('#leave-btn').click(function () {
        socket.emit("leave",{});
        window.location.href = "/"
    });
});

/* -----------------------------------------------------------------------------
 * Socket event handlers
 * -----------------------------------------------------------------------------
 */
socket.on('waiting', function(data) {
    // Show game lobby
    $('#game-over').hide();
    $("#overcooked").empty();
    $('#lobby').show();

    // If not currently in a game, keep trying to join
    if (!data.in_game) {
        if (window.intervalID === -1) {
            // Occassionally ping server to try and join
            window.intervalID = setInterval(function() {
                socket.emit('join', {});
            }, 1000);
        }
    }

    if (window.lobbyTimeout === -1) {
        // Waiting animation
        window.ellipses = setInterval(function () {
            var e = $("#ellipses").text();
            $("#ellipses").text(".".repeat((e.length + 1) % 10));
        }, 500);

        
        // Timeout to leave lobby if no-one is found
        window.lobbyTimeout = setTimeout(function() {
            socket.emit('leave', {});
        }, config.lobbyWaitTime)
    }
});

socket.on('creation_failed', function(data) {
    // Tell user what went wrong
    let err = data['error']
    $("#overcooked").empty();
    $('#overcooked').append(`<h4>Sorry, game creation code failed with error: ${JSON.stringify(err)}</>`);
    $("error-exit").show();

    // Let parent window know error occurred
    window.top.postMessage({ name : "error"}, "*");
});

socket.on('start_game', function(data) {
    // Hide game-over and lobby, show game title header
    if (window.intervalID !== -1) {
        clearInterval(window.intervalID);
        window.intervalID = -1;
    }
    if (window.lobbyTimeout !== -1) {
        clearInterval(window.ellipses);
        clearTimeout(window.lobbyTimeout);
        window.lobbyTimeout = -1;
        window.ellipses = -1;
    }
    graphics_config = {
        container_id : "overcooked",
        start_info : data.start_info
    };
    $("#overcooked").empty();
    $('#game-over').hide();
    $('#lobby').hide();
    $('#reset-game').hide();
    $('#game-title').show();


    enable_key_listener();
    graphics_start(graphics_config);
});

socket.on('reset_game', function(data) {
    // Called if the server wants to reset to a new layout mid-experiment
    graphics_end();
    disable_key_listener();


    $("#overcooked").empty();
    $("#reset-game").show();


    setTimeout(function() {
        $("#reset-game").hide();
        graphics_config = {
            container_id : "overcooked",
            start_info : data.state
        };
        graphics_start(graphics_config);
        enable_key_listener();

        // Propogate game stats to parent window 
        window.top.postMessage({ name : "data", data : data.data, done : false}, "*");
    }, data.timeout);
});

/*
socket.on('state_pong', function(data) {
    // Redraw the environment with the new game state
    drawState(data['state']);
});
*/

socket.on('state_pong', function(data){
  // advance drawing
  graphics_update(data.state);
});

socket.on('end_game', function(data) {
    // Hide game data and display game-over title
  graphics_end();
  disable_key_listener();
  $('#game-title').hide();
  $('#game-over').show();
  $("#overcooked").empty();

  // Game ended unexpectedly
  if (data.status === 'inactive') {
      $("#error").show();
      $("#error-exit").show();
  }

    // Propogate game stats to parent window
    window.top.postMessage({ name : "data", data : data.data, done : true }, "*");
});

socket.on('end_lobby', function() {
    // Display join game timeout text
    $("#finding_partner").text(
        "We were unable to find you a partner."
    );
    $("#error-exit").show();

    // Stop trying to join
    clearInterval(window.intervalID);
    clearInterval(window.ellipses);
    window.intervalID = -1;

    // Let parent window know what happened
    window.top.postMessage({ name : "timeout" }, "*");
})


/* -----------------------------------------------------------------------------
 * Keyboard event listeners
 * -----------------------------------------------------------------------------
 * Similar to index.js->watch arrow keys + space to send "action" to server.
 */
function enable_key_listener() {
    $(document).on('keydown', function(e) {
        let action = 'STAY';
        switch (e.which) {
            case 37: // left
                action = 'LEFT';
                break;
            case 38: // up
                action = 'UP';
                break;
            case 39: // right
                action = 'RIGHT';
                break;
            case 40: // down
                action = 'DOWN';
                break;
            case 32: // space
                action = 'SPACE';
                break;
            default:
                return; 
        }
        e.preventDefault();
        socket.emit('action', { 'action' : action });
    });
}

function disable_key_listener() {
    $(document).off('keydown');
}

/* -----------------------------------------------------------------------------
 * Game Initialisation
 * -----------------------------------------------------------------------------
 * On "connect", read our config from the page (#config), then automatically
 * attempt to join the experiment’s game.
 */
socket.on("connect", function() {
    // Load the config object from the hidden div
    set_config();

    // Config for this specific game
    let uid = $('#uid').text();
    let params = JSON.parse(JSON.stringify(config.experimentParams));
    let data = {
        "params" : params,
        "game_name" : "overcooked"
    };

    // create (or join if it exists) new game
    socket.emit("join", data);
});


/* -----------------------------------------------------------------------------
 * Utility Functions
 * -----------------------------------------------------------------------------
 */

// Convert an array from jQuery .serializeArray() to a JSON object
var arrToJSON = function(arr) {
    let retval = {}
    for (let i = 0; i < arr.length; i++) {
        elem = arr[i];
        key = elem['name'];
        value = elem['value'];
        retval[key] = value;
    }
    return retval;
};

var set_config = function() {
    // Populate the #config div
    config = JSON.parse($("#config").text());
}