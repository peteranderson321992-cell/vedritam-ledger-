/* avatars.js — one profile photo per account, kept on the server.

   The picture chosen in Settings is uploaded once and then reused everywhere:
   the header, the conversation list, chat bubbles and the group member panel.
   Photos are cached in memory (and mirrored into localStorage) so the pages
   paint instantly and only ask the server for faces it has not seen yet. */
(function () {
  var CACHE = {};            // lowercase username -> data URL ('' = no photo)
  var PRESENCE = {};         // lowercase username -> {online,last_seen}
  var pending = {};          // usernames already requested in this page load
  var LS_PREFIX = 'v_avatar_';

  function api(p, m, b) { return window.App.apiCall(p, m || 'GET', b || null); }
  function key(u) { return String(u || '').trim().toLowerCase(); }

  function readLocal(u) {
    try { return localStorage.getItem(LS_PREFIX + u) || ''; } catch (e) { return ''; }
  }
  function writeLocal(u, v) {
    try { v ? localStorage.setItem(LS_PREFIX + u, v) : localStorage.removeItem(LS_PREFIX + u); } catch (e) {}
  }

  function get(username) {
    var k = key(username);
    if (CACHE[k] != null) return CACHE[k];
    var local = readLocal(username);
    return local || '';
  }

  function presence(username) { return PRESENCE[key(username)] || null; }

  /* Fetches any photo we do not already have. Safe to call often. */
  function prefetch(usernames, force) {
    var want = [];
    (usernames || []).forEach(function (u) {
      var k = key(u);
      if (!k) return;
      if (!force && (CACHE[k] != null || pending[k])) return;
      pending[k] = true;
      want.push(u);
    });
    if (!want.length) return Promise.resolve(CACHE);
    return api('/profile/avatars', 'POST', { usernames: want })
      .then(function (r) {
        want.forEach(function (u) { CACHE[key(u)] = ''; });
        Object.keys(r.data || {}).forEach(function (u) {
          CACHE[key(u)] = r.data[u];
          writeLocal(u, r.data[u]);
        });
        Object.keys(r.presence || {}).forEach(function (u) { PRESENCE[key(u)] = r.presence[u]; });
        return CACHE;
      })
      .catch(function () { want.forEach(function (u) { pending[key(u)] = false; }); return CACHE; });
  }

  function mine() {
    var me = '';
    try { me = localStorage.getItem('v_username') || ''; } catch (e) {}
    return get(me);
  }

  function save(dataUrl) {
    var me = '';
    try { me = localStorage.getItem('v_username') || ''; } catch (e) {}
    return api('/profile/avatar', 'PUT', { data: dataUrl }).then(function (r) {
      CACHE[key(me)] = r.avatar || '';
      writeLocal(me, r.avatar || '');
      document.dispatchEvent(new CustomEvent('v-avatar-changed', { detail: { username: me } }));
      return r.avatar;
    });
  }

  function remove() {
    var me = '';
    try { me = localStorage.getItem('v_username') || ''; } catch (e) {}
    return api('/profile/avatar', 'DELETE').then(function () {
      CACHE[key(me)] = '';
      writeLocal(me, '');
      document.dispatchEvent(new CustomEvent('v-avatar-changed', { detail: { username: me } }));
    });
  }

  /* Shrinks any picked image to a square thumbnail before it is uploaded. */
  function resize(file, size) {
    size = size || 192;
    return new Promise(function (resolve, reject) {
      if (!file || !/^image\//.test(file.type)) { reject(new Error('Please choose an image file.')); return; }
      var reader = new FileReader();
      reader.onerror = function () { reject(new Error('That image could not be read.')); };
      reader.onload = function (ev) {
        var img = new Image();
        img.onerror = function () { reject(new Error('That image could not be read.')); };
        img.onload = function () {
          var canvas = document.createElement('canvas');
          canvas.width = size; canvas.height = size;
          var scale = Math.max(size / img.width, size / img.height);
          var w = img.width * scale, h = img.height * scale;
          canvas.getContext('2d').drawImage(img, (size - w) / 2, (size - h) / 2, w, h);
          resolve(canvas.toDataURL('image/jpeg', 0.85));
        };
        img.src = ev.target.result;
      };
      reader.readAsDataURL(file);
    });
  }

  /* Loads the signed-in account's own photo as early as possible. */
  function boot() {
    var me = '';
    try { me = localStorage.getItem('v_username') || ''; } catch (e) {}
    if (!me) return Promise.resolve('');
    return prefetch([me], true).then(function () {
      document.dispatchEvent(new CustomEvent('v-avatar-changed', { detail: { username: me } }));
      return get(me);
    });
  }

  var COLORS = ['#0EA5E9', '#6366F1', '#EC4899', '#F97316', '#14B8A6', '#8B5CF6', '#22C55E', '#E11D48'];
  function color(name) {
    var h = 0, s = String(name || '');
    for (var i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
    return COLORS[h % COLORS.length];
  }
  function initials(name) {
    var parts = String(name || '?').trim().split(/[\s._-]+/).filter(Boolean);
    if (!parts.length) return '?';
    return (parts[0][0] + (parts[1] ? parts[1][0] : (parts[0][1] || ''))).toUpperCase();
  }

  window.VAvatars = {
    get: get, save: save, remove: remove, prefetch: prefetch, resize: resize,
    mine: mine, boot: boot, presence: presence, color: color, initials: initials
  };
})();
