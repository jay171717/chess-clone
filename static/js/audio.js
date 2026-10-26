/**
 * High-Quality Chess Sound Effects Engine
 * Uses official open-source chess audio assets with instant low-latency playback.
 */

class ChessAudio {
  constructor() {
    this.muted = false;
    this.sounds = {};
    this.initAudioFiles();
  }

  initAudioFiles() {
    const soundList = {
      move: '/static/sounds/move.mp3',
      capture: '/static/sounds/capture.mp3',
      check: '/static/sounds/check.mp3',
      notify: '/static/sounds/notify.mp3',
      lowtime: '/static/sounds/lowtime.mp3',
      victory: '/static/sounds/victory.mp3',
      defeat: '/static/sounds/defeat.mp3',
      draw: '/static/sounds/draw.mp3'
    };

    for (const [key, path] of Object.entries(soundList)) {
      const audio = new Audio(path);
      audio.preload = 'auto';
      this.sounds[key] = audio;
    }
  }

  play(key) {
    if (this.muted) return;
    try {
      const original = this.sounds[key];
      if (original) {
        // Clone node or reset time for instant response without cutoffs
        const clone = original.cloneNode();
        clone.volume = 0.85;
        const playPromise = clone.play();
        if (playPromise !== undefined) {
          playPromise.catch(() => {
            // Browser autoplay policy might require first user gesture
          });
        }
      }
    } catch (e) {
      console.warn('Audio play error:', e);
    }
  }

  playMove() {
    this.play('move');
  }

  playCapture() {
    this.play('capture');
  }

  playCheck() {
    this.play('check');
  }

  playCastle() {
    this.play('move');
    setTimeout(() => this.play('move'), 120);
  }

  playNotify() {
    this.play('notify');
  }

  playLowTime() {
    this.play('lowtime');
  }

  playGameOver(result = 'win') {
    if (result === 'win') {
      this.play('victory');
    } else if (result === 'loss') {
      this.play('defeat');
    } else {
      this.play('draw');
    }
  }
}

window.chessAudio = new ChessAudio();
