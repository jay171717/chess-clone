/**
 * Game Review & Stockfish Analysis Controller
 * Features move navigation, interactive notation table, and live Stockfish evaluation bar.
 */

document.addEventListener('DOMContentLoaded', () => {
  const gameData = window.GAME_DATA;
  if (!gameData) return;

  const moves = gameData.moves || [];
  let currentMoveIndex = -1; // -1 = start position
  const evalCache = {}; // fen -> eval object
  let boardUI = null;

  // Build list of FENs for each position: 0 is starting position, 1 is after move 1, etc.
  const positions = [
    {
      fen: 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1',
      lastMove: null,
      moveIndex: -1,
      san: 'Start'
    }
  ];

  moves.forEach((m, idx) => {
    positions.push({
      fen: m.fen,
      lastMove: { from: m.from, to: m.to },
      moveIndex: idx,
      san: m.san
    });
  });

  // Initialize board UI
  boardUI = new ChessBoardUI('chess-board', {
    orientation: 'white',
    draggable: false // Review mode is click-to-navigate
  });

  // Populate moves table
  const tableBody = document.getElementById('review-moves-body');
  if (tableBody) {
    tableBody.innerHTML = '';
    for (let i = 0; i < moves.length; i += 2) {
      const moveNum = Math.floor(i / 2) + 1;
      const wMove = moves[i];
      const bMove = moves[i + 1];

      const row = document.createElement('tr');
      row.innerHTML = `
        <td class="move-num">${moveNum}.</td>
        <td class="move-w" data-index="${i}">
          <span class="move-san" id="move-san-${i}">${wMove.san}</span>
        </td>
        <td class="move-b" data-index="${i + 1}">
          ${bMove ? `<span class="move-san" id="move-san-${i + 1}">${bMove.san}</span>` : ''}
        </td>
      `;

      // Click move to jump
      row.querySelector('.move-w').addEventListener('click', () => jumpToMove(i));
      if (bMove) {
        row.querySelector('.move-b').addEventListener('click', () => jumpToMove(i + 1));
      }

      tableBody.appendChild(row);
    }
  }

  // Navigation functions
  function jumpToMove(index) {
    if (index < -1) index = -1;
    if (index >= moves.length) index = moves.length - 1;
    currentMoveIndex = index;

    const pos = positions[index + 1];
    boardUI.setPosition(pos.fen, pos.lastMove);
    highlightActiveMove(index);
    updateStockfishEvaluation(pos.fen);
  }

  function highlightActiveMove(index) {
    document.querySelectorAll('.move-san').forEach(el => el.classList.remove('active-move'));
    if (index >= 0) {
      const activeEl = document.getElementById(`move-san-${index}`);
      if (activeEl) {
        activeEl.classList.add('active-move');
        activeEl.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
      }
    }
  }

  // Navigation Buttons
  const btnFirst = document.getElementById('btn-nav-first');
  const btnPrev = document.getElementById('btn-nav-prev');
  const btnNext = document.getElementById('btn-nav-next');
  const btnLast = document.getElementById('btn-nav-last');
  const btnFlip = document.getElementById('btn-flip-board');

  if (btnFirst) btnFirst.addEventListener('click', () => jumpToMove(-1));
  if (btnPrev) btnPrev.addEventListener('click', () => jumpToMove(currentMoveIndex - 1));
  if (btnNext) btnNext.addEventListener('click', () => jumpToMove(currentMoveIndex + 1));
  if (btnLast) btnLast.addEventListener('click', () => jumpToMove(moves.length - 1));
  if (btnFlip) btnFlip.addEventListener('click', () => boardUI.flip());

  // Keyboard navigation
  window.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowLeft') {
      jumpToMove(currentMoveIndex - 1);
    } else if (e.key === 'ArrowRight') {
      jumpToMove(currentMoveIndex + 1);
    } else if (e.key === 'Home') {
      jumpToMove(-1);
    } else if (e.key === 'End') {
      jumpToMove(moves.length - 1);
    }
  });

  // ----------------- Stockfish Evaluation Bar -----------------

  const evalWhiteBar = document.getElementById('eval-bar-white');
  const evalBlackBar = document.getElementById('eval-bar-black');
  const evalPill = document.getElementById('eval-score-pill');
  const evalAdvantageText = document.getElementById('eval-advantage-text');
  const evalBestMoveText = document.getElementById('eval-best-move-text');

  function updateStockfishEvaluation(fen) {
    if (evalCache[fen]) {
      renderEvaluation(evalCache[fen]);
      return;
    }

    if (evalPill) evalPill.textContent = '...';

    fetch('/api/eval', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ fen: fen })
    })
    .then(res => res.json())
    .then(data => {
      evalCache[fen] = data;
      renderEvaluation(data);
    })
    .catch(err => {
      console.error('Eval error:', err);
      if (evalPill) evalPill.textContent = '0.0';
    });
  }

  function renderEvaluation(data) {
    let winChance = data.win_chance !== undefined ? data.win_chance : 50.0;
    // Cap visual limits so the bar doesn't completely disappear
    winChance = Math.max(5.0, Math.min(95.0, winChance));

    if (evalWhiteBar) evalWhiteBar.style.height = `${winChance}%`;
    if (evalBlackBar) evalBlackBar.style.height = `${100 - winChance}%`;

    let scoreLabel = '0.0';
    if (data.mate !== null && data.mate !== undefined) {
      scoreLabel = data.mate > 0 ? `+M${data.mate}` : `-M${Math.abs(data.mate)}`;
    } else if (data.evaluation !== null && data.evaluation !== undefined) {
      scoreLabel = data.evaluation > 0 ? `+${data.evaluation.toFixed(1)}` : `${data.evaluation.toFixed(1)}`;
    }

    if (evalPill) {
      evalPill.textContent = scoreLabel;
      // Position score label along the dividing line
      evalPill.style.bottom = `calc(${winChance}% - 10px)`;
    }

    // Advantage description text
    if (evalAdvantageText) {
      if (data.mate !== null) {
        evalAdvantageText.textContent = data.mate > 0 ? `White has mate in ${data.mate}!` : `Black has mate in ${Math.abs(data.mate)}!`;
      } else if (data.evaluation > 1.5) {
        evalAdvantageText.textContent = `White is winning (${scoreLabel})`;
      } else if (data.evaluation > 0.5) {
        evalAdvantageText.textContent = `White has a slight advantage (${scoreLabel})`;
      } else if (data.evaluation < -1.5) {
        evalAdvantageText.textContent = `Black is winning (${scoreLabel})`;
      } else if (data.evaluation < -0.5) {
        evalAdvantageText.textContent = `Black has a slight advantage (${scoreLabel})`;
      } else {
        evalAdvantageText.textContent = `Equal position (${scoreLabel})`;
      }
    }

    if (evalBestMoveText) {
      evalBestMoveText.textContent = data.bestmove ? `Engine Best Move: ${data.bestmove}` : '';
    }
  }

  // Copy PGN button
  const copyPgnBtn = document.getElementById('btn-copy-pgn');
  if (copyPgnBtn) {
    copyPgnBtn.addEventListener('click', () => {
      const pgn = gameData.pgn || '';
      navigator.clipboard.writeText(pgn).then(() => {
        alert('PGN copied to clipboard!');
      });
    });
  }

  // Start at the end of the game or beginning
  jumpToMove(moves.length - 1);
});
