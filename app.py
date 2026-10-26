import os
import time
import uuid
import datetime
import threading
import chess
import chess.pgn
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from flask_socketio import SocketIO, emit, join_room, leave_room
import db
import chess_engine

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'chess-secret-key-super-secure-2026')
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# In-memory active game rooms
# room_id -> { ... }
active_rooms = {}
# Matchmaking queue: time_control -> list of {"username": ..., "sid": ..., "joined_at": ...}
match_queues = {
    "1m": [],
    "3m": [],
    "10m": [],
    "30m": []
}
room_lock = threading.RLock()

# ----------------- Background Clock & Timeout Worker -----------------

def background_clock_checker():
    """
    Background worker that checks active games for clock timeouts.
    """
    while True:
        try:
            time.sleep(0.5)
            now = time.time()
            rooms_to_end = []
            
            with room_lock:
                for room_id, game in active_rooms.items():
                    if game.get('status') != 'playing' or not game.get('timer_running', False):
                        continue
                    
                    active_turn = game.get('active_turn', 'white')
                    elapsed = now - game.get('last_turn_timestamp', now)
                    current_clock = game['clocks'][active_turn] - elapsed
                    
                    if current_clock <= 0:
                        # Timeout detected!
                        rooms_to_end.append((room_id, active_turn))
            
            for room_id, timed_out_color in rooms_to_end:
                handle_game_timeout(room_id, timed_out_color)
        except Exception as e:
            time.sleep(1)

clock_thread = threading.Thread(target=background_clock_checker, daemon=True)
clock_thread.start()

def handle_game_timeout(room_id, timed_out_color):
    with room_lock:
        game = active_rooms.get(room_id)
        if not game or game.get('status') != 'playing':
            return
        
        game['status'] = 'finished'
        game['clocks'][timed_out_color] = 0.0
        winner_color = 'black' if timed_out_color == 'white' else 'white'
        board = game['board']
        
        # Check if winner has sufficient material to checkmate
        winner_chess_color = chess.BLACK if winner_color == 'black' else chess.WHITE
        has_material = any(
            piece.color == winner_chess_color and piece.piece_type in [chess.QUEEN, chess.ROOK, chess.PAWN]
            for piece in board.piece_map().values()
        ) or sum(1 for piece in board.piece_map().values() if piece.color == winner_chess_color) >= 2
        
        if not has_material:
            result = "1/2-1/2"
            winner_user = None
            reason = "timeout_insufficient_material"
        else:
            result = "1-0" if winner_color == "white" else "0-1"
            winner_user = game[winner_color]['username']
            reason = "timeout"
        
        finalize_game_record(game, result, reason, winner_user)
        
        socketio.emit('game_over', {
            'result': result,
            'reason': reason,
            'winner': winner_user,
            'winner_color': winner_color if winner_user else None,
            'white_username': game['white']['username'],
            'black_username': game['black']['username'],
            'clocks': game['clocks'],
            'game_id': game['game_id']
        }, room=room_id)

