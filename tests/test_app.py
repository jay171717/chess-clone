import unittest
import json
import time
import chess
from app import app, socketio
import db
import chess_engine

class ChessCloneTestCase(unittest.TestCase):
    def setUp(self):
        app.config['TESTING'] = True
        app.config['SECRET_KEY'] = 'test-secret-key'
        self.client = app.test_client()

    def test_database_user_and_stats(self):
        username = "TestMagnus"
        password = "magnuspassword"
        # Register user
        success, msg = db.create_user(username, password)
        # Should succeed or already exist
        self.assertIn("registered", msg.lower() if success else "taken")

        # Verify login
        user, msg = db.verify_user(username, password)
        self.assertIsNotNone(user)
        self.assertEqual(user['username'], username)

        # Invalid login
        bad_user, _ = db.verify_user(username, "wrongpass")
        self.assertIsNone(bad_user)

        # Update stats
        initial_rating = user['rating']
        db.update_user_stats(username, 'win', 16)
        updated = db.get_user_profile(username)
        self.assertEqual(updated['rating'], initial_rating + 16)
        self.assertGreaterEqual(updated['wins'], 1)
        self.assertGreaterEqual(updated['games_played'], 1)

    def test_chess_rules_detection(self):
        # 1. Scholar's Mate (Checkmate detection)
        board = chess.Board()
        moves = ["e4", "e5", "Qh5", "Nc6", "Bc4", "Nf6", "Qxf7#"]
        for m in moves:
            mv = board.parse_san(m)
            board.push(mv)

        self.assertTrue(board.is_checkmate())
        is_over, result, reason, winner = chess_engine.check_game_over(board)
        self.assertTrue(is_over)
        self.assertEqual(result, "1-0")
        self.assertEqual(reason, "checkmate")
        self.assertEqual(winner, "white")

        # 2. Stalemate detection
        stalemate_board = chess.Board("7k/5Q2/6K1/8/8/8/8/8 b - - 0 1")
        self.assertTrue(stalemate_board.is_stalemate())
        is_over, result, reason, winner = chess_engine.check_game_over(stalemate_board)
        self.assertTrue(is_over)
        self.assertEqual(result, "1/2-1/2")
        self.assertEqual(reason, "stalemate")
        self.assertEqual(winner, "draw")

        # 3. Castling legality
        c_board = chess.Board("r3k2r/pppppppp/8/8/8/8/PPPPPPPP/R3K2R w KQkq - 0 1")
        # Kingside castle
        self.assertIn(chess.Move.from_uci("e1g1"), c_board.legal_moves)
        # Queenside castle
        self.assertIn(chess.Move.from_uci("e1c1"), c_board.legal_moves)

        # 4. En Passant
        ep_board = chess.Board("rnbqkbnr/ppp1p1pp/8/3pPp2/8/8/PPPP1PPP/RNBQKBNR w KQkq f6 0 3")
        self.assertIn(chess.Move.from_uci("e5f6"), ep_board.legal_moves)

        # 5. Promotion
        promo_board = chess.Board("8/4P3/8/8/8/8/8/4K2k w - - 0 1")
        self.assertIn(chess.Move.from_uci("e7e8q"), promo_board.legal_moves)
        self.assertIn(chess.Move.from_uci("e7e8n"), promo_board.legal_moves)

    def test_bot_and_eval(self):
        board = chess.Board()
        for diff in ['easy', 'medium', 'hard']:
            m = chess_engine.get_bot_move(board, diff)
            self.assertIsNotNone(m)
            self.assertIn(m, board.legal_moves)

        # Evaluation
        eval_res = chess_engine.get_stockfish_evaluation(board.fen())
        self.assertIn('win_chance', eval_res)
        self.assertIn('evaluation', eval_res)
        self.assertIn('bestmove', eval_res)

    def test_http_routes(self):
        # Lobby
        res = self.client.get('/')
        self.assertEqual(res.status_code, 200)
        self.assertIn(b'Play Chess', res.data)

        # Offline routes
        res = self.client.get('/play/offline?mode=vs_bot&difficulty=medium')
        self.assertEqual(res.status_code, 200)
        res = self.client.get('/play/offline?mode=pass_and_play')
        self.assertEqual(res.status_code, 200)

        # Leaderboard
        res = self.client.get('/leaderboard')
        self.assertEqual(res.status_code, 200)

        # Create Room API
        res = self.client.post('/api/create_room', json={'time_control': '10m', 'color': 'white'})
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get('success'))
        room_id = data.get('room_id')
        self.assertIsNotNone(room_id)

        # Bot Move API
        res = self.client.post('/api/bot_move', json={'fen': chess.STARTING_FEN, 'difficulty': 'easy'})
        self.assertEqual(res.status_code, 200)
        bot_data = res.get_json()
        self.assertTrue(bot_data.get('success'))
        self.assertIn('uci', bot_data)

        # Eval API
        res = self.client.post('/api/eval', json={'fen': chess.STARTING_FEN})
        self.assertEqual(res.status_code, 200)
        eval_data = res.get_json()
        self.assertIn('win_chance', eval_data)

        # Save Offline Game API
        res = self.client.post('/api/save_offline_game', json={
            'moves': [{'from': 'e2', 'to': 'e4', 'san': 'e4', 'fen': '...'}],
            'result': '1-0',
            'reason': 'resignation',
            'white_username': 'Hero',
            'black_username': 'Bot'
        })
        self.assertEqual(res.status_code, 200)
        save_data = res.get_json()
        self.assertTrue(save_data.get('success'))
        game_id = save_data.get('game_id')

        # Review page
        res = self.client.get(f'/review/{game_id}')
        self.assertEqual(res.status_code, 200)
        self.assertIn(b'Stockfish Review', res.data)

    def test_socketio_flow(self):
        # Test SocketIO multiplayer client connection & gameplay
        socket_client1 = socketio.test_client(app)
        socket_client2 = socketio.test_client(app)

        self.assertTrue(socket_client1.is_connected())
        self.assertTrue(socket_client2.is_connected())

        # Create room
        res = self.client.post('/api/create_room', json={'time_control': '10m', 'color': 'white'})
        room_id = res.get_json()['room_id']

        # Join room
        socket_client1.emit('join_game', {'room_id': room_id, 'username': 'Player1'})
        received1 = socket_client1.get_received()
        init_ev = next(ev for ev in received1 if ev['name'] == 'game_init')
        self.assertEqual(init_ev['args'][0]['role'], 'white')

        socket_client2.emit('join_game', {'room_id': room_id, 'username': 'Player2'})
        received2 = socket_client2.get_received()
        init_ev2 = next(ev for ev in received2 if ev['name'] == 'game_init')
        self.assertEqual(init_ev2['args'][0]['role'], 'black')

        # White makes move e2->e4
        socket_client1.emit('make_move', {'room_id': room_id, 'from': 'e2', 'to': 'e4'})
        moves_received = socket_client1.get_received()
        move_ev = next(ev for ev in moves_received if ev['name'] == 'move_made')
        self.assertEqual(move_ev['args'][0]['move']['san'], 'e4')
        self.assertEqual(move_ev['args'][0]['active_turn'], 'black')

        # Send in-game chat
        socket_client1.emit('send_chat', {'room_id': room_id, 'message': 'Good luck!'})
        chat_received = socket_client2.get_received()
        chat_ev = next(ev for ev in chat_received if ev['name'] == 'chat_received')
        self.assertEqual(chat_ev['args'][0]['message'], 'Good luck!')

        # Resign match
        socket_client2.emit('resign', {'room_id': room_id})
        over_received = socket_client1.get_received()
        over_ev = next(ev for ev in over_received if ev['name'] == 'game_over')
        self.assertEqual(over_ev['args'][0]['winner'], 'Player1')
        self.assertEqual(over_ev['args'][0]['reason'], 'resignation')

        socket_client1.disconnect()
        socket_client2.disconnect()

if __name__ == '__main__':
    unittest.main()
