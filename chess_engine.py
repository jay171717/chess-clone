import urllib.request
import urllib.parse
import json
import random
import time
import chess
import chess.pgn

# Piece-square tables for offline heuristic bot and fallback evaluator
PAWN_TABLE = [
    0,  0,  0,  0,  0,  0,  0,  0,
    50, 50, 50, 50, 50, 50, 50, 50,
    10, 10, 20, 30, 30, 20, 10, 10,
     5,  5, 10, 25, 25, 10,  5,  5,
     0,  0,  0, 20, 20,  0,  0,  0,
     5, -5,-10,  0,  0,-10, -5,  5,
     5, 10, 10,-20,-20, 10, 10,  5,
     0,  0,  0,  0,  0,  0,  0,  0
]

KNIGHT_TABLE = [
    -50,-40,-30,-30,-30,-30,-40,-50,
    -40,-20,  0,  0,  0,  0,-20,-40,
    -30,  0, 10, 15, 15, 10,  0,-30,
    -30,  5, 15, 20, 20, 15,  5,-30,
    -30,  0, 15, 20, 20, 15,  0,-30,
    -30,  5, 10, 15, 15, 10,  5,-30,
    -40,-20,  0,  5,  5,  0,-20,-40,
    -50,-40,-30,-30,-30,-30,-40,-50,
]

BISHOP_TABLE = [
    -20,-10,-10,-10,-10,-10,-10,-20,
    -10,  0,  0,  0,  0,  0,  0,-10,
    -10,  0,  5, 10, 10,  5,  0,-10,
    -10,  5,  5, 10, 10,  5,  5,-10,
    -10,  0, 10, 10, 10, 10,  0,-10,
    -10, 10, 10, 10, 10, 10, 10,-10,
    -10,  5,  0,  0,  0,  0,  5,-10,
    -20,-10,-10,-10,-10,-10,-10,-20,
]

ROOK_TABLE = [
      0,  0,  0,  0,  0,  0,  0,  0,
      5, 10, 10, 10, 10, 10, 10,  5,
     -5,  0,  0,  0,  0,  0,  0, -5,
     -5,  0,  0,  0,  0,  0,  0, -5,
     -5,  0,  0,  0,  0,  0,  0, -5,
     -5,  0,  0,  0,  0,  0,  0, -5,
     -5,  0,  0,  0,  0,  0,  0, -5,
      0,  0,  0,  5,  5,  0,  0,  0
]

QUEEN_TABLE = [
    -20,-10,-10, -5, -5,-10,-10,-20,
    -10,  0,  0,  0,  0,  0,  0,-10,
    -10,  0,  5,  5,  5,  5,  0,-10,
     -5,  0,  5,  5,  5,  5,  0, -5,
      0,  0,  5,  5,  5,  5,  0, -5,
    -10,  5,  5,  5,  5,  5,  0,-10,
    -10,  0,  5,  0,  0,  0,  0,-10,
    -20,-10,-10, -5, -5,-10,-10,-20
]

KING_TABLE_MID = [
    -30,-40,-40,-50,-50,-40,-40,-30,
    -30,-40,-40,-50,-50,-40,-40,-30,
    -30,-40,-40,-50,-50,-40,-40,-30,
    -30,-40,-40,-50,-50,-40,-40,-30,
    -20,-30,-30,-40,-40,-30,-30,-20,
    -10,-20,-20,-20,-20,-20,-20,-10,
     20, 20,  0,  0,  0,  0, 20, 20,
     20, 30, 10,  0,  0, 10, 30, 20
]

PIECE_VALUES = {
    chess.PAWN: 100,
    chess.KNIGHT: 320,
    chess.BISHOP: 330,
    chess.ROOK: 500,
    chess.QUEEN: 900,
    chess.KING: 20000
}

TABLES = {
    chess.PAWN: PAWN_TABLE,
    chess.KNIGHT: KNIGHT_TABLE,
    chess.BISHOP: BISHOP_TABLE,
    chess.ROOK: ROOK_TABLE,
    chess.QUEEN: QUEEN_TABLE,
    chess.KING: KING_TABLE_MID
}

def evaluate_board_heuristic(board: chess.Board) -> float:
    """
    Evaluates the board from White's perspective in pawn units (e.g. +1.50).
    """
    if board.is_checkmate():
        return -999.0 if board.turn == chess.WHITE else 999.0
    if board.is_stalemate() or board.is_insufficient_material() or board.can_claim_fifty_moves():
        return 0.0

    score = 0
    for square in chess.SQUARES:
        piece = board.piece_at(square)
        if piece is None:
            continue
        val = PIECE_VALUES.get(piece.piece_type, 0)
        table = TABLES.get(piece.piece_type)
        if table:
            sq_idx = square if piece.color == chess.BLACK else chess.square_mirror(square)
            pst_val = table[sq_idx]
        else:
            pst_val = 0

        piece_score = val + pst_val
        if piece.color == chess.WHITE:
            score += piece_score
        else:
            score -= piece_score

    # Mobility bonus
    mobility = len(list(board.legal_moves))
    if board.turn == chess.WHITE:
        score += mobility * 2
    else:
        score -= mobility * 2

    return round(score / 100.0, 2)


