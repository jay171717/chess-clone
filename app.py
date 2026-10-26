import os
import time
import uuid
import random
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

# Matchmaking queue: time_control -> list of { 'token': ..., 'username': ..., 'sid': ..., 'joined_at': ... }
match_queues = {
    "1m": [],
    "3m": [],
    "10m": [],
    "30m": []
}

BOT_NAMES = [
    ("Magnus_Vibe", 1250),
    ("Alex_TheGrand", 1215),
    ("QueenSlayer24", 1190),
    ("ChessViper99", 1270),
    ("KnightRider_Pro", 1225),
    ("Valkyrie_Queen", 1240),
    ("GambitKing", 1260),
    ("BlitzMaster_07", 1205)
]

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
                        rooms_to_end.append((room_id, active_turn))
            
            for room_id, timed_out_color in rooms_to_end:
                handle_game_timeout(room_id, timed_out_color)
        except Exception:
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
            'white_username': game['white']['username'] if game.get('white') else 'White',
            'black_username': game['black']['username'] if game.get('black') else 'Black',
            'clocks': game['clocks'],
            'game_id': game['game_id']
        }, room=room_id)

def finalize_game_record(game, result, reason, winner_username):
    """
    Persists finished game to TinyDB and updates Elo ratings.
    """
    white_user = game['white']['username'] if game.get('white') else 'White'
    black_user = game['black']['username'] if game.get('black') else 'Black'
    
    white_delta = 0
    black_delta = 0
    w_prof = db.get_user_profile(white_user)
    b_prof = db.get_user_profile(black_user)
    
    if w_prof and b_prof and white_user != black_user:
        score_w = 1.0 if result == "1-0" else (0.0 if result == "0-1" else 0.5)
        white_delta, black_delta = db.calculate_elo_change(w_prof['rating'], b_prof['rating'], score_w)
        
        w_res = 'win' if result == '1-0' else ('loss' if result == '0-1' else 'draw')
        b_res = 'win' if result == '0-1' else ('loss' if result == '1-0' else 'draw')
        db.update_user_stats(white_user, w_res, white_delta)
        db.update_user_stats(black_user, b_res, black_delta)
    elif w_prof and not b_prof:
        # Player played vs bot
        w_res = 'win' if result == '1-0' else ('loss' if result == '0-1' else 'draw')
        db.update_user_stats(white_user, w_res, 0)
    elif b_prof and not w_prof:
        b_res = 'win' if result == '0-1' else ('loss' if result == '1-0' else 'draw')
        db.update_user_stats(black_user, b_res, 0)
    
    # Build PGN
    pgn_game = chess.pgn.Game()
    pgn_game.headers["Event"] = f"ChessClone {game.get('time_control_name', 'Match')}"
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
        'white_rating': w_prof['rating'] if w_prof else (game['white'].get('rating', 1200) if game.get('white') else 1200),
        'black_rating': b_prof['rating'] if b_prof else (game['black'].get('rating', 1200) if game.get('black') else 1200),
        'white_delta': white_delta,
        'black_delta': black_delta,
        'time_control': game.get('time_control_name', '10 min Rapid'),
        'time_seconds': game.get('time_seconds', 600),
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


# ----------------- Simulated Online Bot Logic -----------------

def schedule_bot_move(room_id, bot_color):
    """
    Schedules a realistic online bot move with human-like delay (1.5 - 3.5s).
    """
    def bot_task():
        delay = random.uniform(1.4, 3.2)
        time.sleep(delay)
        with room_lock:
            game = active_rooms.get(room_id)
            if not game or game.get('status') != 'playing' or game.get('active_turn') != bot_color:
                return
            
            board = game['board']
            bot_move = chess_engine.get_bot_move(board, 'medium')
            if not bot_move:
                return
            
            # Execute move
            from_sq = chess.square_name(bot_move.from_square)
            to_sq = chess.square_name(bot_move.to_square)
            promo = chess.piece_symbol(bot_move.promotion).lower() if bot_move.promotion else None
            
            execute_move_internal(game, from_sq, to_sq, promo, bot_color)
            
    threading.Thread(target=bot_task, daemon=True).start()

