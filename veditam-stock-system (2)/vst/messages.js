/* messages.js - messaging UI: conversations, thread, attachments, read
   receipts, typing indicator, search, emoji/stickers/GIFs, camera,
   group info panel and live voice/video calls with screen sharing. */
(function () {
  var api = function (p, m, b) { return window.App.apiCall(p, m || 'GET', b || null); };
  var me = '';
  try { me = localStorage.getItem('v_username') || ''; } catch (e) {}
  var role = 'user';
  try { role = localStorage.getItem('v_role') || 'user'; } catch (e) {}

  var current = null;      // active conversation id
  var currentConv = null;  // active conversation record (members, type, ...)
  var currentDetails = null;
  var convAvatars = {};    // conversation id -> group photo
  var loaded = 0;          // how many messages of this thread are on screen
  var lastSeenId = '';   // id of the newest message we have already shown
  var totalKnown = 0;
  var PAGE = 200;          // the whole recent history arrives on the first paint
  var STEP = 200;          // extra history pulled in when scrolling to the top
  var pollTimer = null, signalTimer = null, typingTimer = null, lastTypingPing = 0;
  var rawBodies = {};      // message id -> original (encrypted) body envelope
  var painted = '';        // signature of what is on screen (stops flicker)
  var blobCache = {};      // attachment id -> object URL
  var loadingOlder = false;

  // Announcements are a public channel and stay readable; every other
  // conversation is end-to-end encrypted in the browser, so the server (and
  // therefore any administrator) only ever stores ciphertext.
  function isPrivate(conv) { return !!conv && conv.type !== 'announcement'; }
  var LOCK = '\uD83D\uDD12';

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function toast(m, t) { if (window.App && App.ui) App.ui.showToast(m, t || 'success'); }
  function el(id) { return document.getElementById(id); }

  // ---- avatars -------------------------------------------------------------
  // Direct messages show the other person's own profile photo (the one they
  // picked in Settings). Groups show their group photo, announcements one
  // fixed channel avatar.
  function avColor(name) { return window.VAvatars ? VAvatars.color(name) : '#0EA5E9'; }
  function initials(name) {
    return window.VAvatars ? VAvatars.initials(name) : String(name || '?').slice(0, 2).toUpperCase();
  }
  function photoOf(username) { return window.VAvatars ? VAvatars.get(username) : ''; }

  function faceHtml(photo, name, cls) {
    if (photo) {
      return '<span class="' + cls + '" style="background:transparent"><img src="' + esc(photo) +
        '" alt="' + esc(name) + '"></span>';
    }
    return '<span class="' + cls + '" style="background:' + avColor(name) + '">' + esc(initials(name)) + '</span>';
  }

  /* One avatar for a whole conversation row / header. */
  function convAvatarHtml(conv, small) {
    var cls = 'mg-av' + (small ? ' sm' : '');
    if (conv.type === 'announcement') return '<span class="' + cls + ' ann" title="Announcements">&#128226;</span>';
    if (conv.type === 'group') {
      var gp = convAvatars[conv.id] || '';
      if (gp) return '<span class="' + cls + '"><img src="' + esc(gp) + '" alt="' + esc(conv.title || 'Group') + '"></span>';
      return '<span class="' + cls + ' group" title="' + esc(conv.title || 'Group') + '">&#128101;</span>';
    }
    var other = otherMember(conv);
    return faceHtml(photoOf(other), other, cls);
  }

  function otherMember(conv) {
    var others = (conv.members || []).filter(function (m) { return m !== me; });
    return others[0] || conv.title || 'Direct message';
  }

  function convTitle(c) {
    if (c.type === 'announcement') return 'Announcements';
    if (c.type === 'dm') {
      return (c.members || []).filter(function (m) { return m !== me; }).join(', ') || c.title || 'Direct message';
    }
    return c.title || 'Group';
  }
  /* Parses a stored "YYYY-MM-DD HH:MM:SS" (server local time) into a Date. */
  function parseTs(ts) {
    var m = String(ts || '').match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?/);
    if (!m) return null;
    return new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +(m[6] || 0));
  }
  function dayKey(d) {
    var pad = function (n) { return n < 10 ? '0' + n : String(n); };
    return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate());
  }
  function clockText(ts) {
    var d = parseTs(ts);
    if (!d) return String(ts || '');
    var h = d.getHours(), mm = d.getMinutes();
    var ap = h >= 12 ? 'PM' : 'AM';
    h = h % 12; if (!h) h = 12;
    return h + ':' + (mm < 10 ? '0' + mm : mm) + ' ' + ap;
  }
  function fullWhen(ts) {
    var d = parseTs(ts);
    if (!d) return String(ts || '');
    return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' }) +
      ', ' + clockText(ts);
  }
  function shortWhen(ts) {
    var d = parseTs(ts);
    if (!d) return String(ts || '').slice(0, 16);
    var now = new Date();
    if (dayKey(d) === dayKey(now)) return clockText(ts);
    var y = new Date(); y.setDate(y.getDate() - 1);
    if (dayKey(d) === dayKey(y)) return 'Yesterday';
    var sameYear = d.getFullYear() === now.getFullYear();
    return d.toLocaleDateString(undefined, sameYear
      ? { day: 'numeric', month: 'short' }
      : { day: 'numeric', month: 'short', year: 'numeric' });
  }
  function niceSize(bytes) {
    var n = Number(bytes || 0);
    if (!n) return '';
    if (n < 1024) return n + ' B';
    if (n < 1024 * 1024) return (n / 1024).toFixed(0) + ' KB';
    return (n / 1024 / 1024).toFixed(1) + ' MB';
  }
  function fileIcon(name, type) {
    var ext = String(name || '').split('.').pop().toLowerCase();
    if (/^image\//.test(type || '')) return '&#128247;';
    if (ext === 'pdf') return '&#128209;';
    if (ext === 'csv' || ext === 'xls' || ext === 'xlsx') return '&#128200;';
    if (ext === 'doc' || ext === 'docx' || ext === 'txt') return '&#128196;';
    return '&#128206;';
  }

  // ---- conversation list ---------------------------------------------------
  function renderList(items) {
    var box = el('mgList');
    if (!items.length) { box.innerHTML = '<div class="mg-empty">No conversations yet.</div>'; return; }
    box.innerHTML = items.map(function (c) {
      var title = convTitle(c);
      return '<button class="mg-item' + (c.id === current ? ' active' : '') + '" data-id="' + esc(c.id) + '">' +
        convAvatarHtml(c, false) +
        '<span class="mg-body">' +
          '<span class="mg-top"><b>' + esc(title) + '</b>' +
            '<span class="mg-when">' + esc(shortWhen(c.last_message_at)) + '</span></span>' +
          '<span class="mg-prev"><span>' + esc(c.last_message || 'No messages yet') + '</span>' +
            (c.unread ? '<span class="mg-badge">' + c.unread + '</span>' : '') +
          '</span>' +
        '</span></button>';
    }).join('');
    Array.prototype.forEach.call(box.querySelectorAll('.mg-item'), function (b) {
      b.addEventListener('click', function () { open(b.getAttribute('data-id')); });
    });
  }

  /* Loads every photo used by the rail so no avatar pops in late. */
  function warmAvatars(items) {
    var people = [];
    (items || []).forEach(function (c) {
      (c.members || []).forEach(function (m) { people.push(m); });
      if (c.type === 'group' && convAvatars[c.id] === undefined) {
        convAvatars[c.id] = '';
        api('/messaging/conversations/' + c.id + '/details').then(function (d) {
          convAvatars[c.id] = d.avatar || '';
          if (d.avatar) renderList(lastList);
        }).catch(function () {});
      }
    });
    return window.VAvatars ? VAvatars.prefetch(people) : Promise.resolve();
  }

  var lastList = [];
  function loadList() {
    return api('/messaging/conversations').then(function (r) {
      lastList = r.data || [];
      renderList(lastList);
      return warmAvatars(lastList).then(function () { renderList(lastList); });
    }).catch(function (e) { el('mgList').innerHTML = '<div class="mg-empty">' + esc(e.message) + '</div>'; });
  }

  // ---- thread --------------------------------------------------------------
  function dayLabel(day) {
    var d = parseTs(day + ' 00:00');
    if (!d) return day;
    var today = new Date();
    if (dayKey(d) === dayKey(today)) return 'Today';
    var y = new Date(); y.setDate(y.getDate() - 1);
    if (dayKey(d) === dayKey(y)) return 'Yesterday';
    return d.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' });
  }

  /* Turns a stored message into something renderable, decrypting when needed. */
  function decryptItem(m, conv) {
    rawBodies[m.id] = m.body;
    if (!isPrivate(conv) || !window.E2EE || !E2EE.isEncrypted(m.body)) {
      return Promise.resolve(m);
    }
    return E2EE.decryptMessage(m.body).then(function (res) {
      var out = {};
      Object.keys(m).forEach(function (k) { out[k] = m[k]; });
      out.body = res.text;
      out.encrypted = true;
      if (res.file) {
        out.attachment_name = res.file.name;
        out.attachment_type = res.file.type;
      }
      return out;
    }).catch(function () {
      var out = {};
      Object.keys(m).forEach(function (k) { out[k] = m[k]; });
      out.body = '';
      out.encrypted = true;
      out.undecryptable = true;
      out.attachment_id = '';
      return out;
    });
  }

  /* True for a message that is nothing but emoji — shown big, like a sticker. */
  function isSticker(text) {
    var t = String(text || '').replace(/\s/g, '');
    if (!t || t.length > 8) return false;
    return !/[a-zA-Z0-9]/.test(t) && /\p{Extended_Pictographic}/u.test(t);
  }

  function bubble(m, conv) {
    var mine = m.sender === me;
    var html = '<div class="bubble' + (mine ? ' mine' : '') + '" data-mid="' + esc(m.id) + '">';
    // In a group each incoming message shows who wrote it, with their photo.
    if (!mine && conv && conv.type === 'group') {
      html += '<span class="who">' + faceHtml(photoOf(m.sender), m.sender, 'mg-av xs') +
        '<span>' + esc(m.sender) + '</span></span>';
    }

    if (m.undecryptable) {
      html += '<span class="mg-locked">' + LOCK + ' Older encrypted message — unavailable.</span>';
    } else if (m.body) {
      html += isSticker(m.body)
        ? '<span class="mg-sticker">' + esc(m.body) + '</span>'
        : esc(m.body).replace(/\n/g, '<br>');
    }
    if (m.attachment_id) {
      var isImage = /^image\//.test(m.attachment_type || '');
      if (isImage) {
        html += '<img class="att-img" alt="' + esc(m.attachment_name) + '" title="Click to open full size" ' +
          'data-att="' + esc(m.attachment_id) + '" data-msg="' + esc(m.id) + '" ' +
          'data-name="' + esc(m.attachment_name) + '">';
      }
      // A proper file card: icon, name, size and one obvious Download button.
      html += '<span class="att-card">' +
        '<span class="att-ic">' + fileIcon(m.attachment_name, m.attachment_type) + '</span>' +
        '<span class="att-meta"><b>' + esc(m.attachment_name || 'Attachment') + '</b>' +
        '<i>' + esc(niceSize(m.attachment_size)) + (isImage ? ' · image' : '') + '</i></span>' +
        '<button type="button" class="att-btn" data-open="' + esc(m.attachment_id) + '" data-msg="' + esc(m.id) +
        '" data-name="' + esc(m.attachment_name) + '" title="Open">&#128065;</button>' +
        '<button type="button" class="att-btn" data-download="' + esc(m.attachment_id) + '" data-msg="' + esc(m.id) +
        '" data-name="' + esc(m.attachment_name) + '" title="Download">&#11015;</button>' +
        '</span>';
    }
    var readers = (m.read_by || []).filter(function (u) { return u !== m.sender; });
    var receipt = mine
      ? (readers.length ? ' · Read by ' + esc(readers.join(', ')) : ' · Sent')
      : '';
    html += '<span class="meta" title="' + esc(fullWhen(m.timestamp)) + '">' +
      esc(clockText(m.timestamp)) + receipt + '</span></div>';

    return html;
  }

  /* Downloads an attachment and, when it belongs to an encrypted message,
     decrypts the bytes in the browser before showing or saving them.
     Each attachment is fetched once and then reused from the cache, which is
     what stops the thread from flickering while it refreshes. */
  function fetchBlobUrl(attId, msgId) {
    if (blobCache[attId]) return Promise.resolve(blobCache[attId]);
    var token = localStorage.getItem('v_token');
    var raw = rawBodies[msgId];
    return fetch((window.App.API_BASE_URL || '/api/v1') + '/messaging/attachments/' + encodeURIComponent(attId),
      { headers: { Authorization: 'Bearer ' + token } })
      .then(function (r) { if (!r.ok) throw new Error('Attachment unavailable'); return r.arrayBuffer(); })
      .then(function (buf) {
        if (window.E2EE && E2EE.isEncrypted(raw)) {
          return E2EE.decryptAttachment(raw, buf).then(function (res) { return res.blob; });
        }
        return new Blob([buf]);
      })
      .then(function (b) { blobCache[attId] = URL.createObjectURL(b); return blobCache[attId]; });
  }

  function hydrateAttachments(scope) {
    Array.prototype.forEach.call(scope.querySelectorAll('img[data-att]'), function (img) {
      if (img.getAttribute('data-done')) return;
      img.setAttribute('data-done', '1');
      fetchBlobUrl(img.getAttribute('data-att'), img.getAttribute('data-msg'))
        .then(function (u) { img.src = u; })
        .catch(function () { img.remove(); });
      img.addEventListener('click', function () {
        fetchBlobUrl(img.getAttribute('data-att'), img.getAttribute('data-msg'))
          .then(function (u) { window.open(u, '_blank'); }).catch(function () {});
      });
    });
  }

  function saveBlob(url, name) {
    var link = document.createElement('a');
    link.href = url; link.download = name || 'attachment';
    document.body.appendChild(link); link.click(); link.remove();
  }

  function renderThread(page, prepend) {
    var conv = page.conversation || {};
    return Promise.all((page.items || []).map(function (m) { return decryptItem(m, conv); }))
      .then(function (items) {
        var people = items.map(function (m) { return m.sender; });
        return (window.VAvatars ? VAvatars.prefetch(people) : Promise.resolve())
          .then(function () { paintThread(page, items, prepend); });
      });
  }

  function paintThread(page, items, prepend) {
    var box = el('mgThread');
    var conv = page.conversation || {};
    currentConv = conv;
    var lastDay = '';
    var html = items.map(function (m) {
      var day = String(m.timestamp || '').slice(0, 10);
      var sep = '';
      if (day && day !== lastDay) { lastDay = day; sep = '<div class="mg-daysep">' + esc(dayLabel(day)) + '</div>'; }
      return sep + bubble(m, conv);
    }).join('');

    if (prepend) {
      var prevHeight = box.scrollHeight, prevTop = box.scrollTop;
      box.insertAdjacentHTML('afterbegin', html);
      box.scrollTop = box.scrollHeight - prevHeight + prevTop;
      painted = '';
    } else {
      // Only repaint when something actually changed, and keep the reading
      // position if the user has scrolled up.
      var signature = items.map(function (m) {
        return m.id + ':' + (m.read_by || []).length + ':' + (m.body || '').length;
      }).join('|');
      if (signature === painted) { paintHeader(page); return; }
      var atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 80;
      var keep = box.scrollTop;
      painted = signature;
      box.innerHTML = html || '<div class="mg-empty">No messages yet — say hello.</div>';
      box.scrollTop = atBottom ? box.scrollHeight : keep;
    }
    hydrateAttachments(box);
    paintHeader(page);
  }

  function paintHeader(page) {
    var conv = page.conversation || currentConv || {};
    var title = convTitle(conv);
    el('mgTitle').textContent = title || 'Conversation';
    var av = el('mgAvatar');
    if (av) {
      av.style.display = '';
      av.outerHTML = convAvatarHtml(conv, true).replace('class="mg-av', 'id="mgAvatar" class="mg-av');
    }
    var count = typeof page.total === 'number' ? page.total : totalKnown;
    totalKnown = count;
    var who = conv.type === 'announcement' ? 'Announcement channel'
      : conv.type === 'group' ? (conv.members || []).length + ' members'
      : presenceLine(otherMember(conv));
    el('mgMeta').textContent = who + ' · ' + count + ' message' + (count === 1 ? '' : 's');

    var readOnly = conv.type === 'announcement' && role !== 'super_admin' && role !== 'admin';
    el('mgForm').style.display = readOnly ? 'none' : '';
    var callable = conv.id && conv.type !== 'announcement';
    el('mgCallBar').style.display = callable ? '' : 'none';
    el('mgInfoBtn').style.display = conv.id ? '' : 'none';
  }

  function presenceLine(username) {
    var p = window.VAvatars ? VAvatars.presence(username) : null;
    if (!p) return 'Direct message';
    if (p.online) return 'Online';
    return p.last_seen ? 'Last online ' + fullWhen(p.last_seen) : 'Offline';
  }

  function open(id) {
    current = id; loaded = 0; painted = ''; lastSeenId = '';
    currentDetails = null;
    closeInfo();
    el('mgThread').innerHTML = '<div class="mg-empty">Loading conversation...</div>';
    // The full history lives on the server, so it comes back straight away —
    // there is no "load older" button to press any more.
    api('/messaging/conversations/' + id + '/messages?limit=' + PAGE)
      .then(function (page) {
        loaded = (page.items || []).length;
        return renderThread(page, false);
      })
      .then(function () { return api('/messaging/conversations/' + id + '/read', 'POST'); })
      .then(function () {
        if (window.vRefreshMessageBadge) window.vRefreshMessageBadge();
        loadDetails(true);
        return loadList();
      })
      .catch(function (e) { el('mgThread').innerHTML = '<div class="mg-empty">' + esc(e.message) + '</div>'; });
  }

  /* Scrolling to the very top quietly pulls in the older history. */
  function maybeLoadOlder() {
    var box = el('mgThread');
    if (!current || loadingOlder || box.scrollTop > 40) return;
    if (loaded >= totalKnown) return;
    loadingOlder = true;
    api('/messaging/conversations/' + current + '/messages?limit=' + STEP + '&offset=' + loaded)
      .then(function (page) {
        loaded += (page.items || []).length;
        return renderThread(page, true);
      })
      .catch(function () {})
      .then(function () { loadingOlder = false; });
  }

  // ---- group / people info panel ------------------------------------------
  function loadDetails(silent) {
    if (!current) return Promise.resolve();
    return api('/messaging/conversations/' + current + '/details').then(function (d) {
      currentDetails = d;
      convAvatars[current] = d.avatar || '';
      if (!silent) paintInfo();
      paintHeader({ conversation: d.conversation, total: totalKnown });
      return d;
    }).catch(function () {});
  }

  function paintInfo() {
    var d = currentDetails;
    if (!d) return;
    var conv = d.conversation || {};
    var isGroup = conv.type === 'group';
    var title = convTitle(conv);
    var photo = isGroup ? (d.avatar || '') : photoOf(otherMember(conv));
    el('mgInfoTitle').textContent = isGroup ? 'Group info' : 'Contact info';
    el('mgInfoHead').innerHTML =
      '<div class="mg-info-face">' +
        (photo ? '<img src="' + esc(photo) + '" alt="">' :
          '<span style="background:' + avColor(title) + '">' + esc(initials(title)) + '</span>') +
        (isGroup ? '<button type="button" class="mg-info-cam" id="mgGroupPhotoBtn" title="Change group photo">&#128247;</button>' : '') +
      '</div>' +
      '<h4>' + esc(title) + '</h4>' +
      '<p>' + (isGroup ? (d.members || []).length + ' members' : esc(presenceLine(otherMember(conv)))) + '</p>' +
      (isGroup ? '<input type="file" id="mgGroupPhoto" accept="image/*" hidden>' : '');

    el('mgInfoMembers').innerHTML = (d.members || []).map(function (p) {
      return '<div class="mg-person">' +
        faceHtml(p.avatar, p.username, 'mg-av sm') +
        '<div class="mg-person-body"><b>' + esc(p.fullName || p.username) +
          (p.is_you ? ' <span class="mg-you">You</span>' : '') + '</b>' +
          '<span>@' + esc(p.username) + (p.role ? ' · ' + esc(String(p.role).replace('_', ' ')) : '') + '</span>' +
          '<span class="' + (p.online ? 'mg-on' : 'mg-off') + '">' +
            (p.online ? 'Online now' : (p.last_seen ? 'Last online ' + esc(fullWhen(p.last_seen)) : 'Offline')) +
          '</span>' +
        '</div></div>';
    }).join('') || '<div class="mg-empty">No members.</div>';

    var btn = el('mgGroupPhotoBtn');
    if (btn) {
      btn.addEventListener('click', function () { el('mgGroupPhoto').click(); });
      el('mgGroupPhoto').addEventListener('change', function () {
        var file = this.files && this.files[0];
        if (!file) return;
        VAvatars.resize(file, 256)
          .then(function (dataUrl) {
            return api('/messaging/conversations/' + current + '/avatar', 'PUT', { data: dataUrl });
          })
          .then(function (r) {
            convAvatars[current] = r.avatar || '';
            toast('Group photo updated.');
            return loadDetails(false).then(function () { return loadList(); });
          })
          .catch(function (e) { toast(e.message, 'error'); });
      });
    }
  }

  function openInfo() {
    if (!current) return;
    el('mgInfo').classList.add('open');
    (currentDetails ? Promise.resolve(currentDetails) : loadDetails(true)).then(paintInfo);
  }
  function closeInfo() { var p = el('mgInfo'); if (p) p.classList.remove('open'); }

  // ---- polling: new messages, typing indicator and call signalling --------
  function poll() {
    if (!current) { loadList(); return; }
    api('/messaging/conversations/' + current + '/messages?limit=' + Math.max(PAGE, loaded))
      .then(function (page) {
        var items = page.items || [];
        var count = items.length;
        var newest = count ? String(items[count - 1].id) : '';
        // Comparing ids as well as counts catches the case where one message is
        // deleted and another arrives between two polls (same count, new mail).
        var changed = count !== loaded || String(page.total) !== String(totalKnown) || newest !== lastSeenId;
        // Chime only for someone else's message, and never for the very first
        // load of a thread.
        if (lastSeenId && newest && newest !== lastSeenId) {
          var last = items[count - 1];
          if (last && last.sender !== me && window.VSound) VSound.chime();
        }
        lastSeenId = newest;
        loaded = count;
        renderThread(page, false);
        if (changed) {
          api('/messaging/conversations/' + current + '/read', 'POST').then(function () {
            if (window.vRefreshMessageBadge) window.vRefreshMessageBadge();
            return loadList();
          }).catch(function () {});
        }
        var typing = page.typing || [];
        el('mgTyping').textContent = typing.length
          ? typing.join(', ') + (typing.length === 1 ? ' is typing...' : ' are typing...') : '';
      }).catch(function () {});
  }

  /* Call setup messages and incoming-call rings. */
  function pollSignals() {
    api('/messaging/signals').then(function (r) {
      (r.data || []).forEach(function (sig) {
        if (window.VCalls) VCalls.handleSignal(sig);
      });
    }).catch(function () {});
  }

  // ---- composing -----------------------------------------------------------
  var pendingFile = null;

  function setPending(file) {
    pendingFile = file || null;
    if (pendingFile && pendingFile.size > 10 * 1024 * 1024) {
      toast('Files must be 10 MB or smaller.', 'error');
      pendingFile = null;
    }
    el('mgFileName').innerHTML = pendingFile
      ? '<span class="mg-pending">' + fileIcon(pendingFile.name, pendingFile.type) + ' ' +
        esc(pendingFile.name) + ' <i>' + esc(niceSize(pendingFile.size)) + '</i>' +
        '<button type="button" id="mgDropFile" title="Remove">&times;</button></span>'
      : '';
    var drop = el('mgDropFile');
    if (drop) drop.addEventListener('click', function () { el('mgFile').value = ''; setPending(null); });
  }

  function readFile(file) {
    return new Promise(function (resolve, reject) {
      var fr = new FileReader();
      fr.onload = function () {
        resolve({ name: file.name, type: file.type, data: String(fr.result).split(',')[1] });
      };
      fr.onerror = function () { reject(new Error('Could not read that file.')); };
      fr.readAsDataURL(file);
    });
  }

  /* Builds the request payload. Messages are stored server-side so that any
     account can read its own conversations from any device or browser. */
  function buildPayload(conv, body, file) {
    return Promise.resolve(file ? readFile(file) : null)
      .then(function (att) { return { body: body, attachment: att }; });
  }

  function sendNow(body, file) {
    if (!current) return Promise.resolve();
    return buildPayload(currentConv, body, file)
      .then(function (payload) {
        return api('/messaging/conversations/' + current + '/messages', 'POST', payload);
      })
      .then(function () {
        return api('/messaging/conversations/' + current + '/messages?limit=' + Math.max(PAGE, loaded));
      })
      .then(function (page) {
        loaded = (page.items || []).length;
        return renderThread(page, false).then(function () {
          var box = el('mgThread'); box.scrollTop = box.scrollHeight;
          return loadList();
        });
      });
  }

  function send(e) {
    if (e) e.preventDefault();
    if (!current) return;
    var input = el('mgInput');
    var body = input.value.trim();
    var file = pendingFile;
    if (!body && !file) return;
    var btn = el('mgSend'); btn.disabled = true;
    input.value = ''; input.style.height = 'auto';
    setPending(null); el('mgFile').value = '';
    sendNow(body, file)
      .catch(function (err) { toast(err.message, 'error'); input.value = body; })
      .then(function () { btn.disabled = false; });
  }

  // ---- search --------------------------------------------------------------
  /* Search runs in the browser so legacy encrypted messages are searched too. */
  function runSearch(q) {
    if (q.length < 2) { loadList(); return; }
    var box = el('mgList');
    box.innerHTML = '<div class="mg-empty">Searching your messages...</div>';
    var needle = q.toLowerCase();

    api('/messaging/conversations').then(function (r) {
      var convs = r.data || [];
      return Promise.all(convs.map(function (c) {
        return api('/messaging/conversations/' + c.id + '/messages?limit=200')
          .then(function (page) {
            var conv = page.conversation || c;
            return Promise.all((page.items || []).map(function (m) { return decryptItem(m, conv); }))
              .then(function (items) {
                return items.filter(function (m) {
                  var text = (m.body || '') + ' ' + (m.attachment_name || '');
                  return text.toLowerCase().indexOf(needle) !== -1;
                }).map(function (m) {
                  return { conversation_id: c.id, conversation_title: convTitle(conv),
                           sender: m.sender, body: m.body || m.attachment_name || '',
                           timestamp: m.timestamp };
                });
              });
          }).catch(function () { return []; });
      }));
    }).then(function (groups) {
      var hits = [].concat.apply([], groups);
      if (!hits.length) { box.innerHTML = '<div class="mg-empty">No messages match "' + esc(q) + '".</div>'; return; }
      box.innerHTML = hits.map(function (h) {
        var body = esc(h.body);
        var re = new RegExp('(' + q.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + ')', 'ig');
        return '<div class="mg-hit" data-id="' + esc(h.conversation_id) + '">' +
          '<strong>' + esc(h.conversation_title) + '</strong> · ' + esc(h.sender) +
          '<div style="color:var(--v-text-muted);font-size:12.5px">' + body.replace(re, '<em>$1</em>') + '</div>' +
          '<div style="font-size:11px;color:var(--v-text-muted)">' + esc(fullWhen(h.timestamp)) + '</div></div>';
      }).join('');
      Array.prototype.forEach.call(box.querySelectorAll('.mg-hit'), function (d) {
        d.addEventListener('click', function () { open(d.getAttribute('data-id')); });
      });
    }).catch(function (e) { toast(e.message, 'error'); });
  }

  // ---- new conversations (in-page dialogs, no browser prompts) -------------
  function openModal(id) {
    var m = el(id);
    if (!m) return;
    m.classList.add('open');
    var first = m.querySelector('input, textarea');
    if (first) setTimeout(function () { first.focus(); }, 30);
  }
  function closeModal(id) {
    var m = el(id);
    if (!m) return;
    m.classList.remove('open');
    var form = m.querySelector('form');
    if (form) form.reset();
  }
  function bindModals() {
    Array.prototype.forEach.call(document.querySelectorAll('.mg-modal'), function (m) {
      m.addEventListener('click', function (e) { if (e.target === m) closeModal(m.id); });
    });
    Array.prototype.forEach.call(document.querySelectorAll('[data-close]'), function (b) {
      b.addEventListener('click', function () { closeModal(b.getAttribute('data-close')); });
    });
    document.addEventListener('keydown', function (e) {
      if (e.key !== 'Escape') return;
      Array.prototype.forEach.call(document.querySelectorAll('.mg-modal.open'), function (m) { closeModal(m.id); });
      closeInfo();
    });

    el('mgDmForm').addEventListener('submit', function (e) {
      e.preventDefault();
      var who = el('mgDmUser').value.trim();
      if (!who) return;
      api('/messaging/conversations', 'POST', { type: 'dm', members: [who] })
        .then(function (r) { closeModal('mgDmModal'); return loadList().then(function () { open(r.data.id); }); })
        .catch(function (err) { toast(err.message, 'error'); });
    });

    el('mgGroupForm').addEventListener('submit', function (e) {
      e.preventDefault();
      var title = el('mgGroupName').value.trim();
      if (!title) return;
      var members = (el('mgGroupMembers').value || '').split(',')
        .map(function (x) { return x.trim(); }).filter(Boolean);
      var picked = el('mgGroupPhotoNew').files && el('mgGroupPhotoNew').files[0];
      api('/messaging/conversations', 'POST', { type: 'group', title: title, members: members })
        .then(function (r) {
          var id = r.data.id;
          closeModal('mgGroupModal');
          var next = picked
            ? VAvatars.resize(picked, 256).then(function (d) {
                return api('/messaging/conversations/' + id + '/avatar', 'PUT', { data: d });
              }).catch(function () {})
            : Promise.resolve();
          return next.then(function () { return loadList(); }).then(function () { open(id); });
        })
        .catch(function (err) { toast(err.message, 'error'); });
    });

    el('mgAnnForm').addEventListener('submit', function (e) {
      e.preventDefault();
      var title = el('mgAnnTitle').value.trim();
      var body = el('mgAnnBody').value.trim();
      if (!title || !body) return;
      api('/messaging/announcements', 'POST', { title: title, body: body })
        .then(function (r) {
          closeModal('mgAnnModal');
          toast('Announcement sent to ' + r.recipients + ' account(s).');
          return loadList().then(function () { open(r.data.id); });
        })
        .catch(function (err) { toast(err.message, 'error'); });
    });
  }

  function newDm() { openModal('mgDmModal'); }
  function newGroup() { openModal('mgGroupModal'); }
  function announce() { openModal('mgAnnModal'); }

  // ---- calls ---------------------------------------------------------------
  function startCall(mode) {
    if (!current || !currentConv) return;
    if (!navigator.mediaDevices || !window.RTCPeerConnection) {
      toast('This browser cannot make calls.', 'error');
      return;
    }
    VCalls.start(current, currentConv.members || [], mode);
  }

  // ---- boot ----------------------------------------------------------------
  document.addEventListener('DOMContentLoaded', function () {
    if (!localStorage.getItem('v_token')) { window.location.replace('index.html'); return; }
    bindModals();
    if (window.VAvatars) VAvatars.boot();
    if (window.E2EE && E2EE.supported()) {
      // Only used to try to read legacy encrypted messages on this device.
      E2EE.ready().catch(function () {});
    }
    el('mgForm').addEventListener('submit', send);
    el('mgNewDm').addEventListener('click', newDm);
    el('mgNewGroup').addEventListener('click', newGroup);
    el('mgAnnounce').addEventListener('click', announce);
    el('mgInfoBtn').addEventListener('click', openInfo);
    el('mgInfoClose').addEventListener('click', closeInfo);
    el('mgAudioCall').addEventListener('click', function () { startCall('audio'); });
    el('mgVideoCall').addEventListener('click', function () { startCall('video'); });
    el('mgShareCall').addEventListener('click', function () {
      // Start the call first, then open the screen-sharing prompt as soon as
      // the call controls actually exist (the old fixed delay often missed).
      startCall('audio');
      var tries = 0;
      var wait = setInterval(function () {
        var b = document.getElementById('vcShare');
        if (b) { clearInterval(wait); b.click(); }
        else if (++tries > 40) clearInterval(wait);
      }, 250);
    });

    // Answering a ring from anywhere on the page.
    document.addEventListener('v-call-answer', function (e) {
      var id = e.detail.conversation_id;
      var go = function () { VCalls.start(id, (currentConv && currentConv.members) || [], e.detail.mode); };
      if (String(id) === String(current)) go();
      else { open(id); setTimeout(go, 900); }
    });

    // Attachments: one delegated handler, so repainting never loses the clicks.
    el('mgThread').addEventListener('click', function (e) {
      var dl = e.target.closest('[data-download]');
      if (dl) {
        fetchBlobUrl(dl.getAttribute('data-download'), dl.getAttribute('data-msg'))
          .then(function (u) { saveBlob(u, dl.getAttribute('data-name')); })
          .catch(function (err) { toast(err.message, 'error'); });
        return;
      }
      var op = e.target.closest('[data-open]');
      if (op) {
        fetchBlobUrl(op.getAttribute('data-open'), op.getAttribute('data-msg'))
          .then(function (u) { window.open(u, '_blank'); })
          .catch(function (err) { toast(err.message, 'error'); });
      }
    });
    el('mgThread').addEventListener('scroll', maybeLoadOlder);

    el('mgFile').addEventListener('change', function () {
      setPending(this.files && this.files[0] ? this.files[0] : null);
    });
    el('mgCamera').addEventListener('click', function () {
      VChatKit.openCamera({ onFile: function (file) { setPending(file); sendPendingSoon(); } });
    });
    el('mgEmoji').addEventListener('click', function () {
      VChatKit.togglePicker(this, {
        onText: function (text, isSticker) {
          if (isSticker) { sendNow(text, null).catch(function (e) { toast(e.message, 'error'); }); return; }
          var input = el('mgInput');
          input.value += text;
          input.focus();
        },
        onFile: function (file) { setPending(file); sendPendingSoon(); }
      });
    });

    function sendPendingSoon() {
      // GIFs and camera photos are sent straight away.
      setTimeout(function () { send(null); }, 60);
    }

    var input = el('mgInput');
    input.addEventListener('input', function () {
      input.style.height = 'auto';
      input.style.height = Math.min(input.scrollHeight, 150) + 'px';
      var now = Date.now();
      if (current && now - lastTypingPing > 2500) {
        lastTypingPing = now;
        api('/messaging/conversations/' + current + '/typing', 'POST').catch(function () {});
      }
    });
    input.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); el('mgForm').requestSubmit(); }
    });

    var search = el('mgSearch');
    search.addEventListener('input', function () {
      clearTimeout(typingTimer);
      var q = search.value.trim();
      typingTimer = setTimeout(function () { runSearch(q); }, 280);
    });

    loadList().then(function () {
      var qp = new URLSearchParams(window.location.search).get('c');
      if (qp) open(qp);
    });
    pollTimer = setInterval(poll, 4000);
    signalTimer = setInterval(pollSignals, 3000);
    pollSignals();
    window.addEventListener('beforeunload', function () {
      clearInterval(pollTimer); clearInterval(signalTimer);
    });
  });
})();
