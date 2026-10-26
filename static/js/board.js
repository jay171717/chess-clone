/**
 * Interactive Chessboard Component
 * Supports drag-and-drop, click-to-move, coordinates, highlights, board flipping,
 * legal move hints, pawn promotion, and captured pieces tally.
 */

class ChessBoardUI {
  constructor(containerId, options = {}) {
    this.container = document.getElementById(containerId);
    this.options = Object.assign({
      orientation: 'white', // 'white' or 'black'
      pieceTheme: '/static/pieces/{piece}.svg',
      draggable: true,
      onMove: null, // callback (from, to, promotion)
      getLegalMoves: null // function(square) -> list of valid target squares
    }, options);

    this.orientation = this.options.orientation;
    this.selectedSquare = null;
    this.legalDestinations = [];
    this.lastMove = null; // { from: 'e2', to: 'e4' }
    this.checkSquare = null; // e.g. 'e1'
    this.fen = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1';
    this.draggedPiece = null;
    this.dragGhost = null;

    this.files = ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h'];
    this.ranks = ['8', '7', '6', '5', '4', '3', '2', '1'];

    this.initBoard();
    this.setupGlobalEvents();
  }

  setOrientation(color) {
    this.orientation = color;
    this.render();
  }

  flip() {
    this.orientation = (this.orientation === 'white') ? 'black' : 'white';
    this.render();
  }

  initBoard() {
    this.container.innerHTML = '';
    this.container.classList.add('chess-board');
    this.render();
  }

  getPieceImg(code) {
    // code: 'wP', 'bK', etc.
    return this.options.pieceTheme.replace('{piece}', code);
  }

  parseFen(fen) {
    const boardState = {};
    const parts = (fen || '').split(' ');
    const rows = parts[0].split('/');

    for (let r = 0; r < 8; r++) {
      let f = 0;
      const rankName = (8 - r).toString();
      for (let c = 0; c < rows[r].length; c++) {
        const char = rows[r][c];
        if (!isNaN(char)) {
          f += parseInt(char, 10);
        } else {
          const fileName = this.files[f];
          const color = (char === char.toUpperCase()) ? 'w' : 'b';
          const type = char.toUpperCase();
          boardState[fileName + rankName] = color + type;
          f++;
        }
      }
    }
    return boardState;
  }

  setPosition(fen, lastMove = null, checkSquare = null) {
    this.fen = fen;
    if (lastMove !== undefined) this.lastMove = lastMove;
    if (checkSquare !== undefined) this.checkSquare = checkSquare;
    this.selectedSquare = null;
    this.legalDestinations = [];
    this.render();
    this.updateCapturedPieces();
  }

  render() {
    this.container.innerHTML = '';
    const boardState = this.parseFen(this.fen);

    const ranksOrder = (this.orientation === 'white') ? ['8', '7', '6', '5', '4', '3', '2', '1'] : ['1', '2', '3', '4', '5', '6', '7', '8'];
    const filesOrder = (this.orientation === 'white') ? ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h'] : ['h', 'g', 'f', 'e', 'd', 'c', 'b', 'a'];

    ranksOrder.forEach((rank, rIdx) => {
      filesOrder.forEach((file, fIdx) => {
        const sqName = file + rank;
        const squareEl = document.createElement('div');
        squareEl.className = 'square';
        squareEl.dataset.square = sqName;

        const isLight = (rIdx + fIdx) % 2 === 0;
        squareEl.classList.add(isLight ? 'light' : 'dark');

        // Coordinate labels
        if (fIdx === 0) {
          const rankLabel = document.createElement('span');
          rankLabel.className = 'coord-rank';
          rankLabel.textContent = rank;
          squareEl.appendChild(rankLabel);
        }
        if (rIdx === 7) {
          const fileLabel = document.createElement('span');
          fileLabel.className = 'coord-file';
          fileLabel.textContent = file;
          squareEl.appendChild(fileLabel);
        }

        // Highlights
        if (this.selectedSquare === sqName) {
          squareEl.classList.add('selected');
        }
        if (this.lastMove && (this.lastMove.from === sqName || this.lastMove.to === sqName)) {
          squareEl.classList.add('last-move');
        }
        if (this.checkSquare === sqName) {
          squareEl.classList.add('in-check');
        }

        // Piece
        const pieceCode = boardState[sqName];
        if (pieceCode) {
          const pieceEl = document.createElement('div');
          pieceEl.className = 'piece';
          pieceEl.dataset.piece = pieceCode;
          pieceEl.dataset.square = sqName;
          pieceEl.style.backgroundImage = `url("${this.getPieceImg(pieceCode)}")`;

          if (this.options.draggable) {
            this.attachPieceDrag(pieceEl, sqName);
          }
          squareEl.appendChild(pieceEl);
        }

        // Legal move destination hints
        if (this.legalDestinations.includes(sqName)) {
          const hint = document.createElement('div');
          hint.className = pieceCode ? 'legal-capture' : 'legal-dot';
          squareEl.appendChild(hint);
        }

        // Click handler
        squareEl.addEventListener('click', (e) => this.handleSquareClick(sqName, pieceCode));

        this.container.appendChild(squareEl);
      });
    });
  }