def get_stockfish_evaluation(fen: str) -> dict:
    """
    Queries Stockfish Online API with fallback to local heuristic evaluation.
    Returns:
    {
       "source": "stockfish" or "heuristic",
       "evaluation": float (from White's perspective),
       "mate": int or None,
       "bestmove": str,
       "win_chance": float (0-100)
    }
    """
    try:
        encoded_fen = urllib.parse.quote(fen)
        url = f"https://stockfish.online/api/s/v2.php?fen={encoded_fen}&depth=10"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 ChessClone/1.0'})
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            data = json.loads(resp.read().decode())
            if data.get('success'):
                ev = data.get('evaluation')
                mate = data.get('mate')
                bestmove = data.get('bestmove', '')
                if ' ' in bestmove:
                    parts = bestmove.split()
                    if len(parts) >= 2:
                        bestmove = parts[1]

                # Convert mate or eval to win percentage
                if mate is not None:
                    win_chance = 100.0 if mate > 0 else 0.0
                elif ev is not None:
                    # Lichess win chance formula: 50 + 50 * (2 / (1 + exp(-0.00368208 * cp)) - 1)
                    cp = ev * 100
                    win_chance = round(100.0 / (1.0 + 10 ** (-ev / 4.0)), 1)
                else:
                    win_chance = 50.0

                return {
                    "source": "stockfish",
                    "evaluation": ev,
                    "mate": mate,
                    "bestmove": bestmove,
                    "win_chance": win_chance
                }
    except Exception as e:
        pass

    # Fallback to python-chess heuristic evaluation
    try:
        board = chess.Board(fen)
        ev = evaluate_board_heuristic(board)
        # Find best legal move shallowly
        bestmove = ""
        moves = list(board.legal_moves)
        if moves:
            best_val = -99999 if board.turn == chess.WHITE else 99999
            best_m = moves[0]
            for m in moves:
                board.push(m)
                v = evaluate_board_heuristic(board)
                board.pop()
                if board.turn == chess.WHITE:
                    if v > best_val:
                        best_val = v
                        best_m = m
                else:
                    if v < best_val:
                        best_val = v
                        best_m = m
            bestmove = best_m.uci()

        win_chance = round(100.0 / (1.0 + 10 ** (-ev / 4.0)), 1)
        return {
            "source": "heuristic",
            "evaluation": ev,
            "mate": None,
            "bestmove": bestmove,
            "win_chance": win_chance
        }
    except Exception:
        return {
            "source": "fallback",
            "evaluation": 0.0,
            "mate": None,
            "bestmove": "",
            "win_chance": 50.0
        }


def check_game_over(board: chess.Board):
    """
    Checks if game has concluded and returns (is_over, result, reason, winner)
    """
    if board.is_checkmate():
        winner = "black" if board.turn == chess.WHITE else "white"
        result = "0-1" if winner == "black" else "1-0"
        return True, result, "checkmate", winner
    
    if board.is_stalemate():
        return True, "1/2-1/2", "stalemate", "draw"
        
    if board.is_insufficient_material():
        return True, "1/2-1/2", "insufficient_material", "draw"
        
    if board.can_claim_fifty_moves():
        return True, "1/2-1/2", "50_move_rule", "draw"
        
    if board.can_claim_threefold_repetition() or board.is_repetition(3):
        return True, "1/2-1/2", "threefold_repetition", "draw"
        
    return False, None, None, None


# ----------------- AI / Bot Move Generation -----------------

def get_bot_move(board: chess.Board, difficulty="medium") -> chess.Move:
    """
    Generates a move for the AI bot based on difficulty:
    'easy' (Level 1), 'medium' (Level 2), 'hard' (Level 3)
    """
    legal_moves = list(board.legal_moves)
    if not legal_moves:
        return None

    if difficulty == "easy":
        # 50% random move, 50% prioritize captures
        captures = [m for m in legal_moves if board.is_capture(m)]
        if captures and random.random() < 0.6:
            return random.choice(captures)
        return random.choice(legal_moves)

    # For medium and hard, run alpha-beta minimax
    depth = 2 if difficulty == "medium" else 3
    is_maximizing = (board.turn == chess.WHITE)
    
    best_move = legal_moves[0]
    alpha = -100000
    beta = 100000

    # Sort moves to improve alpha-beta pruning (captures first)
    legal_moves.sort(key=lambda m: (board.is_capture(m), board.gives_check(m)), reverse=True)

    if is_maximizing:
        max_eval = -100000
        for move in legal_moves:
            board.push(move)
            eval_score = _minimax(board, depth - 1, alpha, beta, False)
            board.pop()
            if eval_score > max_eval:
                max_eval = eval_score
                best_move = move
            alpha = max(alpha, eval_score)
            if beta <= alpha:
                break
    else:
        min_eval = 100000
        for move in legal_moves:
            board.push(move)
            eval_score = _minimax(board, depth - 1, alpha, beta, True)
            board.pop()
            if eval_score < min_eval:
                min_eval = eval_score
                best_move = move
            beta = min(beta, eval_score)
            if beta <= alpha:
                break

    return best_move

def _minimax(board: chess.Board, depth: int, alpha: float, beta: float, is_maximizing: bool) -> float:
    if depth == 0 or board.is_game_over():
        return evaluate_board_heuristic(board) * 100

    moves = list(board.legal_moves)
    moves.sort(key=lambda m: (board.is_capture(m), board.gives_check(m)), reverse=True)

    if is_maximizing:
        max_eval = -100000
        for move in moves:
            board.push(move)
            score = _minimax(board, depth - 1, alpha, beta, False)
            board.pop()
            max_eval = max(max_eval, score)
            alpha = max(alpha, score)
            if beta <= alpha:
                break
        return max_eval
    else:
        min_eval = 100000
        for move in moves:
            board.push(move)
            score = _minimax(board, depth - 1, alpha, beta, True)
            board.pop()
            min_eval = min(min_eval, score)
            beta = min(beta, score)
            if beta <= alpha:
                break
        return min_eval
