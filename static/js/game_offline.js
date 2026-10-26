/**
 * Offline Chess Controller
 * Handles Pass & Play (Local 2-player) and Play vs Computer (AI bot) modes.
 */

document.addEventListener('DOMContentLoaded', () => {
  const mode = window.OFFLINE_MODE || 'pass_and_play'; // 'pass_and_play' or 'vs_bot'
  const difficulty = window.BOT_DIFFICULTY || 'medium';
  const playerSide = window.USER_COLOR || 'white';
  const timeControlSetting = window.TIME_CONTROL || '10m';

  const chess = new Chess();
  let boardUI = null;
  let moveHistory = [];
  let gameOver = false;
  let activeTurn = 'white';

  // Time control mapping
  const timeSecMap = {
    '1m': 60,
    '3m': 180,
    '10m': 600,
    '30m': 1800,
    'unlimited': 0
  };
  const initialTime = timeSecMap[timeControlSetting] !== undefined ? timeSecMap[timeControlSetting] : 600;
  let clocks = { white: initialTime, black: initialTime };
  let clockInterval = null;
  let timerActive = false;

  const isVsBot = (mode === 'vs_bot');
  const botSide = (playerSide === 'white') ? 'black' : 'white';

  // Initialize board UI
  boardUI = new ChessBoardUI('chess-board', {
    orientation: playerSide === 'black' ? 'black' : 'white',
    playerColor: isVsBot ? playerSide : 'both',
    getLegalMoves: (square) => {
      if (gameOver) return [];
      if (isVsBot && chess.turn() !== (playerSide === 'white' ? 'w' : 'b')) return [];
      const moves = chess.moves({ square: square, verbose: true });
      return moves.map(m => m.to);
    },
    onMove: (from, to, promotion) => {
      handleMove(from, to, promotion);
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

  // Handle local player move
  function handleMove(from, to, promotion) {
    if (gameOver) return;

    const moveObj = chess.move({
      from: from,
      to: to,
      promotion: promotion || undefined
    });

    if (!moveObj) return;

    recordAndDisplayMove(moveObj, from, to, promotion);

    // If vs Bot and game not over, trigger bot move
    if (isVsBot && !gameOver && chess.turn() === (botSide === 'white' ? 'w' : 'b')) {
      triggerBotMove();
    }
  }

  function triggerBotMove() {
    // Slight delay so the bot move doesn't feel instantaneous/jarring
    setTimeout(() => {
      if (gameOver) return;
      fetch('/api/bot_move', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          fen: chess.fen(),
          difficulty: difficulty
        })
      })
      .then(res => res.json())
      .then(data => {
        if (data.success) {
          const moveObj = chess.move({
            from: data.from,
            to: data.to,
            promotion: data.promotion || undefined
          });
          if (moveObj) {
            recordAndDisplayMove(moveObj, data.from, data.to, data.promotion);
          }
        }
      })
      .catch(err => console.error('Bot move error:', err));
    }, 450);
  }

  function recordAndDisplayMove(moveObj, from, to, promotion) {
    const isCapture = moveObj.flags.includes('c') || moveObj.flags.includes('e');
    const isCheck = chess.in_check();
    
    // Check square for King
    let checkSquare = null;
    if (isCheck) {
      const turn = chess.turn();
      for (let r = 0; r < 8; r++) {
        for (let f = 0; f < 8; f++) {
          const sq = 'abcdefgh'[f] + (8 - r);
          const p = chess.get(sq);
          if (p && p.type === 'k' && p.color === turn) {
            checkSquare = sq;
            break;
          }
        }
      }
    }

    boardUI.setPosition(chess.fen(), { from, to }, checkSquare);

    // Audio
    if (moveObj.flags.includes('k') || moveObj.flags.includes('q')) {
      window.chessAudio.playCastle();
    } else if (isCapture) {
      window.chessAudio.playCapture();
    } else if (isCheck) {
      window.chessAudio.playCheck();
    } else {
      window.chessAudio.playMove();
    }

    // Record move
    const moveInfo = {
      from: from,
      to: to,
      promotion: promotion,
      san: moveObj.san,
      move_number: moveHistory.length + 1,
      fen: chess.fen()
    };
    moveHistory.push(moveInfo);
    appendMoveRow(moveInfo);

    // Start timer on first move
    if (initialTime > 0 && !timerActive) {
      startClockTimer();
    }

    // Switch active turn
    activeTurn = (chess.turn() === 'w') ? 'white' : 'black';

    // Game Over check
    if (chess.game_over()) {
      handleGameOver();
    }
  }

  function handleGameOver() {
    gameOver = true;
    stopClockTimer();
    window.chessAudio.playGameOver();

    let result = '1/2-1/2';
    let reason = 'draw';
    let winner = null;

    if (chess.in_checkmate()) {
      winner = (chess.turn() === 'w') ? 'black' : 'white';
      result = (winner === 'white') ? '1-0' : '0-1';
      reason = 'checkmate';
    } else if (chess.in_stalemate()) {
      reason = 'stalemate';
    } else if (chess.in_threefold_repetition()) {
      reason = 'threefold_repetition';
    } else if (chess.insufficient_material()) {
      reason = 'insufficient_material';
    } else if (chess.in_draw()) {
      reason = '50_move_rule';
    }

    showGameOverModal(result, reason, winner);
  }

  // ----------------- Clock Timers -----------------

  function startClockTimer() {
    timerActive = true;
    let lastTime = Date.now();
    if (clockInterval) clearInterval(clockInterval);

    clockInterval = setInterval(() => {
      const now = Date.now();
      const delta = (now - lastTime) / 1000.0;
      lastTime = now;

      if (clocks[activeTurn] > 0) {
        clocks[activeTurn] = Math.max(0, clocks[activeTurn] - delta);
      } else {
        // Flag fall!
        gameOver = true;
        stopClockTimer();
        const winner = (activeTurn === 'white') ? 'black' : 'white';
        const result = (winner === 'white') ? '1-0' : '0-1';
        showGameOverModal(result, 'timeout', winner);
      }
      updateClocksDisplay();
    }, 100);
  }

  function stopClockTimer() {
    timerActive = false;
    if (clockInterval) clearInterval(clockInterval);
    updateClocksDisplay();
  }

  function formatTime(s) {
    if (initialTime === 0) return '∞';
    const total = Math.max(0, Math.floor(s));
    const mins = Math.floor(total / 60);
    const secs = total % 60;
    return `${mins}:${secs < 10 ? '0' + secs : secs}`;
  }

  function updateClocksDisplay() {
    const whiteClockEl = document.getElementById('white-clock');
    const blackClockEl = document.getElementById('black-clock');
    if (!whiteClockEl || !blackClockEl) return;

    whiteClockEl.textContent = formatTime(clocks.white);
    blackClockEl.textContent = formatTime(clocks.black);

    if (timerActive) {
      whiteClockEl.classList.toggle('active', activeTurn === 'white');
      blackClockEl.classList.toggle('active', activeTurn === 'black');
    }
    whiteClockEl.classList.toggle('low-time', clocks.white < 20 && clocks.white > 0);
    blackClockEl.classList.toggle('low-time', clocks.black < 20 && clocks.black > 0);
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

  // ----------------- Save Offline Game -----------------

  function saveGameRecord(result, reason, callback) {
    let whiteName = isVsBot ? (playerSide === 'white' ? (window.CURRENT_USERNAME || 'Player') : `Bot (${difficulty})`) : 'Player 1';
    let blackName = isVsBot ? (playerSide === 'black' ? (window.CURRENT_USERNAME || 'Player') : `Bot (${difficulty})`) : 'Player 2';

    fetch('/api/save_offline_game', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        moves: moveHistory,
        result: result,
        reason: reason,
        white_username: whiteName,
        black_username: blackName,
        time_control: isVsBot ? `vs Bot (${difficulty})` : 'Pass & Play'
      })
    })
    .then(res => res.json())
    .then(data => {
      if (data.success && callback) {
        callback(data.game_id);
      }
    })
    .catch(err => console.error('Save offline game error:', err));
  }

  function showGameOverModal(result, reason, winner) {
    const modal = document.getElementById('game-over-modal');
    if (!modal) return;

    const titleEl = document.getElementById('game-over-title');
    const descEl = document.getElementById('game-over-desc');
    const reviewBtn = document.getElementById('game-over-review-btn');

    let titleText = 'Game Over';
    let descText = '';

    if (result === '1/2-1/2') {
      titleText = 'Draw';
      descText = reason === 'stalemate' ? 'Draw by Stalemate!' : 'Draw by rule.';
    } else if (winner) {
      const winnerName = (winner === 'white') 
        ? (isVsBot && playerSide === 'black' ? 'Bot' : 'White') 
        : (isVsBot && playerSide === 'white' ? 'Bot' : 'Black');
      titleText = `${winnerName} Won!`;
      descText = reason === 'checkmate' ? 'Won by Checkmate!' : (reason === 'timeout' ? 'Won on Time!' : 'Victory!');
    }

    if (titleEl) titleEl.textContent = titleText;
    if (descEl) descEl.textContent = descText;

    // Automatically save game record so it can be reviewed!
    saveGameRecord(result, reason, (gameId) => {
      if (reviewBtn) {
        reviewBtn.href = `/review/${gameId}`;
      }
    });

    modal.classList.add('open');
  }

  // Resign button
  const resignBtn = document.getElementById('btn-resign');
  if (resignBtn) {
    resignBtn.addEventListener('click', () => {
      if (confirm('Resign game?')) {
        gameOver = true;
        stopClockTimer();
        const winner = (activeTurn === 'white') ? 'black' : 'white';
        const result = (winner === 'white') ? '1-0' : '0-1';
        showGameOverModal(result, 'resignation', winner);
      }
    });
  }

  // Initial clocks display
  updateClocksDisplay();
});
