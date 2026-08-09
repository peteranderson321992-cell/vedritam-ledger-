/* sounds.js — the app's small sound engine.

   Everything is generated with the Web Audio API, so there are no audio files
   to ship and nothing to download at runtime.

     VSound.chime()        a soft two-note "ding" for a new chat message
     VSound.notify()       a single softer note for a general notification
     VSound.ringIn()       looping incoming-call ringtone (until stopRing())
     VSound.ringOut()      looping outgoing "ringing..." tone
     VSound.stopRing()     stops whichever ringtone is playing
     VSound.connected()    short rising pair when a call connects
     VSound.ended()        short falling pair when a call ends
     VSound.enabled()      true when the user has sounds switched on
     VSound.setEnabled(b)  persists the on/off choice (key: v_sound)

   Volume: the master gain is 0.34 — comfortably audible on a laptop speaker
   without being startling. Individual tones are shaped with a gentle attack
   and release so nothing clicks or pops.
*/
(function () {
  'use strict';

  var KEY = 'v_sound';
  var MASTER = 0.34;

  var ctx = null;
  var master = null;
  var ringTimer = null;
  var ringNodes = [];
  var unlocked = false;

  function enabled() {
    try { return localStorage.getItem(KEY) !== 'off'; } catch (e) { return true; }
  }
  function setEnabled(on) {
    try { localStorage.setItem(KEY, on ? 'on' : 'off'); } catch (e) {}
    if (!on) stopRing();
  }

  function audio() {
    if (ctx) return ctx;
    var AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return null;
    try {
      ctx = new AC();
      master = ctx.createGain();
      master.gain.value = MASTER;
      master.connect(ctx.destination);
    } catch (e) { ctx = null; }
    return ctx;
  }

  /* Browsers block audio until the person has interacted with the page. The
     first click/keypress resumes the context so later sounds are not lost. */
  function unlock() {
    if (unlocked) return;
    var c = audio();
    if (!c) return;
    unlocked = true;
    if (c.state === 'suspended') c.resume().catch(function () {});
  }
  ['pointerdown', 'keydown', 'touchstart'].forEach(function (ev) {
    document.addEventListener(ev, unlock, { once: false, passive: true });
  });

  /* One shaped sine tone. `when` is an offset in seconds from now. */
  function tone(freq, when, dur, peak, type) {
    var c = audio();
    if (!c) return null;
    if (c.state === 'suspended') c.resume().catch(function () {});
    var t0 = c.currentTime + Math.max(0, when || 0);
    var osc = c.createOscillator();
    var gain = c.createGain();
    osc.type = type || 'sine';
    osc.frequency.setValueAtTime(freq, t0);
    var top = peak == null ? 0.9 : peak;
    gain.gain.setValueAtTime(0.0001, t0);
    gain.gain.exponentialRampToValueAtTime(top, t0 + 0.012);
    gain.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
    osc.connect(gain);
    gain.connect(master);
    osc.start(t0);
    osc.stop(t0 + dur + 0.05);
    return osc;
  }

  function play(fn) {
    if (!enabled()) return;
    var c = audio();
    if (!c) return;
    try { fn(); } catch (e) {}
  }

  /* ---- one-shot cues ------------------------------------------------------ */

  // New chat message: a friendly major-third "ding-dong" (E6 -> C6).
  // Two parts of the app can spot the same new message (the chat thread and
  // the sidebar badge), so identical chimes inside one second collapse to one.
  var lastChime = 0;
  function chime() {
    var now = Date.now();
    if (now - lastChime < 900) return;
    lastChime = now;
    play(function () {
      tone(1318.51, 0, 0.20, 0.85);
      tone(1046.50, 0.13, 0.34, 0.75);
    });
  }

  // Generic notification: one softer note.
  function notify() {
    play(function () { tone(880, 0, 0.28, 0.6); });
  }

  function connected() {
    play(function () {
      tone(587.33, 0, 0.14, 0.7);
      tone(880, 0.12, 0.22, 0.7);
    });
  }

  function ended() {
    play(function () {
      tone(587.33, 0, 0.14, 0.6);
      tone(392, 0.12, 0.28, 0.6);
    });
  }

  /* ---- ringtones (looping until stopped) ---------------------------------- */

  function stopRing() {
    if (ringTimer) { clearInterval(ringTimer); ringTimer = null; }
    ringNodes.forEach(function (n) { try { n.stop(); } catch (e) {} });
    ringNodes = [];
  }

  function loop(everyMs, pattern) {
    stopRing();
    if (!enabled()) return;
    if (!audio()) return;
    var run = function () {
      ringNodes = ringNodes.filter(function (n) { return n.playbackState !== 'finished'; });
      pattern();
    };
    run();
    ringTimer = setInterval(run, everyMs);
  }

  // Incoming call: the classic two-burst "brrring ... brrring", repeated.
  function ringIn() {
    loop(3200, function () {
      [0, 0.42].forEach(function (off) {
        [0, 0.06, 0.12, 0.18, 0.24].forEach(function (step) {
          var n = tone(step % 0.12 === 0 ? 880 : 660, off + step, 0.07, 0.55, 'triangle');
          if (n) ringNodes.push(n);
        });
      });
    });
  }

  // Outgoing call: a calmer single low pulse while we wait for an answer.
  function ringOut() {
    loop(3400, function () {
      var a = tone(440, 0, 0.5, 0.32, 'sine');
      var b = tone(440, 1.1, 0.5, 0.32, 'sine');
      if (a) ringNodes.push(a);
      if (b) ringNodes.push(b);
    });
  }

  window.VSound = {
    chime: chime,
    notify: notify,
    ringIn: ringIn,
    ringOut: ringOut,
    stopRing: stopRing,
    connected: connected,
    ended: ended,
    enabled: enabled,
    setEnabled: setEnabled,
    unlock: unlock
  };
})();
