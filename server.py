from flask import Flask, request
from flask_socketio import SocketIO, emit, join_room, leave_room
import random
import threading

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")

players = {}
rooms = {}
lock = threading.Lock()

def reset_room(room_id):
    with lock:
        if room_id in rooms:
            for sid in rooms[room_id]['sids']:
                try:
                    leave_room(room_id, sid=sid)
                except Exception:
                    pass
                players.pop(sid, None)
            del rooms[room_id]

@socketio.on('connect')
def on_connect():
    print(f"[Connected] {request.sid}")

@socketio.on('disconnect')
def on_disconnect():
    sid = request.sid
    print(f"[Disconnected] {sid}")
    with lock:
        for room_id, room in list(rooms.items()):
            if sid in room['sids']:
                emit('chat', {'from': 'System', 'msg': 'Opponent disconnected!'}, to=room_id)
                reset_room(room_id)
                break
        players.pop(sid, None)

@socketio.on('setup_profile')
def setup_profile(data):
    sid = request.sid
    name = data.get('name', f"Player_{sid[:5]}")
    with lock:
        players[sid] = {'name': name, 'room': None}
    emit('profile_set', {'name': name})

@socketio.on('create_room')
def create_room(data):
    sid = request.sid
    with lock:
        if sid not in players:
            emit('error', {'msg': 'Setup profile first.'})
            return
        room_id = f"room_{sid[:5]}"
        overs = int(data.get('overs', 2))
        wickets = int(data.get('wickets', 2))
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

@socketio.on('get_rooms')
def get_rooms():
    joinable = []
    with lock:
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

@socketio.on('join_room')
def join_room_handler(data):
    sid = request.sid
    room_id = data.get('room_id')
    with lock:
        if room_id not in rooms or len(rooms[room_id]['sids']) != 1:
            emit('error', {'msg': 'Room is full or does not exist.'})
            return
        if sid in rooms[room_id]['sids']:
            emit('error', {'msg': 'Already in the room.'})
            return
        if sid not in players:
            emit('error', {'msg': 'Setup profile first.'})
            return
        rooms[room_id]['sids'].append(sid)
        join_room(room_id, sid=sid)
        players[sid]['room'] = room_id
    emit('room_joined', {'room_id': room_id}, to=sid)
    emit('opponent_joined', {'msg': 'Opponent joined. Ready to toss.'}, to=room_id)

@socketio.on('toss')
def toss():
    sid = request.sid
    with lock:
        room_id = players.get(sid, {}).get('room')
        if not room_id or room_id not in rooms:
            return
        if len(rooms[room_id]['sids']) != 2:
            emit('error', {'msg': 'Need two players to toss.'})
            return
        striker = random.choice(rooms[room_id]['sids'])
        rooms[room_id]['bat_first'] = striker
        for s in rooms[room_id]['sids']:
            emit('toss_result', {
                'you_win': s == striker,
                'choose': s == striker
            }, to=s)

@socketio.on('toss_choice')
def toss_choice(data):
    choice = data.get('choice')
    sid = request.sid
    with lock:
        room_id = players.get(sid, {}).get('room')
        if not room_id or room_id not in rooms:
            return
        if choice not in ('bat', 'bowl'):
            emit('error', {'msg': 'Invalid choice.'})
            return
        sids = rooms[room_id]['sids']
        striker = sid if choice == 'bat' else [s for s in sids if s != sid][0]
        rooms[room_id]['bat_first'] = striker
        # Track balls as integers
        rooms[room_id]['scores'] = {
            s: {'score': 0, 'wickets': 0, 'balls': 0} for s in sids
        }
        rooms[room_id]['status'] = 'in_progress'
        for s in sids:
            emit('match_start', {
                'you_bat': s == striker,
                'opponent': players[[x for x in sids if x != s][0]]['name']
            }, to=s)

@socketio.on('play_turn')
def play_turn(data):
    sid = request.sid
    number = data.get('number')
    try:
        number = int(number)
    except (TypeError, ValueError):
        emit('error', {'msg': 'Invalid number.'})
        return
    if not (0 <= number <= 6):
        emit('error', {'msg': 'Number must be between 0 and 6.'})
        return
    with lock:
        room_id = players.get(sid, {}).get('room')
        room = rooms.get(room_id)
        if not room or room['status'] != 'in_progress':
            emit('error', {'msg': 'Game not in progress.'})
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
            scores[s]['balls'] += 1
        game_over = any(
            scores[s]['wickets'] >= room['wickets'] or scores[s]['balls'] >= room['overs'] * 6
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
                'score': {
                    'score': scores[s]['score'],
                    'wickets': scores[s]['wickets'],
                    'overs': f"{scores[s]['balls'] // 6}.{scores[s]['balls'] % 6}"
                },
                **result
            }, to=s)
        room['moves'] = {}

@socketio.on('chat')
def handle_chat(data):
    sid = request.sid
    with lock:
        room_id = players.get(sid, {}).get('room')
        msg = data.get('msg')
        if not room_id or not msg:
            return
        emit('chat', {'from': players[sid]['name'], 'msg': msg}, to=room_id)

if __name__ == '__main__':
    socketio.run(app, port=10000)
