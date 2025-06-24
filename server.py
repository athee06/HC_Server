from flask import Flask, request, jsonify
from flask_socketio import SocketIO, emit, join_room, leave_room
import random

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")

players = {}
rooms = {}

# Root endpoint for browser / health check
@app.route('/')
def index():
    return jsonify({"status": "ok", "message": "Hand Cricket Server is Running!"})

# Disconnect cleanup
def reset_room(room_id):
    if room_id in rooms:
        for sid in rooms[room_id]['sids']:
            leave_room(room_id, sid=sid)
        del rooms[room_id]

# Client connected
@socketio.on('connect')
def handle_connect():
    print(f"[+] Client connected: {request.sid}")

# Client disconnected
@socketio.on('disconnect')
def handle_disconnect():
    sid = request.sid
    print(f"[-] Client disconnected: {sid}")
    
    for room_id, room in list(rooms.items()):
        if sid in room['sids']:
            emit('chat', {'from': 'System', 'msg': 'Opponent disconnected!'}, to=room_id)
            reset_room(room_id)
            break
    players.pop(sid, None)

# Player join and matchmaking
@socketio.on('join')
def handle_join(data):
    sid = request.sid
    name = data.get('name')
    players[sid] = {'name': name, 'score': 0, 'wickets': 0}

    # Matchmaking: find waiting player
    waiting = [s for s in players if s != sid and not any(s in r['sids'] for r in rooms.values())]
    
    if waiting:
        opponent_sid = waiting[0]
        room_id = f"room_{opponent_sid[:5]}_{sid[:5]}"
        join_room(room_id, sid=opponent_sid)
        join_room(room_id, sid=sid)

        rooms[room_id] = {
            'sids': [opponent_sid, sid],
            'moves': {},
            'chat': []
        }

        # Toss to decide who starts
        striker = random.choice([sid, opponent_sid])

        for s in rooms[room_id]['sids']:
            emit('start', {
                'you': players[s]['name'],
                'opponent': players[opponent_sid if s == sid else sid]['name'],
                'your_turn': s == striker
            }, room=s)
    else:
        emit('waiting', {'msg': 'Waiting for opponent...'})

# Game play logic
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
                    players[s1]['wickets'] += 1
                    players[s2]['wickets'] += 1
                    result['msg'] = "Same number! Wicket!"
                else:
                    players[s1]['score'] += n1
                    players[s2]['score'] += n2
                    result['msg'] = "Runs added."

                result['score'] = {
                    s1: {'score': players[s1]['score'], 'wickets': players[s1]['wickets']},
                    s2: {'score': players[s2]['score'], 'wickets': players[s2]['wickets']}
                }

                # Game Over Check
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

                room['moves'] = {}  # reset after round
            break

# Chat feature
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

# Run the server
if __name__ == '__main__':
    socketio.run(app, host="0.0.0.0", port=10000)
