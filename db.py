import os
import math
import datetime
import threading
from tinydb import TinyDB, Query
from werkzeug.security import generate_password_hash, check_password_hash

DB_PATH = os.path.join(os.path.dirname(__file__), 'data', 'chess_db.json')
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

_db_lock = threading.RLock()
_db = None

def get_db():
    global _db
    if _db is None:
        _db = TinyDB(DB_PATH, indent=2)
    return _db

def get_users_table():
    return get_db().table('users')

def get_games_table():
    return get_db().table('games')

# ----------------- User Management -----------------

def create_user(username, password):
    username = username.strip()
    if not username or len(username) < 2:
        return False, "Username must be at least 2 characters long."
    if len(password) < 3:
        return False, "Password must be at least 3 characters long."

    with _db_lock:
        users = get_users_table()
        User = Query()
        # Case-insensitive username check
        existing = users.search(User.username_lower == username.lower())
        if existing:
            return False, "Username is already taken."

        now = datetime.datetime.now()
        user_doc = {
            "username": username,
            "username_lower": username.lower(),
            "password_hash": generate_password_hash(password),
            "created_at": now.strftime("%b %d, %Y"),
            "created_timestamp": now.timestamp(),
            "games_played": 0,
            "wins": 0,
            "losses": 0,
            "draws": 0,
            "rating": 1200
        }
        users.insert(user_doc)
        return True, "User registered successfully."

def verify_user(username, password):
    username = username.strip()
    with _db_lock:
        users = get_users_table()
        User = Query()
        found = users.search(User.username_lower == username.lower())
        if not found:
            return None, "Invalid username or password."
        user = found[0]
        if check_password_hash(user['password_hash'], password):
            return get_user_profile(user['username']), "Login successful."
        return None, "Invalid username or password."

def get_user_profile(username):
    with _db_lock:
        users = get_users_table()
        User = Query()
        found = users.search(User.username_lower == username.lower())
        if not found:
            return None
        user = dict(found[0])
        user.pop('password_hash', None)
        total = user.get('games_played', 0)
        wins = user.get('wins', 0)
        losses = user.get('losses', 0)
        draws = user.get('draws', 0)
        
        # Calculate Win / Draw / Loss percentages
        user['win_rate'] = round((wins / total * 100), 1) if total > 0 else 0.0
        user['draw_rate'] = round((draws / total * 100), 1) if total > 0 else 0.0
        user['loss_rate'] = round((losses / total * 100), 1) if total > 0 else 0.0
        return user

def update_user_stats(username, result, rating_delta=0):
    """
    result: 'win', 'loss', 'draw'
    """
    with _db_lock:
        users = get_users_table()
        User = Query()
        found = users.search(User.username_lower == username.lower())
        if not found:
            return
        user = found[0]
        games_played = user.get('games_played', 0) + 1
        wins = user.get('wins', 0) + (1 if result == 'win' else 0)
        losses = user.get('losses', 0) + (1 if result == 'loss' else 0)
        draws = user.get('draws', 0) + (1 if result == 'draw' else 0)
        new_rating = max(100, user.get('rating', 1200) + rating_delta)

        users.update({
            'games_played': games_played,
            'wins': wins,
            'losses': losses,
            'draws': draws,
            'rating': new_rating
        }, User.username_lower == username.lower())

def calculate_elo_change(rating_a, rating_b, score_a, k=32):
    """
    score_a: 1.0 (win), 0.5 (draw), 0.0 (loss)
    returns (delta_a, delta_b)
    """
    expected_a = 1.0 / (1.0 + math.pow(10, (rating_b - rating_a) / 400.0))
    expected_b = 1.0 - expected_a
    score_b = 1.0 - score_a

    delta_a = round(k * (score_a - expected_a))
    delta_b = round(k * (score_b - expected_b))
    return int(delta_a), int(delta_b)

def get_leaderboard(limit=10):
    with _db_lock:
        users = get_users_table().all()
        # Sort by rating descending, then games played
        sorted_users = sorted(users, key=lambda u: (u.get('rating', 1200), u.get('wins', 0)), reverse=True)
        res = []
        for rank, u in enumerate(sorted_users[:limit], 1):
            clean = dict(u)
            clean.pop('password_hash', None)
            clean['rank'] = rank
            total = clean.get('games_played', 0)
            clean['win_rate'] = round((clean.get('wins', 0) / total * 100), 1) if total > 0 else 0.0
            res.append(clean)
        return res

# ----------------- Game Management -----------------

def save_game(game_data):
    """
    Saves a completed game to the document database.
    """
    with _db_lock:
        games = get_games_table()
        Game = Query()
        game_id = game_data.get('game_id')
        if not game_id:
            return None
        existing = games.search(Game.game_id == game_id)
        if existing:
            games.update(game_data, Game.game_id == game_id)
        else:
            games.insert(game_data)
        return game_id

def get_game(game_id):
    with _db_lock:
        games = get_games_table()
        Game = Query()
        found = games.search(Game.game_id == game_id)
        return found[0] if found else None

def get_user_games(username, limit=50):
    with _db_lock:
        games = get_games_table().all()
        u_lower = username.lower()
        user_matches = [
            g for g in games 
            if g.get('white_username', '').lower() == u_lower or g.get('black_username', '').lower() == u_lower
        ]
        # Sort newest first
        user_matches.sort(key=lambda g: g.get('created_timestamp', 0), reverse=True)
        return user_matches[:limit]

def get_recent_games(limit=15):
    with _db_lock:
        games = get_games_table().all()
        games.sort(key=lambda g: g.get('created_timestamp', 0), reverse=True)
        return games[:limit]
