/* chatkit.js — the fun parts of the chat box: emoji, stickers, GIFs and the
   camera. Everything is self contained and exposed as window.VChatKit.

     VChatKit.togglePicker(button, { onText, onFile })
     VChatKit.openCamera({ onFile })

   onText(str)   receives an emoji or sticker to drop into the message box.
   onFile(File)  receives a GIF or a photo ready to be sent as an attachment. */
(function () {
  var EMOJI = {
    'Smileys': '😀 😃 😄 😁 😆 😅 🤣 😂 🙂 🙃 😉 😊 😇 🥰 😍 🤩 😘 😗 😚 😋 😛 😜 🤪 🤨 🧐 🤓 😎 🥳 😏 😒 😞 😔 😟 😕 🙁 😣 😖 😫 😩 🥺 😢 😭 😤 😠 😡 🤬 🤯 😳 🥵 🥶 😱 😨 😰 😥 😓 🤗 🤔 🤭 🤫 🤥 😶 😐 😑 😬 🙄 😯 😴 🤤 😪 😵 🤐 🥴 🤢 🤮 🤧 😷 🤒 🤕',
    'People': '👋 🤚 🖐 ✋ 🖖 👌 🤌 🤏 ✌ 🤞 🤟 🤘 🤙 👈 👉 👆 👇 ☝ 👍 👎 ✊ 👊 🤛 🤜 👏 🙌 👐 🤲 🤝 🙏 💪 🦾 👀 🧠 👶 🧒 👦 👧 🧑 👨 👩 🧓 👴 👵 🙋 🙇 🤦 🤷 👮 👷 💂 🕵 👨‍🏫 👩‍🏫 👨‍💻 👩‍💻',
    'Nature': '🐶 🐱 🐭 🐹 🐰 🦊 🐻 🐼 🐨 🐯 🦁 🐮 🐷 🐸 🐵 🐔 🐧 🐦 🦆 🦉 🦄 🐝 🦋 🐌 🐢 🐍 🐙 🦕 🌵 🌲 🌳 🌴 🌱 🌿 ☘ 🍀 🍁 🍄 🌷 🌹 🌺 🌸 🌼 🌻 🌞 🌝 🌚 ⭐ 🌟 ✨ ⚡ 🔥 🌈 ☁ 🌧 ❄ 💧 🌊',
    'Food': '🍏 🍎 🍐 🍊 🍋 🍌 🍉 🍇 🍓 🫐 🍒 🍑 🥭 🍍 🥥 🥝 🍅 🥑 🥦 🥕 🌽 🌶 🥔 🍞 🥐 🥖 🧀 🥚 🍳 🥞 🧇 🥓 🍔 🍟 🍕 🌭 🥪 🌮 🌯 🥗 🍝 🍜 🍲 🍛 🍣 🍱 🍚 🍥 🍦 🍰 🎂 🍫 🍬 🍭 🍩 🍪 ☕ 🍵 🥤 🧃 🍺',
    'Activity': '⚽ 🏀 🏈 ⚾ 🎾 🏐 🏉 🎱 🏓 🏸 🥅 🏒 🏑 🏏 ⛳ 🏹 🎣 🥊 🥋 🎽 🛹 🛼 🎿 ⛷ 🏂 🏋 🤼 🤸 🤾 🏌 🏇 🧘 🏄 🏊 🚴 🚵 🎯 🎮 🕹 🎲 🎼 🎤 🎧 🎸 🎹 🥁 🎺 🎻 🎬 🎨 📚 ✏ 📝 🎓 🏆 🥇 🥈 🥉',
    'Objects': '📱 💻 🖥 ⌨ 🖨 🖱 💾 💿 📷 📸 🎥 📞 ☎ 📟 📠 📺 📻 ⏰ ⏱ ⌛ 🔋 🔌 💡 🔦 🕯 🧯 🛢 💸 💵 💳 🧾 💰 ⚖ 🔧 🔨 ⚙ 🧰 🧲 🔒 🔑 🗝 🚪 🛎 🧳 📦 📫 📮 📅 📆 🗓 📋 📁 📂 🗂 📊 📈 📉 📌 📎 🖇 ✂ 🗑',
    'Symbols': '❤ 🧡 💛 💚 💙 💜 🖤 🤍 💔 ❣ 💕 💞 💓 💗 💖 💘 💝 ✅ ☑ ✔ ❌ ❎ ➕ ➖ ➗ ❓ ❗ 💯 🔔 🔕 🎉 🎊 🎈 🎁 🏁 🚩 ⚠ ♻ 🔴 🟠 🟡 🟢 🔵 🟣 ⚫ ⚪ 🔺 🔻 ⭕ 🕐 📍 🆗 🆕 🔝 💤'
  };
  var STICKERS = ['👍', '🎉', '🔥', '❤️', '😂', '🙏', '👏', '💯', '🥳', '😎', '🤝', '✅',
                  '⭐', '🚀', '📚', '🏫', '📦', '🧾', '⏰', '💡', '☕', '🌈', '😴', '🤯'];
  var GIPHY_KEY = 'dc6zaTOxFJmzC';   // Giphy's public demo key
  var panel = null, handlers = {}, gifTimer = null;

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function styles() {
    if (document.getElementById('vChatKitCss')) return;
    var css = document.createElement('style');
    css.id = 'vChatKitCss';
    css.textContent =
      '.ck-panel{position:absolute;z-index:1200;width:min(360px,92vw);height:330px;display:flex;flex-direction:column;' +
      'background:var(--v-surface,#fff);color:inherit;border:1px solid var(--v-border,#e2e8f0);border-radius:14px;' +
      'box-shadow:0 18px 48px rgba(0,0,0,.22);overflow:hidden}' +
      '.ck-tabs{display:flex;gap:4px;padding:8px;border-bottom:1px solid var(--v-border,#e2e8f0);flex-wrap:wrap}' +
      '.ck-tab{border:0;background:transparent;color:inherit;font:inherit;font-size:12px;font-weight:650;padding:4px 9px;' +
      'border-radius:999px;cursor:pointer;opacity:.65}' +
      '.ck-tab.active{background:var(--v-primary-soft,#eff6ff);color:var(--v-primary,#2563EB);opacity:1}' +
      '.ck-body{flex:1;overflow:auto;padding:8px}' +
      '.ck-grid{display:grid;grid-template-columns:repeat(8,1fr);gap:2px}' +
      '.ck-grid button{border:0;background:transparent;font-size:20px;line-height:1;padding:5px 0;border-radius:8px;cursor:pointer}' +
      '.ck-grid button:hover{background:var(--v-sidebar-hover,#f1f5f9)}' +
      '.ck-stickers{display:grid;grid-template-columns:repeat(4,1fr);gap:6px}' +
      '.ck-stickers button{border:1px solid var(--v-border,#e2e8f0);background:transparent;border-radius:12px;' +
      'font-size:34px;padding:8px 0;cursor:pointer}' +
      '.ck-gifsearch{width:100%;padding:7px 11px;border:1px solid var(--v-border,#e2e8f0);border-radius:999px;' +
      'background:transparent;color:inherit;font:inherit;font-size:13px;margin-bottom:8px}' +
      '.ck-gifs{display:grid;grid-template-columns:1fr 1fr;gap:6px}' +
      '.ck-gifs img{width:100%;border-radius:8px;cursor:pointer;display:block}' +
      '.ck-note{font-size:12px;color:var(--v-text-muted,#64748b);text-align:center;padding:14px}' +
      '.ck-cam{position:fixed;inset:0;background:rgba(15,23,42,.7);display:flex;align-items:center;justify-content:center;z-index:1400;padding:16px}' +
      '.ck-cam-box{background:var(--v-surface,#fff);border-radius:16px;padding:14px;width:min(520px,100%);text-align:center}' +
      '.ck-cam video,.ck-cam canvas{width:100%;border-radius:12px;background:#000;max-height:60vh}' +
      '.ck-cam-actions{display:flex;gap:8px;justify-content:center;margin-top:12px;flex-wrap:wrap}';
    document.head.appendChild(css);
  }

  function close() {
    if (panel) { panel.remove(); panel = null; }
    document.removeEventListener('mousedown', outside, true);
  }
  function outside(e) {
    if (panel && !panel.contains(e.target) && !(handlers.anchor && handlers.anchor.contains(e.target))) close();
  }

  function place(anchor) {
    var r = anchor.getBoundingClientRect();
    panel.style.left = Math.max(8, Math.min(r.left, window.innerWidth - 372)) + 'px';
    panel.style.top = Math.max(8, r.top + window.scrollY - 340) + 'px';
  }

  function paintEmoji(body) {
    var names = Object.keys(EMOJI);
    body.innerHTML = names.map(function (group) {
      return '<div style="font-size:11px;font-weight:700;opacity:.6;margin:6px 2px 3px">' + esc(group) + '</div>' +
        '<div class="ck-grid">' + EMOJI[group].split(' ').filter(Boolean).map(function (e) {
          return '<button type="button" data-emoji="' + esc(e) + '">' + e + '</button>';
        }).join('') + '</div>';
    }).join('');
  }

  function paintStickers(body) {
    body.innerHTML = '<div class="ck-stickers">' + STICKERS.map(function (s) {
      return '<button type="button" data-sticker="' + esc(s) + '">' + s + '</button>';
    }).join('') + '</div>';
  }

  function paintGifs(body) {
    body.innerHTML = '<input class="ck-gifsearch" id="ckGifQ" placeholder="Search GIFs (e.g. thank you)">' +
      '<div class="ck-gifs" id="ckGifs"></div><div class="ck-note" id="ckGifNote">Loading trending GIFs...</div>';
    var input = body.querySelector('#ckGifQ');
    input.addEventListener('input', function () {
      clearTimeout(gifTimer);
      gifTimer = setTimeout(function () { loadGifs(body, input.value.trim()); }, 320);
    });
    loadGifs(body, '');
  }

  function loadGifs(body, q) {
    var grid = body.querySelector('#ckGifs'), note = body.querySelector('#ckGifNote');
    if (!grid) return;
    var url = q
      ? 'https://api.giphy.com/v1/gifs/search?api_key=' + GIPHY_KEY + '&limit=18&rating=g&q=' + encodeURIComponent(q)
      : 'https://api.giphy.com/v1/gifs/trending?api_key=' + GIPHY_KEY + '&limit=18&rating=g';
    note.textContent = 'Searching...';
    fetch(url).then(function (r) { return r.json(); }).then(function (r) {
      var items = (r.data || []).filter(function (g) { return g.images && g.images.fixed_width; });
      if (!items.length) { grid.innerHTML = ''; note.textContent = 'No GIFs found.'; return; }
      note.textContent = '';
      grid.innerHTML = items.map(function (g) {
        return '<img loading="lazy" src="' + esc(g.images.fixed_width_small.url || g.images.fixed_width.url) +
          '" data-gif="' + esc(g.images.fixed_width.url) + '" alt="' + esc(g.title || 'GIF') + '">';
      }).join('');
    }).catch(function () {
      grid.innerHTML = '';
      note.textContent = 'GIFs need an internet connection — emoji and stickers still work offline.';
    });
  }

  function sendGif(url) {
    fetch(url).then(function (r) { return r.blob(); }).then(function (blob) {
      if (blob.size > 10 * 1024 * 1024) throw new Error('That GIF is larger than 10 MB.');
      var file = new File([blob], 'gif-' + Date.now() + '.gif', { type: 'image/gif' });
      if (handlers.onFile) handlers.onFile(file);
      close();
    }).catch(function (e) {
      if (window.App && App.ui) App.ui.showToast(e.message || 'That GIF could not be loaded.', 'error');
    });
  }

  function togglePicker(anchor, opts) {
    styles();
    if (panel) { close(); return; }
    handlers = opts || {};
    handlers.anchor = anchor;
    panel = document.createElement('div');
    panel.className = 'ck-panel';
    panel.innerHTML =
      '<div class="ck-tabs">' +
        '<button type="button" class="ck-tab active" data-tab="emoji">Emoji</button>' +
        '<button type="button" class="ck-tab" data-tab="stickers">Stickers</button>' +
        '<button type="button" class="ck-tab" data-tab="gifs">GIFs</button>' +
      '</div><div class="ck-body" id="ckBody"></div>';
    document.body.appendChild(panel);
    place(anchor);
    var body = panel.querySelector('#ckBody');
    paintEmoji(body);

    panel.addEventListener('click', function (e) {
      var tab = e.target.closest('.ck-tab');
      if (tab) {
        Array.prototype.forEach.call(panel.querySelectorAll('.ck-tab'), function (t) { t.classList.remove('active'); });
        tab.classList.add('active');
        var which = tab.getAttribute('data-tab');
        if (which === 'emoji') paintEmoji(body);
        else if (which === 'stickers') paintStickers(body);
        else paintGifs(body);
        return;
      }
      var em = e.target.closest('[data-emoji]');
      if (em) { if (handlers.onText) handlers.onText(em.getAttribute('data-emoji')); return; }
      var st = e.target.closest('[data-sticker]');
      if (st) { if (handlers.onText) handlers.onText(st.getAttribute('data-sticker'), true); close(); return; }
      var gif = e.target.closest('[data-gif]');
      if (gif) sendGif(gif.getAttribute('data-gif'));
    });
    setTimeout(function () { document.addEventListener('mousedown', outside, true); }, 0);
  }

  /* ---- camera ------------------------------------------------------------ */
  function openCamera(opts) {
    styles();
    var wrap = document.createElement('div');
    wrap.className = 'ck-cam';
    wrap.innerHTML = '<div class="ck-cam-box">' +
      '<video id="ckVideo" autoplay playsinline muted></video>' +
      '<canvas id="ckCanvas" hidden></canvas>' +
      '<div class="ck-cam-actions">' +
        '<button class="v-btn" type="button" id="ckShot" style="width:auto;padding:8px 18px">Take photo</button>' +
        '<button class="v-btn ghost" type="button" id="ckRetake" style="width:auto;padding:8px 18px;display:none">Retake</button>' +
        '<button class="v-btn" type="button" id="ckUse" style="width:auto;padding:8px 18px;display:none">Use photo</button>' +
        '<button class="v-btn ghost" type="button" id="ckCancel" style="width:auto;padding:8px 18px">Cancel</button>' +
      '</div><p id="ckCamNote" style="font-size:12px;color:var(--v-text-muted,#64748b);margin:8px 0 0"></p></div>';
    document.body.appendChild(wrap);

    var video = wrap.querySelector('#ckVideo'), canvas = wrap.querySelector('#ckCanvas');
    var note = wrap.querySelector('#ckCamNote'), stream = null, shot = null;

    function stop() { if (stream) stream.getTracks().forEach(function (t) { t.stop(); }); }
    function done() { stop(); wrap.remove(); }

    navigator.mediaDevices.getUserMedia({ video: { facingMode: 'user' }, audio: false })
      .then(function (s) { stream = s; video.srcObject = s; })
      .catch(function () { note.textContent = 'The camera could not be opened. Check the browser permission.'; });

    wrap.querySelector('#ckShot').addEventListener('click', function () {
      canvas.width = video.videoWidth || 640;
      canvas.height = video.videoHeight || 480;
      canvas.getContext('2d').drawImage(video, 0, 0, canvas.width, canvas.height);
      canvas.hidden = false; video.hidden = true;
      this.style.display = 'none';
      wrap.querySelector('#ckRetake').style.display = '';
      wrap.querySelector('#ckUse').style.display = '';
      canvas.toBlob(function (b) { shot = b; }, 'image/jpeg', 0.9);
    });
    wrap.querySelector('#ckRetake').addEventListener('click', function () {
      canvas.hidden = true; video.hidden = false; shot = null;
      this.style.display = 'none';
      wrap.querySelector('#ckUse').style.display = 'none';
      wrap.querySelector('#ckShot').style.display = '';
    });
    wrap.querySelector('#ckUse').addEventListener('click', function () {
      if (!shot) { note.textContent = 'Still saving the photo — try again in a second.'; return; }
      var file = new File([shot], 'photo-' + Date.now() + '.jpg', { type: 'image/jpeg' });
      if (opts && opts.onFile) opts.onFile(file);
      done();
    });
    wrap.querySelector('#ckCancel').addEventListener('click', done);
    wrap.addEventListener('click', function (e) { if (e.target === wrap) done(); });
  }

  window.VChatKit = { togglePicker: togglePicker, openCamera: openCamera, close: close };
})();
