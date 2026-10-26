import unittest
import json
import time
import os
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
        success, msg = db.create_user(username, password)
        self.assertTrue(success or "taken" in msg.lower())

        user, msg = db.verify_user(username, password)
        self.assertIsNotNone(user)
        self.assertEqual(user['username'], username)

        bad_user, _ = db.verify_user(username, "wrongpass")
        self.assertIsNone(bad_user)

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
        self.assertIn(chess.Move.from_uci("e1g1"), c_board.legal_moves)
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

        eval_res = chess_engine.get_stockfish_evaluation(board.fen())
        self.assertIn('win_chance', eval_res)
        self.assertIn('evaluation', eval_res)
        self.assertIn('bestmove', eval_res)

    def test_sound_assets_exist(self):
        sounds = ['move.mp3', 'capture.mp3', 'check.mp3', 'notify.mp3', 'lowtime.mp3', 'victory.mp3', 'defeat.mp3', 'draw.mp3']
        for s in sounds:
            path = os.path.join('static', 'sounds', s)
            self.assertTrue(os.path.exists(path), f"Sound {s} missing!")
            self.assertGreater(os.path.getsize(path), 500, f"Sound {s} is empty!")

    def test_multi_move_online_flow(self):
        # Verify multiple turns (e.g. 1. e4 e5 2. Nf3 Nc6) record full move history
        socket_client1 = socketio.test_client(app)
        socket_client2 = socketio.test_client(app)

        res = self.client.post('/api/create_room', json={'time_control': '10m', 'color': 'white'})
        room_id = res.get_json()['room_id']

        token1 = 'token_p1_unique'
        token2 = 'token_p2_unique'

        socket_client1.emit('join_game', {'room_id': room_id, 'username': 'Player1', 'token': token1})
        socket_client2.emit('join_game', {'room_id': room_id, 'username': 'Player2', 'token': token2})

        # Move 1: White e2 -> e4
        socket_client1.emit('make_move', {'room_id': room_id, 'from': 'e2', 'to': 'e4', 'token': token1})
        rec1 = socket_client1.get_received()
        mv1 = [ev for ev in rec1 if ev['name'] == 'move_made'][-1]
        self.assertEqual(mv1['args'][0]['move']['san'], 'e4')
        self.assertEqual(mv1['args'][0]['active_turn'], 'black')

        # Clear queues before next moves
        socket_client1.get_received()
        socket_client2.get_received()

        # Move 2: Black e7 -> e5
        socket_client2.emit('make_move', {'room_id': room_id, 'from': 'e7', 'to': 'e5', 'token': token2})
        rec2 = socket_client2.get_received()
        mv2 = [ev for ev in rec2 if ev['name'] == 'move_made'][-1]
        self.assertEqual(mv2['args'][0]['move']['san'], 'e5')
        self.assertEqual(mv2['args'][0]['active_turn'], 'white')

        socket_client1.get_received()
        socket_client2.get_received()

        # Move 3: White g1 -> f3
        socket_client1.emit('make_move', {'room_id': room_id, 'from': 'g1', 'to': 'f3', 'token': token1})
        rec3 = socket_client1.get_received()
        mv3 = [ev for ev in rec3 if ev['name'] == 'move_made'][-1]
        self.assertEqual(mv3['args'][0]['move']['san'], 'Nf3')
        self.assertEqual(mv3['args'][0]['active_turn'], 'black')

        socket_client1.get_received()
        socket_client2.get_received()

        # Move 4: Black b8 -> c6
        socket_client2.emit('make_move', {'room_id': room_id, 'from': 'b8', 'to': 'c6', 'token': token2})
        rec4 = socket_client2.get_received()
        mv4 = [ev for ev in rec4 if ev['name'] == 'move_made'][-1]
        self.assertEqual(mv4['args'][0]['move']['san'], 'Nc6')
        self.assertEqual(mv4['args'][0]['active_turn'], 'white')

        # End game with resignation
        socket_client2.emit('resign', {'room_id': room_id, 'token': token2})
        
        # Check saved game in database has all 4 moves!
        game = db.get_game(room_id)
        self.assertIsNotNone(game)
        self.assertEqual(len(game['moves']), 4)
        self.assertEqual(game['moves'][0]['san'], 'e4')
        self.assertEqual(game['moves'][1]['san'], 'e5')
        self.assertEqual(game['moves'][2]['san'], 'Nf3')
        self.assertEqual(game['moves'][3]['san'], 'Nc6')

        socket_client1.disconnect()
        socket_client2.disconnect()

    def test_room_add_bot(self):
        res = self.client.post('/api/create_room', json={'time_control': '10m', 'color': 'white'})
        room_id = res.get_json()['room_id']

        # Add bot to room
        res_bot = self.client.post('/api/room_add_bot', json={'room_id': room_id})
        self.assertEqual(res_bot.status_code, 200)
        self.assertTrue(res_bot.get_json()['success'])
        self.assertIn('bot_username', res_bot.get_json())

if __name__ == '__main__':
    unittest.main()
