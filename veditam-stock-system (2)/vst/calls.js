/* calls.js — voice calls, video calls, screen sharing and group voice chat.

   The audio and video travel directly between the browsers (WebRTC); the
   server only relays the small setup messages. Every participant connects to
   every other participant, which is fine for the small groups this system
   uses.

     VCalls.start(conversationId, members, mode)   mode: 'audio' | 'video'
     VCalls.hangup()
     VCalls.handleSignal(signal)                   fed by the messages page
     VCalls.active()                               current conversation id
*/
(function () {
  var ICE = { iceServers: [
    { urls: 'stun:stun.l.google.com:19302' },
    { urls: 'stun:stun1.l.google.com:19302' }
  ] };

  var me = '';
  try { me = localStorage.getItem('v_username') || ''; } catch (e) {}

  var cid = null, mode = 'audio';
  var local = null, screen = null, peers = {}, tiles = {}, beat = null, timer = null, started = 0;
  var pending = {};      // peer -> queued ICE candidates (before the answer/offer lands)
  var polite = {};       // peer -> are we the polite side of a glare collision
  var connectedOnce = false;
  var ringing = {};      // conversation id -> incoming-ring element
  var muted = false, camOff = false, sharing = false;

  function api(p, m, b) { return window.App.apiCall(p, m || 'GET', b || null); }
  function toast(m, t) { if (window.App && App.ui) App.ui.showToast(m, t || 'success'); }
  function snd(name) { if (window.VSound && VSound[name]) { try { VSound[name](); } catch (e) {} } }
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function styles() {
    if (document.getElementById('vCallsCss')) return;
    var css = document.createElement('style');
    css.id = 'vCallsCss';
    css.textContent =
      '.vc-stage{position:fixed;inset:0;background:#0b141a;z-index:1500;display:flex;flex-direction:column;color:#e9edef}' +
      '.vc-head{padding:12px 18px;display:flex;align-items:center;gap:10px;font-size:14px;font-weight:650}' +
      '.vc-grid{flex:1;display:grid;gap:10px;padding:0 14px 10px;overflow:auto;' +
      'grid-template-columns:repeat(auto-fit,minmax(220px,1fr));align-content:center}' +
      '.vc-tile{position:relative;background:#12242c;border-radius:14px;overflow:hidden;min-height:170px;' +
      'display:flex;align-items:center;justify-content:center}' +
      '.vc-tile video{width:100%;height:100%;object-fit:cover;background:#000}' +
      '.vc-tile .vc-face{width:84px;height:84px;border-radius:50%;object-fit:cover;background:#25D366;color:#04310f;' +
      'display:flex;align-items:center;justify-content:center;font-size:28px;font-weight:700;overflow:hidden}' +
      '.vc-tile .vc-name{position:absolute;left:10px;bottom:8px;font-size:12px;font-weight:650;background:rgba(0,0,0,.45);' +
      'color:#e9edef;padding:3px 9px;border-radius:999px}' +
      '.vc-bar{display:flex;gap:10px;justify-content:center;padding:14px;flex-wrap:wrap;background:rgba(0,0,0,.25)}' +
      '.vc-btn{width:52px;height:52px;border-radius:50%;border:0;background:#243b45;color:#e9edef;font-size:20px;cursor:pointer}' +
      '.vc-btn.on{background:#25D366;color:#04310f}.vc-btn.end{background:#ef4444;color:#fff}' +
      '.vc-ring{position:fixed;right:18px;bottom:18px;z-index:1450;background:var(--v-surface,#fff);' +
      'color:var(--v-text,#0f172a);border:1px solid var(--v-border,#e2e8f0);border-radius:14px;padding:14px 16px;' +
      'box-shadow:0 18px 44px rgba(0,0,0,.28);width:270px}' +
      '.vc-ring .vc-ring-actions{display:flex;gap:8px;margin-top:12px}' +
      /* Consent sheet shown before the camera or the screen is switched on. */
      '.vc-ask{position:fixed;inset:0;z-index:1600;background:rgba(15,23,42,.62);display:flex;' +
      'align-items:center;justify-content:center;padding:16px}' +
      '.vc-ask-box{background:var(--v-surface,#fff);color:var(--v-text,#0f172a);border:1px solid var(--v-border,#e2e8f0);' +
      'border-radius:16px;width:min(430px,100%);padding:20px 22px;box-shadow:0 22px 60px rgba(0,0,0,.34)}' +
      '.vc-ask-box h3{margin:0 0 6px;font-size:16.5px;font-weight:700;display:flex;align-items:center;gap:8px}' +
      '.vc-ask-box p{margin:0 0 6px;font-size:13px;line-height:1.55;color:var(--v-text-muted,#64748b)}' +
      '.vc-ask-box ul{margin:8px 0 0;padding-left:18px;font-size:12.5px;line-height:1.6;color:var(--v-text-muted,#64748b)}' +
      '.vc-ask-acts{display:flex;justify-content:flex-end;gap:8px;margin-top:18px;flex-wrap:wrap}' +
      '.vc-ask-acts button{width:auto;padding:8px 16px;border-radius:10px;border:1px solid var(--v-border,#e2e8f0);' +
      'background:transparent;color:inherit;font:inherit;font-size:13px;font-weight:650;cursor:pointer}' +
      '.vc-ask-acts button.go{background:var(--v-primary,#1d4ed8);border-color:var(--v-primary,#1d4ed8);color:#fff}';
    document.head.appendChild(css);
  }

  /* ---- consent / warning sheet -------------------------------------------
     Shown *before* the browser's own permission prompt so nobody's camera or
     screen is ever switched on without a clear, explicit "yes" first. */
  function ask(opts) {
    styles();
    return new Promise(function (resolve) {
      var wrap = document.createElement('div');
      wrap.className = 'vc-ask';
      wrap.innerHTML =
        '<div class="vc-ask-box" role="dialog" aria-modal="true">' +
          '<h3>' + opts.icon + ' ' + esc(opts.title) + '</h3>' +
          '<p>' + esc(opts.body) + '</p>' +
          '<ul>' + opts.points.map(function (p) { return '<li>' + esc(p) + '</li>'; }).join('') + '</ul>' +
          '<div class="vc-ask-acts">' +
            '<button type="button" data-no="1">Cancel</button>' +
            '<button type="button" class="go" data-yes="1">' + esc(opts.confirm) + '</button>' +
          '</div>' +
        '</div>';
      document.body.appendChild(wrap);
      var done = function (ok) {
        document.removeEventListener('keydown', onKey);
        wrap.remove();
        resolve(ok);
      };
      var onKey = function (e) { if (e.key === 'Escape') done(false); };
      document.addEventListener('keydown', onKey);
      wrap.addEventListener('click', function (e) {
        if (e.target === wrap || e.target.closest('[data-no]')) return done(false);
        if (e.target.closest('[data-yes]')) return done(true);
      });
      var go = wrap.querySelector('[data-yes]');
      if (go) go.focus();
    });
  }

  function askCamera() {
    return ask({
      icon: '&#127909;',
      title: 'Turn on your camera?',
      body: 'This will switch on your camera and microphone and send the live picture to everyone in this call.',
      points: [
        'Your browser will ask for camera and microphone permission next.',
        'Everyone in the conversation can see and hear you until you leave.',
        'You can switch the camera off again at any time with the camera button.'
      ],
      confirm: 'Turn on camera'
    });
  }

  function askMic() {
    return ask({
      icon: '&#127908;',
      title: 'Turn on your microphone?',
      body: 'This will switch on your microphone so the other people in this conversation can hear you.',
      points: [
        'Your browser will ask for microphone permission next.',
        'You can mute yourself at any time during the call.'
      ],
      confirm: 'Start call'
    });
  }

  function askScreen() {
    return ask({
      icon: '&#128421;',
      title: 'Share your screen?',
      body: 'Everyone in this call will see whatever you pick to share, live.',
      points: [
        'Only share a window you are happy for others to see.',
        'Passwords, private messages, e-mail and open documents on that screen will be visible.',
        'Prefer sharing a single window instead of your whole screen.',
        'Stop at any time with the share button or your browser\u2019s "Stop sharing" bar.'
      ],
      confirm: 'Choose what to share'
    });
  }

  /* ---- UI ---------------------------------------------------------------- */
  function stage() {
    styles();
    var s = document.createElement('div');
    s.className = 'vc-stage';
    s.id = 'vcStage';
    s.innerHTML =
      '<div class="vc-head"><span id="vcTitle">Connecting...</span>' +
      '<span id="vcTimer" style="margin-left:auto;opacity:.75;font-weight:600"></span></div>' +
      '<div class="vc-grid" id="vcGrid"></div>' +
      '<div class="vc-bar">' +
        '<button class="vc-btn" id="vcMute" title="Mute">&#127908;</button>' +
        '<button class="vc-btn" id="vcCam" title="Camera">&#127909;</button>' +
        '<button class="vc-btn" id="vcShare" title="Share screen">&#128421;</button>' +
        '<button class="vc-btn end" id="vcEnd" title="Leave call">&#128222;</button>' +
      '</div>';
    document.body.appendChild(s);
    s.querySelector('#vcMute').addEventListener('click', toggleMute);
    s.querySelector('#vcCam').addEventListener('click', toggleCam);
    s.querySelector('#vcShare').addEventListener('click', toggleShare);
    s.querySelector('#vcEnd').addEventListener('click', function () { hangup(true); });
    if (mode !== 'video') s.querySelector('#vcCam').style.display = 'none';
    return s;
  }

  function face(name) {
    var photo = window.VAvatars ? VAvatars.get(name) : '';
    return photo
      ? '<span class="vc-face"><img src="' + esc(photo) + '" alt="" style="width:100%;height:100%;object-fit:cover"></span>'
      : '<span class="vc-face">' + esc(window.VAvatars ? VAvatars.initials(name) : String(name || '?').slice(0, 2).toUpperCase()) + '</span>';
  }

  function tile(name, stream, isMe) {
    var grid = document.getElementById('vcGrid');
    if (!grid) return;
    var box = tiles[name];
    if (!box) {
      box = document.createElement('div');
      box.className = 'vc-tile';
      box.innerHTML = face(name) + '<span class="vc-name">' + esc(isMe ? name + ' (you)' : name) + '</span>';
      grid.appendChild(box);
      tiles[name] = box;
    }
    var hasVideo = stream && stream.getVideoTracks().length;
    var video = box.querySelector('video');
    var faceEl = box.querySelector('.vc-face');
    if (hasVideo) {
      if (!video) {
        video = document.createElement('video');
        video.autoplay = true; video.playsInline = true;
        box.insertBefore(video, box.firstChild);
      }
      if (video.srcObject !== stream) video.srcObject = stream;
      video.muted = !!isMe;              // never echo your own microphone
      video.play().catch(function () {});
      if (faceEl) faceEl.style.display = 'none';
    } else {
      // Voice only: drop any stale video element and show the avatar again.
      if (video) { video.srcObject = null; video.remove(); }
      if (faceEl) faceEl.style.display = '';
      if (stream && !isMe) {
        var audioEl = box.querySelector('audio');
        if (!audioEl) { audioEl = document.createElement('audio'); audioEl.autoplay = true; box.appendChild(audioEl); }
        if (audioEl.srcObject !== stream) audioEl.srcObject = stream;
        audioEl.play().catch(function () {});
      }
    }
  }

  function dropTile(name) {
    if (tiles[name]) { tiles[name].remove(); delete tiles[name]; }
  }

  function tick() {
    var t = document.getElementById('vcTimer');
    if (!t) return;
    var s = Math.floor((Date.now() - started) / 1000);
    var m = Math.floor(s / 60);
    var sec = s % 60;
    t.textContent = (m < 10 ? '0' + m : m) + ':' + (sec < 10 ? '0' : '') + sec;
  }

  function headline(extra) {
    var t = document.getElementById('vcTitle');
    if (t) t.textContent = (mode === 'video' ? 'Video call' : 'Voice call') + (extra ? ' \u00b7 ' + extra : '');
  }

  /* ---- peers ------------------------------------------------------------- */
  function signal(to, kind, payload) {
    if (!cid) return Promise.resolve();
    return api('/messaging/conversations/' + cid + '/call/signal', 'POST',
      { to: to, kind: kind, payload: payload }).catch(function () {});
  }

  function peer(name) {
    if (peers[name]) return peers[name];
    var pc = new RTCPeerConnection(ICE);
    peers[name] = pc;
    pending[name] = pending[name] || [];
    if (local) local.getTracks().forEach(function (t) { pc.addTrack(t, local); });
    pc.onicecandidate = function (e) { if (e.candidate) signal(name, 'ice', e.candidate); };
    pc.ontrack = function (e) { tile(name, e.streams[0], false); onPeerConnected(); };
    // Adding a track later (screen share on a voice call) needs a fresh offer,
    // otherwise the other side never receives the new stream.
    pc.onnegotiationneeded = function () {
      if (pc.signalingState !== 'stable') return;
      pc.createOffer()
        .then(function (o) { return pc.setLocalDescription(o); })
        .then(function () { return signal(name, 'offer', pc.localDescription); })
        .catch(function () {});
    };
    pc.onconnectionstatechange = function () {
      if (pc.connectionState === 'connected') onPeerConnected();
      if (pc.connectionState === 'failed' || pc.connectionState === 'closed') dropPeer(name);
    };
    return pc;
  }

  function onPeerConnected() {
    if (connectedOnce) return;
    connectedOnce = true;
    if (window.VSound) VSound.stopRing();
    snd('connected');
    headline(Object.keys(peers).length + 1 + ' people');
  }

  function dropPeer(name) {
    if (peers[name]) { try { peers[name].close(); } catch (e) {} delete peers[name]; }
    delete pending[name];
    delete polite[name];
    dropTile(name);
  }

  function flushIce(name, pc) {
    (pending[name] || []).forEach(function (c) {
      pc.addIceCandidate(new RTCIceCandidate(c)).catch(function () {});
    });
    pending[name] = [];
  }

  function callPeer(name) {
    var pc = peer(name);
    polite[name] = false;   // the joiner offers first, so it is the impolite side
    return pc.createOffer()
      .then(function (o) { return pc.setLocalDescription(o); })
      .then(function () { return signal(name, 'offer', pc.localDescription); })
      .catch(function () {});
  }

  function handleSignal(sig) {
    if (!sig) return;
    if (sig.kind === 'ring') { ring(sig); return; }
    if (!cid || String(sig.conversation_id) !== String(cid)) return;
    var from = sig.from;
    if (!from) return;
    if (sig.kind === 'bye') { dropPeer(from); return; }
    var pc = peer(from);

    if (sig.kind === 'offer') {
      // Glare handling: if we already have a local offer out, roll it back.
      var busy = pc.signalingState !== 'stable';
      var chain = busy
        ? pc.setLocalDescription({ type: 'rollback' }).catch(function () {})
        : Promise.resolve();
      chain
        .then(function () { return pc.setRemoteDescription(new RTCSessionDescription(sig.payload)); })
        .then(function () { flushIce(from, pc); return pc.createAnswer(); })
        .then(function (a) { return pc.setLocalDescription(a); })
        .then(function () { return signal(from, 'answer', pc.localDescription); })
        .catch(function () {});
    } else if (sig.kind === 'answer') {
      if (pc.signalingState !== 'have-local-offer') return;
      pc.setRemoteDescription(new RTCSessionDescription(sig.payload))
        .then(function () { flushIce(from, pc); })
        .catch(function () {});
    } else if (sig.kind === 'ice') {
      // A candidate can arrive before the description it belongs to: queue it.
      if (!pc.remoteDescription || !pc.remoteDescription.type) {
        (pending[from] = pending[from] || []).push(sig.payload);
        return;
      }
      pc.addIceCandidate(new RTCIceCandidate(sig.payload)).catch(function () {});
    }
  }

  /* ---- incoming call banner ---------------------------------------------- */
  function ring(sig) {
    styles();
    var key = String(sig.conversation_id);
    if (cid && String(cid) === key) return;
    if (ringing[key] || document.getElementById('vcRing' + key)) return;

    var box = document.createElement('div');
    box.className = 'vc-ring';
    box.id = 'vcRing' + key;
    box.innerHTML = '<strong>' + esc(sig.from) + '</strong> is calling' +
      (sig.mode === 'video' ? ' (video)' : '') +
      '<div class="vc-ring-actions">' +
      '<button class="v-btn" type="button" data-answer="1" style="width:auto;padding:7px 14px">Join</button>' +
      '<button class="v-btn ghost" type="button" data-decline="1" style="width:auto;padding:7px 14px">Ignore</button></div>';
    document.body.appendChild(box);
    ringing[key] = box;
    if (window.VSound) VSound.ringIn();

    var close = function () {
      clearTimeout(kill);
      box.remove();
      delete ringing[key];
      if (!Object.keys(ringing).length && window.VSound) VSound.stopRing();
    };
    var kill = setTimeout(close, 45000);
    box.addEventListener('click', function (e) {
      if (e.target.closest('[data-answer]')) {
        close();
        document.dispatchEvent(new CustomEvent('v-call-answer', {
          detail: { conversation_id: sig.conversation_id, mode: sig.mode }
        }));
      } else if (e.target.closest('[data-decline]')) {
        close();
      }
    });
  }

  function clearRings() {
    Object.keys(ringing).forEach(function (k) {
      if (ringing[k]) ringing[k].remove();
      delete ringing[k];
    });
    if (window.VSound) VSound.stopRing();
  }

  /* ---- lifecycle --------------------------------------------------------- */
  function start(conversationId, memberList, wanted, opts) {
    if (cid) { toast('You are already in a call.', 'error'); return Promise.resolve(); }
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || !window.RTCPeerConnection) {
      toast('This browser cannot make calls.', 'error');
      return Promise.resolve();
    }
    mode = wanted === 'video' ? 'video' : 'audio';
    clearRings();

    // Always ask first — nobody's camera or microphone turns on unannounced.
    var consent = (opts && opts.skipConsent)
      ? Promise.resolve(true)
      : (mode === 'video' ? askCamera() : askMic());

    return consent.then(function (ok) {
      if (!ok) return;
      cid = String(conversationId);
      connectedOnce = false;
      var constraints = mode === 'video'
        ? { audio: true, video: { width: { ideal: 640 } } }
        : { audio: true, video: false };

      return navigator.mediaDevices.getUserMedia(constraints)
        .then(function (s) {
          local = s;
          stage();
          started = Date.now();
          timer = setInterval(tick, 1000);
          tile(me, local, true);
          headline('connecting');
          return api('/messaging/conversations/' + cid + '/call/join', 'POST', { mode: mode });
        })
        .then(function (r) {
          var inRoom = (((r || {}).data || {}).members || []).filter(function (u) {
            return String(u).toLowerCase() !== me.toLowerCase();
          });
          // Whoever joins later offers to everyone already in the room.
          inRoom.forEach(callPeer);
          if (inRoom.length) {
            headline((inRoom.length + 1) + ' people');
          } else {
            headline('ringing\u2026');
            if (window.VSound) VSound.ringOut();
          }
          beat = setInterval(function () {
            if (!cid) return;
            api('/messaging/conversations/' + cid + '/call').catch(function () {});
          }, 20000);
        })
        .catch(function (e) {
          var msg = (e && (e.name === 'NotAllowedError' || e.name === 'SecurityError'))
            ? 'Microphone/camera permission was refused in your browser settings.'
            : (e && e.name === 'NotFoundError')
              ? 'No microphone or camera was found on this device.'
              : ((e && e.message) || 'The call could not be started.');
          toast(msg, 'error');
          // We may already have joined the room server-side, so always tell it.
          hangup(true);
        });
    });
  }

  function hangup(notify) {
    var wasIn = !!cid;
    if (timer) { clearInterval(timer); timer = null; }
    if (beat) { clearInterval(beat); beat = null; }
    if (window.VSound) VSound.stopRing();
    Object.keys(peers).forEach(function (n) {
      try { peers[n].close(); } catch (e) {}
      signal(n, 'bye', null);
    });
    peers = {}; tiles = {}; pending = {}; polite = {};
    if (local) { local.getTracks().forEach(function (t) { t.stop(); }); local = null; }
    if (screen) { screen.getTracks().forEach(function (t) { t.stop(); }); screen = null; }
    sharing = false; muted = false; camOff = false;
    var s = document.getElementById('vcStage');
    if (s) s.remove();
    if (cid && notify !== false) {
      api('/messaging/conversations/' + cid + '/call/leave', 'POST').catch(function () {});
    }
    cid = null;
    if (wasIn && connectedOnce) snd('ended');
    connectedOnce = false;
  }

  function toggleMute() {
    if (!local) return;
    muted = !muted;
    local.getAudioTracks().forEach(function (t) { t.enabled = !muted; });
    var b = document.getElementById('vcMute');
    if (b) { b.classList.toggle('on', muted); b.title = muted ? 'Unmute' : 'Mute'; }
  }

  function toggleCam() {
    if (!local) return;
    var tracks = local.getVideoTracks();
    if (!tracks.length) return;
    var turningOn = camOff;   // it is currently off, so this switches it back on
    var go = function () {
      camOff = !camOff;
      tracks.forEach(function (t) { t.enabled = !camOff; });
      var b = document.getElementById('vcCam');
      if (b) { b.classList.toggle('on', camOff); b.title = camOff ? 'Turn camera on' : 'Turn camera off'; }
    };
    if (turningOn) { askCamera().then(function (ok) { if (ok) go(); }); return; }
    go();
  }

  function replaceOutgoingVideo(track) {
    Object.keys(peers).forEach(function (n) {
      var pc = peers[n];
      var sender = pc.getSenders().filter(function (s) { return s.track && s.track.kind === 'video'; })[0];
      if (!sender) {
        sender = pc.getSenders().filter(function (s) {
          return !s.track && s.transport === null;
        })[0];
      }
      if (sender) {
        sender.replaceTrack(track).catch(function () {});
      } else if (track) {
        // No video sender yet (voice call): add one and renegotiate.
        try { pc.addTrack(track, screen || local); } catch (e) {}
      }
    });
  }

  function toggleShare() {
    if (!cid) return;
    var b = document.getElementById('vcShare');

    if (sharing) {
      if (screen) screen.getTracks().forEach(function (t) { t.stop(); });
      screen = null; sharing = false;
      var own = (local && !camOff) ? (local.getVideoTracks()[0] || null) : null;
      replaceOutgoingVideo(own);
      tile(me, local, true);
      if (b) { b.classList.remove('on'); b.title = 'Share screen'; }
      toast('You stopped sharing your screen.');
      return;
    }

    if (!navigator.mediaDevices || !navigator.mediaDevices.getDisplayMedia) {
      toast('This browser cannot share the screen.', 'error');
      return;
    }

    askScreen().then(function (ok) {
      if (!ok || !cid) return;
      navigator.mediaDevices.getDisplayMedia({ video: true, audio: false }).then(function (s) {
        if (!cid) { s.getTracks().forEach(function (t) { t.stop(); }); return; }
        screen = s; sharing = true;
        if (b) { b.classList.add('on'); b.title = 'Stop sharing'; }
        var track = s.getVideoTracks()[0];
        replaceOutgoingVideo(track);
        tile(me, s, true);
        toast('You are sharing your screen with everyone in this call.', 'warn');
        track.addEventListener('ended', function () { if (sharing) toggleShare(); });
      }).catch(function (e) {
        if (e && e.name === 'NotAllowedError') return;   // simply cancelled
        toast('The screen could not be shared.', 'error');
      });
    });
  }

  window.addEventListener('beforeunload', function () { if (cid) hangup(true); });

  window.VCalls = {
    start: start, hangup: hangup, handleSignal: handleSignal,
    askScreen: askScreen, askCamera: askCamera,
    active: function () { return cid; }, mode: function () { return mode; }
  };
})();
