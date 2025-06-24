from flask import Flask, request
from flask_socketio import SocketIO, emit, join_room, leave_room
import random
import time
import uuid

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")

players = {}  # sid -> player info: {id, name, score, wickets, room, connected, last_active}
rooms = {}    # room_id -> {sids: [sid1, sid2], moves: {}, chat: [], start_time, current_turn_sid, game_over, ai_player_sid (optional)}

RECONNECT_TIMEOUT = 300  # seconds to keep disconnected player state

def reset_room(room_id):
    if room_id in rooms:
        for sid in rooms[room_id]['sids']:
            leave_room(room_id, sid=sid)
        del rooms[room_id]

def get_opponent_sid(room, sid):
    return room['sids'][1] if room['sids'][0] == sid else room['sids'][0]

def current_time():
    return time.strftime("%H:%M:%S", time.localtime())

@app.route("/")
def index():
    return "Hand Cricket Server is Running!"

@socketio.on('connect')
def handle_connect():
    print(f"Client connected: {request.sid}")

@socketio.on('disconnect')
def handle_disconnect():
    sid = request.sid
    print(f"Client disconnected: {sid}")
    player = players.get(sid)
    if not player:
        return

    player['connected'] = False
    player['last_active'] = time.time()

    room_id = player.get('room')
    if not room_id or room_id not in rooms:
        players.pop(sid, None)
        return

    room = rooms[room_id]
    # Inform opponent about disconnect
    opponent_sid = get_opponent_sid(room, sid)
    emit('chat', {'from': 'System', 'msg': f"{player['name']} disconnected!"}, room=room_id)
    
    # Mark player as disconnected, keep state for reconnect timeout
    # Start a background task to clean up if no reconnect
    socketio.start_background_task(cleanup_player, sid, room_id)

def cleanup_player(sid, room_id):
    # Wait for reconnect timeout
    time.sleep(RECONNECT_TIMEOUT)
    player = players.get(sid)
    if player and not player['connected'] and player.get('room') == room_id:
        # Player didn't reconnect
        print(f"Cleaning up player {sid} after timeout")
        room = rooms.get(room_id)
        if room:
            # Inform opponent about game end due to disconnect
            opponent_sid = get_opponent_sid(room, sid)
            emit('chat', {'from': 'System', 'msg': f"{player['name']} did not reconnect. Game ended."}, room=room_id)
            emit('game_over', {'msg': 'Opponent disconnected. You win by default!'}, room=opponent_sid)
            reset_room(room_id)
        players.pop(sid, None)

@socketio.on('reconnect_player')
def handle_reconnect(data):
    player_id = data.get('player_id')
    name = data.get('name')
    sid = request.sid
    print(f"Player attempting reconnect: {player_id} ({name}) with sid {sid}")

    # Find player by id
    for old_sid, p in players.items():
        if p['id'] == player_id:
            # Update sid
            players[sid] = p
            players[sid]['connected'] = True
            players[sid]['last_active'] = time.time()
            players.pop(old_sid)
            
            room_id = p.get('room')
            if room_id and room_id in rooms:
                join_room(room_id)
                emit('chat', {'from': 'System', 'msg': f"{name} reconnected."}, room=room_id)
                # Send current game state to reconnecting player
                send_game_state(sid, rooms[room_id], players[sid])
                return

    # Not found - treat as new join
    emit('reconnect_failed', {'msg': 'No saved session found. Please join again.'})

def send_game_state(sid, room, player):
    # Send scores, moves (last turn?), chat history, whose turn, etc.
    opponent_sid = get_opponent_sid(room, sid)
    opponent = players.get(opponent_sid, {})
    emit('reconnect_success', {
        'your_score': player['score'],
        'your_wickets': player['wickets'],
        'opponent_name': opponent.get('name', ''),
        'opponent_score': opponent.get('score', 0),
        'opponent_wickets': opponent.get('wickets', 0),
        'your_turn': room['current_turn_sid'] == sid,
        'chat_history': room['chat']
    }, room=sid)

@socketio.on('join')
def handle_join(data):
    sid = request.sid
    name = data.get('name')
    wants_ai = data.get('ai', False)

    # Assign persistent player ID
    player_id = str(uuid.uuid4())
    players[sid] = {'id': player_id, 'name': name, 'score': 0, 'wickets': 0, 'room': None, 'connected': True, 'last_active': time.time()}
    print(f"Player {name} joined with sid {sid}, wants_ai={wants_ai}")

    # Matchmaking
    waiting = [s for s, p in players.items() if s != sid and p['room'] is None and p['connected']]
    if wants_ai:
        # Create AI room vs this player
        room_id = f"room_ai_{sid[:5]}"
        rooms[room_id] = {
            'sids': [sid],
            'moves': {},
            'chat': [],
            'start_time': time.time(),
            'current_turn_sid': sid,
            'game_over': False,
            'ai_player_sid': 'AI_BOT'
        }
        players[sid]['room'] = room_id
        emit('start', {
            'you': name,
            'opponent': 'AI Bot',
            'your_turn': True,
            'player_id': player_id,
        }, room=sid)
        # AI does not join room, handled separately
        return

    if waiting:
        opponent_sid = waiting[0]
        room_id = f"room_{opponent_sid[:5]}_{sid[:5]}"
        join_room(room_id, sid)
        join_room(room_id, opponent_sid)
        rooms[room_id] = {
            'sids': [opponent_sid, sid],
            'moves': {},
            'chat': [],
            'start_time': time.time(),
            'current_turn_sid': None,
            'game_over': False,
            'ai_player_sid': None
        }
        players[sid]['room'] = room_id
        players[opponent_sid]['room'] = room_id

        # Toss
        striker = random.choice([sid, opponent_sid])
        rooms[room_id]['current_turn_sid'] = striker

        for s in rooms[room_id]['sids']:
            emit('start', {
                'you': players[s]['name'],
                'opponent': players[opponent_sid if s == sid else sid]['name'],
                'your_turn': s == striker,
                'player_id': players[s]['id'],
            }, room=s)

        emit('chat', {'from': 'System', 'msg': 'Match started!'}, room=room_id)
    else:
        emit('waiting', {'msg': 'Waiting for opponent...'}, room=sid)

