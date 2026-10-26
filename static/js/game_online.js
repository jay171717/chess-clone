/**
 * Online Multiplayer Game Controller
 * Manages SocketIO events, live turn clocks, sound effects, in-match chat, and game state.
 */

document.addEventListener('DOMContentLoaded', () => {
  const roomId = window.GAME_ROOM_ID;
  if (!roomId) return;

  // Unique tab player token (persists on refresh, distinct across tabs)
  let playerToken = sessionStorage.getItem('chess_player_token');
  if (!playerToken) {
    playerToken = 'p_' + Math.random().toString(36).substr(2, 9);
    sessionStorage.setItem('chess_player_token', playerToken);
  }

  const socket = io();
  const chess = new Chess();
  let boardUI = null;
  let playerRole = 'spectator'; // 'white', 'black', or 'spectator'
  let activeTurn = 'white';
  let clocks = { white: 600, black: 600 };
  let timerRunning = false;
  let clockInterval = null;
  let lastSyncTime = Date.now();
  let warnedLowTime = false;

  // Initialize board UI
  boardUI = new ChessBoardUI('chess-board', {
    orientation: 'white',
    playerColor: 'white',
    getLegalMoves: (square) => {
      if (playerRole !== activeTurn && playerRole !== 'spectator') return [];
      const moves = chess.moves({ square: square, verbose: true });
      return moves.map(m => m.to);
    },
    onMove: (from, to, promotion) => {
      if (playerRole !== activeTurn) return;
      socket.emit('make_move', {
        room_id: roomId,
        from: from,
        to: to,
        promotion: promotion,
        token: playerToken
      });
    }
  });

  // Sound toggle button
  const soundBtn = document.getElementById('btn-sound-toggle');
  if (soundBtn) {
    soundBtn.addEventListener('click', () => {
      window.chessAudio.muted = !window.chessAudio.muted;
      soundBtn.innerHTML = window.chessAudio.muted 
        ? '🔇 <span>Sound Off</span>' 
        : '🔊 <span>Sound On</span>';
    });
  }

  // Flip board button
  const flipBtn = document.getElementById('btn-flip-board');
  if (flipBtn) {
    flipBtn.addEventListener('click', () => {
      boardUI.flip();
    });
  }

  // Waiting banner invite bot button
  const inviteBotBtn = document.getElementById('btn-invite-bot');
  if (inviteBotBtn) {
    inviteBotBtn.addEventListener('click', () => {
      fetch('/api/room_add_bot', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ room_id: roomId })
      })
      .then(res => res.json())
      .then(data => {
        if (data.success) {
          hideWaitingBanner();
        }
      });
    });
  }

  // Copy room link button
  const copyLinkBtn = document.getElementById('btn-copy-room-link-arena');
  if (copyLinkBtn) {
    copyLinkBtn.addEventListener('click', () => {
      navigator.clipboard.writeText(window.location.href).then(() => {
        alert('Room link copied to clipboard!');
      });
    });
  }

  function hideWaitingBanner() {
    const banner = document.getElementById('waiting-opponent-banner');
    if (banner) banner.style.display = 'none';
  }

  function showWaitingBanner() {
    const banner = document.getElementById('waiting-opponent-banner');
    if (banner) banner.style.display = 'block';
  }

  // ----------------- SocketIO Handlers -----------------

  socket.on('connect', () => {
    socket.emit('join_game', {
      room_id: roomId,
      token: playerToken,
      username: window.CURRENT_USERNAME || ''
    });
  });

  socket.on('game_init', (data) => {
    playerRole = data.role;
    activeTurn = data.active_turn;
    clocks = data.clocks;
    
    // Set board perspective
    if (playerRole === 'black') {
      boardUI.setOrientation('black');
      boardUI.options.playerColor = 'black';
    } else if (playerRole === 'white') {
      boardUI.setOrientation('white');
      boardUI.options.playerColor = 'white';
    } else {
      boardUI.options.playerColor = 'none'; // spectator cannot drag
    }

    // Load FEN
    if (data.fen) {
      chess.load(data.fen);
      boardUI.setPosition(data.fen);
    }

    // Load previous moves
    const movesContainer = document.getElementById('moves-table-body');
    if (movesContainer) movesContainer.innerHTML = '';
    if (data.moves && data.moves.length > 0) {
      data.moves.forEach(m => appendMoveRow(m));
    }

    updatePlayerCards(data.white, data.black);
    updateClocksDisplay();

    if (data.status === 'playing') {
      hideWaitingBanner();
      startClockTimer();
    } else {
      showWaitingBanner();
    }
  });

  socket.on('player_update', (data) => {
    updatePlayerCards(data.white, data.black);
    if (data.clocks) clocks = data.clocks;
    if (data.active_turn) activeTurn = data.active_turn;

    if (data.status === 'playing') {
      hideWaitingBanner();
      window.chessAudio.playNotify();
      if (!timerRunning) {
        startClockTimer();
      }
    }
  });

  socket.on('move_made', (data) => {
    const moveInfo = data.move;
    activeTurn = data.active_turn;
    clocks = data.clocks;
    lastSyncTime = Date.now();
    warnedLowTime = false;

    // Push move into local chess.js
    const resultMove = chess.move({
      from: moveInfo.from,
      to: moveInfo.to,
      promotion: moveInfo.promotion || undefined
    });

    const isCheck = chess.in_check();
    let checkSquare = null;
    if (isCheck) {
      const turn = chess.turn();
      for (let r = 0; r < 8; r++) {
        for (let f = 0; f < 8; f++) {
          const sq = 'abcdefgh'[f] + (8 - r);
          const piece = chess.get(sq);
          if (piece && piece.type === 'k' && piece.color === turn) {
            checkSquare = sq;
            break;
          }
        }
      }
    }

    boardUI.setPosition(chess.fen(), { from: moveInfo.from, to: moveInfo.to }, checkSquare);
    appendMoveRow(moveInfo);

    // Audio effects
    if (resultMove && (resultMove.flags.includes('k') || resultMove.flags.includes('q'))) {
      window.chessAudio.playCastle();
    } else if (moveInfo.is_capture) {
      window.chessAudio.playCapture();
    } else if (moveInfo.is_check) {
      window.chessAudio.playCheck();
    } else {
      window.chessAudio.playMove();
    }

    if (!timerRunning && !data.is_game_over) {
      startClockTimer();
    }

    if (data.is_game_over) {
      stopClockTimer();
      const isWinner = (data.winner_color === playerRole);
      const isDraw = (data.result === '1/2-1/2');
      window.chessAudio.playGameOver(isWinner ? 'win' : (isDraw ? 'draw' : 'loss'));
      showGameOverModal(data.result, data.reason, data.winner, data.game_id);
    }
  });

  socket.on('invalid_move', () => {
    boardUI.setPosition(chess.fen());
  });

  socket.on('game_over', (data) => {
    stopClockTimer();
    const isWinner = (data.winner_color === playerRole);
    const isDraw = (data.result === '1/2-1/2');
    window.chessAudio.playGameOver(isWinner ? 'win' : (isDraw ? 'draw' : 'loss'));
    showGameOverModal(data.result, data.reason, data.winner, data.game_id);
  });

  // ----------------- Clock & Timers -----------------

  function startClockTimer() {
    timerRunning = true;
    lastSyncTime = Date.now();
    if (clockInterval) clearInterval(clockInterval);

    clockInterval = setInterval(() => {
      const now = Date.now();
      const deltaSec = (now - lastSyncTime) / 1000.0;
      lastSyncTime = now;

      if (clocks[activeTurn] > 0) {
        clocks[activeTurn] = Math.max(0, clocks[activeTurn] - deltaSec);
        
        // Low time sound alert when your turn is under 15 seconds
        if (activeTurn === playerRole && clocks[activeTurn] <= 15 && clocks[activeTurn] > 0) {
          if (Math.floor(clocks[activeTurn]) % 2 === 0 && !warnedLowTime) {
            window.chessAudio.playLowTime();
            warnedLowTime = true;
          } else if (Math.floor(clocks[activeTurn]) % 2 !== 0) {
            warnedLowTime = false;
          }
        }
      }
      updateClocksDisplay();
    }, 100);
  }

  function stopClockTimer() {
    timerRunning = false;
    if (clockInterval) clearInterval(clockInterval);
    updateClocksDisplay();
  }

  function formatTime(totalSeconds) {
    const s = Math.max(0, Math.floor(totalSeconds));
    const mins = Math.floor(s / 60);
    const secs = s % 60;
    const formattedSecs = secs < 10 ? '0' + secs : secs;
    if (mins >= 60) {
      const hrs = Math.floor(mins / 60);
      const remMins = mins % 60;
      return `${hrs}:${remMins < 10 ? '0' + remMins : remMins}:${formattedSecs}`;
    }
    return `${mins}:${formattedSecs}`;
  }

  function updateClocksDisplay() {
    const whiteClockEl = document.getElementById('white-clock');
    const blackClockEl = document.getElementById('black-clock');
    if (!whiteClockEl || !blackClockEl) return;

    whiteClockEl.textContent = formatTime(clocks.white);
    blackClockEl.textContent = formatTime(clocks.black);

    if (timerRunning) {
      whiteClockEl.classList.toggle('active', activeTurn === 'white');
      blackClockEl.classList.toggle('active', activeTurn === 'black');
    } else {
      whiteClockEl.classList.remove('active');
      blackClockEl.classList.remove('active');
    }

    whiteClockEl.classList.toggle('low-time', clocks.white < 20 && clocks.white > 0);
    blackClockEl.classList.toggle('low-time', clocks.black < 20 && clocks.black > 0);
  }

  function updatePlayerCards(white, black) {
    const whiteNameEl = document.getElementById('white-username');
    const whiteRatingEl = document.getElementById('white-rating');
    const blackNameEl = document.getElementById('black-username');
    const blackRatingEl = document.getElementById('black-rating');

    // Adapt top/bottom card based on player role orientation
    const topNameEl = (playerRole === 'black') ? whiteNameEl : blackNameEl;
    const topRatingEl = (playerRole === 'black') ? whiteRatingEl : blackRatingEl;
    const bottomNameEl = (playerRole === 'black') ? blackNameEl : whiteNameEl;
    const bottomRatingEl = (playerRole === 'black') ? blackRatingEl : whiteRatingEl;

    if (white && white.username) {
      if (whiteNameEl) whiteNameEl.textContent = white.username;
      if (whiteRatingEl) whiteRatingEl.textContent = `(${white.rating || 1200})`;
    }
    if (black && black.username) {
      if (blackNameEl) blackNameEl.textContent = black.username;
      if (blackRatingEl) blackRatingEl.textContent = `(${black.rating || 1200})`;
    } else if (!black || !black.username) {
      if (blackNameEl) blackNameEl.textContent = 'Waiting for opponent...';
      if (blackRatingEl) blackRatingEl.textContent = '';
    }
  }

  function appendMoveRow(move) {
    const tableBody = document.getElementById('moves-table-body');
    if (!tableBody) return;

    const moveNum = Math.ceil(move.move_number / 2);
    let row = document.getElementById(`move-row-${moveNum}`);

    if (move.move_number % 2 !== 0) {
      row = document.createElement('tr');
      row.id = `move-row-${moveNum}`;
      row.innerHTML = `
        <td class="move-num">${moveNum}.</td>
        <td class="move-w"><span class="move-san">${move.san}</span></td>
        <td class="move-b"></td>
      `;
      tableBody.appendChild(row);
    } else {
      if (row) {
        const bCell = row.querySelector('.move-b');
        if (bCell) bCell.innerHTML = `<span class="move-san">${move.san}</span>`;
      }
    }

    const container = document.getElementById('moves-container');
    if (container) container.scrollTop = container.scrollHeight;
  }

  // ----------------- Chat Handling -----------------

  const chatInput = document.getElementById('chat-input');
  const chatSendBtn = document.getElementById('chat-send-btn');
  const chatMessages = document.getElementById('chat-messages');

  function sendChatMessage() {
    if (!chatInput) return;
    const msg = chatInput.value.trim();
    if (!msg) return;
    socket.emit('send_chat', { room_id: roomId, message: msg, token: playerToken });
    chatInput.value = '';
  }

  if (chatSendBtn) chatSendBtn.addEventListener('click', sendChatMessage);
  if (chatInput) {
    chatInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') sendChatMessage();
    });
  }

  socket.on('chat_received', (data) => {
    if (!chatMessages) return;
    const bubble = document.createElement('div');
    const isMe = data.sender === (window.CURRENT_USERNAME || '') || (data.sender && data.sender.includes(playerRole));
    bubble.className = `chat-bubble ${isMe ? 'mine' : 'theirs'}`;
    bubble.innerHTML = `
      <div class="chat-sender">${data.sender}</div>
      <div>${data.message} <span class="chat-time">${data.time}</span></div>
    `;
    chatMessages.appendChild(bubble);
    chatMessages.scrollTop = chatMessages.scrollHeight;
  });

  // Tab switching
  const tabBtns = document.querySelectorAll('.tab-btn');
  tabBtns.forEach(btn => {
    btn.addEventListener('click', () => {
      tabBtns.forEach(b => b.classList.remove('active'));
      document.querySelectorAll('.tab-content').forEach(c => c.style.display = 'none');
      btn.classList.add('active');
      const targetId = btn.dataset.tab;
      const targetEl = document.getElementById(targetId);
      if (targetEl) targetEl.style.display = 'flex';
    });
  });

  // ----------------- Resign & Draw Actions -----------------

  const resignBtn = document.getElementById('btn-resign');
  if (resignBtn) {
    resignBtn.addEventListener('click', () => {
      if (confirm('Are you sure you want to resign this match?')) {
        socket.emit('resign', { room_id: roomId, token: playerToken });
      }
    });
  }

  const drawBtn = document.getElementById('btn-draw');
  if (drawBtn) {
    drawBtn.addEventListener('click', () => {
      if (confirm('Offer a draw to your opponent?')) {
        socket.emit('offer_draw', { room_id: roomId, token: playerToken });
        alert('Draw offer sent.');
      }
    });
  }

  socket.on('draw_offered', (data) => {
    if (data.by !== playerRole) {
      if (confirm(`${data.username} offers a draw. Do you accept?`)) {
        socket.emit('respond_draw', { room_id: roomId, accepted: true, token: playerToken });
      } else {
        socket.emit('respond_draw', { room_id: roomId, accepted: false, token: playerToken });
      }
    }
  });

  socket.on('draw_declined', () => {
    alert('Draw offer was declined.');
  });

  // ----------------- Game Over Modal -----------------

  function showGameOverModal(result, reason, winner, gameId) {
    const modal = document.getElementById('game-over-modal');
    if (!modal) return;

    const titleEl = document.getElementById('game-over-title');
    const descEl = document.getElementById('game-over-desc');
    const reviewBtn = document.getElementById('game-over-review-btn');

    let titleText = 'Game Over';
    let descText = '';

    if (result === '1/2-1/2') {
      titleText = 'Draw';
      descText = reason === 'stalemate' ? 'Draw by Stalemate!' : (reason === 'insufficient_material' ? 'Draw by Insufficient Material!' : 'Game drawn.');
    } else if (winner) {
      titleText = `${winner} Won!`;
      descText = reason === 'checkmate' ? 'Won by Checkmate!' : (reason === 'timeout' ? 'Won on Time!' : 'Won by Resignation!');
    }

    if (titleEl) titleEl.textContent = titleText;
    if (descEl) descEl.textContent = descText;
    if (reviewBtn && gameId) {
      reviewBtn.href = `/review/${gameId}`;
    }

    modal.classList.add('open');
  }
});