def finalize_game_record(game, result, reason, winner_username):
    """
    Persists finished game to TinyDB and updates Elo ratings.
    """
    white_user = game['white']['username']
    black_user = game['black']['username']
    
    # Rating changes
    white_delta = 0
    black_delta = 0
    w_prof = db.get_user_profile(white_user)
    b_prof = db.get_user_profile(black_user)
    
    if w_prof and b_prof and white_user != black_user:
        if result == "1-0":
            score_w = 1.0
        elif result == "0-1":
            score_w = 0.0
        else:
            score_w = 0.5
        white_delta, black_delta = db.calculate_elo_change(w_prof['rating'], b_prof['rating'], score_w)
        
        # Update user stats
        w_res = 'win' if result == '1-0' else ('loss' if result == '0-1' else 'draw')
        b_res = 'win' if result == '0-1' else ('loss' if result == '1-0' else 'draw')
        db.update_user_stats(white_user, w_res, white_delta)
        db.update_user_stats(black_user, b_res, black_delta)
    
    # Build PGN
    pgn_game = chess.pgn.Game()
    pgn_game.headers["Event"] = f"ChessClone {game['time_control_name']}"
    pgn_game.headers["Site"] = "ChessClone Online"
    pgn_game.headers["Date"] = datetime.datetime.now().strftime("%Y.%m.%d")
    pgn_game.headers["White"] = white_user
    pgn_game.headers["Black"] = black_user
    pgn_game.headers["Result"] = result
    pgn_game.headers["Termination"] = reason
    
    node = pgn_game
    temp_board = chess.Board()
    for m in game.get('move_history', []):
        try:
            move = chess.Move.from_uci(m['from'] + m['to'] + (m.get('promotion', '') or ''))
            if move in temp_board.legal_moves:
                node = node.add_variation(move)
                temp_board.push(move)
        except Exception:
            pass

    game_record = {
        'game_id': game['game_id'],
        'white_username': white_user,
        'black_username': black_user,
        'white_rating': w_prof['rating'] if w_prof else 1200,
        'black_rating': b_prof['rating'] if b_prof else 1200,
        'white_delta': white_delta,
        'black_delta': black_delta,
        'time_control': game['time_control_name'],
        'time_seconds': game['time_seconds'],
        'increment': game.get('increment', 0),
        'result': result,
        'winner': winner_username,
        'termination_reason': reason,
        'created_at': datetime.datetime.now().strftime("%b %d, %Y %H:%M"),
        'created_timestamp': game.get('created_timestamp', time.time()),
        'ended_at': datetime.datetime.now().strftime("%b %d, %Y %H:%M"),
        'moves': game.get('move_history', []),
        'pgn': str(pgn_game),
        'is_offline': game.get('is_offline', False)
    }
    db.save_game(game_record)
    game['record'] = game_record


# ----------------- HTTP Routes -----------------

@app.context_processor
def inject_user():
    current_username = session.get('username')
    user = db.get_user_profile(current_username) if current_username else None
    return {'current_user': user}

@app.route('/')
def index():
    recent = db.get_recent_games(8)
    leaderboard = db.get_leaderboard(5)
    return render_template('index.html', recent_games=recent, leaderboard=leaderboard)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        action = request.form.get('action', 'login')
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        
        if action == 'register':
            success, msg = db.create_user(username, password)
            if success:
                user_prof = db.get_user_profile(username)
                session['username'] = user_prof['username']
                return redirect(url_for('index'))
            else:
                return render_template('login.html', error=msg, tab='register')
        else:
            user_prof, msg = db.verify_user(username, password)
            if user_prof:
                session['username'] = user_prof['username']
                return redirect(url_for('index'))
            else:
                return render_template('login.html', error=msg, tab='login')
                
    tab = request.args.get('tab', 'login')
    return render_template('login.html', tab=tab)

@app.route('/logout')
def logout():
    session.pop('username', None)
    return redirect(url_for('index'))

@app.route('/profile/<username>')
def profile(username):
    user = db.get_user_profile(username)
    if not user:
        return render_template('error.html', message=f"Player '{username}' not found."), 404
    history = db.get_user_games(username, 30)
    return render_template('profile.html', user=user, history=history)

@app.route('/leaderboard')
def leaderboard():
    players = db.get_leaderboard(25)
    return render_template('leaderboard.html', players=players)

@app.route('/play/online')
def play_online_lobby():
    # If a specific room is requested
    room_id = request.args.get('room')
    if not room_id:
        return redirect(url_for('index'))
    return render_template('play_online.html', room_id=room_id)

@app.route('/play/offline')
def play_offline():
    mode = request.args.get('mode', 'pass_and_play') # pass_and_play or vs_bot
    difficulty = request.args.get('difficulty', 'medium')
    time_control = request.args.get('time', '10m')
    user_color = request.args.get('color', 'white')
    return render_template('play_offline.html', mode=mode, difficulty=difficulty, time_control=time_control, user_color=user_color)

@app.route('/review/<game_id>')
def review(game_id):
    game = db.get_game(game_id)
    if not game:
        return render_template('error.html', message="Game record not found."), 404
    return render_template('review.html', game=game)

# ----------------- REST APIs -----------------