def execute_move_internal(game, from_sq, to_sq, promo, moving_color):
    now = time.time()
    room_id = game['game_id']
    board = game['board']
    
    # Deduct time
    if game.get('timer_running', False) and game.get('last_turn_timestamp'):
        elapsed = now - game['last_turn_timestamp']
        game['clocks'][moving_color] = max(0.0, game['clocks'][moving_color] - elapsed + game.get('increment', 0))
        if game['clocks'][moving_color] <= 0:
            handle_game_timeout(room_id, moving_color)
            return
    else:
        game['timer_running'] = True
        
    move_str = f"{from_sq}{to_sq}{promo if promo else ''}"
    move = chess.Move.from_uci(move_str)
    if move not in board.legal_moves:
        return
        
    san = board.san(move)
    is_capture = board.is_capture(move)
    is_check = board.gives_check(move)
    board.push(move)
    
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
    
    # If the other player is a bot and game is not over, schedule next bot move!
    if not is_over and game.get(next_turn) and game[next_turn].get('is_bot'):
        schedule_bot_move(room_id, next_turn)


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
    room_id = request.args.get('room')
    if not room_id:
        return redirect(url_for('index'))
    return render_template('play_online.html', room_id=room_id)

@app.route('/play/offline')
def play_offline():
    mode = request.args.get('mode', 'pass_and_play')
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
    room_id = uuid.uuid4().hex[:6].upper()
    
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
            'creator': session.get('username'),
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
    
    curr = session.get('username')
    if curr and (curr.lower() == white_user.lower() or curr.lower() == black_user.lower()):
        res = 'win' if (curr.lower() == white_user.lower() and result == '1-0') or (curr.lower() == black_user.lower() and result == '0-1') else ('draw' if result == '1/2-1/2' else 'loss')
        db.update_user_stats(curr, res, 0)
        
    return jsonify({'success': True, 'game_id': game_id})

@app.route('/api/room_add_bot', methods=['POST'])
def api_room_add_bot():
    """
    Fills an empty opponent slot in a custom room with a simulated online player.
    """
    data = request.get_json() or {}
    room_id = data.get('room_id')
    with room_lock:
        game = active_rooms.get(room_id)
        if not game:
            return jsonify({'error': 'Room not found'}), 404
        
        bot_name, bot_rating = random.choice(BOT_NAMES)
        bot_obj = {
            'username': bot_name,
            'token': 'BOT_' + uuid.uuid4().hex[:6],
            'sid': 'BOT_SID',
            'connected': True,
            'rating': bot_rating,
            'is_bot': True
        }
        
        if game['white'] is None:
            game['white'] = bot_obj
            bot_color = 'white'
        elif game['black'] is None:
            game['black'] = bot_obj
            bot_color = 'black'
        else:
            return jsonify({'error': 'Room already full'}), 400
            
        game['status'] = 'playing'
        game['last_turn_timestamp'] = time.time()
        
        # Broadcast player update
        socketio.emit('player_update', {
            'white': {
                'username': game['white']['username'] if game.get('white') else None,
                'rating': game['white'].get('rating', 1200) if game.get('white') else 1200,
                'connected': True
            },
            'black': {
                'username': game['black']['username'] if game.get('black') else None,
                'rating': game['black'].get('rating', 1200) if game.get('black') else 1200,
                'connected': True
            },
            'status': 'playing'
        }, room=room_id)
        
        # If bot is white, schedule its first move!
        if bot_color == 'white':
            schedule_bot_move(room_id, 'white')
            
        return jsonify({'success': True, 'bot_username': bot_name})


# ----------------- SocketIO Real-Time Handlers -----------------

