/* =====================================================================
   Vedritam — Post-login welcome clip controller
   Shows "Welcome <name>" for ~1.4s right after a successful sign-in.
   Fires only once per login (flag set by app.js), never on refresh.
   ===================================================================== */
(function () {
  var HOLD = 1100;   // on-screen time before it starts leaving (ms)
  var OUT  = 320;    // exit animation length (ms)

  var flag;
  try { flag = localStorage.getItem('v_welcome'); } catch (e) { return; }
  if (!flag) return;
  try { localStorage.removeItem('v_welcome'); } catch (e) {}

  // The clip greets the signed-in account by its username.
  function displayName() {
    var n = '';
    try { n = localStorage.getItem('v_username') || localStorage.getItem('v_fullname') || ''; } catch (e) {}
    n = String(n).trim();
    return n || 'back';
  }

  function esc(s) {
    return String(s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }

  function start() {
    if (document.getElementById('vWelcome')) return;

    var wrap = document.createElement('div');
    wrap.id = 'vWelcome';
    wrap.setAttribute('role', 'presentation');
    wrap.setAttribute('aria-hidden', 'true');
    wrap.innerHTML =
      '<div class="v-glow"></div><div class="v-glow v-glow-2"></div>' +
      '<div class="v-grid"></div>' +
      '<div class="v-stage">' +
        '<p class="v-hi">Welcome</p>' +
        '<h1 class="v-name">' + esc(displayName()) + '</h1>' +
        '<div class="v-rule"></div>' +
      '</div>';

    document.body.appendChild(wrap);
    document.body.classList.add('v-welcome-lock');

    var done = false;
    function dismiss() {
      if (done) return;
      done = true;
      wrap.classList.add('v-out');
      wrap.removeEventListener('click', dismiss);
      window.removeEventListener('keydown', dismiss, true);
      setTimeout(function () {
        if (wrap.parentNode) wrap.parentNode.removeChild(wrap);
        document.body.classList.remove('v-welcome-lock');
      }, OUT + 20);
    }

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
