/* vmodule.js — shared building blocks for the Vedritam management modules.
   Searchable dropdowns, sticky data tables with a column selector, a details
   drawer, exports (CSV / Excel / PDF-print) and keyboard shortcuts. */
(function () {
  var VM = {};

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  VM.esc = esc;

  VM.toast = function (msg, type) {
    if (window.App && App.ui && App.ui.showToast) return App.ui.showToast(msg, type || 'success');
    var c = document.getElementById('toast-container');
    if (!c) { c = document.createElement('div'); c.id = 'toast-container'; document.body.appendChild(c); }
    var t = document.createElement('div'); t.className = 'toast ' + (type || 'success'); t.textContent = msg;
    c.appendChild(t); setTimeout(function () { t.remove(); }, 3500);
  };

  VM.api = function (endpoint, method, body) {
    if (!(window.App && App.apiCall)) return Promise.reject(new Error('App is not loaded.'));
    return App.apiCall(endpoint, method || 'GET', body || null);
  };

  VM.money = function (v) {
    var n = Number(v || 0);
    return '₹' + n.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  };
  VM.num = function (v) { var n = Number(String(v == null ? 0 : v).replace(/,/g, '')); return isNaN(n) ? 0 : n; };
  VM.today = function () { return new Date().toISOString().slice(0, 10); };

  /* ---------------------------------------------------------------------
     Searchable dropdown — replaces manual typing everywhere.
     VM.select(el, { options:[{value,label,meta}], value, placeholder, onChange })
  --------------------------------------------------------------------- */
  VM.select = function (host, cfg) {
    cfg = cfg || {};
    var options = cfg.options || [];
    var value = cfg.value == null ? '' : String(cfg.value);
    host.classList.add('vm-select');
    host.innerHTML =
      '<input type="text" class="vm-select-input" placeholder="' + esc(cfg.placeholder || 'Search…') + '" autocomplete="off">' +
      '<div class="vm-select-list" hidden></div>';
    var input = host.querySelector('.vm-select-input');
    var list = host.querySelector('.vm-select-list');

    function labelFor(v) {
      for (var i = 0; i < options.length; i++) if (String(options[i].value) === String(v)) return options[i].label;
      return '';
    }
    function render(filter) {
      var q = String(filter || '').toLowerCase();
      var items = options.filter(function (o) {
        return !q || String(o.label).toLowerCase().indexOf(q) >= 0 ||
          String(o.meta || '').toLowerCase().indexOf(q) >= 0;
      }).slice(0, 200);
      list.innerHTML = items.length
        ? items.map(function (o) {
            return '<button type="button" class="vm-option' + (String(o.value) === value ? ' sel' : '') +
              '" data-v="' + esc(o.value) + '">' + esc(o.label) +
              (o.meta ? '<span class="vm-option-meta">' + esc(o.meta) + '</span>' : '') + '</button>';
          }).join('')
        : '<div class="vm-option empty">No matches</div>';
    }
    function open() { render(''); list.hidden = false; }
    function close() { list.hidden = true; input.value = labelFor(value); }

    input.value = labelFor(value);
    input.addEventListener('focus', function () { input.select(); open(); });
    input.addEventListener('input', function () { list.hidden = false; render(input.value); });
    input.addEventListener('keydown', function (e) { if (e.key === 'Escape') close(); });
    list.addEventListener('mousedown', function (e) {
      var btn = e.target.closest('.vm-option[data-v]');
      if (!btn) return;
      e.preventDefault();
      value = btn.getAttribute('data-v');
      close();
      if (cfg.onChange) cfg.onChange(value, options.filter(function (o) { return String(o.value) === value; })[0] || null);
    });
    document.addEventListener('click', function (e) { if (!host.contains(e.target)) close(); });

    return {
      get: function () { return value; },
      set: function (v) { value = v == null ? '' : String(v); input.value = labelFor(value); },
      setOptions: function (opts) { options = opts || []; input.value = labelFor(value); },
      selected: function () {
        return options.filter(function (o) { return String(o.value) === value; })[0] || null;
      }
    };
  };

  /* Plain <select> from a list of values */
  VM.fillSelect = function (el, values, selected, blankLabel) {
    el.innerHTML = (blankLabel ? '<option value="">' + esc(blankLabel) + '</option>' : '') +
      (values || []).map(function (v) {
        var val = (v && v.value !== undefined) ? v.value : v;
        var lab = (v && v.label !== undefined) ? v.label : v;
        return '<option value="' + esc(val) + '"' + (String(val) === String(selected) ? ' selected' : '') + '>' + esc(lab) + '</option>';
      }).join('');
  };

  /* ---------------------------------------------------------------------
     Data table: sticky header, column selector, row click -> details drawer
  --------------------------------------------------------------------- */
  VM.table = function (host, cfg) {
    cfg = cfg || {};
    var columns = cfg.columns || [];
    var rows = cfg.rows || [];
    var storeKey = 'vm_cols_' + (cfg.id || 'table');
    var hidden = {};
    try { hidden = JSON.parse(localStorage.getItem(storeKey) || '{}'); } catch (e) { hidden = {}; }

    host.classList.add('vm-table-host');
    host.innerHTML =
      '<div class="vm-table-bar">' +
      '<input type="search" class="vm-table-search" placeholder="Search…">' +
      '<div class="vm-spacer"></div>' +
      '<div class="vm-colpick"><button type="button" class="vm-btn ghost vm-colbtn">Columns</button>' +
      '<div class="vm-colmenu" hidden></div></div>' +
      '<button type="button" class="vm-btn ghost" data-x="csv">CSV</button>' +
      '<button type="button" class="vm-btn ghost" data-x="xls">Excel</button>' +
      '<button type="button" class="vm-btn ghost" data-x="pdf">PDF</button>' +
      '</div><div class="vm-table-wrap"><table class="vm-table"><thead></thead><tbody></tbody></table></div>';

    var search = host.querySelector('.vm-table-search');
    var thead = host.querySelector('thead');
    var tbody = host.querySelector('tbody');
    var colMenu = host.querySelector('.vm-colmenu');

    function visible() { return columns.filter(function (c) { return !hidden[c.key]; }); }
    function cellValue(row, col) {
      return col.render ? col.render(row[col.key], row) : (row[col.key] == null ? '' : String(row[col.key]));
    }
    function filtered() {
      var q = String(search.value || '').toLowerCase();
      if (!q) return rows;
      return rows.filter(function (r) {
        return columns.some(function (c) {
          return String(r[c.key] == null ? '' : r[c.key]).toLowerCase().indexOf(q) >= 0;
        });
      });
    }
    function draw() {
      var cols = visible();
      thead.innerHTML = '<tr>' + cols.map(function (c) {
        return '<th' + (c.align ? ' style="text-align:' + c.align + '"' : '') + '>' + esc(c.label) + '</th>';
      }).join('') + '</tr>';
      var data = filtered();
      tbody.innerHTML = data.length ? data.map(function (r, i) {
        return '<tr data-i="' + i + '">' + cols.map(function (c) {
          return '<td' + (c.align ? ' style="text-align:' + c.align + '"' : '') + '>' + cellValue(r, c) + '</td>';
        }).join('') + '</tr>';
      }).join('') : '<tr><td colspan="' + cols.length + '" class="vm-empty">No records yet.</td></tr>';
      tbody.querySelectorAll('tr[data-i]').forEach(function (tr) {
        tr.addEventListener('click', function () {
          var row = data[Number(tr.getAttribute('data-i'))];
          if (cfg.onRow) cfg.onRow(row);
          else VM.drawer(cfg.title || 'Details', columns.map(function (c) {
            return { label: c.label, value: cellValue(row, c) };
          }));
        });
      });
      colMenu.innerHTML = columns.map(function (c) {
        return '<label><input type="checkbox" data-k="' + esc(c.key) + '"' + (hidden[c.key] ? '' : ' checked') + '> ' + esc(c.label) + '</label>';
      }).join('');
      if (window.lucide) window.lucide.createIcons();
    }

    search.addEventListener('input', draw);
    host.querySelector('.vm-colbtn').addEventListener('click', function () { colMenu.hidden = !colMenu.hidden; });
    colMenu.addEventListener('change', function (e) {
      var k = e.target.getAttribute('data-k'); if (!k) return;
      hidden[k] = !e.target.checked;
      try { localStorage.setItem(storeKey, JSON.stringify(hidden)); } catch (err) {}
      draw();
    });
    document.addEventListener('click', function (e) {
      if (!host.querySelector('.vm-colpick').contains(e.target)) colMenu.hidden = true;
    });
    host.querySelectorAll('[data-x]').forEach(function (b) {
      b.addEventListener('click', function () {
        var kind = b.getAttribute('data-x');
        var cols = visible(), data = filtered();
        var name = (cfg.title || 'report').replace(/\s+/g, '_');
        if (kind === 'csv') VM.exportCSV(name, cols, data);
        else if (kind === 'xls') VM.exportExcel(name, cols, data);
        else VM.printTable(cfg.title || 'Report', cols, data);
      });
    });

    draw();
    return {
      setRows: function (r) { rows = r || []; draw(); },
      rows: function () { return rows; },
      redraw: draw
    };
  };

  function plain(v) { return String(v == null ? '' : v).replace(/<[^>]*>/g, '').trim(); }

  VM.exportCSV = function (name, cols, rows) {
    var lines = [cols.map(function (c) { return '"' + c.label.replace(/"/g, '""') + '"'; }).join(',')];
    rows.forEach(function (r) {
      lines.push(cols.map(function (c) {
        var v = plain(c.render ? c.render(r[c.key], r) : r[c.key]);
        return '"' + v.replace(/"/g, '""') + '"';
      }).join(','));
    });
    download(name + '.csv', 'text/csv;charset=utf-8;', '\ufeff' + lines.join('\n'));
  };

  VM.exportExcel = function (name, cols, rows) {
    var html = '<table border="1"><tr>' + cols.map(function (c) { return '<th>' + esc(c.label) + '</th>'; }).join('') + '</tr>' +
      rows.map(function (r) {
        return '<tr>' + cols.map(function (c) {
          return '<td>' + esc(plain(c.render ? c.render(r[c.key], r) : r[c.key])) + '</td>';
        }).join('') + '</tr>';
      }).join('') + '</table>';
    download(name + '.xls', 'application/vnd.ms-excel', '<html><head><meta charset="utf-8"></head><body>' + html + '</body></html>');
  };

  VM.printTable = function (title, cols, rows) {
    var w = window.open('', '_blank');
    if (!w) { VM.toast('Allow pop-ups to print or save as PDF.', 'warn'); return; }
    w.document.write('<html><head><title>' + esc(title) + '</title><style>' +
      'body{font-family:Segoe UI,Arial,sans-serif;padding:24px;color:#111}' +
      'h1{font-size:18px;margin:0 0 4px}.sub{color:#666;font-size:12px;margin-bottom:14px}' +
      'table{width:100%;border-collapse:collapse;font-size:12px}' +
      'th,td{border:1px solid #ccc;padding:6px 8px;text-align:left}th{background:#f3f4f6}' +
      '@media print{@page{size:landscape;margin:12mm}}</style></head><body>' +
      '<h1>' + esc(title) + '</h1><div class="sub">Vedritam School Resource, Procurement &amp; Accounting Management System · ' +
      new Date().toLocaleString() + '</div><table><tr>' +
      cols.map(function (c) { return '<th>' + esc(c.label) + '</th>'; }).join('') + '</tr>' +
      rows.map(function (r) {
        return '<tr>' + cols.map(function (c) {
          return '<td>' + esc(plain(c.render ? c.render(r[c.key], r) : r[c.key])) + '</td>';
        }).join('') + '</tr>';
      }).join('') + '</table></body></html>');
    w.document.close();
    setTimeout(function () { w.print(); }, 400);
  };

  function download(filename, mime, content) {
    var blob = new Blob([content], { type: mime });
    var a = document.createElement('a');
    a.href = URL.createObjectURL(blob); a.download = filename;
    document.body.appendChild(a); a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 500);
  }
  VM.download = download;

  /* Details drawer — used instead of very wide tables */
  VM.drawer = function (title, fields, actionsHTML) {
    var el = document.getElementById('vmDrawer');
    if (!el) {
      el = document.createElement('div');
      el.id = 'vmDrawer';
      el.className = 'vm-drawer';
      el.innerHTML = '<div class="vm-drawer-panel"><div class="vm-drawer-head">' +
        '<h3></h3><button type="button" class="vm-btn ghost vm-drawer-x">Close</button></div>' +
        '<div class="vm-drawer-body"></div><div class="vm-drawer-foot"></div></div>';
      document.body.appendChild(el);
      el.addEventListener('click', function (e) {
        if (e.target === el || e.target.classList.contains('vm-drawer-x')) el.classList.remove('open');
      });
    }
    el.querySelector('h3').textContent = title;
    el.querySelector('.vm-drawer-body').innerHTML = (fields || []).map(function (f) {
      return '<div class="vm-field"><span>' + esc(f.label) + '</span><div>' + (f.html || esc(plain(f.value)) || '—') + '</div></div>';
    }).join('');
    el.querySelector('.vm-drawer-foot').innerHTML = actionsHTML || '';
    el.classList.add('open');
    return el;
  };

  /* Inventory status badge: 🟢 Available / 🟠 Low Stock / 🔴 Out of Stock */
  VM.stockBadge = function (status) {
    var map = { 'Available': '🟢', 'Low Stock': '🟠', 'Out of Stock': '🔴' };
    var cls = String(status || '').toLowerCase().replace(/\s+/g, '-');
    return '<span class="vm-badge stock-' + cls + '">' + (map[status] || '') + ' ' + esc(status || '') + '</span>';
  };
  VM.payBadge = function (status) {
    return '<span class="vm-badge pay-' + String(status || '').toLowerCase() + '">' + esc(status || '') + '</span>';
  };

  /* Keyboard shortcuts: / focus search, n new, e export, ? help, Esc close */
  VM.shortcuts = function (map) {
    document.addEventListener('keydown', function (e) {
      var tag = (e.target.tagName || '').toLowerCase();
      var typing = tag === 'input' || tag === 'textarea' || tag === 'select' || e.target.isContentEditable;
      if (e.key === 'Escape') {
        var d = document.getElementById('vmDrawer'); if (d) d.classList.remove('open');
        if (map && map.escape) map.escape();
        return;
      }
      if (typing || e.ctrlKey || e.metaKey || e.altKey) return;
      if (e.key === '/') { e.preventDefault(); var s = document.querySelector('.vm-table-search'); if (s) s.focus(); return; }
      if (e.key === '?') { e.preventDefault(); VM.drawer('Keyboard shortcuts', [
        { label: '/', value: 'Focus the search box' },
        { label: 'n', value: 'New record' },
        { label: 'e', value: 'Export the current view to CSV' },
        { label: 'Esc', value: 'Close drawer / dialog' },
        { label: '?', value: 'Show this help' }
      ]); return; }
      if (map && map[e.key]) { e.preventDefault(); map[e.key](); }
    });
  };

  /* Cached dropdown options from /options */
  var optionsPromise = null;
  VM.options = function (force) {
    if (force || !optionsPromise) optionsPromise = VM.api('/options');
    return optionsPromise;
  };

  window.VM = VM;
})();