@socketio.on('join_game')
def on_join_game(data):
    room_id = data.get('room_id')
    token = data.get('token') or f"tok_{request.sid[:6]}"
    session_user = session.get('username')
    user_name = session_user or data.get('username') or f"Guest_{token[:4]}"
    
    with room_lock:
        game = active_rooms.get(room_id)
        if not game:
            emit('error_message', {'message': 'Room not found or expired.'})
            return
            
        join_room(room_id)
        
        # Re-attach or assign role
        assigned_role = None
        user_prof = db.get_user_profile(user_name) if session_user else None
        rating = user_prof['rating'] if user_prof else 1200
        
        # 1. Existing White player reconnecting
        if game.get('white') and game['white'].get('token') == token:
            game['white']['sid'] = request.sid
            game['white']['connected'] = True
            assigned_role = 'white'
        # 2. Existing Black player reconnecting
        elif game.get('black') and game['black'].get('token') == token:
            game['black']['sid'] = request.sid
            game['black']['connected'] = True
            assigned_role = 'black'
        # 3. New player joins White if open
        elif game.get('white') is None:
            game['white'] = {
                'username': user_name,
                'token': token,
                'sid': request.sid,
                'connected': True,
                'rating': rating,
                'is_bot': False
            }
            assigned_role = 'white'
        # 4. New player joins Black if open
        elif game.get('black') is None:
            # If same username (e.g. testing in 2 tabs without login or same session), append tab tag
            if game['white']['username'] == user_name:
                user_name = f"{user_name} (Black)"
            game['black'] = {
                'username': user_name,
                'token': token,
                'sid': request.sid,
                'connected': True,
                'rating': rating,
                'is_bot': False
            }
            assigned_role = 'black'
        # 5. Spectator
        else:
            assigned_role = 'spectator'
            
        # If both slots filled, transition status to playing
        if game.get('white') and game.get('black') and game['status'] == 'waiting':
            game['status'] = 'playing'
            game['last_turn_timestamp'] = time.time()
            # If black is bot and white is not, bot can send welcome chat
            if game['black'].get('is_bot'):
                welcome_chat = {
                    'sender': game['black']['username'],
                    'message': random.choice(["Good luck! Have fun! 👍", "Hello! Let's have a great match.", "Good luck! ♟"]),
                    'time': datetime.datetime.now().strftime("%H:%M")
                }
                game['chat_history'].append(welcome_chat)
                socketio.emit('chat_received', welcome_chat, room=room_id)
            
        emit('game_init', {
            'room_id': room_id,
            'role': assigned_role,
            'token': token,
            'username': user_name,
            'white': {
                'username': game['white']['username'] if game.get('white') else None,
                'rating': game['white'].get('rating', 1200) if game.get('white') else 1200,
                'connected': game['white'].get('connected', False) if game.get('white') else False
            },
            'black': {
                'username': game['black']['username'] if game.get('black') else None,
                'rating': game['black'].get('rating', 1200) if game.get('black') else 1200,
                'connected': game['black'].get('connected', False) if game.get('black') else False
            },
            'status': game['status'],
            'fen': game['board'].fen(),
            'moves': game['move_history'],
            'clocks': game['clocks'],
            'active_turn': game['active_turn'],
            'time_control': game.get('time_control_name', '10 min Rapid'),
            'time_seconds': game.get('time_seconds', 600)
        })
        
        socketio.emit('player_update', {
            'white': {
                'username': game['white']['username'] if game.get('white') else None,
                'rating': game['white'].get('rating', 1200) if game.get('white') else 1200,
                'connected': game['white'].get('connected', False) if game.get('white') else False
            },
            'black': {
                'username': game['black']['username'] if game.get('black') else None,
                'rating': game['black'].get('rating', 1200) if game.get('black') else 1200,
                'connected': game['black'].get('connected', False) if game.get('black') else False
            },
            'status': game['status'],
            'active_turn': game['active_turn'],
            'clocks': game['clocks']
        }, room=room_id)

@socketio.on('make_move')
def on_make_move(data):
    room_id = data.get('room_id')
    from_sq = data.get('from')
    to_sq = data.get('to')
    promo = data.get('promotion')
    token = data.get('token')
    
    with room_lock:
        game = active_rooms.get(room_id)
        if not game or game.get('status') != 'playing':
            return
            
        board = game['board']
        turn_color = 'white' if board.turn == chess.WHITE else 'black'
        
        # Verify caller authorization by token or sid
        player_obj = game.get(turn_color)
        if not player_obj:
            return
            
        is_authorized = (token and player_obj.get('token') == token) or (player_obj.get('sid') == request.sid)
        if not is_authorized:
            return
            
        execute_move_internal(game, from_sq, to_sq, promo, turn_color)

