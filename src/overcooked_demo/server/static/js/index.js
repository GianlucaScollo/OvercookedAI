// This file powers the main Overcooked Demo page (index.html).
// It uses Socket.IO to communicate with the server, handling the creation/joining
// of games, keyboard inputs, and real-time state updates. It also updates the UI
// (e.g., "Create Game" button) based on server events.
//
// The code sets up jQuery click handlers for the "create", "join", and "leave"
// buttons, and listens for Socket.IO events like "start_game" or "end_game"
// to start or stop the Overcooked game in the browser.
//
// -----------------------------------------------------------------------------

// Create a persistent Socket.IO connection with the server.
var socket = io();

// Expose the socket globally, so the function withSocket() in index.html
// can find it via window.socket.
window.socket = socket;

/* -----------------------------------------------------------------------------
 * Button click event handlers
 * -----------------------------------------------------------------------------
 *  jQuery functions are attached to various UI buttons so that when a user clicks
 * "Create Game" or "Join", the server is notified.
 * -----------------------------------------------------------------------------
 */


// The #create and #join click handlers have been moved to index.html 
// to properly handle agent:start emission as well.

/*
$(function() {
    // When "Create Game" is clicked
    $('#create').click(function () {
        // Convert all form data (player agent selection, layout, etc.) to JSON
        params = arrToJSON($('form').serializeArray());
        // The "layouts" array is just a single layout chosen in the drop-down
        params.layouts = [params.layout];

        data = {
            "params": params,
            "game_name": "overcooked",
            "create_if_not_found": false
        };
        // Emit a "create" event to the server with these parameters
        socket.emit("create", data);

        // Update UI: show the "waiting" text, hide the create/join buttons
        $('#waiting').show();
        $('#join').hide();
        $('#join').attr("disabled", true);
        $('#create').hide();
        $('#create').attr("disabled", true);

        // Hide links to instructions/tutorial while waiting
        $("#instructions").hide();
        $('#tutorial').hide();
    });
});
*/

$(function(){
    // Initialises any Bootstrap tooltips that might be on the page
    $('[data-toggle="tooltip"]').tooltip({
      delay: { "show": 100, "hide": 100 } 
    });
});

/*
$(function() {
    // When "Join Existing Game" is clicked
    $('#join').click(function() {
        // Emit a "join" event with no extra data
        socket.emit("join", {});
        // Disable both join & create while waiting
        $('#join').attr("disabled", true);
        $('#create').attr("disabled", true);
    });
});

$(function() {
    // When "Leave" is clicked
    $('#leave').click(function() {
        socket.emit('leave', {});
        $('#leave').attr("disabled", true);
    });
});
*/


/* -----------------------------------------------------------------------------
 * Socket.IO event handlers
 * -----------------------------------------------------------------------------
 * The server emits these events to notify the client of what’s happening.
 * We listen for them and update the browser UI or game state accordingly.
 * -----------------------------------------------------------------------------
 */

// A global interval ID used if we repeatedly attempt to join an existing game.
window.intervalID = -1;
// If the user is 'spectating' (not controlling a chef), this can be set to true
window.spectating = true;

/**
 * "waiting": The server says "you’re in the waiting room".
 *   -Waiting for another player to join.
 */
socket.on('waiting', function(data) {
    // Hide or reset certain UI elements
    $('#error-exit').hide();
    $('#waiting').hide();
    $('#game-over').hide();
    $('#instructions').hide();
    $('#tutorial').hide();
    $("#overcooked").empty();

    // Show the "lobby" status
    $('#lobby').show();

    // Hide join/create buttons (since already waiting)
    //$('#join').hide();
    //$('#join').attr("disabled", true);
    $('#survey').hide();
    $('#survey').attr("disabled", true);
    $('#create').hide();
    $('#create').attr("disabled", true);

    // Show "Leave" button
    //$('#leave').show();
    //$('#leave').attr("disabled", false);

    // If not currently in a game, periodically attempt to "join"
    if (!data.in_game) {
        if (window.intervalID === -1) {
            window.intervalID = setInterval(function() {
                socket.emit('join', {});
            }, 1000);
        }
    }
});

/**
 * "creation_failed": The server tried to create a new game but there was an error.
 *   - display the error, re-enable the user’s ability to create/join again.
 */
socket.on('creation_failed', function(data) {
    let err = data['error'];
    $("#overcooked").empty();
    $('#lobby').hide();

    // Show instructions/tutorial again
    $("#instructions").show();
    $('#tutorial').show();
    $('#waiting').hide();

    // Re-enable join/create
    //$('#join').show();
    //$('#join').attr("disabled", false);
    $('#create').show();
    $('#create').attr("disabled", false);

    // Display the error inside the "overcooked" container
    $('#overcooked').append(
        `<h4>Sorry, game creation code failed with error: ${JSON.stringify(err)}</h4>`
    );
});