  handleSquareClick(sqName, pieceCode) {
    if (!this.options.draggable) return;

    // If a square was already selected and user clicked a valid destination
    if (this.selectedSquare && this.legalDestinations.includes(sqName)) {
      this.attemptMove(this.selectedSquare, sqName);
      return;
    }

    // If clicking on their own piece
    if (pieceCode) {
      const pieceColor = pieceCode[0] === 'w' ? 'white' : 'black';
      // In online mode or vs bot, only allow clicking player's pieces if configured
      if (this.options.playerColor && this.options.playerColor !== 'both' && pieceColor !== this.options.playerColor) {
        this.clearSelection();
        return;
      }

      this.selectedSquare = sqName;
      this.legalDestinations = this.options.getLegalMoves ? this.options.getLegalMoves(sqName) : [];
      this.render();
      return;
    }

    this.clearSelection();
  }

  clearSelection() {
    this.selectedSquare = null;
    this.legalDestinations = [];
    this.render();
  }

  attachPieceDrag(pieceEl, fromSquare) {
    pieceEl.addEventListener('mousedown', (e) => this.onDragStart(e, fromSquare, pieceEl));
    pieceEl.addEventListener('touchstart', (e) => this.onDragStart(e, fromSquare, pieceEl), { passive: false });
  }

  onDragStart(e, fromSquare, pieceEl) {
    if (e.type === 'mousedown' && e.button !== 0) return;
    if (e.cancelable && e.type.startsWith('touch')) e.preventDefault();

    const pieceCode = pieceEl.dataset.piece;
    const pieceColor = pieceCode[0] === 'w' ? 'white' : 'black';
    if (this.options.playerColor && this.options.playerColor !== 'both' && pieceColor !== this.options.playerColor) {
      return;
    }

    this.selectedSquare = fromSquare;
    this.legalDestinations = this.options.getLegalMoves ? this.options.getLegalMoves(fromSquare) : [];
    this.render();

    this.draggedPiece = {
      fromSquare,
      code: pieceCode
    };

    // Create ghost
    this.dragGhost = document.createElement('div');
    this.dragGhost.className = 'drag-ghost';
    this.dragGhost.style.backgroundImage = `url("${this.getPieceImg(pieceCode)}")`;
    document.body.appendChild(this.dragGhost);

    const clientX = e.clientX || (e.touches && e.touches[0].clientX);
    const clientY = e.clientY || (e.touches && e.touches[0].clientY);
    this.updateGhostPos(clientX, clientY);
  }

  updateGhostPos(x, y) {
    if (this.dragGhost) {
      this.dragGhost.style.left = `${x}px`;
      this.dragGhost.style.top = `${y}px`;
    }
  }

  setupGlobalEvents() {
    const onMove = (e) => {
      if (!this.dragGhost) return;
      if (e.cancelable && e.type.startsWith('touch')) e.preventDefault();
      const clientX = e.clientX || (e.touches && e.touches[0].clientX);
      const clientY = e.clientY || (e.touches && e.touches[0].clientY);
      this.updateGhostPos(clientX, clientY);
    };

    const onEnd = (e) => {
      if (!this.dragGhost) return;

      const clientX = e.clientX || (e.changedTouches && e.changedTouches[0].clientX);
      const clientY = e.clientY || (e.changedTouches && e.changedTouches[0].clientY);

      // Remove ghost
      if (this.dragGhost && this.dragGhost.parentNode) {
        this.dragGhost.parentNode.removeChild(this.dragGhost);
      }
      this.dragGhost = null;

      // Find drop square
      const dropEl = document.elementFromPoint(clientX, clientY);
      const squareEl = dropEl ? dropEl.closest('.square') : null;

      if (squareEl && squareEl.dataset.square && this.draggedPiece) {
        const toSquare = squareEl.dataset.square;
        const fromSquare = this.draggedPiece.fromSquare;
        if (fromSquare !== toSquare && this.legalDestinations.includes(toSquare)) {
          this.attemptMove(fromSquare, toSquare);
        }
      }

      this.draggedPiece = null;
    };

    window.addEventListener('mousemove', onMove);
    window.addEventListener('touchmove', onMove, { passive: false });
    window.addEventListener('mouseup', onEnd);
    window.addEventListener('touchend', onEnd);
  }

