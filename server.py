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
                players.pop(sid, None)
            del rooms[room_id]

def cleanup_empty_rooms():
    with lock:
        to_delete = [rid for rid, room in rooms.items() if len(room['sids']) == 0]
        for rid in to_delete:
            del rooms[rid]

@socketio.on('connect')
def on_connect():
    print(f"[Connected] {request.sid}")

@socketio.on('disconnect')
def on_disconnect():
    sid = request.sid
    print(f"[Disconnected] {sid}")
    with lock:
        room_id = players.get(sid, {}).get('room')
        if room_id and room_id in rooms:
            rooms[room_id]['sids'] = [s for s in rooms[room_id]['sids'] if s != sid]
            # Notify remaining player if any
            if len(rooms[room_id]['sids']) == 1:
                emit('chat', {'from': 'System', 'msg': 'Opponent disconnected!'}, to=room_id)
            elif len(rooms[room_id]['sids']) == 0:
                # Remove empty room
                del rooms[room_id]
        players.pop(sid, None)
    cleanup_empty_rooms()

@socketio.on('leave_room')
def on_leave_room():
    sid = request.sid
    with lock:
        room_id = players.get(sid, {}).get('room')
        if not room_id or room_id not in rooms:
            emit('error', {'msg': 'Not in any room.'})
            return
        # Remove player from room
        rooms[room_id]['sids'] = [s for s in rooms[room_id]['sids'] if s != sid]
        leave_room(room_id)
        players[sid]['room'] = None
        emit('left_room', {'msg': 'You left the room.'}, to=sid)
        # Notify remaining player
        if len(rooms[room_id]['sids']) == 1:
            emit('chat', {'from': 'System', 'msg': 'Opponent left the room!'}, to=room_id)
        elif len(rooms[room_id]['sids']) == 0:
            del rooms[room_id]

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
            'status': 'waiting',
            'toss_winner': None  # track who won toss
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
    emit('opponent_joined', {'msg': 'Opponent joined. Ready for toss.'}, to=room_id)

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
        toss_winner = random.choice(rooms[room_id]['sids'])
        rooms[room_id]['toss_winner'] = toss_winner
        # Notify toss winner to choose bat or bowl
        for s in rooms[room_id]['sids']:
            emit('toss_result', {
                'you_win': s == toss_winner,
                'choose': s == toss_winner
            }, to=s)

@socketio.on('toss_choice')
def toss_choice(data):
    choice = data.get('choice')
    sid = request.sid
    with lock:
        room_id = players.get(sid, {}).get('room')
        room = rooms.get(room_id)
        if not room or room['status'] != 'waiting':
            emit('error', {'msg': 'Toss already done or invalid state.'})
            return
        if sid != room['toss_winner']:
            emit('error', {'msg': 'You are not the toss winner.'})
            return
        if choice not in ('bat', 'bowl'):
            emit('error', {'msg': 'Invalid choice.'})
            return
        sids = room['sids']
        # Assign batting based on choice by toss winner
        if choice == 'bat':
            room['bat_first'] = sid
        else:
            room['bat_first'] = [s for s in sids if s != sid][0]

        room['scores'] = {
            s: {'score': 0, 'wickets': 0, 'balls': 0} for s in sids
        }
        room['status'] = 'in_progress'
        # Notify both players match started and who bats first
        for s in sids:
            emit('match_start', {
                'you_bat': s == room['bat_first'],
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