/**
 * "start_game": A new Overcooked game is ready to go.
 *   - The server provides initial game data (start_info).
 *   - Display/hide relevant UI elements and initialise the Phaser graphics.
 */
socket.on('start_game', function(data) {
    // If user was trying to join repeatedly, stop
    if (window.intervalID !== -1) {
        clearInterval(window.intervalID);
        window.intervalID = -1;
    }

    let graphics_config = {
        container_id : "overcooked",   // The DOM element ID for the game
        start_info : data.start_info   // Initial environment state from server
    };

    // Check if the server says we’re spectating or controlling a chef
    window.spectating = data.spectating;

    // Hide certain elements, show game area
    $('#error-exit').hide();
    $("#overcooked").empty();
    $('#game-over').hide();
    $('#lobby').hide();
    $('#waiting').hide();
    //$('#join').hide();
    //$('#join').attr("disabled", true);
    $('#survey').hide();
    $('#survey').attr("disabled", true);
    $('#create').hide();
    $('#create').attr("disabled", true);
    $("#instructions").hide();
    $('#tutorial').hide();
    //$('#leave').show();
    //$('#leave').attr("disabled", false);
    $('#game-title').show();
   

    // If we are an active player, enable keyboard input
    if (!window.spectating) {
        enable_key_listener();
    }

    // Start rendering the Overcooked environment (Phaser initialisation)
    graphics_start(graphics_config);
});


/**
 * "reset_game": The game environment is resetting (e.g., new layout or next round).
 *   - End the old Phaser graphics and start anew after a timeout.
 */
socket.on('reset_game', function(data) {
    graphics_end();
    if (!window.spectating) {
        disable_key_listener();
    }

    // Clear the display
    $("#overcooked").empty();
    $("#reset-game").show();

    // After a given timeout, start again with the new state
    setTimeout(function() {
        $("reset-game").hide();
        let graphics_config = {
            container_id : "overcooked",
            start_info : data.state
        };
        if (!window.spectating) {
            enable_key_listener();
        }
        graphics_start(graphics_config);
    }, data.timeout);
});

/**
 * "state_pong": A regular state update from the server.
 *   - Call drawState(...) to update the Phaser display.
 */
socket.on('state_pong', function(data) {
    drawState(data['state']);
});


/**
 * "end_game": The server signals the game is finished.
 *   - End all graphics, disable key listener, and revert the UI to a post-game state.
 */
socket.on('end_game', function(data) {
    // End the Phaser session
    graphics_end();
    if (!window.spectating) {
        disable_key_listener();
    }

    // Hide in-game elements
    $('#game-title').hide();
    $('#game-over').show();

    

    //$("#join").show();
    //$('#join').attr("disabled", false);
    $("#survey").show();
    $('#survey').attr("disabled", false);
    $("#create").hide();
    $('#create').attr("disabled", false);
    $("#instructions").hide();
    $('#tutorial').hide();
    //$("#leave").hide();
    //$('#leave').attr("disabled", true);

    // If the game ended unexpectedly (another user disconnected, etc.)
    if (data.status === 'inactive') {
        $('#error-exit').show();
    }
});

/**
 * "end_lobby": The waiting room is closed.
 *   -time ran out or the user left.
 */
socket.on('end_lobby', function() {
    // Hide lobby
    $('#lobby').hide();

    // Show/enable create/join
    //$("#join").show();
    //$('#join').attr("disabled", false);
    $("#create").show();
    $('#create').attr("disabled", false);
    //$("#leave").hide();
    //$('#leave').attr("disabled", true);
    $("#instructions").hide();
    $('#tutorial').hide();

    // Stop the repeated attempts to join
    clearInterval(window.intervalID);
    window.intervalID = -1;
});


/* -----------------------------------------------------------------------------
 * Game Key Event Listener
 * -----------------------------------------------------------------------------
 * enable_key_listener / disable_key_listener let the user control a chef in-game
 * by sending "action" events to the server: LEFT/RIGHT/UP/DOWN/SPACE.
 * -----------------------------------------------------------------------------
 */

/**
 * enable_key_listener():
 *   - Attach a keydown event that interprets arrow keys or WASD and spacebar
 *     and sends them to the server as actions.
 */