  attemptMove(fromSquare, toSquare) {
    // Check for pawn promotion (rank 7 to 8 for white, rank 2 to 1 for black)
    const boardState = this.parseFen(this.fen);
    const piece = boardState[fromSquare];
    const isPawn = piece && piece[1] === 'P';
    const isPromotion = isPawn && ((piece[0] === 'w' && toSquare[1] === '8') || (piece[0] === 'b' && toSquare[1] === '1'));

    if (isPromotion) {
      this.promptPromotion(piece[0], (chosenPiece) => {
        if (this.options.onMove) {
          this.options.onMove(fromSquare, toSquare, chosenPiece);
        }
      });
    } else {
      if (this.options.onMove) {
        this.options.onMove(fromSquare, toSquare, null);
      }
    }

    this.selectedSquare = null;
    this.legalDestinations = [];
  }

  promptPromotion(color, callback) {
    const modal = document.getElementById('promotion-modal');
    if (!modal) {
      callback('q');
      return;
    }

    const pieces = ['q', 'r', 'b', 'n'];
    const container = modal.querySelector('.promotion-pieces');
    container.innerHTML = '';

    pieces.forEach(p => {
      const btn = document.createElement('button');
      btn.className = 'promotion-piece-btn';
      const pieceCode = color + p.toUpperCase();
      btn.innerHTML = `<img src="${this.getPieceImg(pieceCode)}" alt="${p}" />`;
      btn.onclick = () => {
        modal.classList.remove('open');
        callback(p);
      };
      container.appendChild(btn);
    });

    modal.classList.add('open');
  }

  updateCapturedPieces() {
    const boardState = this.parseFen(this.fen);
    const initialCounts = {
      'P': 8, 'N': 2, 'B': 2, 'R': 2, 'Q': 1
    };
    const values = { 'P': 1, 'N': 3, 'B': 3, 'R': 5, 'Q': 9 };

    const currentW = { 'P': 0, 'N': 0, 'B': 0, 'R': 0, 'Q': 0 };
    const currentB = { 'P': 0, 'N': 0, 'B': 0, 'R': 0, 'Q': 0 };

    Object.values(boardState).forEach(p => {
      const color = p[0];
      const type = p[1];
      if (type === 'K') return;
      if (color === 'w') currentW[type] = (currentW[type] || 0) + 1;
      else currentB[type] = (currentB[type] || 0) + 1;
    });

    // Captured white pieces (held by Black) = initial - currentW
    // Captured black pieces (held by White) = initial - currentB
    let whiteScore = 0;
    let blackScore = 0;

    const capturedByWhite = []; // Black pieces taken
    const capturedByBlack = []; // White pieces taken

    ['P', 'N', 'B', 'R', 'Q'].forEach(type => {
      const whiteLosses = Math.max(0, initialCounts[type] - (currentW[type] || 0));
      const blackLosses = Math.max(0, initialCounts[type] - (currentB[type] || 0));

      for (let i = 0; i < blackLosses; i++) capturedByWhite.push('b' + type);
      for (let i = 0; i < whiteLosses; i++) capturedByBlack.push('w' + type);

      blackScore += whiteLosses * values[type];
      whiteScore += blackLosses * values[type];
    });

    const wDiff = whiteScore - blackScore;
    const bDiff = blackScore - whiteScore;

    this.renderCapturedRow('captured-by-white', capturedByWhite, wDiff > 0 ? `+${wDiff}` : '');
    this.renderCapturedRow('captured-by-black', capturedByBlack, bDiff > 0 ? `+${bDiff}` : '');
  }

  renderCapturedRow(elementId, pieceCodes, diffText) {
    const el = document.getElementById(elementId);
    if (!el) return;
    el.innerHTML = '';

    pieceCodes.forEach(code => {
      const img = document.createElement('img');
      img.src = this.getPieceImg(code);
      img.className = 'captured-img';
      el.appendChild(img);
    });

    if (diffText) {
      const span = document.createElement('span');
      span.className = 'material-diff';
      span.textContent = diffText;
      el.appendChild(span);
    }
  }
}

window.ChessBoardUI = ChessBoardUI;
