/* dashboard-ui.js - renders KPIs, charts and activity for dashboard.html
   All figures are live: they come from GET /api/v1/dashboard, which computes
   them from the real schools, ledger and audit-trail data. */
(function () {
  var EMPTY = {
    kpis: { books: { value: 0, delta: '\u2013' }, schools: { value: 0, delta: '\u2013' },
            balance: { value: 0, delta: '\u2013' }, lowStock: { value: 0, delta: '\u2013' } },
    monthly: { labels: [], issued: [], received: [] },
    comparison: [], activity: [], schools: []
  };

  function api() {
    if (window.App && window.App.apiCall) return window.App.apiCall;
    if (typeof App !== 'undefined' && App && App.apiCall) return App.apiCall;
    return null;
  }

  function currentFilters() {
    function val(id) { var el = document.getElementById(id); return el ? String(el.value || '').trim() : ''; }
    return { school_id: val('fltSchool'), date_from: val('fltFrom'), date_to: val('fltTo'), staff: val('fltStaff') };
  }

  function query(f) {
    var parts = [];
    Object.keys(f).forEach(function (k) { if (f[k]) parts.push(k + '=' + encodeURIComponent(f[k])); });
    return parts.length ? '?' + parts.join('&') : '';
  }

  function loadData() {
    var call = api();
    if (!call) return Promise.resolve(EMPTY);
    return call('/dashboard' + query(currentFilters())).catch(function (e) {
      if (window.App && App.ui) App.ui.showToast(e.message || 'Could not load dashboard data', 'error');
      return EMPTY;
    });
  }


  var fmt = function (n) { return n.toLocaleString('en-IN'); };
  var esc = function (s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); };

  function countUp(el, target) {
    var start = performance.now(), dur = 900;
    function tick(now) {
      var p = Math.min(1, (now - start) / dur);
      var eased = 1 - Math.pow(1 - p, 3);
      el.textContent = fmt(Math.round(target * eased));
      if (p < 1) requestAnimationFrame(tick);
    }
    requestAnimationFrame(tick);
  }

  function renderKPIs(d) {
    var map = { kpiBooks: d.kpis.books, kpiSchools: d.kpis.schools, kpiBalance: d.kpis.balance, kpiLow: d.kpis.lowStock };
    Object.keys(map).forEach(function (id) {
      var el = document.getElementById(id);
      if (!el) return;
      countUp(el.querySelector('.val'), map[id].value);
      el.querySelector('.delta span').textContent = map[id].delta;
    });
  }

  function renderActivity(d) {
    var ul = document.getElementById('activityList');
    if (!ul) return;
    // Ordinary user accounts do not see the Recent Activity feed at all.
    var card = document.getElementById('activityCard');
    var hidden = d.showActivity === false || role() === 'user';
    if (card) card.style.display = hidden ? 'none' : '';
    var items = (hidden || !d.activity) ? [] : d.activity;
    if (!items.length) { ul.innerHTML = '<li><div><div class="txt">No activity recorded yet</div><div class="meta">Actions appear here as stock is added, issued or edited</div></div></li>'; return; }
    ul.innerHTML = items.map(function (a) {
      return '<li><span class="pip ' + a.tone + '"></span><div><div class="txt">' +
        esc(a.text) + '</div><div class="meta">' + esc(a.meta) + '</div></div></li>';
    }).join('');
  }

  function renderBars(d) {
    var wrap = document.getElementById('schoolBars');
    if (!wrap) return;
    if (!d.comparison.length) { wrap.innerHTML = '<div class="meta">No issued-stock data yet.</div>'; return; }
    var max = Math.max.apply(null, d.comparison.map(function (s) { return s.value; })) || 1;
    wrap.innerHTML = d.comparison.map(function (s) {
      return '<div class="v-bar-row"><div class="t"><span>' + esc(s.name) + '</span><span>' +
        fmt(s.value) + '</span></div><div class="v-track"><div class="v-fill" data-w="' +
        Math.round((s.value / max) * 100) + '"></div></div></div>';
    }).join('');
    requestAnimationFrame(function () {
      Array.prototype.forEach.call(wrap.querySelectorAll('.v-fill'), function (f) {
        f.style.width = f.getAttribute('data-w') + '%';
      });
    });
  }

  function renderSchools(d) {
    var wrap = document.getElementById('schoolCards');
    if (!wrap) return;
    if (!d.schools.length) { wrap.innerHTML = '<div class="v-card"><h4>No schools yet</h4><div class="code">Add a DAV branch from the Schools page to see its figures here.</div><a class="v-btn" href="schools.html">Add school</a></div>'; return; }
    wrap.innerHTML = d.schools.map(function (s) {
      return '<div class="v-school"><h4>' + esc(s.name) + '</h4>' +
        '<div class="code">' + esc(s.code || '') + '</div>' +
        '<div class="stats">' +
          '<div><div class="n">' + fmt(s.students) + '</div><div class="k">Students</div></div>' +
          '<div><div class="n">' + fmt(s.issued) + '</div><div class="k">Issued</div></div>' +
          '<div><div class="n">' + fmt(s.balance) + '</div><div class="k">Balance</div></div>' +
        '</div>' +
        '<a class="v-btn" href="schools.html">Open Ledger</a></div>';
    }).join('');
  }

  var chart = null;
  function chartColors() {
    var dark = document.documentElement.getAttribute('data-theme') === 'dark';
    return { grid: dark ? 'rgba(148,163,184,.16)' : 'rgba(15,23,42,.08)', tick: dark ? '#94A3B8' : '#64748B' };
  }

  function renderChart(d) {
    var cvs = document.getElementById('trendChart');
    if (!cvs || !window.Chart) return;
    var c = chartColors();
    var ctx = cvs.getContext('2d');
    var g = ctx.createLinearGradient(0, 0, 0, 260);
    g.addColorStop(0, 'rgba(37,99,235,.35)');
    g.addColorStop(1, 'rgba(37,99,235,0)');
    if (chart) { chart.destroy(); chart = null; }
    // Guard against a stray Chart instance left bound to this canvas, which is
    // what produced the flickering / half-drawn graph on repeated refreshes.
    var stray = Chart.getChart && Chart.getChart(cvs);
    if (stray) stray.destroy();
    var monthly = (d && d.monthly) || { labels: [], issued: [], received: [] };
    chart = new Chart(ctx, {
      type: 'line',
      data: {
        labels: monthly.labels || [],
        datasets: [
          { label: 'Books issued', data: monthly.issued || [], borderColor: '#2563EB', backgroundColor: g,
            fill: true, tension: .4, borderWidth: 2.5, pointRadius: 3, pointBackgroundColor: '#2563EB' },
          { label: 'Books received', data: monthly.received || [], borderColor: '#16A34A', backgroundColor: 'transparent',
            tension: .4, borderWidth: 2.5, pointRadius: 3, borderDash: [5, 4], pointBackgroundColor: '#16A34A' }
        ]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        animation: false, resizeDelay: 120,
        layout: { padding: { left: 6, right: 6, top: 4 } },
        plugins: {
          legend: { labels: { color: c.tick, usePointStyle: true, boxWidth: 8, font: { family: 'Inter' } } },
          tooltip: { callbacks: { label: function (i) { return i.dataset.label + ': ' + i.formattedValue + ' books'; } } }
        },
        scales: {
          x: { title: { display: true, text: 'Month', color: c.tick }, grid: { display: false },
               ticks: { color: c.tick, font: { family: 'Inter' } } },
          y: { beginAtZero: true, title: { display: true, text: 'Number of books', color: c.tick },
               grid: { color: c.grid }, border: { display: false },
               ticks: { color: c.tick, precision: 0, font: { family: 'Inter' } } }
        }
      }
    });
  }

  function role() { try { return localStorage.getItem('v_role') || ''; } catch (e) { return ''; } }
  // Super Admin and Admin share the platform-wide dashboard view.
  function isAdmin() { var r = role(); return r === 'super_admin' || r === 'admin'; }

  function renderPresence(d) {
    var card = document.getElementById('presenceCard');
    var inner = document.getElementById('presenceCardInner');
    if (!card || !inner) return;
    card.style.display = '';
    if (isAdmin()) {
      var count = (d.presence && d.presence.count) || 0;
      var names = (d.presence && d.presence.usernames) || [];
      var chips = names.slice(0, 20).map(function (n) {
        return '<span style="display:inline-flex;align-items:center;gap:6px;padding:4px 10px;border-radius:999px;background:rgba(22,163,74,.15);color:#15803D;font-size:12px;font-weight:600;margin:2px"><span style="width:8px;height:8px;border-radius:50%;background:#16A34A"></span>' + esc(n) + '</span>';
      }).join('');
      inner.innerHTML = '<h3 style="margin-top:0">Active users <span style="font-size:13px;color:var(--text-muted);font-weight:500">(right now)</span></h3>' +
        '<div style="font-size:28px;font-weight:700;color:#15803D">' + count + ' online</div>' +
        '<div style="margin-top:10px">' + (chips || '<span style="color:var(--text-muted);font-size:13px">No one else is active in the last 2 minutes.</span>') + '</div>';
    } else {
      var mine = d.mySchoolsAdded || 0;
      var yourName = (d.me && d.me.username) || 'you';
      inner.innerHTML = '<h3 style="margin-top:0">Your contributions</h3>' +
        '<div style="display:flex;gap:26px;flex-wrap:wrap;margin-top:6px">' +
          '<div><div style="font-size:28px;font-weight:700;color:var(--v-primary,#2563EB)">' + mine + '</div><div style="font-size:12.5px;color:var(--text-muted)">School' + (mine===1?'':'s') + ' you added</div></div>' +
          '<div><div style="font-size:15px;font-weight:600">' + esc(yourName) + '</div><div style="font-size:12.5px;color:var(--text-muted)">Signed in as</div></div>' +
        '</div>';
    }
  }

  function applyRoleVisibility() {
    var admin = isAdmin();
    Array.prototype.forEach.call(document.querySelectorAll('.admin-only'), function (el) {
      el.style.display = admin ? '' : 'none';
    });
  }

  var latest = EMPTY;

  function renderAll(d) {
    latest = d;
    renderKPIs(d);
    renderPresence(d);
    renderTotals(d);
    renderEmptyNotice(d);
    // Charts, activity and school cards are part of the common workspace.
    renderActivity(d); renderBars(d); renderSchools(d); renderChart(d);
    if (window.VShell) VShell.refreshIcons();
  }

  function renderTotals(d) {
    var el = document.getElementById('dashTotals');
    if (!el || !d.totals) return;
    var t = d.totals;
    var items = [['Schools', t.schools], ['Classes', t.classes], ['Students', t.students],
                 ['Book records', t.records], ['Users', t.users], ['Staff', t.staff],
                 ['Distributed', t.distributed], ['Returned', t.returned]];
    el.innerHTML = items.map(function (i) {
      return '<div style="min-width:104px"><div style="font-size:20px;font-weight:700">' + fmt(i[1] || 0) +
        '</div><div style="font-size:12px;color:var(--text-muted)">' + i[0] + '</div></div>';
    }).join('');
  }

  function todayISO() { return new Date().toISOString().slice(0, 10); }

  function warn(msg) {
    var el = document.getElementById('dashFilterWarn');
    if (!el) return;
    el.textContent = msg || '';
    el.style.display = msg ? '' : 'none';
  }

  function validRange() {
    var f = currentFilters(), today = todayISO();
    if (f.date_from && f.date_from > today) { warn('The "From" date cannot be in the future.'); return false; }
    if (f.date_to && f.date_to > today) { warn('The "To" date cannot be in the future.'); return false; }
    if (f.date_from && f.date_to && f.date_from > f.date_to) {
      warn('The "From" date must be on or before the "To" date.'); return false;
    }
    warn('');
    return true;
  }

  function renderEmptyNotice(d) {
    var el = document.getElementById('dashEmptyNotice');
    if (!el) return;
    var t = d.totals || {};
    var f = (d.filters) || {};
    if (d.emptyRange || t.records === 0) {
      el.textContent = (f.date_from || f.date_to)
        ? 'No records between ' + (f.date_from || 'the beginning') + ' and ' + (f.date_to || 'today') + '. All figures below are zero.'
        : 'No records match these filters. All figures below are zero.';
      el.style.display = '';
    } else {
      el.style.display = 'none';
    }
  }

  // Serialised refresh: overlapping loads (filters + focus + interval + the
  // v-data-changed event) were what made the chart flicker or come up blank.
  var busy = false, queued = false, lastAt = 0;
  function refresh() {
    if (!validRange()) return Promise.resolve();
    if (busy) { queued = true; return Promise.resolve(); }
    busy = true;
    return loadData().then(function (d) { lastAt = Date.now(); renderAll(d); })
      .catch(function (e) { console.error(e); })
      .then(function () {
        busy = false;
        if (queued) { queued = false; setTimeout(refresh, 60); }
      });
  }

  function fillSchoolOptions() {
    var call = api();
    var sel = document.getElementById('fltSchool');
    if (!call || !sel) return Promise.resolve();
    return call('/schools').then(function (list) {
      sel.innerHTML = '<option value="">All Schools</option>' + (list || []).map(function (s) {
        return '<option value="' + s.id + '">' + esc(s.name) + '</option>';
      }).join('');
    }).catch(function () {});
  }

  function init() {
    applyRoleVisibility();
    var bar = document.getElementById('dashFilters');
    if (bar && !isAdmin()) bar.style.display = 'none';
    // Filters apply automatically as they change — no Apply button.
    var timer = null;
    function debounced() { clearTimeout(timer); timer = setTimeout(refresh, 350); }
    ['fltFrom', 'fltTo'].forEach(function (id) {
      var el = document.getElementById(id);
      if (!el) return;
      el.max = todayISO();
      el.addEventListener('change', debounced);
      el.addEventListener('input', debounced);
    });
    ['fltSchool', 'fltStaff'].forEach(function (id) {
      var el = document.getElementById(id);
      if (el) el.addEventListener('change', refresh);
      if (el && el.tagName === 'INPUT') el.addEventListener('input', debounced);
    });
    var reset = document.getElementById('fltReset');
    if (reset) reset.addEventListener('click', function () {
      ['fltSchool', 'fltFrom', 'fltTo', 'fltStaff'].forEach(function (id) {
        var el = document.getElementById(id); if (el) el.value = '';
      });
      warn('');
      refresh();
    });
    var btn = document.getElementById('fltRefresh');
    if (btn) btn.addEventListener('click', refresh);
    document.addEventListener('v-theme-change', function () { renderChart(latest); });
    // Metrics stay current as records are added, edited or deleted elsewhere.
    document.addEventListener('v-data-changed', function () { refresh(); });
    window.addEventListener('focus', function () {
      if (Date.now() - lastAt > 20000) refresh();
    });
    setInterval(refresh, 30000);
    fillSchoolOptions().then(refresh);
  }

  window.VDashboard = { refresh: refresh };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