function enable_key_listener() {
    $(document).on('keydown', function(e) {
        let action = 'STAY';
        switch (e.which) {
            case 37: // left arrow
            case 65: // A key
                action = 'LEFT';
                break;
            case 38: // up arrow
            case 87: // W key
                action = 'UP';
                break;
            case 39: // right arrow
            case 68: // D key
                action = 'RIGHT';
                break;
            case 40: // down arrow
            case 83: // S key
                action = 'DOWN';
                break;
            case 32: // space
                action = 'SPACE';
                break;
            default:
                // If it's not one of the above keys, do nothing
                return; 
        }
        e.preventDefault();
        socket.emit('action', { 'action' : action });
    });
};


/**
 * disable_key_listener():
 *   - Remove the keydown handler so the user no longer controls anything.
 */
function disable_key_listener() {
    $(document).off('keydown');
};

/* -----------------------------------------------------------------------------
 * Java connection
 * -----------------------------------------------------------------------------
 */
// Listen for a custom "java_connected" event from the server
socket.on('java_connected', function(data) {
    console.log("Received 'java_connected' from server with data:", data);
    // Append a message to the #overcooked div
    $('#overcooked').append(
        '<p style="color:green;">Java agent says: ' + data + '</p>'
    );
});

// Listen for thought messages (what the agent is doing) from the server.
socket.on("thought", function(data) {
    console.log("Received thought:", data.thought);
    // Only display if the user has enabled thought display.
    if (window.showThoughts) {
        // Update the #thoughts element (you can choose to replace or append the message)
        $("#thoughts").text(data);
        // Optionally, you could fade in/out the text, or add a timestamp, etc.
    }
});

$(document).ready(function () {
    /* ---------------- Thought toggle ---------------- */
    window.showThoughts = false;
    $("#toggle-thoughts").on("change", function () {
      window.showThoughts = this.checked;
      if (!this.checked) {
        $("#thoughts").text(""); // clear if disabled
      }
    });
  
    /* ---------------- Chat widget wiring ---------------- */
    const $panel = $("#chat-panel");
    const $toggle = $("#chat-toggle");
    const $close = $("#chat-close");
    const $body = $("#chat-body");
    const $input = $("#chat-input");
    const $send = $("#chat-send");
  
    function openChat() {
      $panel.show();
      $input.focus();
    }
    function closeChat() {
      $panel.hide();
    }
    function toggleChat() {
      $panel.is(":visible") ? closeChat() : openChat();
    }
  
    // Toggle handlers
    $toggle.on("click", function (e) {
      e.preventDefault();
      toggleChat();
    });
    $close.on("click", function (e) {
      e.preventDefault();
      closeChat();
    });
  
    // Escape to close
    $(document).on("keydown.chat", function (e) {
      if (e.key === "Escape" && $panel.is(":visible")) closeChat();
    });
  
    // Simple HTML escape
    function esc(s) {
      return String(s).replace(/[&<>"']/g, (m) => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;"
      }[m]));
    }
  
    // Append a line to the chat body
    function appendLine(sender, text) {
      const html = `<div class="chat-line"><span class="sender">${esc(sender)}:</span><span class="text"> ${esc(text)}</span></div>`;
      $body.append(html);
      // Scroll to bottom
      $body.scrollTop($body[0].scrollHeight);
    }
  
    // Send a chat message
    function sendChat() {
      const text = $input.val().trim();
      if (!text) return;
      socket.emit("chat:send", { text: text });
      // rely on server broadcast to append (single source of truth)
      $input.val("");
    }
  
    // Click send
    $send.on("click", function (e) {
      e.preventDefault();
      sendChat();
    });
  
    // Enter to send
    $input.on("keydown", function (e) {
      if (e.key === "Enter") {
        e.preventDefault();
        sendChat();
      }
    });
  
    // Receive broadcast messages
    socket.on("chat:message", function (data) {
      appendLine(data.sender || "unknown", data.text || "");
    });
  
    // (Optional) Auto-open chat when the game ends:
    // socket.on("end_game", function () { openChat(); });
  
    // Debug: ensure elements exist
    if (!$("#chat-toggle").length || !$("#chat-panel").length) {
      console.warn("[chat] Chat HTML not found in DOM.");
    }
  });
  
  
/* -----------------------------------------------------------------------------
 * Utility Functions
 * -----------------------------------------------------------------------------
 */

/**
 * arrToJSON():
 *   - Takes an array of form elements (like from $('form').serializeArray())
 *     and converts them to a key-value object.
 *
 * Example:
 *   [{name: "playerZero", value: "human"}, {name: "layout", value: "cramped_room"}]
 *   becomes { playerZero: "human", layout: "cramped_room" }
 */

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
