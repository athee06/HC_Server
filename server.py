from flask import Flask, request
from flask_socketio import SocketIO, emit, join_room, leave_room
import random

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")

players = {}
rooms = {}

def reset_room(room_id):
    if room_id in rooms:
        for sid in rooms[room_id]['sids']:
            leave_room(room_id, sid=sid)
        del rooms[room_id]

@socketio.on('connect')
def handle_connect():
    print(f"Client connected: {request.sid}")

@socketio.on('disconnect')
def handle_disconnect():
    sid = request.sid
    print(f"Client disconnected: {sid}")
    # Remove from players and reset room if needed
    for room_id, room in list(rooms.items()):
        if sid in room['sids']:
            emit('chat', {'from': 'System', 'msg': 'Opponent disconnected!'}, to=room_id)
            reset_room(room_id)
            break
    players.pop(sid, None)

@socketio.on('join')
def handle_join(data):
    sid = request.sid
    name = data.get('name')
    players[sid] = {'name': name, 'score': 0, 'wickets': 0}
    
    # Matchmaking
    waiting = [s for s in players if s != sid and not any(s in r['sids'] for r in rooms.values())]
    if waiting:
        opponent_sid = waiting[0]
        room_id = f"room_{opponent_sid[:5]}_{sid[:5]}"
        join_room(room_id, sid=opponent_sid)
        join_room(room_id, sid=sid)
        rooms[room_id] = {'sids': [opponent_sid, sid], 'moves': {}, 'chat': []}

        # Toss
        striker = random.choice([sid, opponent_sid])
        for s in rooms[room_id]['sids']:
            emit('start', {
                'you': players[s]['name'],
                'opponent': players[opponent_sid if s == sid else sid]['name'],
                'your_turn': s == striker
            }, room=s)
    else:
        emit('waiting', {'msg': 'Waiting for opponent...'})

@socketio.on('play')
def handle_play(data):
    sid = request.sid
    number = data.get('number')

    for room_id, room in rooms.items():
        if sid in room['sids']:
            room['moves'][sid] = number
            if len(room['moves']) == 2:
                s1, s2 = room['sids']
                n1, n2 = room['moves'][s1], room['moves'][s2]
                
                result = {}
                if n1 == n2:
                    # Wicket for both
                    players[s1]['wickets'] += 1
                    players[s2]['wickets'] += 1
                    result['msg'] = "Both players hit the same number! Wicket!"
                else:
                    players[s1]['score'] += n1
                    players[s2]['score'] += n2
                    result['msg'] = "Runs added."

                result['score'] = {
                    s1: {'score': players[s1]['score'], 'wickets': players[s1]['wickets']},
                    s2: {'score': players[s2]['score'], 'wickets': players[s2]['wickets']}
                }

                # Check for game over
                if players[s1]['wickets'] >= 2 or players[s2]['wickets'] >= 2:
                    if players[s1]['score'] > players[s2]['score']:
                        winner = players[s1]['name']
                    elif players[s2]['score'] > players[s1]['score']:
                        winner = players[s2]['name']
                    else:
                        winner = "It's a tie!"
                    result['game_over'] = True
                    result['winner'] = winner

                for s in room['sids']:
                    emit('move_result', {
                        'your_move': room['moves'][s],
                        'opponent_move': room['moves'][room['sids'][1] if s == room['sids'][0] else room['sids'][0]],
                        'msg': result['msg'],
                        'score': result['score'][s],
                        'game_over': result.get('game_over', False),
                        'winner': result.get('winner', '')
                    }, room=s)
                room['moves'] = {}
            break

@socketio.on('chat')
def handle_chat(data):
    sid = request.sid
    msg = data.get('msg')
    for room_id, room in rooms.items():
        if sid in room['sids']:
            sender = players[sid]['name']
            chat_msg = {'from': sender, 'msg': msg}
            room['chat'].append(chat_msg)
            emit('chat', chat_msg, to=room_id)
            break

if __name__ == '__main__':
    socketio.run(app, port=10000)