@app.route('/api/create_room', methods=['POST'])
def api_create_room():
    data = request.get_json() or {}
    tc = data.get('time_control', '10m')
    color_pref = data.get('color', 'random')
    
    tc_map = {
        '1m': ('1 min Bullet', 60, 0),
        '3m': ('3 min Blitz', 180, 0),
        '10m': ('10 min Rapid', 600, 0),
        '30m': ('30 min Classical', 1800, 0),
        'unlimited': ('Unlimited Casual', 0, 0)
    }
    tc_name, seconds, inc = tc_map.get(tc, ('10 min Rapid', 600, 0))
    room_id = uuid.uuid4().hex[:7].upper()
    
    with room_lock:
        active_rooms[room_id] = {
            'game_id': room_id,
            'time_control': tc,
            'time_control_name': tc_name,
            'time_seconds': seconds,
            'increment': inc,
            'clocks': {'white': float(seconds), 'black': float(seconds)},
            'white': None,
            'black': None,
            'color_pref': color_pref,
            'creator': session.get('username', f"Guest_{room_id[:4]}"),
            'board': chess.Board(),
            'move_history': [],
            'chat_history': [],
            'status': 'waiting',
            'timer_running': False,
            'active_turn': 'white',
            'last_turn_timestamp': None,
            'draw_offered_by': None,
            'created_timestamp': time.time()
        }
    
    return jsonify({'success': True, 'room_id': room_id})

