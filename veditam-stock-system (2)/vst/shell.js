/* shell.js - app shell: theme, header, sidebar and quick actions. */
(function () {
  var THEME_KEY = 'v_theme';
  function currentTheme() { return document.documentElement.getAttribute('data-theme') || 'light'; }
  function setTheme(t) {
    document.documentElement.setAttribute('data-theme', t);
    try { localStorage.setItem(THEME_KEY, t); } catch (e) {}
    var i = document.getElementById('vThemeIcon');
    if (i) { i.setAttribute('data-lucide', t === 'dark' ? 'sun' : 'moon'); redrawIcons(); }
  }
  function redrawIcons() {
    if (window.lucide && window.lucide.createIcons) window.lucide.createIcons();
  }

  var signOutStarted = false;
  function signOut(reason) {
    if (signOutStarted) return;
    signOutStarted = true;
    var token = '';
    try { token = localStorage.getItem('v_token') || ''; } catch (e) {}
    // Clear browser state before contacting the server. Use fetch directly so
    // an expired/replaced token cannot re-enter App.apiCall's 401 handler.
    try {
      ['v_token','v_username','v_role','v_fullname','v_school_id','v_school','v_class'].forEach(function (k) {
        localStorage.removeItem(k);
      });
      var keep = {};
      ['v_theme', 'v_sound', 'v_api_base'].forEach(function (k) {
        var v = localStorage.getItem(k);
        if (v !== null) keep[k] = v;
      });
      localStorage.clear();
      // Dark mode and the sound setting belong to the device, not the account,
      // so they survive signing out.
      Object.keys(keep).forEach(function (k) { localStorage.setItem(k, keep[k]); });
    } catch (e) {}
    try { sessionStorage.clear(); } catch (e) {}
    if (token) {
      try {
        fetch(((window.App && App.API_BASE_URL) || '/api/v1') + '/security/logout', {
          method: 'POST',
          headers: { Authorization: 'Bearer ' + token },
          keepalive: true
        }).catch(function () {});
      } catch (e) {}
    }
    var suffix = reason === 'timeout' ? 'index.html?timeout=1' : 'index.html?logout=1';
    try { window.location.replace(suffix); }
    catch (e) { window.location.href = suffix; }
  }
  window.vSignOut = signOut;

  // Presence heartbeat: marks this user active every 30s.
  function heartbeat() {
    try {
      var token = localStorage.getItem('v_token');
      var u = localStorage.getItem('v_username');
      if (!token || !u || signOutStarted) return;
      localStorage.setItem('v_last_active_' + u, String(Date.now()));
      if (window.App && App.apiCall) {
        App.apiCall('/presence/ping', 'POST').catch(function(){});
      }
    } catch (e) {}
  }
  setInterval(heartbeat, 30000);

  // --- Client-side idle session timeout -------------------------------------
  // Mirrors the server policy: no interaction for the configured window signs
  // the account out instead of leaving a live session on an unattended screen.
  var idleMinutes = 30;
  try { idleMinutes = Number(localStorage.getItem('v_session_timeout') || 30) || 30; } catch (e) {}
  var lastInteraction = Date.now();
  ['click', 'keydown', 'mousemove', 'touchstart', 'scroll'].forEach(function (evt) {
    document.addEventListener(evt, function () { lastInteraction = Date.now(); }, { passive: true });
  });
  setInterval(function () {
    if (!localStorage.getItem('v_token')) return;
    if (Date.now() - lastInteraction > idleMinutes * 60 * 1000) signOut('timeout');
  }, 30000);
  window.addEventListener('focus', heartbeat);
  document.addEventListener('DOMContentLoaded', heartbeat);

  function openAssistant() {
    function tryOpen() {
      if (window.VAssistant && typeof window.VAssistant.open === 'function') { window.VAssistant.open(); return true; }
      var b = document.getElementById('cbBtn');
      if (b) { b.click(); return true; }
      return false;
    }
    if (tryOpen()) return;
    var script = document.querySelector('script[data-v-chatbot-loader="1"],script[src$="chatbot.js"]');
    if (script) {
      script.addEventListener('load', function () { if (!tryOpen()) toast('Assistant is unavailable on this page.', 'warn'); }, { once: true });
      setTimeout(function () { tryOpen(); }, 350);
      return;
    }
    script = document.createElement('script');
    script.src = 'chatbot.js'; script.defer = true;
    script.setAttribute('data-v-chatbot-loader', '1');
    script.onload = function () { if (!tryOpen()) toast('Assistant is unavailable on this page.', 'warn'); };
    script.onerror = function () { toast('Assistant could not be loaded.', 'warn'); };
    document.body.appendChild(script);
  }

  var NAV_LEDGER = [
    { label: 'Dashboard', icon: 'layout-dashboard', href: 'dashboard.html', page: 'dashboard' },
    { label: 'Schools', icon: 'school', page: 'schools', children: [
      { label: 'All Schools', href: 'schools.html', page: 'schools' },
      { label: 'Add School', href: 'schools.html?add=1', page: 'schools-add' },
      { label: 'School Analytics', href: 'reports.html#schools', page: 'schools-analytics' }
    ]},
    { label: 'Vendors', icon: 'truck', href: 'vendors.html', page: 'vendors' },
    { label: 'Stock & Inventory', icon: 'boxes', href: 'inventory.html', page: 'inventory' },
    { label: 'Distribution', icon: 'send', href: 'distribution.html', page: 'distribution' },
    { label: 'Transfers', icon: 'repeat', href: 'transfers.html', page: 'transfers' },
    { label: 'Messages', icon: 'message-square', href: 'messages.html', page: 'messages' },
    { label: 'Reports', icon: 'bar-chart-3', href: 'reports.html', page: 'reports' },
    { label: 'Users', icon: 'users', href: 'users.html', page: 'users', admin: true, staff: true },
    { label: 'To-do', icon: 'list-checks', href: 'todo.html', page: 'todo' },
    // Activity Log: Super Admin sees everyone, staff see their own users. Hidden from 'user'.
    { label: 'Activity Log', icon: 'history', href: 'activity.html', page: 'activity', admin: true, staff: true },
    { label: 'Security', icon: 'shield-check', href: 'security.html', page: 'security', superOnly: true },
    { label: 'Settings', icon: 'settings', href: 'settings.html', page: 'settings', superOnly: true }
  ];
  // RBAC: super_admin > admin > staff > user.
  var ROLE_RANK = { user: 0, staff: 1, admin: 2, super_admin: 3 };
  function currentRole() {
    try { return String(localStorage.getItem('v_role') || 'user'); } catch (e) { return 'user'; }
  }
  function roleRank() { return ROLE_RANK[currentRole()] || 0; }
  function isSuperAdmin() { return currentRole() === 'super_admin'; }
  function isStaff() { return currentRole() === 'staff'; }
  // "admin" here means Super Admin or Admin; managers also include Staff.
  function isAdmin() { return roleRank() >= ROLE_RANK.admin; }
  function isManager() { return roleRank() >= ROLE_RANK.staff; }

  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }

  function icon(name, cls) { return '<i data-lucide="' + name + '"' + (cls ? ' class="' + cls + '"' : '') + '></i>'; }


  // ---------------------------------------------------------------------------
  // Orbital navigation
  // ---------------------------------------------------------------------------
  var ORB_KEY = 'v_orb_pos';
  function orbScale() { return window.innerWidth < 640 ? 0.66 : (window.innerWidth < 900 ? 0.82 : 1); }

  function orbNavItems() {
    var sup = isSuperAdmin(), admin = isAdmin(), manager = isManager();
    var items = [];
    NAV_LEDGER.forEach(function (item) {
      if (item.superOnly && !sup) return;
      if (item.admin && !admin && !(item.staff && manager)) return;
      items.push({ label: item.label, icon: item.icon, href: item.href || (item.children && item.children[0].href), page: item.page });
    });
    items.push({ label: 'Assistant', icon: 'message-circle', href: '#assistant', page: '__chat' });
    return items;
  }

  // Longest continuous span of directions in which a satellite placed at
  // `radius` still lands fully on screen. Using one shared arc (instead of
  // shrinking single buttons) keeps the constellation perfectly circular no
  // matter which corner or edge the orb is parked on.
  function orbArc(x, y, radius) {
    var w = window.innerWidth, h = window.innerHeight, pad = 46;
    var step = 5, n = 360 / step, ok = [], all = true, i;
    for (i = 0; i < n; i++) {
      var a = i * step * Math.PI / 180;
      var ax = x + Math.cos(a) * radius, ay = y + Math.sin(a) * radius;
      var good = ax > pad && ax < w - pad && ay > pad && ay < h - pad;
      ok.push(good); if (!good) all = false;
    }
    if (all) return { center: -90, span: 360, full: true };
    var bestStart = -1, bestLen = 0, cur = 0, curStart = 0;
    for (i = 0; i < n * 2; i++) {
      var idx = i % n;
      if (ok[idx]) {
        if (cur === 0) curStart = idx;
        cur++;
        if (cur > bestLen && cur <= n) { bestLen = cur; bestStart = curStart; }
      } else cur = 0;
    }
    if (bestLen < 2) return { center: -90, span: 0, full: false, none: true };
    var span = (bestLen - 1) * step;
    return { center: bestStart * step + span / 2, span: span, full: false };
  }

  function buildOrbItems(active, x, y) {
    var items = orbNavItems();
    var vw = window.innerWidth, vh = window.innerHeight;
    var ox0 = typeof x === 'number' ? x : vw / 2;
    var oy0 = typeof y === 'number' ? y : vh / 2;
    var sc = orbScale();
    var base = Math.round(112 * sc), step = Math.round(72 * sc);
    var maxR = Math.round(Math.max(vw, vh) * 0.42);
    var gap = Math.round(34 * sc);

    // Rings grow outward until every link has a comfortable slot. Each ring
    // keeps one uniform radius, so the constellation stays a clean circle or
    // fan instead of twisting when the orb sits in a corner.
    var rings = [], left = items.length, k = 0;
    while (left > 0 && k < 6) {
      var r = Math.min(maxR, base + k * step);
      var arc = orbArc(ox0, oy0, r);
      if (arc.none) { k++; continue; }
      var usable = arc.full ? 360 : Math.max(0, arc.span - 14);
      var minStep = 2 * Math.asin(Math.min(0.99, gap / r)) * 180 / Math.PI;
      var cap = arc.full ? Math.floor(360 / minStep) : Math.floor(usable / minStep) + 1;
      cap = Math.max(1, cap);
      var take = Math.min(cap, left);
      rings.push({ r: r, arc: arc, usable: usable, cap: cap, n: take });
      left -= take; k++;
    }
    // Spread the links across the rings we ended up with so the last ring is
    // never left with a single lonely satellite.
    if (rings.length > 1) {
      var totalCap = 0;
      rings.forEach(function (rg) { totalCap += rg.cap; });
      var assigned = 0;
      rings.forEach(function (rg, i) {
        var want = i === rings.length - 1
          ? items.length - assigned
          : Math.max(1, Math.min(rg.cap, Math.round(items.length * rg.cap / totalCap)));
        rg.n = Math.max(0, want); assigned += rg.n;
      });
    }
    if (!rings.length) rings.push({ r: base, arc: { center: -90, span: 360, full: true }, usable: 360, n: items.length });
    if (left > 0) rings[rings.length - 1].n += left;

    var html = '', at = 0;
    rings.forEach(function (ring, ri) {
      var list = items.slice(at, at + ring.n); at += ring.n;
      var n = list.length, arc = ring.arc;
      list.forEach(function (it, i) {
        var deg;
        if (arc.full) deg = arc.center + (360 / n) * i + (ri % 2 ? (360 / n) / 2 : 0);
        else if (n === 1) deg = arc.center;
        else deg = arc.center - ring.usable / 2 + (ring.usable / (n - 1)) * i;
        var a = deg * Math.PI / 180;
        var xx = Math.round(Math.cos(a) * ring.r);
        var yy = Math.round(Math.sin(a) * ring.r);
        var cx = ox0 + xx, cy = oy0 + yy;
        var cls = '';
        if (cx < 110) cls += ' lab-start';
        else if (cx > vw - 110) cls += ' lab-end';
        if (cy > vh - 92) cls += ' lab-top';
        html += '<a class="v-orb-item' + cls + (it.page === active ? ' active' : '') + '" href="' + it.href + '"' +
          (it.page === '__chat' ? ' data-orb-chat="1"' : '') +
          ' style="--x:' + xx + 'px;--y:' + yy + 'px;--d:' + (40 + ri * 70 + i * 30) + 'ms;--dout:' +
          (Math.max(0, (n - i) * 16) + ri * 40) + 'ms;--z:' + (100 - i) + '"' +
          ' title="' + esc(it.label) + '">' +
          icon(it.icon) + '<span class="v-orb-lab">' + esc(it.label) + '</span></a>';
      });
    });
    return html;
  }

  function wireOrb(orb) {
    var core = orb.querySelector('.v-orb-core');
    var ring = orb.querySelector('.v-orb-ring');
    var active = document.body.getAttribute('data-page') || '';
    var margin = 34;

    function clamp(x, y) {
      var w = window.innerWidth, h = window.innerHeight;
      return [Math.max(margin, Math.min(w - margin, x)), Math.max(margin, Math.min(h - margin, y))];
    }
    function relayout(x, y) {
      ring.innerHTML = buildOrbItems(active, x, y);
      redrawIcons();
    }
    function place(x, y, layout) {
      var p = clamp(x, y);
      orb.style.left = p[0] + 'px';
      orb.style.top = p[1] + 'px';
      if (layout) relayout(p[0], p[1]);
      try { localStorage.setItem(ORB_KEY, JSON.stringify({ x: p[0], y: p[1] })); } catch (e) {}
    }

    var saved = null;
    try { saved = JSON.parse(localStorage.getItem(ORB_KEY) || 'null'); } catch (e) {}
    place(saved && saved.x ? saved.x : window.innerWidth - 92,
          saved && saved.y ? saved.y : window.innerHeight - 92, true);
    window.addEventListener('resize', function () {
      place(parseFloat(orb.style.left), parseFloat(orb.style.top), true);
    });

    var dragging = false, moved = false, sx = 0, sy = 0, ox = 0, oy = 0;
    core.addEventListener('pointerdown', function (e) {
      dragging = true; moved = false;
      sx = e.clientX; sy = e.clientY;
      ox = parseFloat(orb.style.left); oy = parseFloat(orb.style.top);
      orb.classList.add('dragging');
      try { core.setPointerCapture(e.pointerId); } catch (err) {}
    });
    core.addEventListener('pointermove', function (e) {
      if (!dragging) return;
      var dx = e.clientX - sx, dy = e.clientY - sy;
      if (!moved && Math.abs(dx) + Math.abs(dy) > 5) moved = true;
      if (moved) place(ox + dx, oy + dy, false);
    });
    function endDrag(e) {
      if (!dragging) return;
      dragging = false;
      orb.classList.remove('dragging');
      if (moved) {
        relayout(parseFloat(orb.style.left), parseFloat(orb.style.top));
      } else {
        toggleOrb();
      }
      try { core.releasePointerCapture(e.pointerId); } catch (err) {}
    }
    function toggleOrb() {
      if (orb.classList.contains('open')) {
        orb.classList.remove('open');
        orb.classList.add('closing');
        setTimeout(function () { orb.classList.remove('closing'); }, 480);
      } else {
        relayout(parseFloat(orb.style.left), parseFloat(orb.style.top));
        orb.classList.remove('closing');
        orb.classList.add('open');
      }
    }
    orb.vToggle = toggleOrb;
    function closeOrb() {
      if (!orb.classList.contains('open')) return;
      orb.classList.remove('open');
      orb.classList.add('closing');
      setTimeout(function () { orb.classList.remove('closing'); }, 480);
    }
    orb.vClose = closeOrb;

    core.addEventListener('pointerup', endDrag);
    core.addEventListener('pointercancel', endDrag);
    core.addEventListener('click', function (e) { e.preventDefault(); e.stopPropagation(); });

    orb.addEventListener('click', function (e) { e.stopPropagation(); });
    document.addEventListener('click', closeOrb);
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape') closeOrb(); });

    // Touch/hover on a satellite lifts its label above every other button.
    ring.addEventListener('pointerenter', function (e) {
      var it = e.target.closest && e.target.closest('.v-orb-item');
      if (it) it.classList.add('peek');
    }, true);
    ring.addEventListener('pointerleave', function (e) {
      var it = e.target.closest && e.target.closest('.v-orb-item');
      if (it) it.classList.remove('peek');
    }, true);
    ring.addEventListener('pointerdown', function (e) {
      var it = e.target.closest && e.target.closest('.v-orb-item');
      Array.prototype.forEach.call(ring.querySelectorAll('.v-orb-item.peek'), function (o) {
        if (o !== it) o.classList.remove('peek');
      });
      if (it) it.classList.add('peek');
    }, true);

    orb.addEventListener('click', function (e) {
      var chat = e.target.closest && e.target.closest('[data-orb-chat="1"]');
      if (chat) { e.preventDefault(); closeOrb(); openAssistant(); }
    }, true);
  }

  function build() {
    var body = document.body;
    var active = body.getAttribute('data-page') || '';
    var username = 'User';
    try { username = localStorage.getItem('v_username') || 'User'; } catch (e) {}
    var initials = username.slice(0, 2).toUpperCase();
    var photo = '';
    // The photo comes from the server (Settings), so it matches everywhere.
    if (window.VAvatars) photo = VAvatars.mine();
    if (!photo) { try { photo = localStorage.getItem('v_avatar_' + username) || ''; } catch (e) {} }
    var avatarHTML = photo
      ? '<span class="v-avatar"><img src="' + esc(photo) + '" alt="Profile photo"></span>'
      : '<span class="v-avatar">' + esc(initials) + '</span>';

    var old = document.querySelector('header.app-header');
    if (old) old.remove();

    var brandTitle = 'Vedritam School Stock Ledger';

    var header = document.createElement('header');
    header.className = 'v-header';
    header.innerHTML =
      '<div class="v-brand"><img src="logo.png" alt="Vedritam" onerror="this.style.display=\'none\'">' +
      '<b id="vBrandTitle">' + esc(brandTitle) + '</b></div>' +
      '<div class="v-search" id="vSearchWrap">' + icon('search') +
      '<input id="vGlobalSearch" type="search" name="v-global-search" placeholder="Search anything..." aria-label="Search" autocomplete="off" autocorrect="off" autocapitalize="off" spellcheck="false" data-form-type="other" data-lpignore="true" data-1p-ignore value="">' +
      '<div class="v-search-results" id="vSearchResults" role="listbox"></div></div>' +
      '<div class="v-head-actions">' +
        '<button class="v-icon-btn" id="vBell" aria-label="Notifications">' + icon('bell') + '<span class="v-dot"></span></button>' +
        '<button class="v-icon-btn" id="vTheme" aria-label="Toggle theme"><i data-lucide="moon" id="vThemeIcon"></i></button>' +
        '<button class="v-icon-btn" id="vSoundBtn" aria-label="Message and call sounds"><i data-lucide="bell-ring" id="vSoundIcon"></i></button>' +
        '<span id="vPresence" title="You are online" style="display:inline-flex;align-items:center;gap:6px;font-size:12px;font-weight:600;color:#16A34A;padding:4px 10px;border-radius:999px;background:rgba(22,163,74,.12)"><span style="width:8px;height:8px;border-radius:50%;background:#16A34A;box-shadow:0 0 0 3px rgba(22,163,74,.2)"></span>Online</span>' +
        '<button class="v-user" id="vUserBtn">' + avatarHTML +
        '<span id="navUsername">' + esc(username) + '</span>' + icon('chevron-down') + '</button>' +
      '</div>';

    var menu = document.createElement('div');
    menu.className = 'v-menu'; menu.id = 'vUserMenu';
    menu.innerHTML = '<a href="settings.html">' + icon('settings') + 'Settings</a>' +
      '<a href="reports.html">' + icon('bar-chart-3') + 'Reports</a><hr>' +
      '<button type="button" id="vLogout">' + icon('log-out') + 'Log out</button>';

    var notifications = document.createElement('div');
    notifications.className = 'v-notifications'; notifications.id = 'vNotifications';
    notifications.innerHTML = '<div class="v-notifications-head"><strong>Notifications</strong>' +
      '<button type="button" id="vNotesReadAll" style="margin-left:auto;background:none;border:0;color:var(--v-primary);font:inherit;font-size:12px;font-weight:650;cursor:pointer">Mark all read</button>' +
      '<button type="button" id="vNotificationsClose" aria-label="Close notifications">' + icon('x') + '</button></div>' +
      '<div class="v-notes-tabs" id="vNotesTabs">' +
        ['all','message','report','announcement','alert'].map(function (t) {
          return '<button type="button" class="v-notes-tab' + (t === 'all' ? ' active' : '') + '" data-nt="' + t + '">' +
            (t === 'all' ? 'All' : t.charAt(0).toUpperCase() + t.slice(1) + 's') + '</button>';
        }).join('') +
      '</div>' +
      '<div class="v-notifications-body" id="vNotificationsBody"><div class="v-note">Loading...</div></div>';

    var side = null;
    var scrim = null;

    // --- Orbital navigation: draggable orb replaces the old side panel -------
    var oldOrb = document.getElementById('vOrb');
    if (oldOrb) oldOrb.remove();

    var orb = document.createElement('div');
    orb.className = 'v-orb'; orb.id = 'vOrb';
    orb.innerHTML =
      '<div class="v-orb-veil"></div>' +
      '<div class="v-orb-ring" id="vOrbRing"></div>' +
      '<div class="v-orb-halo"></div>' +
      '<button class="v-orb-core" id="vOrbCore" type="button" aria-label="Open navigation">' +
        '<span class="v-orb-plus">' + icon('plus') + '</span>' +
        '<span class="v-orb-hint">drag me</span>' +
      '</button>';

    body.insertBefore(header, body.firstChild);
    body.insertBefore(menu, header.nextSibling);
    body.insertBefore(notifications, menu.nextSibling);
    body.appendChild(orb);
    body.classList.add('v-orbnav');

    var main = document.querySelector('main.main-content') || document.querySelector('.main-content');
    if (main) main.classList.add('v-main');

    wire();
    wireOrb(orb);
    redrawIcons();
  }

  function wire() {

    // Message chime / ringtone on-off switch, remembered per device.
    var soundBtn = document.getElementById('vSoundBtn');
    function paintSound() {
      var on = !window.VSound || VSound.enabled();
      var i = document.getElementById('vSoundIcon');
      if (i) { i.setAttribute('data-lucide', on ? 'bell-ring' : 'bell-off'); redrawIcons(); }
      if (soundBtn) soundBtn.title = on ? 'Sounds on \u2014 click to mute' : 'Sounds muted \u2014 click to unmute';
    }
    if (soundBtn) {
      paintSound();
      soundBtn.addEventListener('click', function () {
        if (!window.VSound) return;
        var next = !VSound.enabled();
        VSound.setEnabled(next);
        paintSound();
        if (next) VSound.chime();
      });
    }

    document.getElementById('vTheme').addEventListener('click', function () {
      setTheme(currentTheme() === 'dark' ? 'light' : 'dark');
      document.dispatchEvent(new CustomEvent('v-theme-change', { detail: currentTheme() }));
    });

    var userBtn = document.getElementById('vUserBtn');
    var menu = document.getElementById('vUserMenu');
    var notifications = document.getElementById('vNotifications');
    userBtn.addEventListener('click', function (e) {
      e.preventDefault(); e.stopPropagation();
      if (notifications) notifications.classList.remove('open');
      menu.classList.toggle('open');
    });
    menu.addEventListener('click', function (e) { e.stopPropagation(); });
    document.addEventListener('click', function () {
      menu.classList.remove('open');
      if (notifications) notifications.classList.remove('open');
    });

    var logoutBtn = document.getElementById('vLogout');
    if (logoutBtn) logoutBtn.addEventListener('click', function (e) {
      e.preventDefault(); e.stopPropagation(); window.vSignOut();
    });
    // Any click inside the logout row signs out.
    menu.addEventListener('click', function (e) {
      var t = e.target.closest && e.target.closest('#vLogout');
      if (t) { e.preventDefault(); e.stopPropagation(); window.vSignOut(); }
    });


    Array.prototype.forEach.call(document.querySelectorAll('.logout-btn'), function (b) {
      b.onclick = null;
      b.addEventListener('click', function (ev) { ev.preventDefault(); window.vSignOut(); });
    });

    var bell = document.getElementById('vBell');
    bell.addEventListener('click', function (e) {
      e.preventDefault(); e.stopPropagation();
      menu.classList.remove('open');
      if (notifications) notifications.classList.toggle('open');
    });
    if (notifications) {
      notifications.addEventListener('click', function (e) { e.stopPropagation(); });
      var closeNotes = document.getElementById('vNotificationsClose');
      if (closeNotes) closeNotes.addEventListener('click', function (e) {
        e.preventDefault(); e.stopPropagation(); notifications.classList.remove('open');
      });
    }

    var noteFilter = 'all';
    refreshNotifications();
    setInterval(refreshNotifications, 45000);

    Array.prototype.forEach.call(document.querySelectorAll('.v-notes-tab'), function (t) {
      t.addEventListener('click', function (e) {
        e.stopPropagation();
        noteFilter = t.getAttribute('data-nt');
        Array.prototype.forEach.call(document.querySelectorAll('.v-notes-tab'), function (o) {
          o.classList.toggle('active', o === t);
        });
        refreshNotifications();
      });
    });

    var readAll = document.getElementById('vNotesReadAll');
    if (readAll) readAll.addEventListener('click', function (e) {
      e.preventDefault(); e.stopPropagation();
      if (!(window.App && App.apiCall)) return;
      App.apiCall('/notifications/read-all', 'POST').then(refreshNotifications).catch(function () {});
    });

    // A quieter note for non-message notifications, on the same "only when it
    // grows" rule as the message chime.
    var lastNotes = null;
    function notifySound(unread) {
      var n = Number(unread) || 0;
      if (lastNotes !== null && n > lastNotes && window.VSound) VSound.notify();
      lastNotes = n;
    }

    // Notification Center: messages, reports, announcements and alerts.
    function refreshNotifications() {
      var body = document.getElementById('vNotificationsBody');
      var dot = document.querySelector('#vBell .v-dot');
      if (!body) return;
      if (!(window.App && App.apiCall)) { body.innerHTML = '<div class="v-note">You are all caught up.</div>'; return; }
      var path = '/notifications?limit=25' + (noteFilter === 'all' ? '' : '&type=' + noteFilter);
      App.apiCall(path).then(function (r) {
        var items = r.items || [];
        if (dot) dot.style.display = r.unread > 0 ? '' : 'none';
        notifySound(r.unread);
        if (!items.length) {
          body.innerHTML = '<div class="v-note"><strong>No notifications</strong><span>You are all caught up.</span></div>';
          return;
        }
        body.innerHTML = items.map(function (n) {
          var cls = n.type === 'alert' ? ' warn' : '';
          return '<div class="v-note' + cls + (n.read === '1' ? ' read' : '') + '" data-nid="' + esc(n.id) + '"' +
            (n.link ? ' data-link="' + esc(n.link) + '"' : '') + '>' +
            '<strong>' + esc(n.title || 'Notification') + '</strong>' +
            '<span>' + esc(n.body || '') + '</span>' +
            '<span style="opacity:.7;font-size:11px">' + esc(n.timestamp) + ' · ' + esc(n.type) + '</span></div>';
        }).join('');
        Array.prototype.forEach.call(body.querySelectorAll('[data-nid]'), function (d) {
          d.addEventListener('click', function () {
            App.apiCall('/notifications/' + d.getAttribute('data-nid') + '/read', 'POST')
              .then(function () {
                var link = d.getAttribute('data-link');
                if (link) window.location.href = link; else refreshNotifications();
              }).catch(function () {});
          });
        });
      }).catch(function () { body.innerHTML = '<div class="v-note">Could not load notifications.</div>'; });
    }
    window.vRefreshNotifications = refreshNotifications;

    // Unread message badge on the sidebar entry.
    function refreshMessageBadge() {
      if (!(window.App && App.apiCall)) return;
      App.apiCall('/messaging/unread').then(function (r) {
        chimeOnNewMessages(r.total);
        var link = document.querySelector('.v-orb-ring a[href="messages.html"]') || document.querySelector('.v-nav a[href="messages.html"]');
        if (!link) return;
        var badge = link.querySelector('.v-nav-badge');
        if (!r.total) { if (badge) badge.remove(); return; }
        if (!badge) {
          badge = document.createElement('span');
          badge.className = 'v-nav-badge';
          link.appendChild(badge);
        }
        badge.textContent = r.total > 99 ? '99+' : String(r.total);
      }).catch(function () {});
    }

    /* A soft chime whenever the unread count actually grows. The first read
       after a page load only records the baseline, so refreshing the page
       never sets the sound off. */
    var lastUnread = null;
    function chimeOnNewMessages(total) {
      var n = Number(total) || 0;
      if (lastUnread !== null && n > lastUnread && window.VSound) VSound.chime();
      lastUnread = n;
    }
    window.vRefreshMessageBadge = refreshMessageBadge;
    refreshMessageBadge();
    setInterval(refreshMessageBadge, 10000);

    document.addEventListener('click', function (e) {
      var a = e.target.closest && e.target.closest('[data-soon="1"]');
      if (a) { e.preventDefault(); toast('This module is not built yet — coming in a later release.', 'warn'); }
    });

    Array.prototype.forEach.call(document.querySelectorAll('.v-group-btn'), function (b) {
      b.addEventListener('click', function () {
        if (document.body.classList.contains('v-collapsed')) {
          document.body.classList.remove('v-collapsed');
          b.parentElement.classList.add('open');
          return;
        }
        b.parentElement.classList.toggle('open');
      });
    });


    wireGlobalSearch();
  }


  // --- Global search ---------------------------------------------------------
  // Searches everything in the app (pages, schools, classes, users, vendors,
  // catalog, ledger, distributions, transfers, activity) through /api/v1/search
  // and shows a results dropdown. Says "No results found" when nothing matches.
  function apiBase() {
    try {
      var override = (localStorage.getItem('v_api_base') || '').replace(/\/+$/, '');
      if (override) return override + '/api/v1';
    } catch (e) {}
    if (location.protocol === 'http:' || location.protocol === 'https:') return '/api/v1';
    return 'http://127.0.0.1:8000/api/v1';
  }

  function wireGlobalSearch() {
    var search = document.getElementById('vGlobalSearch');
    var panel = document.getElementById('vSearchResults');
    if (!search || !panel) return;

    // Browsers sometimes autofill this box with a saved username (e.g. "admin").
    // Clear anything that was not typed by the user.
    search.value = '';
    setTimeout(function () { if (!search.matches(':focus')) search.value = ''; }, 300);

    var timer = null;
    var lastQuery = '';
    var items = [];
    var cursor = -1;

    function close() { panel.classList.remove('open'); cursor = -1; }
    function open() { panel.classList.add('open'); }

    function render(html) { panel.innerHTML = html; open(); redrawIcons(); }

    function highlight() {
      var nodes = panel.querySelectorAll('.v-sr-item');
      Array.prototype.forEach.call(nodes, function (n, i) {
        n.classList.toggle('active', i === cursor);
        if (i === cursor && n.scrollIntoView) n.scrollIntoView({ block: 'nearest' });
      });
    }

    function go(i) {
      var hit = items[i];
      if (!hit) return;
      close();
      var onSchools = document.body.getAttribute('data-page') === 'schools';
      if (hit.type === 'School' && onSchools && window.App && App.schools &&
          typeof App.schools.applySearch === 'function') {
        search.value = hit.title;
        App.schools.applySearch(hit.title);
        return;
      }
      window.location.href = hit.url;
    }

    function draw(data) {
      items = (data && data.results) || [];
      if (!items.length) {
        render('<div class="v-sr-empty">' + icon('search-x') +
               '<div><strong>No results found</strong><div class="v-sr-sub">' +
               'Nothing in the app matches &ldquo;' + esc(data && data.query ? data.query : lastQuery) +
               '&rdquo;.</div></div></div>');
        return;
      }
      var groups = [];
      var seen = {};
      items.forEach(function (r, i) {
        if (!seen[r.type]) { seen[r.type] = true; groups.push(r.type); }
        r._i = i;
      });
      var html = '<div class="v-sr-head">' + items.length + ' of ' + (data.count || items.length) +
                 ' result' + ((data.count || items.length) === 1 ? '' : 's') + '</div>';
      groups.forEach(function (g) {
        html += '<div class="v-sr-group">' + esc(g) + '</div>';
        items.forEach(function (r) {
          if (r.type !== g) return;
          html += '<button type="button" class="v-sr-item" data-i="' + r._i + '">' +
                  '<span class="v-sr-title">' + esc(r.title || '(untitled)') + '</span>' +
                  '<span class="v-sr-sub">' + esc(r.subtitle || '') + '</span></button>';
        });
      });
      render(html);
    }

    function run(q) {
      lastQuery = q;
      var token = '';
      try { token = localStorage.getItem('v_token') || ''; } catch (e) {}
      render('<div class="v-sr-empty"><div><strong>Searching&hellip;</strong></div></div>');
      fetch(apiBase() + '/search?q=' + encodeURIComponent(q) + '&limit=40', {
        headers: token ? { Authorization: 'Bearer ' + token } : {}
      }).then(function (res) {
        if (res.status === 401 || res.status === 403) {
          render('<div class="v-sr-empty"><div><strong>Sign in to search</strong>' +
                 '<div class="v-sr-sub">Your session expired.</div></div></div>');
          return null;
        }
        if (!res.ok) throw new Error('search failed');
        return res.json();
      }).then(function (data) {
        if (!data) return;
        if (lastQuery !== q) return;   // a newer query is in flight
        draw(data);
      }).catch(function () {
        render('<div class="v-sr-empty"><div><strong>Search unavailable</strong>' +
               '<div class="v-sr-sub">Cannot reach the Vedritam server right now.</div></div></div>');
      });
    }

    search.addEventListener('input', function () {
      var q = search.value.trim();
      if (timer) clearTimeout(timer);
      if (!q) { close(); panel.innerHTML = ''; return; }
      timer = setTimeout(function () { run(q); }, 220);
    });

    search.addEventListener('focus', function () {
      if (search.value.trim() && panel.innerHTML) open();
    });

    search.addEventListener('keydown', function (e) {
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        if (!items.length) return;
        e.preventDefault();
        cursor += (e.key === 'ArrowDown' ? 1 : -1);
        if (cursor < 0) cursor = items.length - 1;
        if (cursor >= items.length) cursor = 0;
        highlight();
        return;
      }
      if (e.key === 'Escape') { close(); return; }
      if (e.key !== 'Enter') return;
      e.preventDefault();
      if (cursor >= 0) { go(cursor); return; }
      var q = search.value.trim();
      if (!q) return;
      if (timer) clearTimeout(timer);
      run(q);
    });

    panel.addEventListener('click', function (e) {
      e.stopPropagation();
      var btn = e.target.closest && e.target.closest('.v-sr-item');
      if (btn) go(parseInt(btn.getAttribute('data-i'), 10));
    });
    panel.addEventListener('mousedown', function (e) { e.preventDefault(); });
    document.addEventListener('click', function (e) {
      if (!e.target.closest || !e.target.closest('#vSearchWrap')) close();
    });

    // Keyboard shortcut: "/" or Ctrl/Cmd+K focuses the search box.
    document.addEventListener('keydown', function (e) {
      var tag = (e.target && e.target.tagName || '').toLowerCase();
      var typing = tag === 'input' || tag === 'textarea' || (e.target && e.target.isContentEditable);
      if ((e.key === 'k' && (e.metaKey || e.ctrlKey)) || (e.key === '/' && !typing)) {
        e.preventDefault(); search.focus(); search.select();
      }
    });

    // Prefill from ?q= so a link into a page keeps the query visible.
    try {
      var qp = new URLSearchParams(window.location.search).get('q');
      if (qp) search.value = qp;
    } catch (e) {}
  }

  function toast(msg, type) {
    if (window.App && App.ui && App.ui.showToast) { App.ui.showToast(msg, type === 'warn' ? 'error' : 'success'); return; }
    var t = document.createElement('div');
    t.textContent = msg;
    t.style.cssText = 'position:fixed;bottom:24px;left:50%;transform:translateX(-50%);z-index:200;' +
      'background:#1E293B;color:#fff;padding:12px 18px;border-radius:12px;font:500 14px Inter,sans-serif;' +
      'box-shadow:0 12px 32px -12px rgba(0,0,0,.5)';
    document.body.appendChild(t);
    setTimeout(function () { t.style.opacity = '0'; t.style.transition = 'opacity .3s'; setTimeout(function () { t.remove(); }, 320); }, 3200);
  }

  window.VShell = { logout: window.vSignOut, openAssistant: openAssistant, setTheme: setTheme,
                    theme: currentTheme,
                    toast: toast, refreshIcons: redrawIcons };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', build);
  else build();
})();

/* Re-paint the header avatar as soon as the server photo arrives or changes. */
document.addEventListener('v-avatar-changed', function () {
  var btn = document.getElementById('vUserBtn');
  if (!btn || !window.VAvatars) return;
  var name = '';
  try { name = localStorage.getItem('v_username') || 'User'; } catch (e) {}
  var photo = VAvatars.mine();
  var av = btn.querySelector('.v-avatar');
  if (!av) return;
  av.innerHTML = photo
    ? '<img src="' + photo + '" alt="Profile photo">'
    : name.slice(0, 2).toUpperCase();
});
document.addEventListener('DOMContentLoaded', function () {
  if (window.VAvatars) VAvatars.boot();
});
