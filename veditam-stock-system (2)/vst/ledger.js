/* ledger.js — Vedritam enterprise Ledger module.
   Minimises manual entry through searchable dropdowns, auto-fill from the
   Institution Directory / Resource Catalog / Vendor Management modules,
   automatic calculations and validations. */
(function () {
    'use strict';

    var api = function () { return window.App.apiCall.apply(null, arguments); };
    var ui = function () { return window.App.ui; };
    var esc = function (v) { return window.App.ui.escapeHTML(v); };
    var toast = function (m, t) { window.App.ui.showToast(m, t || 'success'); };
    var num = function (v) { var n = parseInt(v, 10); return isNaN(n) ? 0 : n; };
    var dec = function (v) { var n = parseFloat(v); return isNaN(n) ? 0 : n; };
    var money = function (v) { return (Math.round(dec(v) * 100) / 100).toFixed(2); };
    var isAdmin = function () {
        var r = window.App.state.role;
        return r === 'super_admin' || r === 'admin';
    };

    /* ---------------- searchable dropdown (autocomplete) ---------------- */
    function Combo(id, opts) {
        this.id = id;
        this.options = [];
        this.value = '';
        this.onSelect = (opts && opts.onSelect) || function () {};
        this.placeholder = (opts && opts.placeholder) || 'Type to search…';
        this.el = null;
    }
    Combo.prototype.mount = function (host) {
        host.innerHTML =
            '<div class="lgx-combo">' +
              '<input type="text" id="' + this.id + '" class="form-control lgx-combo-input" autocomplete="off" ' +
                     'placeholder="' + esc(this.placeholder) + '" role="combobox" aria-expanded="false" aria-autocomplete="list">' +
              '<ul class="lgx-combo-list" id="' + this.id + '_list" role="listbox"></ul>' +
            '</div>';
        this.el = document.getElementById(this.id);
        this.list = document.getElementById(this.id + '_list');
        var self = this;
        this.el.addEventListener('input', function () { self.open(self.el.value); });
        this.el.addEventListener('focus', function () { self.open(''); });
        this.el.addEventListener('blur', function () { setTimeout(function () { self.close(); }, 180); });
        this.el.addEventListener('keydown', function (e) { self.key(e); });
        this.list.addEventListener('mousedown', function (e) {
            var li = e.target.closest('li[data-value]');
            if (li) { e.preventDefault(); self.pick(li.dataset.value); }
        });
        return this;
    };
    Combo.prototype.setOptions = function (options) { this.options = options || []; return this; };
    Combo.prototype.match = function (term) {
        term = String(term || '').trim().toLowerCase();
        return this.options.filter(function (o) {
            if (!term) return true;
            return (o.label + ' ' + (o.meta || '')).toLowerCase().indexOf(term) >= 0;
        }).slice(0, 60);
    };
    Combo.prototype.open = function (term) {
        var items = this.match(term);
        this.list.innerHTML = items.length
            ? items.map(function (o, i) {
                return '<li role="option" data-value="' + esc(o.value) + '" data-i="' + i + '">' +
                    '<span>' + esc(o.label) + '</span>' +
                    (o.meta ? '<em>' + esc(o.meta) + '</em>' : '') + '</li>';
              }).join('')
            : '<li class="lgx-combo-empty">No matches</li>';
        this.list.classList.add('open');
        this.el.setAttribute('aria-expanded', 'true');
        this.active = -1;
    };
    Combo.prototype.close = function () {
        this.list.classList.remove('open');
        this.el.setAttribute('aria-expanded', 'false');
    };
    Combo.prototype.key = function (e) {
        var lis = Array.prototype.slice.call(this.list.querySelectorAll('li[data-value]'));
        if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
            e.preventDefault();
            if (!this.list.classList.contains('open')) this.open(this.el.value);
            this.active = Math.max(0, Math.min(lis.length - 1, (this.active || 0) + (e.key === 'ArrowDown' ? 1 : -1)));
            lis.forEach(function (li, i) { li.classList.toggle('active', i === this.active); }, this);
            if (lis[this.active]) lis[this.active].scrollIntoView({ block: 'nearest' });
        } else if (e.key === 'Enter') {
            if (this.list.classList.contains('open') && lis[this.active]) {
                e.preventDefault();
                this.pick(lis[this.active].dataset.value);
            }
        } else if (e.key === 'Escape') { this.close(); }
    };
    Combo.prototype.pick = function (value) {
        var opt = this.options.filter(function (o) { return String(o.value) === String(value); })[0];
        this.value = value;
        this.el.value = opt ? opt.label : value;
        this.close();
        this.onSelect(opt || { value: value, label: value });
    };
    Combo.prototype.set = function (value) {
        var opt = this.options.filter(function (o) { return String(o.value) === String(value); })[0];
        this.value = opt ? opt.value : (value || '');
        if (this.el) this.el.value = opt ? opt.label : (value || '');
    };
    Combo.prototype.clear = function () { this.value = ''; if (this.el) this.el.value = ''; };

    /* ---------------- column model ---------------- */
    var BASE_COLUMNS = [
        { key: 'academicYear', label: 'Academic Year', type: 'text', group: 'context' },
        { key: 'standard', label: 'Class', type: 'text', group: 'context' },
        { key: 'category', label: 'Resource Category', type: 'text', group: 'resource' },
        { key: 'bookName', label: 'Resource Name', type: 'text', group: 'resource' },
        { key: 'subject', label: 'Subject', type: 'text', group: 'resource' },
        { key: 'publication', label: 'Publication', type: 'text', group: 'resource' },
        { key: 'edition', label: 'Edition', type: 'text', group: 'resource' },
        { key: 'vendor', label: 'Vendor Name', type: 'text', group: 'vendor' },
        { key: 'vendorContact', label: 'Vendor Contact', type: 'text', group: 'vendor' },
        { key: 'vendorGst', label: 'Vendor GST No', type: 'text', group: 'vendor' },
        { key: 'invoiceRef', label: 'Invoice Number', type: 'text', group: 'invoice' },
        { key: 'invoiceDate', label: 'Invoice Date', type: 'date', group: 'invoice' },
        { key: 'openingBalance', label: 'Opening Balance', type: 'number', group: 'qty',
          tip: 'Carried forward from the previous closing balance of the same resource.' },
        { key: 'strength', label: 'Student Strength', type: 'number', group: 'qty',
          tip: 'Auto-filled from the class record in the Institution Directory.' },
        { key: 'requiredQty', label: 'Required Quantity', type: 'derived', group: 'qty',
          tip: 'Required Quantity = Student Strength (or Strength − Opening Balance, per the selected rule).' },
        { key: 'purchased', label: 'Articles Purchased', type: 'number', group: 'qty' },
        { key: 'approvedRate', label: 'Approved Rate', type: 'decimal', group: 'money',
          tip: 'Approved rate published in the Resource Catalog.' },
        { key: 'baseRate', label: 'Base Rate', type: 'decimal', group: 'money' },
        { key: 'grossAmount', label: 'Gross Amount', type: 'derived', group: 'money',
          tip: 'Gross Amount = Articles Purchased × Base Rate.' },
        { key: 'gstPercent', label: 'GST %', type: 'decimal', group: 'money' },
        { key: 'gstAmount', label: 'GST Amount', type: 'derived', group: 'money',
          tip: 'GST Amount = Gross Amount × GST %.' },
        { key: 'discountPercent', label: 'Discount %', type: 'decimal', group: 'money' },
        { key: 'discountAmount', label: 'Discount Amount', type: 'derived', group: 'money',
          tip: 'Discount Amount = Gross Amount × Discount %.' },
        { key: 'totalAmount', label: 'Total Amount', type: 'derived', group: 'money',
          tip: 'Total Amount = Base Rate + GST Amount − Discount Amount.' },
        { key: 'distributed', label: 'Articles Issued', type: 'number', group: 'issue' },
        { key: 'returned', label: 'Returns', type: 'number', group: 'issue',
          tip: 'Updated automatically by the Return to Vendor section.' },
        { key: 'closingBalance', label: 'Closing Balance', type: 'derived', group: 'issue',
          tip: 'Closing Balance = Opening Balance + Purchased − Issued − Returns.' },
        { key: 'remarks', label: 'Remarks', type: 'text', group: 'issue' }
    ];

    var GROUPS = [
        { key: 'context', label: 'Academic Context' },
        { key: 'resource', label: 'Resource Details' },
        { key: 'vendor', label: 'Vendor Details' },
        { key: 'invoice', label: 'Invoice' },
        { key: 'qty', label: 'Quantity' },
        { key: 'money', label: 'Rate & Amount' },
        { key: 'issue', label: 'Issue / Balance' },
        { key: 'custom', label: 'Additional Fields' }
    ];

    var Ledger = {
        meta: null,
        rows: [],
        dirty: new Map(),
        deleted: new Set(),
        selectedId: null,
        selectedField: null,
        catalog: [],
        catalogStandard: null,
        combos: {},
        form: {},
        hidden: {},
        classInfo: null,
        strength: 0,

        /* ---------------- boot ---------------- */
        async init() {
            var st = window.App.state;
            if (!st.selectedSchool || !st.selectedClass) { window.location.href = 'schools.html'; return; }
            try { this.hidden = JSON.parse(localStorage.getItem('v_ledger_hidden') || '{}'); } catch (e) { this.hidden = {}; }
            var rule = localStorage.getItem('v_ledger_reqrule') || 'strength';
            document.getElementById('ctxInstitution');
            document.getElementById('lblSchoolName').textContent = st.selectedSchool.name;

            try {
                this.meta = await api('/ledger/meta/' + st.selectedSchool.id + '?class_id=' + st.selectedClass.id);
            } catch (e) {
                toast('Could not load ledger master data: ' + e.message, 'error');
                this.meta = { institutions: [], vendors: [], resources: [], academicYears: [], gstRates: [],
                              discountRates: [], categories: [], subjects: [], publications: [], editions: [],
                              returnReasons: [], customFields: [], standards: [] };
            }
            this.columns = BASE_COLUMNS.concat((this.meta.customFields || []).map(function (f) {
                return { key: f.key, label: f.label, type: f.type === 'number' ? 'number'
                    : (f.type === 'decimal' ? 'decimal' : (f.type === 'date' ? 'date' : 'text')),
                    group: 'custom', custom: true };
            }));

            this.buildContext(rule);
            this.buildHeader();
            await this.fetchData();
            this.loadBookOptions();
            this.bindTable();
            window.App.applyRoleVisibility();
        },

        /* ---------------- context strip ---------------- */
        buildContext(rule) {
            var st = window.App.state, self = this;
            var inst = new Combo('ctxInstitution', {
                placeholder: 'Search institution…',
                onSelect: function (o) { self.changeInstitution(o.value); }
            }).mount(document.querySelector('[data-combo="ctxInstitution"]'));
            inst.setOptions((this.meta.institutions || []).map(function (i) {
                return { value: i.id, label: i.name, meta: i.code || '' };
            }));
            inst.set(String(st.selectedSchool.id));

            var yr = new Combo('ctxAcademicYear', {
                placeholder: 'Search academic year…',
                onSelect: function (o) { self.academicYear = o.value; self.updateLabels(); self.syncFormDefaults(); }
            }).mount(document.querySelector('[data-combo="ctxAcademicYear"]'));
            yr.setOptions((this.meta.academicYears || []).map(function (y) { return { value: y, label: y }; }));
            var institution = this.institution();
            this.academicYear = localStorage.getItem('v_academic_year') ||
                (institution && institution.academic_year) || (this.meta.academicYears || [])[2] ||
                (this.meta.academicYears || [])[0] || '';
            yr.set(this.academicYear);

            this.combos.institution = inst;
            this.combos.academicYear = yr;

            var classSel = document.getElementById('ctxClass');
            classSel.innerHTML = ((institution && institution.classes) || []).map(function (c) {
                return '<option value="' + esc(c.id) + '">' + esc(c.name) + '</option>';
            }).join('') || '<option value="">No classes</option>';
            classSel.value = String(st.selectedClass.id);
            classSel.addEventListener('change', function () { self.changeClass(this.value); });

            var ruleSel = document.getElementById('ctxRequiredRule');
            ruleSel.value = rule;
            ruleSel.addEventListener('change', function () {
                localStorage.setItem('v_ledger_reqrule', this.value);
                self.render();
            });

            document.getElementById('ledgerSearch').addEventListener('input', function () { self.render(); });
            var cat = document.getElementById('categorySelect');
            cat.innerHTML = '<option value="ALL">All Categories</option>' +
                (this.meta.categories || []).map(function (c) {
                    return '<option value="' + esc(c) + '">' + esc(Ledger.categoryLabel(c)) + '</option>';
                }).join('');
            cat.addEventListener('change', function () { self.render(); });
            document.addEventListener('click', function (e) {
                if (!e.target.closest('.lgx-menu')) {
                    document.querySelectorAll('.lgx-menu-panel.open').forEach(function (p) { p.classList.remove('open'); });
                }
            });
        },

        institution() {
            var id = String(window.App.state.selectedSchool.id);
            return (this.meta.institutions || []).filter(function (i) { return String(i.id) === id; })[0] || null;
        },
        classRecord() {
            var inst = this.institution();
            var cid = String(window.App.state.selectedClass.id);
            return inst ? (inst.classes || []).filter(function (c) { return String(c.id) === cid; })[0] || null : null;
        },
        categoryLabel(c) {
            return ({ TB: 'Textbook', NB: 'Notebook', STATIONERY: 'Stationery', INHOUSE: 'In-house' })[c] || c;
        },
        requiredRule() { return document.getElementById('ctxRequiredRule').value; },

        changeInstitution(id) {
            var inst = (this.meta.institutions || []).filter(function (i) { return String(i.id) === String(id); })[0];
            if (!inst) return;
            if (this.pendingCount() && !confirm('Unsaved ledger changes will be lost. Switch institution?')) {
                this.combos.institution.set(String(window.App.state.selectedSchool.id));
                return;
            }
            var first = (inst.classes || [])[0];
            if (!first) { toast('That institution has no classes yet.', 'error'); return; }
            localStorage.setItem('v_school', JSON.stringify({ id: inst.id, name: inst.name }));
            localStorage.setItem('v_class', JSON.stringify({ id: first.id, name: first.name }));
            window.location.reload();
        },
        changeClass(classId) {
            var inst = this.institution();
            var cls = ((inst && inst.classes) || []).filter(function (c) { return String(c.id) === String(classId); })[0];
            if (!cls) return;
            if (this.pendingCount() && !confirm('Unsaved ledger changes will be lost. Switch class?')) {
                document.getElementById('ctxClass').value = String(window.App.state.selectedClass.id);
                return;
            }
            localStorage.setItem('v_class', JSON.stringify({ id: cls.id, name: cls.name }));
            window.location.reload();
        },

        /* ---------------- entry form ---------------- */
        calc(row) {
            var gross = dec(row.purchased) * dec(row.baseRate);
            var gstAmount = gross * dec(row.gstPercent) / 100;
            var discountAmount = gross * dec(row.discountPercent) / 100;
            // Business rule exactly as specified.
            var total = dec(row.baseRate) + gstAmount - discountAmount;
            var required = this.requiredRule() === 'strengthMinusOpening'
                ? Math.max(num(row.strength) - num(row.openingBalance), 0)
                : num(row.strength);
            var closing = num(row.openingBalance) + num(row.purchased) - num(row.distributed) - num(row.returned);
            return {
                grossAmount: Math.round(gross * 100) / 100,
                gstAmount: Math.round(gstAmount * 100) / 100,
                discountAmount: Math.round(discountAmount * 100) / 100,
                totalAmount: Math.round(total * 100) / 100,
                requiredQty: required,
                closingBalance: closing
            };
        },

        /* ---------------- table ---------------- */
        visibleColumns() {
            var self = this;
            return this.columns.filter(function (c) { return !self.hidden[c.key]; });
        },
        buildHeader() {
            var cols = this.visibleColumns();
            var head = document.getElementById('ledgerHead');
            var top = '<tr class="group-row"><th class="col-idx" rowspan="2">#</th>';
            GROUPS.forEach(function (g) {
                var span = cols.filter(function (c) { return c.group === g.key; }).length;
                if (span) top += '<th colspan="' + span + '" class="group-head group-' + g.key + '">' + esc(g.label) + '</th>';
            });
            top += '</tr><tr>';
            GROUPS.forEach(function (g) {
                cols.filter(function (c) { return c.group === g.key; }).forEach(function (c) {
                    var cls = [c.type === 'derived' ? 'col-derived' : '',
                               ['number', 'decimal', 'derived'].indexOf(c.type) >= 0 ? 'col-num' : ''].join(' ');
                    if (c.custom) cls += ' col-custom';
                    top += '<th class="' + cls + '" data-col="' + esc(c.key) + '"' +
                        (c.custom ? ' data-custom="1" title="Extra field — click to select, then press Delete"'
                                  : (c.tip ? ' title="' + esc(c.tip) + '"' : '')) + '>' +
                        esc(c.label) + (c.tip && !c.custom ? ' <span class="lgx-info">i</span>' : '') + '</th>';
                });
            });
            top += '</tr>';
            head.innerHTML = top;
        },
        toggleExportMenu() { this.toggleMenu('exportMenu'); },
        toggleMenu(id) {
            var panel = document.getElementById(id);
            var open = panel.classList.contains('open');
            document.querySelectorAll('.lgx-menu-panel.open').forEach(function (p) { p.classList.remove('open'); });
            if (!open) panel.classList.add('open');
        },
        toggleSection(id, btn) {
            var body = document.getElementById(id);
            var hidden = body.style.display === 'none';
            body.style.display = hidden ? '' : 'none';
            btn.textContent = hidden ? 'Hide' : 'Show';
        },

        async fetchData() {
            var st = window.App.state;
            try {
                var res = await api('/ledger/class/' + st.selectedSchool.id + '/' + st.selectedClass.id);
                this.classInfo = res;
                this.strength = res.strength || 0;
                this.rows = (res.rows || []).map(this.expandRow, this);
                this.dirty.clear(); this.deleted.clear();
                this.updateLabels(); this.updateSaveButton(); this.render();
            } catch (e) { toast('Failed to load ledger records: ' + e.message, 'error'); }
        },
        expandRow(r) {
            var custom = {};
            try { custom = JSON.parse(r.custom_json || '{}') || {}; } catch (e) { custom = {}; }
            Object.keys(custom).forEach(function (k) { r[k] = custom[k]; });
            return r;
        },
        updateLabels() {
            var cls = this.classRecord();
            document.getElementById('lblClassName').textContent =
                (this.classInfo && this.classInfo.className) || (cls && cls.name) || '';
            document.getElementById('lblClassStrength').textContent = 'Strength: ' + (this.strength || 0) + ' students';
            document.getElementById('lblAcademicYear').textContent = 'Year: ' + (this.academicYear || '—');
            document.getElementById('printTitle').textContent =
                'Stock Ledger — ' + window.App.state.selectedSchool.name + ' — ' +
                ((this.classInfo && this.classInfo.className) || '') + ' — ' + (this.academicYear || '');
            if (this.academicYear) localStorage.setItem('v_academic_year', this.academicYear);
        },
        pendingCount() { return this.dirty.size + this.deleted.size; },

        filteredRows() {
            var term = (document.getElementById('ledgerSearch').value || '').toLowerCase();
            var cat = document.getElementById('categorySelect').value;
            return this.rows.filter(function (r) {
                if (r._deleted) return false;
                if (cat !== 'ALL' && String(r.category || '').toUpperCase() !== cat) return false;
                if (!term) return true;
                return Object.keys(r).some(function (k) {
                    return String(r[k] || '').toLowerCase().indexOf(term) >= 0;
                });
            });
        },

        cellText(row, col, calc) {
            if (col.key === 'requiredQty') return String(calc.requiredQty);
            if (['grossAmount', 'gstAmount', 'discountAmount', 'totalAmount'].indexOf(col.key) >= 0)
                return money(calc[col.key]);
            if (col.key === 'closingBalance') return String(calc.closingBalance);
            if (col.type === 'decimal') return money(row[col.key]);
            return String(row[col.key] === undefined || row[col.key] === null ? '' : row[col.key]);
        },

        render() {
            var self = this;
            var cols = this.visibleColumns();
            var body = document.getElementById('ledgerBody');
            var rows = this.filteredRows();
            if (!rows.length) {
                body.innerHTML = '<tr class="empty-row"><td colspan="' + (cols.length + 1) + '">' +
                    'No ledger entries yet. Use “+ Add from Catalog”, the book dropdown, or “+ Blank Row” to start.</td></tr>';
                document.getElementById('ledgerFoot').innerHTML = '';
                return;
            }
            body.innerHTML = rows.map(function (row, i) {
                var calc = self.calc(row);
                var cells = cols.map(function (c) {
                    var text = self.cellText(row, c, calc);
                    var alignCls = ['number', 'decimal', 'derived'].indexOf(c.type) >= 0 ? 'col-num' : '';
                    if (c.type === 'derived') {
                        var warn = (c.key === 'closingBalance' && calc.closingBalance <= 0) ? ' text-danger' : '';
                        return '<td data-key="' + c.key + '" class="' + alignCls + ' col-derived' + warn + '"' +
                            (c.tip ? ' title="' + esc(c.tip) + '"' : '') + '>' + esc(text) + '</td>';
                    }
                    var dirty = self.dirty.has(row.id) && self.dirty.get(row.id)[c.key] !== undefined;
                    if (c.key === 'vendor') {
                        return '<td class="editable lgx-vendor-cell ' + (dirty ? 'dirty' : '') + '" data-key="vendor"' +
                            ' data-type="text" tabindex="0" title="Click to search vendors">' +
                            (text ? esc(text) : '<span class="muted">Select vendor…</span>') +
                            '<span class="lgx-vendor-caret">▾</span></td>';
                    }
                    return '<td class="editable ' + alignCls + (dirty ? ' dirty' : '') + '" data-key="' + c.key +
                        '" data-type="' + c.type + '" contenteditable="true" tabindex="0">' + esc(text) + '</td>';
                }).join('');
                return '<tr data-id="' + esc(row.id) + '"' +
                    (String(self.selectedId) === String(row.id) ? ' class="row-selected"' : '') +
                    '><td class="col-idx">' + (i + 1) + '</td>' + cells + '</tr>';
            }).join('');

            // Totals footer
            var totals = rows.reduce(function (acc, r) {
                var c = self.calc(r);
                acc.purchased += num(r.purchased); acc.distributed += num(r.distributed);
                acc.returned += num(r.returned); acc.closing += c.closingBalance;
                acc.total += c.totalAmount; acc.gst += c.gstAmount; acc.gross += c.grossAmount;
                acc.discount += c.discountAmount;
                return acc;
            }, { purchased: 0, distributed: 0, returned: 0, closing: 0, total: 0, gst: 0, gross: 0, discount: 0 });
            var map = { purchased: totals.purchased, distributed: totals.distributed, returned: totals.returned,
                        closingBalance: totals.closing, totalAmount: money(totals.total),
                        gstAmount: money(totals.gst), grossAmount: money(totals.gross),
                        discountAmount: money(totals.discount) };
            document.getElementById('ledgerFoot').innerHTML = '<tr class="lgx-total-row"><td>Σ</td>' +
                cols.map(function (c) {
                    return '<td class="col-num">' + (map[c.key] !== undefined ? esc(String(map[c.key])) : '') + '</td>';
                }).join('') + '</tr>';
        },

        bindTable() {
            var self = this;
            var table = document.getElementById('ledgerTable');
            table.addEventListener('focusout', function (e) {
                if (!e.target.classList || !e.target.classList.contains('editable')) return;
                var tr = e.target.closest('tr');
                if (tr && tr.dataset.id) self.cellEdited(tr.dataset.id, e.target);
            });
            table.addEventListener('click', function (e) {
                var vtd = e.target.closest('#ledgerBody td.lgx-vendor-cell');
                if (vtd) {
                    var vtr = vtd.closest('tr');
                    if (vtr && vtr.dataset.id) {
                        self.selectedId = vtr.dataset.id; self.selectedField = null; self.highlight();
                        self.openVendorPicker(vtd, vtr.dataset.id);
                    }
                    return;
                }
                var th = e.target.closest('#ledgerHead th[data-custom]');
                if (th) {
                    self.selectedField = (self.selectedField === th.dataset.col) ? null : th.dataset.col;
                    self.selectedId = null;
                    self.highlight();
                    return;
                }
                var tr = e.target.closest('#ledgerBody tr');
                if (tr && tr.dataset.id) { self.selectedId = tr.dataset.id; self.selectedField = null; self.highlight(); }
            });
            table.addEventListener('keydown', function (e) { self.keyNav(e); });
        },
        highlight() {
            var self = this;
            document.querySelectorAll('#ledgerBody tr').forEach(function (tr) {
                tr.classList.toggle('row-selected', String(tr.dataset.id) === String(self.selectedId));
            });
            document.querySelectorAll('#ledgerHead th[data-custom]').forEach(function (th) {
                th.classList.toggle('col-selected', th.dataset.col === self.selectedField);
            });
        },
        keyNav(e) {
            var td = e.target.closest && e.target.closest('td.editable');
            if (!td) return;
            var tr = td.parentElement;
            var cells = Array.prototype.slice.call(tr.querySelectorAll('td.editable'));
            var i = cells.indexOf(td);
            var move = function (row, index) {
                if (!row) return;
                var next = row.querySelectorAll('td.editable')[index];
                if (next) { next.focus(); e.preventDefault(); }
            };
            if (e.key === 'Enter' && !e.shiftKey) { td.blur(); e.preventDefault(); move(tr.nextElementSibling, i); }
            else if (e.key === 'ArrowDown') move(tr.nextElementSibling, i);
            else if (e.key === 'ArrowUp') move(tr.previousElementSibling, i);
        },
        cellEdited(rowId, td) {
            var row = this.rows.filter(function (r) { return String(r.id) === String(rowId); })[0];
            if (!row) return;
            var key = td.dataset.key, type = td.dataset.type;
            var val = td.innerText.trim();
            if (type === 'number') val = Math.max(num(val), 0);
            else if (type === 'decimal') val = money(Math.max(dec(val), 0));
            if (key === 'category') val = String(val).toUpperCase();
            if (String(row[key] === undefined ? '' : row[key]) === String(val)) return;
            td.innerText = val;
            row[key] = val;
            if (!this.dirty.has(rowId)) this.dirty.set(rowId, {});
            this.dirty.get(rowId)[key] = val;
            var colDef = this.columns.filter(function (c) { return c.key === key; })[0] || {};
            if (colDef.custom === true) {
                var custom = {};
                (this.meta.customFields || []).forEach(function (f) { custom[f.key] = row[f.key] || ''; });
                this.dirty.get(rowId).custom_json = JSON.stringify(custom);
            }
            td.classList.add('dirty');
            var check = this.validateRow(row, this.calc(row));
            if (check) toast(check, 'error');
            this.render();
            this.updateSaveButton();
        },
        validateRow(row, calc) {
            if (num(row.distributed) > num(row.openingBalance) + num(row.purchased))
                return 'Articles issued exceed the available stock for "' + (row.bookName || 'this row') + '".';
            if (calc.closingBalance < 0)
                return 'Closing balance cannot be negative for "' + (row.bookName || 'this row') + '".';
            if (dec(row.gstPercent) < 0 || dec(row.gstPercent) > 100) return 'GST % must be between 0 and 100.';
            if (dec(row.discountPercent) < 0 || dec(row.discountPercent) > 100) return 'Discount % must be between 0 and 100.';
            return '';
        },
        deleteSelection() {
            if (this.selectedField) {
                var key = this.selectedField;
                this.selectedField = null;
                this.highlight();
                this.deleteField(key);
                return;
            }
            this.deleteSelected();
        },
        deleteSelected() {
            if (!this.selectedId) {
                toast('Select a ledger row, or click an extra column header, then press Delete.', 'info');
                return;
            }
            var id = String(this.selectedId);
            if (id.indexOf('new_') === 0) {
                this.rows = this.rows.filter(function (r) { return String(r.id) !== id; });
                this.dirty.delete(id);
            } else {
                var row = this.rows.filter(function (r) { return String(r.id) === id; })[0];
                if (row) row._deleted = true;
                this.deleted.add(id);
            }
            this.selectedId = null;
            this.render(); this.updateSaveButton();
            toast('Row removed — press Save Changes to confirm.', 'info');
        },
        updateSaveButton() {
            var btn = document.getElementById('btnSaveLedger');
            var count = this.pendingCount();
            btn.textContent = 'Save Changes (' + count + ')';
            btn.disabled = count === 0;
        },
        async saveChanges() {
            if (!this.pendingCount()) { toast('No unsaved changes.', 'info'); return; }
            var st = window.App.state;
            var btn = document.getElementById('btnSaveLedger');
            btn.disabled = true; btn.textContent = 'Saving…';
            try {
                await api('/ledger/sync', 'POST', {
                    schoolId: st.selectedSchool.id,
                    classId: st.selectedClass.id,
                    standard: (this.classRecord() || {}).standard || '',
                    updates: Array.from(this.dirty.entries()).map(function (e) {
                        return Object.assign({ id: e[0] }, e[1]);
                    }),
                    deletes: Array.from(this.deleted)
                });
                toast('Ledger saved.', 'success');
                await this.fetchData();
                document.dispatchEvent(new CustomEvent('v-data-changed'));
                try { localStorage.setItem('v_data_version', String(Date.now())); } catch (e) {}
            } catch (err) {
                toast(err.message, 'error');
            } finally { this.updateSaveButton(); }
        },

        /* ---------------- admin: ledger fields ---------------- */
        openFieldModal() {
            this.renderFieldList();
            window.App.ui.openModal('modalField');
        },
        closeFieldModal() { window.App.ui.closeModal('modalField'); },
        renderFieldList() {
            var list = this.meta.customFields || [];
            document.getElementById('fieldList').innerHTML = list.length
                ? list.map(function (f) {
                    return '<div class="lgx-field-row"><span><strong>' + esc(f.label) + '</strong>' +
                        '<em>' + esc(f.type) + '</em></span>' +
                        '<button class="btn-mini danger" type="button" onclick="Ledger.deleteField(\'' +
                        esc(f.key) + '\')">Remove</button>' + '</div>';
                  }).join('')
                : '<p class="muted-small">No extra fields yet.</p>';
        },
        async createField() {
            var label = document.getElementById('newFieldLabel').value.trim();
            var type = document.getElementById('newFieldType').value;
            if (!label) return toast('Enter a field name.', 'error');
            try {
                var res = await api('/ledger/fields', 'POST', { label: label, type: type });
                this.meta.customFields = (this.meta.customFields || []).concat([res.field]);
                document.getElementById('newFieldLabel').value = '';
                toast('Field "' + res.field.label + '" added to your ledger.', 'success');
                this.refreshCustomColumns();
            } catch (e) { toast(e.message, 'error'); }
        },
        async deleteField(key) {
            if (!confirm('Remove this ledger field for everyone? Existing values stay stored but stop being shown.')) return;
            try {
                await api('/ledger/fields/' + encodeURIComponent(key), 'DELETE');
                this.meta.customFields = (this.meta.customFields || []).filter(function (f) { return f.key !== key; });
                toast('Field removed.', 'success');
                this.refreshCustomColumns();
            } catch (e) { toast(e.message, 'error'); }
        },
        refreshCustomColumns() {
            this.columns = BASE_COLUMNS.concat((this.meta.customFields || []).map(function (f) {
                return { key: f.key, label: f.label,
                         type: f.type === 'number' ? 'number' : (f.type === 'decimal' ? 'decimal'
                               : (f.type === 'date' ? 'date' : 'text')), group: 'custom', custom: true };
            }));
            this.renderFieldList();
            this.buildHeader();
            this.render();
        },

        /* ---------------- catalogue ---------------- */
        standardCode() {
            var cls = this.classRecord();
            var name = (cls && (cls.standard || cls.name)) ||
                (this.classInfo && this.classInfo.className) ||
                window.App.state.selectedClass.name || '';
            return this.guessStandard(name) || 'OTHERS';
        },
        guessStandard(name) {
            var t = String(name || '').trim().toUpperCase();
            if (!t) return null;
            if (t.indexOf('PRE') >= 0) return 'PRE KG';
            if (t.indexOf('LKG') >= 0) return 'LKG';
            if (t.indexOf('UKG') >= 0) return 'UKG';
            var roman = ['I','II','III','IV','V','VI','VII','VIII','IX','X','XI','XII'];
            var m = t.match(/\b(1[0-2]|[1-9])\b/);
            if (m) return roman[parseInt(m[1], 10) - 1];
            var rm = t.replace(/CLASS|STD|STANDARD/g, '').trim().match(/^(XII|XI|X|IX|VIII|VII|VI|V|IV|III|II|I)\b/);
            return rm ? rm[1] : null;
        },
        standardLabel(s) {
            return (['PRE KG', 'LKG', 'UKG', 'OTHERS'].indexOf(s) >= 0) ? s : 'Class ' + s;
        },
        async loadCatalog(standard) {
            standard = standard || this.standardCode();
            if (this.catalogStandard === standard && this.catalog.length) return this.catalog;
            this.catalog = await api('/catalog?standard=' + encodeURIComponent(standard));
            this.catalogStandard = standard;
            return this.catalog;
        },
        async loadBookOptions() {
            var sel = document.getElementById('bookSelect');
            if (!sel) return;
            sel.disabled = true;
            sel.innerHTML = '<option value="">Loading books…</option>';
            try {
                await this.loadCatalog();
                if (!this.catalog.length) { sel.innerHTML = '<option value="">No books in this standard</option>'; return; }
                sel.innerHTML = '<option value="">-- add a book --</option>' +
                    this.catalog.map(function (it, i) {
                        var extra = [it.subject, it.publication].filter(Boolean).join(' · ');
                        return '<option value="' + i + '">' + esc(it.title) + (extra ? ' — ' + esc(extra) : '') + '</option>';
                    }).join('');
                sel.disabled = false;
            } catch (e) { sel.innerHTML = '<option value="">Could not load books</option>'; }
        },
        pickBook(value) {
            var sel = document.getElementById('bookSelect');
            var it = this.catalog[parseInt(value, 10)];
            if (sel) sel.value = '';
            if (!it) return;
            this.addRecord(this.presetFromCatalog(it));
            toast('"' + it.title + '" added — press Save Changes to confirm.', 'success');
        },
        presetFromCatalog(it) {
            return {
                bookName: it.title || '', subject: it.subject || '', publication: it.publication || '',
                edition: it.edition || '', category: String(it.category || '').toUpperCase(),
                standard: it.standard || this.standardCode(),
                approvedRate: it.rate || it.approvedRate || 0,
                baseRate: it.rate || it.approvedRate || 0
            };
        },
        async openCatalog() {
            var body = document.getElementById('catalogList');
            var std = this.standardCode();
            document.getElementById('catalogTitle').textContent = 'Catalog — ' + this.standardLabel(std);
            var stdSel = document.getElementById('catalogStandardSel');
            if (stdSel && !stdSel.options.length) {
                var list = (this.meta && this.meta.standards && this.meta.standards.length ? this.meta.standards
                    : ['PRE KG','LKG','UKG','I','II','III','IV','V','VI','VII','VIII','IX','X','XI','XII','OTHERS']);
                if (list.indexOf(std) < 0) list = [std].concat(list);
                stdSel.innerHTML = list.map(function (s) {
                    return '<option value="' + esc(s) + '">' + esc(Ledger.standardLabel(s)) + '</option>';
                }).join('');
            }
            if (stdSel) stdSel.value = this.catalogStandard || std;
            window.App.ui.openModal('catalogModal');
            body.innerHTML = '<p class="muted">Loading catalog…</p>';
            try { await this.loadCatalog(stdSel ? stdSel.value : std); this.renderCatalog(); }
            catch (e) { body.innerHTML = '<p class="text-danger">Could not load the catalog.</p>'; }
        },
        async changeCatalogStandard(std) {
            var body = document.getElementById('catalogList');
            body.innerHTML = '<p class="muted">Loading catalog…</p>';
            document.getElementById('catalogTitle').textContent = 'Catalog — ' + this.standardLabel(std);
            try { await this.loadCatalog(std); this.renderCatalog(); }
            catch (e) { body.innerHTML = '<p class="text-danger">Could not load the catalog.</p>'; }
        },
        closeCatalog() { window.App.ui.closeModal('catalogModal'); },
        renderCatalog() {
            var body = document.getElementById('catalogList');
            var term = (document.getElementById('catalogSearch').value || '').toLowerCase();
            var cat = document.getElementById('catalogCategory').value;
            var self = this;
            var items = this.catalog.filter(function (it) {
                if (cat !== 'ALL' && String(it.category || '').toUpperCase() !== cat) return false;
                if (!term) return true;
                return ((it.title || '') + ' ' + (it.subject || '') + ' ' + (it.publication || ''))
                    .toLowerCase().indexOf(term) >= 0;
            });
            if (!items.length) { body.innerHTML = '<p class="muted">No catalog items match.</p>'; return; }
            body.innerHTML = items.map(function (it) {
                return '<label class="catalog-item">' +
                    '<input type="checkbox" data-idx="' + self.catalog.indexOf(it) + '">' +
                    '<span class="badge badge-' + esc(String(it.category || '').toLowerCase()) + '">' +
                        esc(it.category || '-') + '</span>' +
                    '<span class="catalog-title">' + esc(it.title || '') + '</span>' +
                    '<span class="muted">' + esc(it.subject || '') +
                        (it.publication ? ' · ' + esc(it.publication) : '') + '</span>' +
                    '</label>';
            }).join('');
        },
        addSelectedFromCatalog() {
            var self = this;
            var picked = Array.prototype.slice.call(document.querySelectorAll('#catalogList input:checked'))
                .map(function (cb) { return self.catalog[parseInt(cb.dataset.idx, 10)]; }).filter(Boolean);
            if (!picked.length) { toast('Select at least one item.', 'info'); return; }
            picked.forEach(function (it) { self.addRecord(self.presetFromCatalog(it), false); });
            this.closeCatalog();
            this.render(); this.updateSaveButton();
            toast(picked.length + ' row(s) added. Press Save Changes to confirm.', 'success');
        },

        /* ---------------- vendor searchable dropdown ---------------- */
        vendorOptions() {
            return (this.meta && this.meta.vendors ? this.meta.vendors : []).filter(function (v) {
                return String(v.status || 'Active').toLowerCase() !== 'inactive';
            });
        },
        closeVendorPicker() {
            var old = document.getElementById('vendorPicker');
            if (old && old.parentNode) old.parentNode.removeChild(old);
            if (this._vendorDocHandler) {
                document.removeEventListener('mousedown', this._vendorDocHandler, true);
                this._vendorDocHandler = null;
            }
        },
        openVendorPicker(td, rowId) {
            var self = this;
            this.closeVendorPicker();
            var row = this.rows.filter(function (r) { return String(r.id) === String(rowId); })[0];
            if (!row) return;
            var box = document.createElement('div');
            box.id = 'vendorPicker';
            box.className = 'lgx-vpick';
            box.innerHTML = '<input type="text" class="form-control lgx-vpick-input" ' +
                'placeholder="Search vendor name, code, GST…" autocomplete="off">' +
                '<div class="lgx-vpick-list"></div>' +
                '<div class="lgx-vpick-foot"><span class="muted-small">Enter keeps what you type · Esc closes</span></div>';
            document.body.appendChild(box);
            var r = td.getBoundingClientRect();
            box.style.minWidth = Math.max(r.width, 280) + 'px';
            box.style.top = (r.bottom + window.scrollY + 3) + 'px';
            box.style.left = Math.min(r.left + window.scrollX,
                window.scrollX + document.documentElement.clientWidth - 320) + 'px';

            var input = box.querySelector('.lgx-vpick-input');
            var list = box.querySelector('.lgx-vpick-list');
            var matches = [];
            var draw = function () {
                var term = input.value.trim().toLowerCase();
                matches = self.vendorOptions().filter(function (v) {
                    if (!term) return true;
                    return ((v.name || '') + ' ' + (v.vendorId || '') + ' ' + (v.gst || '') + ' ' +
                            (v.contact || '') + ' ' + (v.email || '')).toLowerCase().indexOf(term) >= 0;
                });
                if (!matches.length) {
                    list.innerHTML = '<div class="lgx-vpick-empty">No vendor matches. Add them in Vendors, ' +
                        'or press Enter to keep the typed name.</div>';
                    return;
                }
                list.innerHTML = matches.map(function (v, i) {
                    var sub = [v.vendorId, v.contact, v.gst].filter(Boolean).join(' · ');
                    return '<button type="button" class="lgx-vpick-item" data-i="' + i + '">' +
                        '<strong>' + esc(v.name || '') + '</strong>' +
                        (sub ? '<em>' + esc(sub) + '</em>' : '') + '</button>';
                }).join('');
            };
            list.addEventListener('mousedown', function (e) {
                var btn = e.target.closest('.lgx-vpick-item');
                if (!btn) return;
                e.preventDefault();
                self.applyVendor(rowId, matches[parseInt(btn.dataset.i, 10)]);
                self.closeVendorPicker();
            });
            input.addEventListener('input', draw);
            input.addEventListener('keydown', function (e) {
                if (e.key === 'Escape') { self.closeVendorPicker(); }
                else if (e.key === 'Enter') {
                    e.preventDefault();
                    if (matches.length === 1) self.applyVendor(rowId, matches[0]);
                    else self.applyVendor(rowId, { name: input.value.trim() });
                    self.closeVendorPicker();
                }
            });
            this._vendorDocHandler = function (e) {
                if (!e.target.closest('#vendorPicker')) self.closeVendorPicker();
            };
            document.addEventListener('mousedown', this._vendorDocHandler, true);
            input.value = '';
            draw();
            input.focus();
        },
        applyVendor(rowId, vendor) {
            if (!vendor) return;
            var row = this.rows.filter(function (r) { return String(r.id) === String(rowId); })[0];
            if (!row) return;
            var patch = {
                vendorId: vendor.vendorId || '',
                vendor: vendor.name || '',
                vendorContact: vendor.contact || '',
                vendorGst: vendor.gst || '',
                vendorEmail: vendor.email || '',
                vendorAddress: vendor.address || '',
                paymentTerms: vendor.payment_terms || ''
            };
            if (!this.dirty.has(rowId)) this.dirty.set(rowId, {});
            var d = this.dirty.get(rowId);
            Object.keys(patch).forEach(function (k) { row[k] = patch[k]; d[k] = patch[k]; });
            this.render();
            this.updateSaveButton();
            if (patch.vendor) toast('Vendor "' + patch.vendor + '" applied to the row.', 'success');
        },

        /* ---------------- catalog: add a new book ---------------- */
        toggleBookForm(show) {
            var form = document.getElementById('newBookForm');
            if (!form) return;
            form.hidden = (show === undefined) ? !form.hidden : !show;
            if (!form.hidden) document.getElementById('nbTitle').focus();
        },
        async saveNewBook() {
            var val = function (id) { var el = document.getElementById(id); return el ? el.value.trim() : ''; };
            var title = val('nbTitle');
            if (!title) { toast('Enter the book or item name.', 'error'); return; }
            var stdSel = document.getElementById('catalogStandardSel');
            var std = (stdSel && stdSel.value) || this.catalogStandard || this.standardCode();
            var payload = {
                title: title,
                category: val('nbCategory') || 'STATIONERY',
                standard: std,
                subject: val('nbSubject'),
                publication: val('nbPublication'),
                edition: val('nbEdition'),
                approved_rate: val('nbRate'),
                academic_year: this.academicYear || '',
                status: 'Active'
            };
            try {
                await api('/catalog', 'POST', payload);
                ['nbTitle', 'nbSubject', 'nbPublication', 'nbEdition', 'nbRate'].forEach(function (id) {
                    var el = document.getElementById(id); if (el) el.value = '';
                });
                this.catalogStandard = null;
                await this.loadCatalog(std);
                this.renderCatalog();
                await this.loadBookOptions();
                toast('"' + title + '" added to the catalog.', 'success');
            } catch (e) { toast(e.message, 'error'); }
        },

        addRecord(preset, doRender) {
            if (doRender === undefined) doRender = true;
            var id = 'new_' + Date.now() + '_' + Math.random().toString(36).slice(2, 6);
            var row = Object.assign({
                id: id, academicYear: this.academicYear || '', standard: this.standardCode(),
                category: '', bookName: 'New Item', subject: '', publication: '', edition: '',
                vendorId: '', vendor: '', vendorContact: '', vendorGst: '',
                invoiceRef: '', invoiceDate: '',
                openingBalance: 0, strength: this.strength || 0, purchased: 0,
                approvedRate: 0, baseRate: 0, gstPercent: 0, discountPercent: 0,
                distributed: 0, returned: 0, remarks: '', _isNew: true
            }, preset || {});
            this.rows.unshift(row);
            var payload = {};
            this.columns.forEach(function (c) { if (c.type !== 'derived') payload[c.key] = row[c.key]; });
            this.dirty.set(id, payload);
            this.selectedId = id;
            if (doRender) { this.render(); this.updateSaveButton(); }
            return row;
        },

        /* ---------------- exports ---------------- */
        exportRows() {
            var self = this;
            var cols = this.visibleColumns();
            var head = ['#'].concat(cols.map(function (c) { return c.label; }));
            var body = this.filteredRows().map(function (r, i) {
                var calc = self.calc(r);
                return [String(i + 1)].concat(cols.map(function (c) { return self.cellText(r, c, calc); }));
            });
            return { head: head, body: body };
        },
        fileName(ext) {
            var st = window.App.state;
            return ('Ledger_' + st.selectedSchool.name + '_' + ((this.classInfo || {}).className || '') + '_' +
                (this.academicYear || '')).replace(/[^A-Za-z0-9_-]+/g, '_') + '.' + ext;
        },
        download(blob, name) {
            var url = URL.createObjectURL(blob);
            var a = document.createElement('a');
            a.href = url; a.download = name;
            document.body.appendChild(a); a.click(); a.remove();
            URL.revokeObjectURL(url);
        },
        exportCSV() {
            var data = this.exportRows();
            var csv = [data.head].concat(data.body).map(function (row) {
                return row.map(function (v) { return '"' + String(v).replace(/"/g, '""') + '"'; }).join(',');
            }).join('\r\n');
            this.download(new Blob(['\ufeff' + csv], { type: 'text/csv;charset=utf-8;' }), this.fileName('csv'));
        },
        exportExcel() {
            var data = this.exportRows();
            var html = '<html xmlns:x="urn:schemas-microsoft-com:office:excel"><head><meta charset="utf-8"></head><body>' +
                '<table border="1"><thead><tr>' + data.head.map(function (h) { return '<th>' + esc(h) + '</th>'; }).join('') +
                '</tr></thead><tbody>' + data.body.map(function (r) {
                    return '<tr>' + r.map(function (v) { return '<td>' + esc(v) + '</td>'; }).join('') + '</tr>';
                }).join('') + '</tbody></table></body></html>';
            this.download(new Blob([html], { type: 'application/vnd.ms-excel' }), this.fileName('xls'));
        },
        exportPDF() {
            var data = this.exportRows();
            var title = document.getElementById('printTitle').textContent;
            var win = window.open('', '_blank');
            if (!win) { toast('Allow pop-ups to export the ledger as PDF.', 'error'); return; }
            win.document.write('<html><head><title>' + esc(title) + '</title><style>' +
                'body{font-family:Segoe UI,Arial,sans-serif;padding:16px;} h2{font-size:15px;margin:0 0 10px;}' +
                'table{border-collapse:collapse;width:100%;font-size:9px;}' +
                'th,td{border:1px solid #888;padding:3px 4px;text-align:left;}' +
                'thead{background:#eef2f7;} @page{size:A3 landscape;margin:10mm;}' +
                '</style></head><body><h2>' + esc(title) + '</h2><table><thead><tr>' +
                data.head.map(function (h) { return '<th>' + esc(h) + '</th>'; }).join('') + '</tr></thead><tbody>' +
                data.body.map(function (r) {
                    return '<tr>' + r.map(function (v) { return '<td>' + esc(v) + '</td>'; }).join('') + '</tr>';
                }).join('') + '</tbody></table></body></html>');
            win.document.close();
            win.focus();
            setTimeout(function () { win.print(); }, 350);
        }
    };

    window.Ledger = Ledger;
    document.addEventListener('DOMContentLoaded', function () {
        if (document.body.getAttribute('data-page') !== 'ledger') return;
        var tries = 0;
        (function boot() {
            if (window.App && window.App.state && window.App.state.token) { Ledger.init(); return; }
            if (tries++ > 40) return;
            setTimeout(boot, 100);
        })();
    });

    /* ---- in-app unsaved-changes guard --------------------------------------
       Chrome's native "Leave site?" dialog cannot be styled, so it is replaced
       by the #leaveModal dialog for every in-app navigation. */
    var pendingNav = null;

    function hasPending() { return !!(Ledger.pendingCount && Ledger.pendingCount()); }

    function openLeaveModal(go) {
        pendingNav = go;
        var msg = document.getElementById('leaveMsg');
        if (msg) {
            var n = Ledger.pendingCount();
            msg.textContent = 'Heads up \u2014 ' + n + ' ledger change' + (n === 1 ? '' : 's') +
                (n === 1 ? ' has' : ' have') + ' not been saved yet. If you close the ledger and move to another page now, ' +
                (n === 1 ? 'this newly added data' : 'this newly added data') + ' will be lost.';
        }
        window.App.ui.openModal('leaveModal');
    }

    function closeLeaveModal() { window.App.ui.closeModal('leaveModal'); }

    Ledger.cancelLeave = function () { pendingNav = null; closeLeaveModal(); };

    Ledger.discardAndLeave = function () {
        var go = pendingNav; pendingNav = null;
        Ledger.dirty.clear(); Ledger.deleted.clear();
        closeLeaveModal();
        if (go) go();
    };

    Ledger.saveThenLeave = function () {
        var go = pendingNav; pendingNav = null;
        closeLeaveModal();
        Promise.resolve(Ledger.saveChanges()).then(function () {
            if (!hasPending() && go) go();
        }).catch(function () { /* saveChanges already reports the failure */ });
    };

    document.addEventListener('click', function (e) {
        if (document.body.getAttribute('data-page') !== 'ledger') return;
        if (!hasPending()) return;
        var a = e.target && e.target.closest ? e.target.closest('a[href]') : null;
        if (!a) return;
        var href = a.getAttribute('href') || '';
        if (!href || href.charAt(0) === '#' || a.target === '_blank' ||
            /^(mailto:|tel:|javascript:)/i.test(href)) return;
        e.preventDefault();
        openLeaveModal(function () { window.location.href = a.href; });
    }, true);
})();
