from flask import Flask, request
from flask_socketio import SocketIO, emit, join_room, leave_room
import random

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")

players = {}
rooms = {}

# Reset a room
def reset_room(room_id):
    if room_id in rooms:
        for sid in rooms[room_id]['sids']:
            leave_room(room_id, sid=sid)
            players.pop(sid, None)
        del rooms[room_id]

# On connect
@socketio.on('connect')
def on_connect():
    print(f"[Connected] {request.sid}")

# On disconnect
@socketio.on('disconnect')
def on_disconnect():
    sid = request.sid
    print(f"[Disconnected] {sid}")
    for room_id, room in list(rooms.items()):
        if sid in room['sids']:
            emit('chat', {'from': 'System', 'msg': 'Opponent disconnected!'}, to=room_id)
            reset_room(room_id)
            break
    players.pop(sid, None)

# Profile setup
@socketio.on('setup_profile')
def setup_profile(data):
    sid = request.sid
    name = data.get('name', f"Player_{sid[:5]}")
    players[sid] = {'name': name, 'room': None}
    emit('profile_set', {'name': name})

# Create room
@socketio.on('create_room')
def create_room(data):
    sid = request.sid
    room_id = f"room_{sid[:5]}"
    overs = data.get('overs', 2)
    wickets = data.get('wickets', 2)
    rooms[room_id] = {
        'sids': [sid],
        'overs': overs,
        'wickets': wickets,
        'moves': {},
        'bat_first': None,
        'scores': {},
        'status': 'waiting'
    }
    join_room(room_id, sid=sid)
    players[sid]['room'] = room_id
    emit('room_created', {'room_id': room_id})

# Get list of joinable rooms
@socketio.on('get_rooms')
def get_rooms():
    joinable = []
    for rid, room in rooms.items():
        if len(room['sids']) == 1:
            host_sid = room['sids'][0]
            joinable.append({
                'room_id': rid,
                'host': players[host_sid]['name'],
                'overs': room['overs'],
                'wickets': room['wickets']
            })
    emit('rooms_list', joinable)

# Join room
@socketio.on('join_room')
def join_room_handler(data):
    sid = request.sid
    room_id = data['room_id']
    if room_id in rooms and len(rooms[room_id]['sids']) == 1:
        rooms[room_id]['sids'].append(sid)
        join_room(room_id, sid=sid)
        players[sid]['room'] = room_id
        emit('room_joined', {'room_id': room_id}, room=sid)
        emit('opponent_joined', {'msg': 'Opponent joined. Ready to toss.'}, to=room_id)
    else:
        emit('error', {'msg': 'Room is full or does not exist.'})

# Toss logic
@socketio.on('toss')
def toss():
    sid = request.sid
    room_id = players[sid].get('room')
    if not room_id or room_id not in rooms:
        return
    striker = random.choice(rooms[room_id]['sids'])
    rooms[room_id]['bat_first'] = striker
    for s in rooms[room_id]['sids']:
        emit('toss_result', {
            'you_win': s == striker,
            'choose': s == striker
        }, room=s)

# Toss choice
@socketio.on('toss_choice')
def toss_choice(data):
    choice = data['choice']  # 'bat' or 'bowl'
    sid = request.sid
    room_id = players[sid].get('room')
    if not room_id or room_id not in rooms:
        return

    striker = sid if choice == 'bat' else [s for s in rooms[room_id]['sids'] if s != sid][0]
    rooms[room_id]['bat_first'] = striker
    rooms[room_id]['scores'] = {
        s: {'score': 0, 'wickets': 0, 'overs': 0} for s in rooms[room_id]['sids']
    }
    rooms[room_id]['status'] = 'in_progress'

    for s in rooms[room_id]['sids']:
        emit('match_start', {
            'you_bat': s == striker,
            'opponent': players[[x for x in rooms[room_id]['sids'] if x != s][0]]['name']
        }, room=s)

# Player turn
@socketio.on('play_turn')
def play_turn(data):
    sid = request.sid
    number = int(data['number'])
    room_id = players[sid]['room']
    room = rooms.get(room_id)
    if not room:
        return

    room['moves'][sid] = number
    if len(room['moves']) < 2:
        return

    s1, s2 = room['sids']
    n1, n2 = room['moves'][s1], room['moves'][s2]
    scores = room['scores']

    result = {'msg': '', 'events': []}

    if n1 == n2:
        scores[s1]['wickets'] += 1
        scores[s2]['wickets'] += 1
        result['msg'] = "WICKET! Both chose same number."
        result['events'].append('wicket')
    else:
        scores[s1]['score'] += n1
        scores[s2]['score'] += n2
        result['msg'] = "Runs added."
        result['events'].append('run')

    for s in [s1, s2]:
        scores[s]['overs'] += 1 / 6

    game_over = any(
        scores[s]['wickets'] >= room['wickets'] or scores[s]['overs'] >= room['overs']
        for s in [s1, s2]
    )
    if game_over:
        room['status'] = 'done'
        if scores[s1]['score'] > scores[s2]['score']:
            winner = players[s1]['name']
        elif scores[s2]['score'] > scores[s1]['score']:
            winner = players[s2]['name']
        else:
            winner = 'Tie'
        result['game_over'] = True
        result['winner'] = winner

    for s in [s1, s2]:
        emit('turn_result', {
            'your': room['moves'][s],
            'opponent': room['moves'][s2 if s == s1 else s1],
            'score': scores[s],
            **result
        }, room=s)

    room['moves'] = {}

# Chat
@socketio.on('chat')
def handle_chat(data):
    sid = request.sid
    room_id = players[sid].get('room')
    msg = data.get('msg')
    if not room_id:
        return
    emit('chat', {'from': players[sid]['name'], 'msg': msg}, to=room_id)

if __name__ == '__main__':
    socketio.run(app, port=10000)