@socketio.on('play')
def handle_play(data):
    sid = request.sid
    number = data.get('number')

    if number is None or not isinstance(number, int) or not (0 <= number <= 6):
        emit('error', {'msg': 'Invalid number. Must be integer 0-6.'}, room=sid)
        return

    player = players.get(sid)
    if not player or not player.get('room'):
        emit('error', {'msg': 'You are not in a game.'}, room=sid)
        return

    room_id = player['room']
    if room_id not in rooms:
        emit('error', {'msg': 'Game room not found.'}, room=sid)
        return

    room = rooms[room_id]
    if room['game_over']:
        emit('error', {'msg': 'Game already over.'}, room=sid)
        return

    # Enforce turn
    if room['current_turn_sid'] != sid:
        emit('error', {'msg': 'Not your turn!'}, room=sid)
        return

    # Record move
    room['moves'][sid] = number
    print(f"{player['name']} played: {number}")

    # For AI game, simulate AI move immediately
    if room['ai_player_sid'] == 'AI_BOT':
        ai_number = random.randint(0, 6)
        print(f"AI Bot played: {ai_number}")
        room['moves']['AI_BOT'] = ai_number
        process_moves(room_id)
    else:
        # Wait for both players to move
        if len(room['moves']) == 2:
            process_moves(room_id)
        else:
            # Switch turn to opponent
            opponent_sid = get_opponent_sid(room, sid)
            room['current_turn_sid'] = opponent_sid
            emit('your_turn', {'msg': 'Your turn!'}, room=opponent_sid)
            emit('waiting_turn', {'msg': 'Waiting for opponent...'}, room=sid)

def process_moves(room_id):
    room = rooms[room_id]
    sids = room['sids'] if room['ai_player_sid'] is None else [room['sids'][0], 'AI_BOT']
    n1 = room['moves'].get(sids[0])
    n2 = room['moves'].get(sids[1])

    # Defensive check
    if n1 is None or n2 is None:
        return

    p1 = players.get(sids[0])
    p2 = players.get(sids[1]) if sids[1] != 'AI_BOT' else None

    # If numbers are same - wicket
    if n1 == n2:
        if p1: p1['wickets'] += 1
        if p2: p2['wickets'] += 1
        msg = f"Wicket! Both played {n1}."
        room['chat'].append({'from': 'System', 'msg': msg, 'time': current_time()})
        emit('chat', {'from': 'System', 'msg': msg}, room=room_id)
    else:
        if p1: p1['score'] += n1
        if p2: p2['score'] += n2
        msg = f"Scores this turn - {p1['name']}: {n1}, {p2['name'] if p2 else 'AI Bot'}: {n2}"
        room['chat'].append({'from': 'System', 'msg': msg, 'time': current_time()})
        emit('chat', {'from': 'System', 'msg': msg}, room=room_id)

    # Clear moves
    room['moves'] = {}

    # Check game over conditions (example: 10 wickets or score limit)
    max_wickets = 10
    max_score = 50  # example winning score

    game_over = False
    winner = None

    def check_win(p):
        return p and (p['wickets'] >= max_wickets or p['score'] >= max_score)

    if check_win(p1) and check_win(p2):
        # Draw
        game_over = True
        winner = None
    elif check_win(p1):
        game_over = True
        winner = p1
    elif check_win(p2):
        game_over = True
        winner = p2

    if game_over:
        room['game_over'] = True
        # Send summary
        duration = time.time() - room['start_time']
        emit('game_summary', {
            'winner': winner['name'] if winner else 'Draw',
            'your_score': p1['score'],
            'your_wickets': p1['wickets'],
            'opponent_score': p2['score'] if p2 else None,
            'opponent_wickets': p2['wickets'] if p2 else None,
            'duration_seconds': int(duration),
            'msg': f"Game Over! Winner: {winner['name'] if winner else 'Draw'}"
        }, room=room_id)
        reset_room(room_id)
        return

    # Switch turn
    if room['ai_player_sid'] == 'AI_BOT':
        # Player always plays first in our logic, so turn remains player's turn
        room['current_turn_sid'] = sids[0]
        emit('your_turn', {'msg': 'Your turn!'}, room=sids[0])
    else:
        # Switch turn to opponent
        room['current_turn_sid'] = get_opponent_sid(room, sids[0])
        emit('your_turn', {'msg': 'Your turn!'}, room=room['current_turn_sid'])

@socketio.on('chat')
def handle_chat(data):
    sid = request.sid
    msg = data.get('msg')
    player = players.get(sid)
    if not player or not player.get('room') or not msg:
        return
    room_id = player['room']
    timestamp = current_time()
    chat_msg = {'from': player['name'], 'msg': msg, 'time': timestamp}
    rooms[room_id]['chat'].append(chat_msg)
    emit('chat', chat_msg, room=room_id)

@socketio.on('typing')
def handle_typing(data):
    sid = request.sid
    player = players.get(sid)
    if not player or not player.get('room'):
        return
    room_id = player['room']
    emit('typing', {'from': player['name']}, room=room_id, include_self=False)

if __name__ == '__main__':
    print("Starting Hand Cricket server on port 10000")
    socketio.run(app, host='0.0.0.0', port=10000)