@socketio.on('send_chat')
def on_send_chat(data):
    room_id = data.get('room_id')
    message = (data.get('message') or '').strip()
    token = data.get('token')
    if not message:
        return
        
    with room_lock:
        game = active_rooms.get(room_id)
        if not game:
            return
        
        # Determine sender name
        sender = session.get('username')
        if not sender:
            if game.get('white') and game['white'].get('token') == token:
                sender = game['white']['username']
            elif game.get('black') and game['black'].get('token') == token:
                sender = game['black']['username']
            else:
                sender = f"Guest_{token[:4] if token else request.sid[:4]}"
                
        chat_item = {
            'sender': sender,
            'message': message[:200],
            'time': datetime.datetime.now().strftime("%H:%M")
        }
        game['chat_history'].append(chat_item)
        socketio.emit('chat_received', chat_item, room=room_id)

@socketio.on('offer_draw')
def on_offer_draw(data):
    room_id = data.get('room_id')
    token = data.get('token')
    with room_lock:
        game = active_rooms.get(room_id)
        if not game or game.get('status') != 'playing':
            return
            
        sender_color = 'white' if game.get('white') and (game['white'].get('token') == token or game['white'].get('sid') == request.sid) else (
            'black' if game.get('black') and (game['black'].get('token') == token or game['black'].get('sid') == request.sid) else None
        )
        if not sender_color:
            return
            
        opponent_color = 'black' if sender_color == 'white' else 'white'
        game['draw_offered_by'] = sender_color
        
        # If opponent is a bot, bot decides whether to accept draw!
        if game.get(opponent_color) and game[opponent_color].get('is_bot'):
            eval_score = chess_engine.evaluate_board_heuristic(game['board'])
            # If position is roughly equal, bot accepts
            if abs(eval_score) <= 0.7:
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
            return
            
        socketio.emit('draw_offered', {'by': sender_color, 'username': game[sender_color]['username']}, room=room_id)

@socketio.on('respond_draw')
def on_respond_draw(data):
    room_id = data.get('room_id')
    accepted = data.get('accepted', False)
    token = data.get('token')
    with room_lock:
        game = active_rooms.get(room_id)
        if not game or game.get('status') != 'playing' or not game.get('draw_offered_by'):
            return
            
        responder_color = 'white' if game.get('white') and (game['white'].get('token') == token or game['white'].get('sid') == request.sid) else (
            'black' if game.get('black') and (game['black'].get('token') == token or game['black'].get('sid') == request.sid) else None
        )
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
    token = data.get('token')
    with room_lock:
        game = active_rooms.get(room_id)
        if not game or game.get('status') != 'playing':
            return
            
        resigning_color = 'white' if game.get('white') and (game['white'].get('token') == token or game['white'].get('sid') == request.sid) else (
            'black' if game.get('black') and (game['black'].get('token') == token or game['black'].get('sid') == request.sid) else None
        )
        if not resigning_color:
            return
            
        winner_color = 'black' if resigning_color == 'white' else 'white'
        result = "0-1" if resigning_color == "white" else "1-0"
        winner_user = game[winner_color]['username'] if game.get(winner_color) else ('Black' if winner_color == 'black' else 'White')
        
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

# ----------------- Realistic Matchmaking Queue -----------------

