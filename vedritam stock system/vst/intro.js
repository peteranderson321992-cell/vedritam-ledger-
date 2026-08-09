/* =====================================================================
   Vedritam — Login intro clip controller
   Runs once per browser session, auto-dismisses after ~2.6s and can be
   skipped by click / tap / any key. Purely additive: it never touches
   the login form, its ids, handlers or any app logic.
   ===================================================================== */
(function () {
  // Play on every load of the login page. Add ?nointro=1 to the URL to skip.
  try {
    if (/[?&]nointro=1/.test(location.search)) return;
  } catch (e) {}

  var WORD = 'VEDRITAM';
  var HOLD = 2600;   // total on-screen time (ms)

  function build() {
    var wrap = document.createElement('div');
    wrap.id = 'vIntro';
    wrap.setAttribute('role', 'presentation');
    wrap.setAttribute('aria-hidden', 'true');

    var letters = '';
    for (var i = 0; i < WORD.length; i++) {
      letters += '<span style="animation-delay:' + (0.62 + i * 0.055).toFixed(3) + 's">' + WORD[i] + '</span>';
    }

    wrap.innerHTML =
      '<div class="v-glow"></div><div class="v-glow v-glow-2"></div>' +
      '<div class="v-grid"></div>' +
      '<div class="v-stage">' +
        '<div class="v-mark"><img src="logo.png" alt=""' +
          ' onerror="this.style.display=\'none\'"></div>' +
        '<svg class="v-ledger" viewBox="0 0 220 52" aria-hidden="true">' +
          '<line x1="10" y1="10" x2="150" y2="10"></line>' +
          '<line x1="10" y1="26" x2="196" y2="26"></line>' +
          '<line x1="10" y1="42" x2="120" y2="42"></line>' +
          '<polyline points="168,44 180,32 210,8"></polyline>' +
        '</svg>' +
        '<h1 class="v-word">' + letters + '</h1>' +
        '<p class="v-sub">School Stock Ledger</p>' +
        '<div class="v-bar"><i></i></div>' +
      '</div>' +
      '<div class="v-skip">Click anywhere to continue</div>';

    return wrap;
  }

  function start() {
    if (document.getElementById('vIntro')) return;
    var wrap = build();
    document.body.appendChild(wrap);
    document.body.classList.add('v-intro-lock');

    var done = false;
    function dismiss() {
      if (done) return;
      done = true;
      wrap.classList.add('v-out');
      window.removeEventListener('keydown', dismiss, true);
      wrap.removeEventListener('pointermove', parallax);
      setTimeout(function () {
        if (wrap.parentNode) wrap.parentNode.removeChild(wrap);
        document.body.classList.remove('v-intro-lock');
        var first = document.getElementById('username');
        if (first) { try { first.focus({ preventScroll: true }); } catch (e) { first.focus(); } }
      }, 560);
    }

    // interactive: pointer parallax on the stage
    var stage = wrap.querySelector('.v-stage');
    function parallax(e) {
      var w = window.innerWidth || 1, h = window.innerHeight || 1;
      var dx = ((e.clientX / w) - 0.5) * 22;
      var dy = ((e.clientY / h) - 0.5) * 16;
      stage.style.setProperty('--vx', dx.toFixed(2) + 'px');
      stage.style.setProperty('--vy', dy.toFixed(2) + 'px');
    }
    wrap.addEventListener('pointermove', parallax);

    // interactive: skip
    wrap.addEventListener('click', dismiss);
    window.addEventListener('keydown', dismiss, true);

    setTimeout(dismiss, HOLD);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();
  }
})();
