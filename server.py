from flask import Flask, request
from flask_socketio import SocketIO, emit, join_room

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")

players = {}
rooms = {}

@app.route("/")
def index():
    return "Hand Cricket Server is Running!"

@socketio.on('join')
def handle_join(data):
    name = data['name']
    sid = request.sid
    if len(players) % 2 == 0:
        room_id = f"room_{sid}"
        rooms[room_id] = [sid]
        join_room(room_id)
        players[sid] = {'name': name, 'room': room_id}
        emit('waiting', {'msg': 'Waiting for second player...'}, room=sid)
    else:
        for rid, sids in rooms.items():
            if len(sids) == 1:
                sids.append(sid)
                players[sid] = {'name': name, 'room': rid}
                join_room(rid)
                emit('start', {'msg': 'Match started!'}, room=rid)
                return

@socketio.on('play')
def handle_play(data):
    sid = request.sid
    number = data['number']
    room = players[sid]['room']
    emit('opponent_move', {'number': number}, room=room)

if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=10000)