@socketio.on('join_matchmaking')
def on_join_matchmaking(data):
    tc = data.get('time_control', '10m')
    token = data.get('token') or f"tok_{request.sid[:6]}"
    username = session.get('username') or data.get('username') or f"Guest_{token[:4]}"
    client_sid = request.sid
    
    tc_map = {
        '1m': ('1 min Bullet', 60, 0),
        '3m': ('3 min Blitz', 180, 0),
        '10m': ('10 min Rapid', 600, 0),
        '30m': ('30 min Classical', 1800, 0)
    }
    tc_name, seconds, inc = tc_map.get(tc, ('10 min Rapid', 600, 0))
    
    with room_lock:
        q = match_queues.get(tc, match_queues['10m'])
        # Remove any stale entry with same token or sid
        q[:] = [entry for entry in q if entry['token'] != token and entry['sid'] != client_sid]
        
        if q:
            # Human vs Human match found!
            opponent = q.pop(0)
            room_id = uuid.uuid4().hex[:6].upper()
            
            players = [
                {'username': username, 'token': token, 'sid': client_sid},
                {'username': opponent['username'], 'token': opponent['token'], 'sid': opponent['sid']}
            ]
            random.shuffle(players)
            
            active_rooms[room_id] = {
                'game_id': room_id,
                'time_control': tc,
                'time_control_name': tc_name,
                'time_seconds': seconds,
                'increment': inc,
                'clocks': {'white': float(seconds), 'black': float(seconds)},
                'white': {
                    'username': players[0]['username'],
                    'token': players[0]['token'],
                    'sid': players[0]['sid'],
                    'connected': True,
                    'rating': 1200,
                    'is_bot': False
                },
                'black': {
                    'username': players[1]['username'],
                    'token': players[1]['token'],
                    'sid': players[1]['sid'],
                    'connected': True,
                    'rating': 1200,
                    'is_bot': False
                },
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
            
            socketio.emit('match_found', {
                'room_id': room_id,
                'opponent_name': opponent['username'],
                'opponent_rating': 1200
            }, room=client_sid)
            socketio.emit('match_found', {
                'room_id': room_id,
                'opponent_name': username,
                'opponent_rating': 1200
            }, room=opponent['sid'])
            return
            
        # No human queued yet: add to queue
        queue_entry = {
            'token': token,
            'sid': client_sid,
            'username': username,
            'time_control': tc,
            'joined_at': time.time()
        }
        q.append(queue_entry)
        emit('queue_joined', {'time_control': tc_name})
        
        # Schedule realistic matchmaking pairing (after 4.2 seconds if no human arrives)
        def auto_pair_worker(target_token, target_sid, target_user, target_tc):
            time.sleep(4.2)
            with room_lock:
                current_q = match_queues.get(target_tc, match_queues['10m'])
                # Check if user is still waiting in queue
                match_entry = next((e for e in current_q if e['token'] == target_token), None)
                if not match_entry:
                    return
                current_q.remove(match_entry)
                
                # Pair with simulated online opponent
                bot_name, bot_rating = random.choice(BOT_NAMES)
                room_id = uuid.uuid4().hex[:6].upper()
                
                # Randomize who gets White
                user_is_white = random.choice([True, False])
                user_prof = db.get_user_profile(target_user)
                user_rating = user_prof['rating'] if user_prof else 1200
                
                user_obj = {
                    'username': target_user,
                    'token': target_token,
                    'sid': target_sid,
                    'connected': True,
                    'rating': user_rating,
                    'is_bot': False
                }
                bot_obj = {
                    'username': bot_name,
                    'token': 'BOT_' + uuid.uuid4().hex[:6],
                    'sid': 'BOT_SID',
                    'connected': True,
                    'rating': bot_rating,
                    'is_bot': True
                }
                
                active_rooms[room_id] = {
                    'game_id': room_id,
                    'time_control': target_tc,
                    'time_control_name': tc_name,
                    'time_seconds': seconds,
                    'increment': inc,
                    'clocks': {'white': float(seconds), 'black': float(seconds)},
                    'white': user_obj if user_is_white else bot_obj,
                    'black': bot_obj if user_is_white else user_obj,
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
                
                socketio.emit('match_found', {
                    'room_id': room_id,
                    'opponent_name': bot_name,
                    'opponent_rating': bot_rating,
                    'your_color': 'white' if user_is_white else 'black'
                }, room=target_sid)
                
                # If bot is White, schedule initial move
                if not user_is_white:
                    schedule_bot_move(room_id, 'white')
                    
        threading.Thread(target=auto_pair_worker, args=(token, client_sid, username, tc), daemon=True).start()

@socketio.on('leave_matchmaking')
def on_leave_matchmaking(data):
    token = data.get('token')
    with room_lock:
        for q in match_queues.values():
            q[:] = [entry for entry in q if (token and entry['token'] != token) and entry['sid'] != request.sid]
        emit('queue_left', {})


if __name__ == '__main__':
    print("ChessClone server running on http://127.0.0.1:5000 ...")
    socketio.run(app, host='0.0.0.0', port=5000, debug=False)
