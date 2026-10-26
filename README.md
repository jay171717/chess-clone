# ChessClone - Simplified Chess.com Web Application

A simplified clone of **Chess.com** built in Python with **Flask**, **Flask-SocketIO**, **python-chess**, and **TinyDB** (a lightweight document-based database).

![ChessClone Preview](static/pieces/wN.svg)

---

## 🌟 Key Features

### 1. 🌐 Online & 📴 Offline Game Modes
- **Online Multiplayer**:
  - **Quick Matchmaking Queue**: Queue up for 1m, 3m, 10m, or 30m matches and automatically get paired with opponents in real-time.
  - **Custom Challenge Rooms**: Create a room with a 6-character room code, choose side (White/Black/Random) and time control, and share the link/code with a friend.
- **Offline Modes**:
  - **Pass & Play**: 2 players on the same screen with optional timers and flip board support.
  - **Play vs Computer / Bot**: Practice offline against the AI bot across 3 difficulty levels: Novice (Level 1), Intermediate (Level 2), and Master (Level 3).

### 2. 🗄️ Document-Based Database (TinyDB) & Authentication
- Accounts are stored as JSON documents in `data/chess_db.json`.
- Simple sign up and login with username and password (securely hashed with `werkzeug.security`).
- Guest mode is also supported for instant play without registration.

### 3. ♟️ Complete Chess Rules & Detection
- Legal move validation backed by `python-chess` (server) and `chess.js` (client).
- **Castling** (Kingside `O-O` and Queenside `O-O-O`).
- **En Passant** capture detection.
- **Pawn Promotion** modal dialog (Queen, Rook, Bishop, Knight with crisp SVGs).
- **Chess.com End-Game Detection**:
  - Checkmate
  - Stalemate
  - Draw by 50-move rule
  - Draw by threefold repetition
  - Draw by insufficient material
  - Flag fall / Timeout loss (or draw if opponent has insufficient material)
  - Resignation
  - Draw offer & accept/decline dialog

### 4. ⏱️ Multiple Game Modes & Clocks
- **1 min (Bullet ⚡)**
- **3 min (Blitz ⏱)**
- **10 min (Rapid 🏆)**
- **30 min (Classical ⌛)**
- **Unlimited (Casual)**
- Synchronized countdown clocks with millisecond accuracy and low-time visual warnings (under 20 seconds).

### 5. 💬 In-Match Chat
- Dockable chat tab right next to the board.
- Real-time messages with timestamps, sender badges, and auto-scroll.

### 6. 📊 Game Review with Stockfish Evaluation Bar
- Automatically saves both online and offline games to the database.
- Interactive move navigation:
  - First (`|<<`), Previous (`<`), Next (`>`), Last (`>>`).
  - Keyboard navigation (Left/Right arrow keys, Home/End).
  - Click any move in the move notation table to jump straight to that position.
- **Stockfish Evaluation Bar**:
  - Dynamic vertical evaluation bar showing White vs Black winning advantage (+1.5, -0.8, +M2).
  - Integrated with the **Stockfish Online API** with an automatic offline heuristic fallback.
  - Displays engine best move recommendations.
  - Copy PGN button to export the game.

### 7. 👤 Player Statistics & Leaderboard
- Player profile showing:
  - Joined Date
  - Current Elo Rating
  - Total Matches Played
  - Wins, Draws, Losses
  - Win/Draw/Loss ratio visual bar and percentage
  - Complete match history table with direct "Review Game" links
- Global Leaderboard ranking the top players by rating and win rate.

### 8. 🎨 Chess.com Dark UI/UX
- Classic green `#739552` and ivory `#ebecd0` board.
- Highlight squares for selected pieces, last moves, and king check radial red glow.
- Legal move hint dots and capture rings.
- Captured pieces tray with real-time material advantage difference (`+1`, `+3`, etc.).
- Procedural audio sound effects powered by the Web Audio API (wooden move taps, capture clicks, check chimes, game-over fanfare).

---

## 🚀 Getting Started

### 1. Requirements
- Python 3.10+ (tested on Python 3.14)
- Pip

### 2. Install Dependencies
```bash
pip install -r requirements.txt
```

### 3. Run the Server
```bash
python app.py
```
Then open your browser at:
```
http://127.0.0.1:5000
```

---

## 🧪 Running Automated Tests

Run the comprehensive unit and integration test suite:
```bash
python -u -m unittest tests/test_app.py
```

---

## 📂 Project Structure

```
focused-nobel/
├── app.py                  # Main Flask app, SocketIO event handlers & REST APIs
├── db.py                   # TinyDB document database wrapper (User & Game models)
├── chess_engine.py         # python-chess rules, Stockfish evaluator, and AI bot
├── requirements.txt        # Project dependencies
├── data/
│   └── chess_db.json       # Document database storing user accounts and games
├── static/
│   ├── css/
│   │   └── style.css       # Chess.com dark theme CSS
│   ├── js/
│   │   ├── chess.min.js    # Client-side chess rules & move generation
│   │   ├── socket.io.min.js# Real-time WebSockets client
│   │   ├── audio.js        # Web Audio API sound effects synthesizer
│   │   ├── board.js        # Interactive SVG chessboard renderer & drag-and-drop
│   │   ├── game_online.js  # Online multiplayer handler & live clocks
│   │   ├── game_offline.js # Pass & Play and vs Computer mode
│   │   └── review.js       # Game review & Stockfish evaluation bar controller
│   └── pieces/             # 12 crisp SVG vector chess pieces
├── templates/
│   ├── base.html           # Layout with navigation and promotion modal
│   ├── index.html          # Lobby: Quick Play, Custom Room, Recent Games
│   ├── play_online.html    # Online multiplayer arena
│   ├── play_offline.html   # Offline Pass & Play / vs Bot arena
│   ├── review.html         # Game review with Stockfish evaluation bar
│   ├── profile.html        # Player profile, stats, and match history
│   ├── leaderboard.html    # Global player leaderboard
│   ├── login.html          # Document-based authentication
│   └── error.html          # Error notice page
└── tests/
    └── test_app.py         # Automated test suite
```
