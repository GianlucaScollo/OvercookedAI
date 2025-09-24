// ===== helpers to read server-injected fields =====
function get_text(id) {
  var el = document.getElementById(id);
  return el ? (el.textContent || el.innerText || '') : '';
}
function get_config() {
  try { return JSON.parse(get_text('config') || '{}'); } catch(e){ return {}; }
}
function get_next_url() { return (get_text('next_url') || '').trim(); }
function get_uid() { return (get_text('uid') || '').trim(); }

// ===== sockets, ui refs =====
var socket = io({ transports: ['websocket'] });
var uid = get_uid();
var config = get_config();
var nextUrl = get_next_url();

// DOM elements
var $lobby = $('#lobby');
var $gameTitle = $('#game-title');
var $gameOver = $('#game-over');
var $overcooked = $('#overcooked');
var $error = $('#error');
var $reset = $('#reset-game');

// Simple "Searching..." animation
(function ellipses(){
  var el = document.getElementById('ellipses'); if(!el) return;
  var n=1; setInterval(function(){ el.textContent = '.'.repeat(((n++)%3)+1); }, 500);
})();

// Show lobby immediately
$(function(){
  $lobby.show();
  // ask to join (or create) the predefined lobby/game
  // The server expects: { game_name, params }
  var exp = (config.experimentParams || {});
  var params = {
    playerZero: exp.playerZero || 'human',
    playerOne:  exp.playerOne  || 'human',
    dataCollection: exp.dataCollection ? 'on' : 'off',
    gameTime: exp.gameTime || 60
  };

  // single layout run: we pass 'layouts' if present, else 'layout'
  if (Array.isArray(exp.layouts) && exp.layouts.length > 0) {
    params.layouts = exp.layouts;
  } else if (exp.layout) {
    params.layout = exp.layout;
  }

  socket.emit('join', {
    game_name: 'overcooked',
    create_if_not_found: true,
    params: params
  });
});

// ===== socket handlers =====
socket.on('waiting', function(data){
  // still in lobby waiting for a second player
  $lobby.show();
  $gameTitle.hide(); $gameOver.hide(); $overcooked.empty();
});

socket.on('start_game', function(payload){
  // hide lobby, show game title & canvas
  $lobby.hide();
  $gameTitle.show();
  $gameOver.hide();
  $error.hide();

  // initialize graphics for the game
  var info = (payload && payload.start_info) || {};
  // graphics_init is provided by graphics.js (already included)
  graphics_init('overcooked', info);
});

socket.on('reset_game', function(payload){
  $reset.show();
  setTimeout(function(){ $reset.hide(); }, (payload && payload.timeout) || 1000);
});

socket.on('state_pong', function(data){
  // advance drawing
  graphics_update(data.state);
});

socket.on('end_game', function(data){
  // End of round
  graphics_end();
  $gameTitle.hide();
  $gameOver.show();
  $overcooked.empty();

  if (data && data.status === 'inactive') {
    $error.show();
  }

  // Auto-redirect to next step if provided
  if (nextUrl) {
    setTimeout(function(){ window.location.href = nextUrl; }, 600);
  }
});

// Allow leaving lobby manually
$('#leave-btn').on('click', function(){
  socket.emit('leave', {});
  window.location.href = '/';
});