@app.route('/api/bot_move', methods=['POST'])
def api_bot_move():
    data = request.get_json() or {}
    fen = data.get('fen', chess.STARTING_FEN)
    difficulty = data.get('difficulty', 'medium')
    try:
        board = chess.Board(fen)
        if board.is_game_over():
            return jsonify({'game_over': True})
        
        bot_m = chess_engine.get_bot_move(board, difficulty)
        if not bot_m:
            return jsonify({'error': 'No legal moves'}), 400
        
        san = board.san(bot_m)
        uci = bot_m.uci()
        from_sq = chess.square_name(bot_m.from_square)
        to_sq = chess.square_name(bot_m.to_square)
        promo = bot_m.promotion
        promo_str = chess.piece_symbol(promo).lower() if promo else None
        
        board.push(bot_m)
        is_over, result, reason, winner = chess_engine.check_game_over(board)
        
        return jsonify({
            'success': True,
            'uci': uci,
            'san': san,
            'from': from_sq,
            'to': to_sq,
            'promotion': promo_str,
            'new_fen': board.fen(),
            'is_game_over': is_over,
            'result': result,
            'reason': reason,
            'winner': winner
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/api/eval', methods=['GET', 'POST'])
def api_eval():
    if request.method == 'POST':
        data = request.get_json() or {}
        fen = data.get('fen', chess.STARTING_FEN)
    else:
        fen = request.args.get('fen', chess.STARTING_FEN)
    
    eval_result = chess_engine.get_stockfish_evaluation(fen)
    return jsonify(eval_result)

@app.route('/api/save_offline_game', methods=['POST'])
def api_save_offline_game():
    data = request.get_json() or {}
    moves = data.get('moves', [])
    result = data.get('result', '*')
    reason = data.get('reason', 'normal')
    white_user = data.get('white_username', 'White')
    black_user = data.get('black_username', 'Black')
    time_control = data.get('time_control', 'Pass & Play')
    
    game_id = 'OFFLINE_' + uuid.uuid4().hex[:8].upper()
    
    # Generate PGN
    pgn_game = chess.pgn.Game()
    pgn_game.headers["Event"] = f"ChessClone Offline ({time_control})"
    pgn_game.headers["Date"] = datetime.datetime.now().strftime("%Y.%m.%d")
    pgn_game.headers["White"] = white_user
    pgn_game.headers["Black"] = black_user
    pgn_game.headers["Result"] = result
    pgn_game.headers["Termination"] = reason
    
    node = pgn_game
    temp_board = chess.Board()
    for m in moves:
        try:
            move = chess.Move.from_uci(m['from'] + m['to'] + (m.get('promotion', '') or ''))
            if move in temp_board.legal_moves:
                node = node.add_variation(move)
                temp_board.push(move)
        except Exception:
            pass

    game_record = {
        'game_id': game_id,
        'white_username': white_user,
        'black_username': black_user,
        'time_control': time_control,
        'time_seconds': 0,
        'increment': 0,
        'result': result,
        'winner': white_user if result == '1-0' else (black_user if result == '0-1' else None),
        'termination_reason': reason,
        'created_at': datetime.datetime.now().strftime("%b %d, %Y %H:%M"),
        'created_timestamp': time.time(),
        'moves': moves,
        'pgn': str(pgn_game),
        'is_offline': True
    }
    db.save_game(game_record)
    
    # If user is logged in and was one of the players, update their games played
    curr = session.get('username')
    if curr and (curr == white_user or curr == black_user):
        res = 'win' if (curr == white_user and result == '1-0') or (curr == black_user and result == '0-1') else ('draw' if result == '1/2-1/2' else 'loss')
        db.update_user_stats(curr, res, 0)
        
    return jsonify({'success': True, 'game_id': game_id})


# ----------------- SocketIO Real-Time Handlers -----------------

@socketio.on('join_game')
def on_join_game(data):
    room_id = data.get('room_id')
    user_name = session.get('username') or data.get('username') or f"Guest_{request.sid[:4]}"
    
    with room_lock:
        game = active_rooms.get(room_id)
        if not game:
            emit('error_message', {'message': 'Room not found or expired.'})
            return
            
        join_room(room_id)
        
        # Assign player roles
        assigned_role = None
        if game['white'] and game['white']['username'] == user_name:
            game['white']['sid'] = request.sid
            game['white']['connected'] = True
            assigned_role = 'white'
        elif game['black'] and game['black']['username'] == user_name:
            game['black']['sid'] = request.sid
            game['black']['connected'] = True
            assigned_role = 'black'
        elif game['white'] is None:
            # Check color preference
            pref = game.get('color_pref', 'random')
            if pref == 'black':
                game['black'] = {'username': user_name, 'sid': request.sid, 'connected': True}
                assigned_role = 'black'
            else:
                game['white'] = {'username': user_name, 'sid': request.sid, 'connected': True}
                assigned_role = 'white'
        elif game['black'] is None:
            game['black'] = {'username': user_name, 'sid': request.sid, 'connected': True}
            assigned_role = 'black'
        else:
            assigned_role = 'spectator'
            
        # Check if game is now ready to start
        if game['white'] and game['black'] and game['status'] == 'waiting':
            game['status'] = 'playing'
            game['last_turn_timestamp'] = time.time()
            
        # Get profiles for ratings
        w_prof = db.get_user_profile(game['white']['username']) if game['white'] else None
        b_prof = db.get_user_profile(game['black']['username']) if game['black'] else None
        
        emit('game_init', {
            'room_id': room_id,
            'role': assigned_role,
            'username': user_name,
            'white': {
                'username': game['white']['username'] if game['white'] else None,
                'rating': w_prof['rating'] if w_prof else 1200
            },
            'black': {
                'username': game['black']['username'] if game['black'] else None,
                'rating': b_prof['rating'] if b_prof else 1200
            },
            'status': game['status'],
            'fen': game['board'].fen(),
            'moves': game['move_history'],
            'clocks': game['clocks'],
            'active_turn': game['active_turn'],
            'time_control': game['time_control_name'],
            'time_seconds': game['time_seconds']
        })
        
        # Broadcast player presence update to the room
        socketio.emit('player_update', {
            'white': {
                'username': game['white']['username'] if game['white'] else None,
                'rating': w_prof['rating'] if w_prof else 1200,
                'connected': game['white']['connected'] if game['white'] else False
            },
            'black': {
                'username': game['black']['username'] if game['black'] else None,
                'rating': b_prof['rating'] if b_prof else 1200,
                'connected': game['black']['connected'] if game['black'] else False
            },
            'status': game['status']
        }, room=room_id)

@socketio.on('make_move')
def on_make_move(data):
    room_id = data.get('room_id')
    from_sq = data.get('from')
    to_sq = data.get('to')
    promo = data.get('promotion')
    
    with room_lock:
        game = active_rooms.get(room_id)
        if not game or game.get('status') != 'playing':
            return
            
        board = game['board']
        turn_color = 'white' if board.turn == chess.WHITE else 'black'
        
        # Verify it is the calling player's turn
        player_obj = game.get(turn_color)
        if not player_obj or player_obj.get('sid') != request.sid:
            # Spectator or not your turn
            return
            
        now = time.time()
        # Compute and deduct elapsed time
        if game.get('timer_running', False) and game.get('last_turn_timestamp'):
            elapsed = now - game['last_turn_timestamp']
            game['clocks'][turn_color] = max(0.0, game['clocks'][turn_color] - elapsed + game.get('increment', 0))
            if game['clocks'][turn_color] <= 0:
                # Timeout!
                handle_game_timeout(room_id, turn_color)
                return
        else:
            # First move starts the timer!
            game['timer_running'] = True
            
        # Parse and execute move
        move_str = f"{from_sq}{to_sq}{promo if promo else ''}"
        try:
            move = chess.Move.from_uci(move_str)
            if move not in board.legal_moves:
                emit('invalid_move', {'reason': 'Illegal move according to chess rules.'})
                return
                
            san = board.san(move)
            is_capture = board.is_capture(move)
            is_check = board.gives_check(move)
            board.push(move)
            
            # Switch active turn and reset last_turn_timestamp
            next_turn = 'white' if board.turn == chess.WHITE else 'black'
            game['active_turn'] = next_turn
            game['last_turn_timestamp'] = now
            
            move_info = {
                'from': from_sq,
                'to': to_sq,
                'promotion': promo,
                'san': san,
                'is_capture': is_capture,
                'is_check': is_check,
                'fen': board.fen(),
                'clock_w': round(game['clocks']['white'], 1),
                'clock_b': round(game['clocks']['black'], 1),
                'move_number': len(game['move_history']) + 1
            }
            game['move_history'].append(move_info)
            
            # Check game over conditions
            is_over, result, reason, winner = chess_engine.check_game_over(board)
            if is_over:
                game['status'] = 'finished'
                winner_username = game[winner]['username'] if winner in ['white', 'black'] else None
                finalize_game_record(game, result, reason, winner_username)
                
            socketio.emit('move_made', {
                'move': move_info,
                'active_turn': next_turn,
                'clocks': game['clocks'],
                'is_game_over': is_over,
                'result': result,
                'reason': reason,
                'winner': game[winner]['username'] if (is_over and winner in ['white', 'black']) else ('Draw' if is_over else None),
                'winner_color': winner if is_over else None,
                'game_id': game['game_id']
            }, room=room_id)
            
        except Exception as e:
            emit('invalid_move', {'reason': str(e)})

@socketio.on('send_chat')
def on_send_chat(data):
    room_id = data.get('room_id')
    message = (data.get('message') or '').strip()
    if not message:
        return
        
    with room_lock:
        game = active_rooms.get(room_id)
        if not game:
            return
        sender = session.get('username') or f"Guest_{request.sid[:4]}"
        chat_item = {
            'sender': sender,
            'message': message[:200], # limit length
            'time': datetime.datetime.now().strftime("%H:%M")
        }
        game['chat_history'].append(chat_item)
        socketio.emit('chat_received', chat_item, room=room_id)

@socketio.on('offer_draw')
def on_offer_draw(data):
    room_id = data.get('room_id')
    with room_lock:
        game = active_rooms.get(room_id)
        if not game or game.get('status') != 'playing':
            return
            
        sender_color = 'white' if game['white'] and game['white']['sid'] == request.sid else ('black' if game['black'] and game['black']['sid'] == request.sid else None)
        if not sender_color:
            return
            
        game['draw_offered_by'] = sender_color
        socketio.emit('draw_offered', {'by': sender_color, 'username': game[sender_color]['username']}, room=room_id)

@socketio.on('respond_draw')
def on_respond_draw(data):
    room_id = data.get('room_id')
    accepted = data.get('accepted', False)
    with room_lock:
        game = active_rooms.get(room_id)
        if not game or game.get('status') != 'playing' or not game.get('draw_offered_by'):
            return
            
        responder_color = 'white' if game['white'] and game['white']['sid'] == request.sid else ('black' if game['black'] and game['black']['sid'] == request.sid else None)
        if responder_color == game['draw_offered_by']:
            return
            
        if accepted:
            game['status'] = 'finished'
            finalize_game_record(game, "1/2-1/2", "draw_agreement", None)
            socketio.emit('game_over', {
                'result': "1/2-1/2",
                'reason': "draw_agreement",
                'winner': None,
                'clocks': game['clocks'],
                'game_id': game['game_id']
            }, room=room_id)
        else:
            game['draw_offered_by'] = None
            socketio.emit('draw_declined', {}, room=room_id)

@socketio.on('resign')
def on_resign(data):
    room_id = data.get('room_id')
    with room_lock:
        game = active_rooms.get(room_id)
        if not game or game.get('status') != 'playing':
            return
            
        resigning_color = 'white' if game['white'] and game['white']['sid'] == request.sid else ('black' if game['black'] and game['black']['sid'] == request.sid else None)
        if not resigning_color:
            return
            
        winner_color = 'black' if resigning_color == 'white' else 'white'
        result = "0-1" if resigning_color == "white" else "1-0"
        winner_user = game[winner_color]['username']
        
        game['status'] = 'finished'
        finalize_game_record(game, result, "resignation", winner_user)
        
        socketio.emit('game_over', {
            'result': result,
            'reason': "resignation",
            'winner': winner_user,
            'winner_color': winner_color,
            'resigned_by': game[resigning_color]['username'],
            'clocks': game['clocks'],
            'game_id': game['game_id']
        }, room=room_id)

# ----------------- Quick Matchmaking Queue -----------------

@socketio.on('join_matchmaking')
def on_join_matchmaking(data):
    tc = data.get('time_control', '10m')
    username = session.get('username') or f"Guest_{request.sid[:4]}"
    
    with room_lock:
        q = match_queues.get(tc, match_queues['10m'])
        # Clean disconnected entries
        q[:] = [entry for entry in q if entry['sid'] != request.sid]
        
        if q:
            # Found opponent! Pair them up
            opponent = q.pop(0)
            room_id = uuid.uuid4().hex[:7].upper()
            
            tc_map = {
                '1m': ('1 min Bullet', 60, 0),
                '3m': ('3 min Blitz', 180, 0),
                '10m': ('10 min Rapid', 600, 0),
                '30m': ('30 min Classical', 1800, 0)
            }
            tc_name, seconds, inc = tc_map.get(tc, ('10 min Rapid', 600, 0))
            
            # Randomize colors
            import random
            players = [(username, request.sid), (opponent['username'], opponent['sid'])]
            random.shuffle(players)
            
            active_rooms[room_id] = {
                'game_id': room_id,
                'time_control': tc,
                'time_control_name': tc_name,
                'time_seconds': seconds,
                'increment': inc,
                'clocks': {'white': float(seconds), 'black': float(seconds)},
                'white': {'username': players[0][0], 'sid': players[0][1], 'connected': True},
                'black': {'username': players[1][0], 'sid': players[1][1], 'connected': True},
                'creator': players[0][0],
                'board': chess.Board(),
                'move_history': [],
                'chat_history': [],
                'status': 'playing',
                'timer_running': False,
                'active_turn': 'white',
                'last_turn_timestamp': time.time(),
                'draw_offered_by': None,
                'created_timestamp': time.time()
            }
            
            # Notify both players
            emit('match_found', {'room_id': room_id}, room=request.sid)
            socketio.emit('match_found', {'room_id': room_id}, room=opponent['sid'])
        else:
            # Add to queue
            q.append({'username': username, 'sid': request.sid, 'joined_at': time.time()})
            emit('queue_joined', {'time_control': tc})

@socketio.on('leave_matchmaking')
def on_leave_matchmaking(data):
    with room_lock:
        for q in match_queues.values():
            q[:] = [entry for entry in q if entry['sid'] != request.sid]
        emit('queue_left', {})


if __name__ == '__main__':
    print("ChessClone server starting on http://127.0.0.1:5000 ...")
    socketio.run(app, host='0.0.0.0', port=5000, debug=False)
