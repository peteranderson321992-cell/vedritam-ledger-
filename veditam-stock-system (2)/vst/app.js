/* app.js - Vedritam School Stock Ledger Management System */

const App = (function() {

    /* Wipes the signed-in session but keeps the few device preferences that
       are not account data — dark mode above all. Using localStorage.clear()
       here used to reset the user's theme every time they signed out. */
    function clearLocalKeepPrefs() {
        var keep = ['v_theme', 'v_sound', 'v_api_base'];
        try {
            var saved = {};
            keep.forEach(function (k) {
                var v = localStorage.getItem(k);
                if (v !== null) saved[k] = v;
            });
            localStorage.clear();
            Object.keys(saved).forEach(function (k) { localStorage.setItem(k, saved[k]); });
        } catch (e) {}
    }
    const USE_MOCK_API = false;

    /* API base URL, overridable with localStorage.v_api_base. */
    const API_BASE_URL = (function () {
        const override = (localStorage.getItem('v_api_base') || '').replace(/\/+$/, '');
        if (override) return override + '/api/v1';
        if (location.protocol === 'http:' || location.protocol === 'https:') return '/api/v1';
        return 'http://127.0.0.1:8000/api/v1';   // opened via file:// — talk to the local server
    })();

    const state = {
        token: localStorage.getItem('v_token'), username: localStorage.getItem('v_username'), role: localStorage.getItem('v_role'),
        selectedSchool: JSON.parse(localStorage.getItem('v_school') || 'null'),
        selectedClass: JSON.parse(localStorage.getItem('v_class') || 'null'),
        selectedRowId: null,
        ledgerData: [], dirtyRecords: new Map(), deletedIds: new Set(),
        selectedStandard: null, standardStrength: 0
    };
    let authRedirectStarted = false;

    const ui = {
        showToast(message, type = 'success') {
            let container = document.getElementById('toast-container');
            if (!container) {
                container = document.createElement('div');
                container.id = 'toast-container';
                document.body.appendChild(container);
            }
            const toast = document.createElement('div'); toast.className = `toast ${type}`; toast.textContent = message;
            container.appendChild(toast);
            setTimeout(() => { toast.style.opacity = '0'; setTimeout(() => toast.remove(), 300); }, 3500);
        },
        openModal(id) { document.getElementById(id).classList.add('active'); },
        closeModal(id) { document.getElementById(id).classList.remove('active'); const f = document.querySelector(`#${id} form`); if(f) f.reset(); },
        escapeHTML(str) { return String(str ?? '').replace(/[&<>'"]/g, tag => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[tag])); }
    };

    async function apiCall(endpoint, method = 'GET', body = null) {
        const headers = { 'Content-Type': 'application/json' };
        if (state.token) headers['Authorization'] = `Bearer ${state.token}`;
        let res;
        try {
            res = await fetch(`${API_BASE_URL}${endpoint}`, { method, headers, body: body ? JSON.stringify(body) : null });
        } catch (networkError) {
            // Network-level failure: server down or unreachable.
            throw new Error(
                'Cannot reach the Vedritam server. Start it with "python start.py" and open ' +
                'http://127.0.0.1:8000 in your browser (do not open index.html directly from the folder).'
            );
        }
        const data = await res.json().catch(() => ({}));
        if (res.status === 401) {
            const p = window.location.pathname;
            const onIndex = p.endsWith('index.html') || p === '/' || p === '';
            const detail = data.detail;
            const message = typeof detail === 'string' ? detail : 'Session expired.';
            if (onIndex && endpoint === '/auth/login') throw new Error(message);
            // Carry the reason across the redirect so the sign-in screen can say
            // "you were signed out because this account signed in elsewhere".
            try { sessionStorage.setItem('v_signout_reason', message); } catch (e) {}
            // Never call logout() here: logout itself contacts the server, so a
            // rejected logout request would recursively create an endless 401
            // request storm. Clear local auth once and redirect directly.
            if (!authRedirectStarted) {
                authRedirectStarted = true;
                clearLocalKeepPrefs();
                state.token = null;
                state.username = null;
                state.role = null;
                if (!onIndex) window.location.replace('index.html');
            }
            throw new Error(message);
        }
        if (!res.ok) {
            const detail = data.detail;
            throw new Error(typeof detail === 'string' ? detail
                : Array.isArray(detail) ? (detail[0]?.msg || `Error ${res.status}`)
                : `Error ${res.status}`);
        }
        return data;
    }

    async function checkAuth() {
        const path = window.location.pathname;
        const isIndex = path.endsWith('index.html') || path === '/' || path === '';
        // Explicit sign-out / "switch account" link: /?logout=1
        if (isIndex && /[?&]logout=1/.test(window.location.search)) {
            clearLocalKeepPrefs();
            state.token = null;
            history.replaceState(null, '', window.location.pathname);
        }
        if (!state.token && !isIndex) {
            window.location.href = 'index.html';
            return false;
        }
        // The root link always opens the login page first.
        applyRoleVisibility();
        const userLbl = document.getElementById('navUsername'); if (userLbl) userLbl.textContent = state.username || 'User';
        return true;
    }

    // RBAC helpers: super_admin > admin > staff > user.
    const ROLE_RANK = { user: 0, staff: 1, admin: 2, super_admin: 3 };
    function roleRank() { return ROLE_RANK[state.role] || 0; }
    function isSuperAdmin() { return state.role === 'super_admin'; }
    function isAdmin() { return state.role === 'admin'; }
    function isStaff() { return state.role === 'staff'; }
    // Super Admin, Admin or Staff - the roles that manage a school.
    function isManager() { return roleRank() >= ROLE_RANK.staff; }
    // Schools, classes, ledger, distribution, transfers, messages, reports and
    // to-do are common to every signed-in role.
    // Adding / removing a school is Super Admin only. Classes stay open to
    // every role, but only inside the schools they can already see.
    function canManageSchools() { return isSuperAdmin(); }

    function applyRoleVisibility() {
        const sup = isSuperAdmin();
        const admin = sup || isAdmin();          // Super Admin + Admin
        const manager = isManager();             // + Staff
        document.querySelectorAll('.superadmin-only').forEach(el => { el.style.display = sup ? '' : 'none'; });
        document.querySelectorAll('.admin-only').forEach(el => { el.style.display = admin ? '' : 'none'; });
        document.querySelectorAll('.user-only').forEach(el => { el.style.display = manager ? 'none' : ''; });
        document.querySelectorAll('.staff-only').forEach(el => { el.style.display = manager ? '' : 'none'; });
    }

    async function login(e) {
        e.preventDefault();
        const btn = document.getElementById('loginBtn'); btn.textContent = 'Authenticating...'; btn.disabled = true;
        const codeField = document.getElementById('twofaField');
        const codeInput = document.getElementById('twofaCode');
        try {
            const payload = {
                username: document.getElementById('username').value.trim(),
                password: document.getElementById('password').value
            };
            if (codeInput && codeInput.value.trim()) payload.code = codeInput.value.trim();
            const res = await apiCall('/auth/login', 'POST', payload);
            // Super Admin accounts with 2FA turned on answer with a challenge first.
            if (res && res.status === '2fa_required') {
                if (codeField) codeField.style.display = '';
                if (codeInput) codeInput.focus();
                ui.showToast(res.message || 'Enter your 6-digit verification code.', 'error');
                btn.textContent = 'Verify & sign in'; btn.disabled = false;
                return;
            }
            localStorage.setItem('v_token', res.access_token);
            localStorage.setItem('v_username', res.username);
            localStorage.setItem('v_role', res.role);
            localStorage.setItem('v_fullname', res.fullName || res.username);
            localStorage.setItem('v_school_id', res.school_id || '');
            localStorage.setItem('v_session_timeout', String(res.session_timeout_minutes || 30));
            localStorage.setItem('v_twofa', res.twofa_enabled ? '1' : '0');
            if (res.twofa_recommended) localStorage.setItem('v_twofa_prompt', '1');
            if (res.twofa_setup_required) {
                // Too many failed passwords and no authenticator enrolled yet:
                // send the account straight to 2FA setup before anything else.
                localStorage.setItem('v_twofa_force', '1');
                ui.showToast('Several sign-in attempts failed. Set up two-factor authentication now.', 'error');
                window.location.href = 'security.html';
                return;
            }
            localStorage.removeItem('v_twofa_force');
            localStorage.setItem('v_welcome', '1');
            window.location.href = 'dashboard.html';
        } catch (error) { ui.showToast(error.message, 'error'); btn.textContent = 'Authenticate'; btn.disabled = false; }
    }

    async function signup(e) {
        e.preventDefault();
        const pwd = document.getElementById('suPassword').value;
        const pwd2 = document.getElementById('suPassword2').value;
        if (pwd !== pwd2) return ui.showToast('The two passwords do not match.', 'error');
        if (pwd.length < 8) return ui.showToast('Password must be at least 8 characters.', 'error');
        if (!/[A-Z]/.test(pwd) || !/[a-z]/.test(pwd) || !/\d/.test(pwd))
            return ui.showToast('Password needs an uppercase letter, a lowercase letter and a number.', 'error');

        const btn = document.getElementById('signupBtn'); btn.textContent = 'Submitting...'; btn.disabled = true;
        try {
            const res = await apiCall('/auth/signup', 'POST', {
                username: document.getElementById('suUsername').value.trim(),
                password: pwd,
                fullName: document.getElementById('suFullName').value.trim(),
                email: document.getElementById('suEmail').value.trim(),
                schoolName: document.getElementById('suSchool').value.trim()
            });
            ui.showToast(res.message || 'Request submitted.', 'success');
            document.getElementById('signupForm').reset();
            auth.showTab('signin');
        } catch (error) { ui.showToast(error.message, 'error'); }
        finally { btn.textContent = 'Submit Request'; btn.disabled = false; }
    }

    const auth = {
        showTab(name) {
            const signin = name === 'signin';
            const pi = document.getElementById('paneSignin');
            const pu = document.getElementById('paneSignup');
            if (!pi || !pu) return;
            pi.style.display = signin ? '' : 'none';
            pu.style.display = signin ? 'none' : '';
            document.querySelectorAll('.auth-tab').forEach(t => t.classList.toggle('active', t.dataset.tab === name));
        },
        init() {
            const f = document.getElementById('loginForm'); if (f) f.addEventListener('submit', login);
            const sf = document.getElementById('signupForm'); if (sf) sf.addEventListener('submit', signup);
            document.querySelectorAll('.auth-tab').forEach(t => t.addEventListener('click', () => this.showTab(t.dataset.tab)));
            document.querySelectorAll('[data-goto]').forEach(a => a.addEventListener('click', (e) => { e.preventDefault(); this.showTab(a.dataset.goto); }));
        },
        async changePassword(currentPassword, newPassword) {
            return apiCall('/auth/change-password', 'POST', { currentPassword, newPassword });
        }
    };

    function clearSessionAndRedirect() {
        clearLocalKeepPrefs();
        try { sessionStorage.clear(); } catch (e) {}
        window.location.replace('index.html?logout=1');
    }

    function logout() {
        if (window.vSignOut && window.vSignOut !== logout) return window.vSignOut();
        return clearSessionAndRedirect();
    }

    if (!window.vSignOut) window.vSignOut = clearSessionAndRedirect;

    const schools = {
        allSchools: [], pendingDeleteId: null, pendingDeleteType: null,
        async init() {
            try {
                // "+ Add School" only exists for the Super Admin.
                const addBtn = document.getElementById('btnAddSchool');
                if (addBtn) addBtn.style.display = canManageSchools() ? '' : 'none';
                this.allSchools = await apiCall('/schools'); this.renderSchools(this.allSchools);
                document.getElementById('schoolSearch').addEventListener('input', (e) => {
                    this.applySearch(e.target.value);
                });
                const openBtn = document.getElementById('btnOpenLedger');
                openBtn.addEventListener('click', () => {
                    if (!state.selectedSchool) return ui.showToast('Please select a school first.', 'error');
                    if (!state.selectedClass) return ui.showToast('Please select a class first.', 'error');
                    window.location.href = 'ledger.html';
                });
                // Re-enable the button so a failed submit can be retried.
                openBtn.disabled = false;
                openBtn.classList.add('is-locked');

                // Handle ?q= (search redirected from global search) and ?add=1 (open Add School modal).
                try {
                    const params = new URLSearchParams(window.location.search);
                    const q = params.get('q');
                    if (q) this.applySearch(q);
                    if (params.get('add') === '1' && canManageSchools()) {
                        ui.openModal('modalAddSchool');
                    }
                } catch (e) {}
            } catch (e) { document.getElementById('schoolList').innerHTML = '<li class="list-item text-danger">Failed to load schools</li>'; }
        },
        classMatches(c, term) {
            const compact = String(term || '').toLowerCase().replace(/\s+/g, '');
            const name = String(c?.name || '').toLowerCase();
            const nameCompact = name.replace(/\s+/g, '');
            return name.includes(String(term || '').toLowerCase()) || (!!compact && nameCompact.includes(compact));
        },
        async applySearch(term) {
            const box = document.getElementById('schoolSearch');
            if (box) box.value = term;
            const raw = String(term || '').trim();
            const t = raw.toLowerCase();
            if (!t) {
                this.renderSchools(this.allSchools);
                document.getElementById('classSelectionArea').innerHTML = '<div style="text-align: center; color: var(--text-muted); margin-top: 2rem;">Please select a school from the left panel first.</div>';
                document.getElementById('selectionSummary').innerHTML = 'No class selected.';
                return;
            }
            const schoolMatches = this.allSchools.filter(s =>
                (s.name || '').toLowerCase().includes(t) ||
                (s.code || '').toLowerCase().includes(t) ||
                (s.location || '').toLowerCase().includes(t)
            );
            this.renderSchools(schoolMatches);
            await this.searchClassesEverywhere(raw, schoolMatches.length > 0);
        },
        async searchClassesEverywhere(term, hasSchoolMatches) {
            const classArea = document.getElementById('classSelectionArea');
            if (!classArea) return;
            classArea.innerHTML = '<div style="padding:20px; text-align:center; color:var(--text-muted);">Searching classes in every school...</div>';
            try {
                const batches = await Promise.all(this.allSchools.map(async (s) => {
                    try {
                        const classes = await apiCall(`/schools/${s.id}/classes`);
                        return classes
                            .filter(c => this.classMatches(c, term))
                            .map(c => ({ school: s, classInfo: c }));
                    } catch (e) { return []; }
                }));
                const matches = batches.flat();
                if (!matches.length) {
                    classArea.innerHTML = `<div style="padding:20px; text-align:center; color:var(--text-muted);">${hasSchoolMatches ? 'No matching classes found.' : 'No schools or classes match your search.'}</div>`;
                    return;
                }
                classArea.innerHTML = `<ul class="list-group">${matches.map(m => `
                    <li class="list-item class-search-result" style="display:flex; justify-content:space-between; align-items:center;" onclick="App.schools.selectClassSearch(${m.school.id}, '${ui.escapeHTML(m.school.name)}', ${m.classInfo.id}, '${ui.escapeHTML(m.classInfo.name)}', ${parseInt(m.classInfo.strength) || 0}, this)">
                        <span><strong>${ui.escapeHTML(m.classInfo.name)}</strong><br><small style="color:var(--text-muted);">${ui.escapeHTML(m.school.name)} • Strength: <strong>${parseInt(m.classInfo.strength) || 0}</strong></small></span>
                    </li>
                `).join('')}</ul>`;
            } catch (e) {
                classArea.innerHTML = '<div class="text-danger" style="padding:20px;">Failed to search classes.</div>';
            }
        },
        renderSchools(list) {
            document.getElementById('schoolList').innerHTML = list.map(s => `
                <li class="list-item" style="display:flex; justify-content:space-between; align-items:center;" onclick="App.schools.selectSchool(${s.id}, '${ui.escapeHTML(s.name)}')">
                    <div><strong>${ui.escapeHTML(s.name)}</strong><br><small style="color:var(--text-muted)">Code: ${ui.escapeHTML(s.code || 'N/A')} | ${ui.escapeHTML(s.location || s.address || 'N/A')}${s.academic_year ? ' | ' + ui.escapeHTML(s.academic_year) : ''}${s.status && s.status !== 'Active' ? ' | ' + ui.escapeHTML(s.status) : ''}</small></div>
                    ${canManageSchools() ? `<button class="icon-btn" title="Delete this school" onclick="event.stopPropagation(); App.schools.confirmDelete('school', ${s.id})">✖</button>` : ''}
                </li>
            `).join('');
        },
        async selectSchool(id, name) {
            document.querySelectorAll('#schoolList .list-item').forEach(el => el.classList.remove('active')); event.currentTarget.classList.add('active');
            state.selectedSchool = { id, name }; localStorage.setItem('v_school', JSON.stringify(state.selectedSchool));
            const btnAddClass = document.getElementById('btnAddClass'); if(btnAddClass) btnAddClass.disabled = false;
            await this.loadClassesForSchool(id);
        },
        async submitAddSchool(e) {
            e.preventDefault(); const btn = document.getElementById('btnSaveSchool'); btn.disabled = true; btn.textContent = 'Saving...';
            try {
                const val = (id) => (document.getElementById(id)?.value || '').trim();
                await apiCall('/schools', 'POST', {
                    name: val('newSchoolName'),
                    code: val('newSchoolCode'),
                    location: val('newSchoolLocation'),
                    logo: val('newSchoolLogo'),
                    address: val('newSchoolAddress'),
                    contact: val('newSchoolContact'),
                    academic_year: val('newSchoolYear'),
                    status: val('newSchoolStatus') || 'Active',
                    assigned_staff: val('newSchoolStaff').split(',').map(s => s.trim()).filter(Boolean)
                });
                ui.showToast('School created successfully.', 'success'); ui.closeModal('modalAddSchool');
                this.allSchools = await apiCall('/schools'); this.renderSchools(this.allSchools);
            } catch (err) { ui.showToast(err.message, 'error'); }
            finally { btn.disabled = false; btn.textContent = 'Save School'; }
        },
        async loadClassesForSchool(id) {
            const classArea = document.getElementById('classSelectionArea'); classArea.innerHTML = '<div style="padding:20px;">Loading classes...</div>';
            try {
                const classes = await apiCall(`/schools/${id}/classes`);
                if(classes.length === 0) { classArea.innerHTML = '<div style="padding:20px; text-align:center;">No classes found.</div>'; return; }
                classArea.innerHTML = `<ul class="list-group">${classes.map(c => `
                    <li class="list-item" style="display:flex; justify-content:space-between; align-items:center;" onclick="App.schools.selectClass(${c.id}, '${c.name}', ${c.strength}, this)">
                        <span>${c.name} <br><small style="color:var(--text-muted);">Strength: <strong>${c.strength}</strong></small></span>
                        <button class="icon-btn" title="Delete this class" onclick="event.stopPropagation(); App.schools.confirmDelete('class', ${c.id})">✖</button>
                    </li>
                `).join('')}</ul>`;
            } catch (e) { classArea.innerHTML = '<div class="text-danger">Failed to load classes.</div>'; }
        },
        async submitAddClass(e) {
            e.preventDefault(); if (!state.selectedSchool) return;
            const btn = document.getElementById('btnSaveClass'); btn.disabled = true; btn.textContent = 'Saving...';
            try {
                await apiCall(`/schools/${state.selectedSchool.id}/classes`, 'POST', { name: document.getElementById('newClassName').value, strength: parseInt(document.getElementById('newClassStrength').value) });
                ui.showToast('Class added successfully.', 'success'); ui.closeModal('modalAddClass');
                await this.loadClassesForSchool(state.selectedSchool.id);
            } catch (err) { ui.showToast(err.message, 'error'); }
            finally { btn.disabled = false; btn.textContent = 'Save Class'; }
        },
        selectClassSearch(schoolId, schoolName, classId, className, strength, element) {
            state.selectedSchool = { id: schoolId, name: schoolName };
            localStorage.setItem('v_school', JSON.stringify(state.selectedSchool));
            this.selectClass(classId, className, strength, element);
        },
        selectClass(id, name, strength, element) {
            document.querySelectorAll('#classSelectionArea .list-item').forEach(el => el.classList.remove('active')); element.classList.add('active');
            state.selectedClass = { id, name, strength }; localStorage.setItem('v_class', JSON.stringify(state.selectedClass));
            document.getElementById('selectionSummary').innerHTML = `<strong>Ready:</strong> ${state.selectedSchool.name} <br>Class: ${name} (Strength: ${strength})`;
            const openBtn = document.getElementById('btnOpenLedger');
            openBtn.disabled = false;
            openBtn.classList.remove('is-locked');
        },
        confirmDelete(type, id) {
            this.pendingDeleteType = type; this.pendingDeleteId = id;
            document.getElementById('lblDeleteType').textContent = type;
            ui.openModal('modalDeleteStruct');
        },
        async executeDeleteStruct() {
            try {
                if (this.pendingDeleteType === 'school') {
                    await apiCall(`/schools/${this.pendingDeleteId}`, 'DELETE');
                    this.allSchools = await apiCall('/schools'); this.renderSchools(this.allSchools);
                    if(state.selectedSchool && state.selectedSchool.id === this.pendingDeleteId) { document.getElementById('classSelectionArea').innerHTML = ''; document.getElementById('btnOpenLedger').disabled = true; }
                } else {
                    await apiCall(`/schools/${state.selectedSchool.id}/classes/${this.pendingDeleteId}`, 'DELETE');
                    await this.loadClassesForSchool(state.selectedSchool.id);
                }
                ui.showToast(`${this.pendingDeleteType} deleted.`, 'success');
                ui.closeModal('modalDeleteStruct');
            } catch (err) { ui.showToast(err.message, 'error'); ui.closeModal('modalDeleteStruct'); }
        }
    };

    const ledger = {
        // Mirrors the printed "STOCK REGISTER FOR TEXT BOOKS / NOTE BOOKS" columns.
        schema: [
            { key: 'standard', label: 'Standard', type: 'text', group: 'std' },
            { key: 'vendorId', label: 'Vendor ID', type: 'text', group: 'vendor' },
            { key: 'vendor', label: 'Vendor Name', type: 'text', group: 'vendor' },
            { key: 'vendorContact', label: 'Contact Number', type: 'text', group: 'vendor' },
            { key: 'vendorGst', label: 'Vendor GST No', type: 'text', group: 'vendor' },
            { key: 'invoiceDate', label: 'Invoice Date', type: 'date', group: 'invoice' },
            { key: 'invoiceRef', label: 'Invoice No', type: 'text', group: 'invoice' },
            { key: 'category', label: 'Category', type: 'text', group: 'article' },
            { key: 'subject', label: 'Subject', type: 'text', group: 'article' },
            { key: 'bookName', label: 'Book / Article Name', type: 'text', group: 'article' },
            { key: 'publication', label: 'Publication', type: 'text', group: 'article' },
            { key: 'edition', label: 'Edition / Year', type: 'text', group: 'article' },
            { key: 'openingBalance', label: 'Opening Balance', type: 'number', group: 'qty' },
            { key: 'purchased', label: 'Qty Purchased', type: 'number', group: 'qty' },
            { key: 'approvedRate', label: 'Approved Rate', type: 'decimal', group: 'money' },
            { key: 'baseRate', label: 'Base Rate', type: 'decimal', group: 'money' },
            { key: 'gstAmount', label: 'GST Amount', type: 'decimal', group: 'money' },
            { key: 'discountPercent', label: 'Discount %', type: 'decimal', group: 'money' },
            { key: 'discountAmount', label: 'Discount Amount', type: 'readonly', group: 'money' },
            { key: 'totalAmount', label: 'Total Amount', type: 'readonly', group: 'money' },
            { key: 'strength', label: 'Strength', type: 'number', group: 'issue' },
            { key: 'booksRequired', label: 'Req. Books', type: 'readonly', group: 'issue' },
            { key: 'distributed', label: 'Issued', type: 'number', group: 'issue' },
            { key: 'returned', label: 'Returns', type: 'number', group: 'issue' },
            { key: 'closingBalance', label: 'Closing Balance', type: 'readonly', group: 'issue' },
            { key: 'remarks', label: 'Remarks', type: 'text', group: 'issue' }
        ],
        standards: [], categories: [], catalog: [], catalogStandard: null,

        classInfo: null,
        get standard() { return state.selectedStandard || 'OTHERS'; },
        // The ledger is always scoped to one class now, so there is no
        // "all standards" view: a class only ever holds its own data.
        isAllView() { return false; },
        isLocked() { return !!(this.classInfo && this.classInfo.locked); },

        async init() {
            if (!state.selectedSchool) { window.location.href = 'schools.html'; return; }
            if (!state.selectedClass) { window.location.href = 'schools.html'; return; }
            document.getElementById('lblSchoolName').textContent = state.selectedSchool.name;

            // The ledger always opens on the class that was selected: each class
            // keeps its own records, so we never restore a previous standard.
            state.selectedStandard = this.guessStandard(state.selectedClass.name) || 'OTHERS';
            try { localStorage.setItem('v_standard', state.selectedStandard); } catch (e) {}


            await this.loadStandards();
            this.buildHeader();
            await this.fetchData();

            document.getElementById('ledgerSearch').addEventListener('input', (e) => this.filterTable(e.target.value));
            const stdSel = document.getElementById('standardSelect');
            if (stdSel) stdSel.addEventListener('change', (e) => this.changeStandard(e.target.value));
            const catSel = document.getElementById('categorySelect');
            if (catSel) catSel.addEventListener('change', () => this.render());

            const table = document.getElementById('ledgerTable');
            table.addEventListener('keydown', this.handleKeydown.bind(this));
            table.addEventListener('focusout', (e) => {
                if (!e.target.classList.contains('editable')) return;
                const tr = e.target.closest('tr');
                if (tr && tr.dataset.id) this.cellEdited(tr.dataset.id, e.target);
            });
            table.addEventListener('click', (e) => {
                const tr = e.target.closest('#ledgerBody tr');
                if (tr && tr.dataset.id) this.selectRow(tr.dataset.id);
            });
            table.addEventListener('focusin', (e) => {
                const tr = e.target.closest('#ledgerBody tr');
                if (tr && tr.dataset.id) this.selectRow(tr.dataset.id);
            });
        },

        // Maps "Class 5-A" / "LKG" onto a register standard so the dropdown opens
        // on the class the user came from.
        guessStandard(name) {
            const t = String(name || '').trim().toUpperCase();
            if (!t) return null;
            if (t.includes('PRE')) return 'PRE KG';
            if (t.includes('LKG')) return 'LKG';
            if (t.includes('UKG')) return 'UKG';
            const roman = ['I','II','III','IV','V','VI','VII','VIII','IX','X','XI','XII'];
            const num = t.match(/\b(1[0-2]|[1-9])\b/);
            if (num) return roman[parseInt(num[1], 10) - 1];
            const rm = t.replace(/CLASS|STD|STANDARD/g, '').trim().match(/^(XII|XI|X|IX|VIII|VII|VI|V|IV|III|II|I)\b/);
            return rm ? rm[1] : null;
        },

        async loadStandards() {
            try {
                const meta = await apiCall('/catalog/standards');
                this.standards = meta.standards || [];
                this.categories = meta.categories || [];
            } catch (e) {
                this.standards = ['PRE KG','LKG','UKG','I','II','III','IV','V','VI','VII','VIII','IX','X','XI','XII','OTHERS'];
                this.categories = ['TB','NB','STATIONERY','INHOUSE'];
            }
            const stdSel = document.getElementById('standardSelect');
            if (stdSel) {
                stdSel.innerHTML = this.standards.map(s => `<option value="${s}">${this.standardLabel(s)}</option>`).join('');
                if (!this.standards.includes(this.standard)) {
                    stdSel.innerHTML = `<option value="${this.standard}">${this.standardLabel(this.standard)}</option>` + stdSel.innerHTML;
                }
                stdSel.value = this.standard;
                stdSel.disabled = this.isLocked();
                stdSel.title = this.isLocked()
                    ? `${(this.classInfo && this.classInfo.className) || 'This class'} accepts only ${this.standardLabel(this.standard)} data.`
                    : '';
            }
            const catSel = document.getElementById('categorySelect');
            if (catSel) {
                catSel.innerHTML = `<option value="ALL">All Categories</option>` +
                    this.categories.map(c => `<option value="${c}">${this.categoryLabel(c)}</option>`).join('');
            }
            this.loadBookOptions();
        },

        /* ---- Book dropdown: every catalog book for the chosen standard.
           Picking one drops it straight into the register (same result as the
           catalog picker, one click instead of a modal). ---- */
        async loadBookOptions() {
            const sel = document.getElementById('bookSelect');
            if (!sel) return;
            if (this.isAllView()) {
                sel.innerHTML = '<option value="">Pick a standard first</option>';
                sel.disabled = true;
                return;
            }
            sel.disabled = true;
            sel.innerHTML = '<option value="">Loading books…</option>';
            try {
                if (this.catalogStandard !== this.standard || !this.catalog.length) {
                    this.catalog = await apiCall(`/catalog?standard=${encodeURIComponent(this.standard)}`);
                    this.catalogStandard = this.standard;
                }
                if (!this.catalog.length) {
                    sel.innerHTML = '<option value="">No books in this standard</option>';
                    return;
                }
                sel.innerHTML = '<option value="">-- add a book --</option>' +
                    this.catalog.map((it, i) => {
                        const extra = [it.subject, it.publication].filter(Boolean).join(' · ');
                        return `<option value="${i}">${ui.escapeHTML(it.title)}${extra ? ' — ' + ui.escapeHTML(extra) : ''}</option>`;
                    }).join('');
                sel.disabled = false;
            } catch (e) {
                sel.innerHTML = '<option value="">Could not load books</option>';
            }
        },
        pickBook(value) {
            const sel = document.getElementById('bookSelect');
            const it = this.catalog[parseInt(value, 10)];
            if (sel) sel.value = '';
            if (!it) return;
            this.addRecord({
                bookName: it.title, subject: it.subject || '', publication: it.publication || '',
                category: it.category || '', standard: it.standard || this.standard
            });
            ui.showToast(`"${it.title}" added — press Save Changes to confirm.`, 'success');
        },
        standardLabel(s) {
            if (['PRE KG', 'LKG', 'UKG', 'OTHERS'].includes(s)) return s;
            return `Class ${s}`;
        },
        categoryLabel(c) {
            return { TB: 'Text Books', NB: 'Note Books', STATIONERY: 'Stationery', INHOUSE: 'Inhouse' }[c] || c;
        },

        async changeStandard(value) {
            if (this.isLocked()) {
                document.getElementById('standardSelect').value = this.standard;
                ui.showToast(`${this.classInfo.className} can only hold ${this.standardLabel(this.standard)} data.`, 'error');
                return;
            }
            if (state.dirtyRecords.size || state.deletedIds.size) {
                if (!confirm('You have unsaved changes that will be lost. Switch standard anyway?')) {
                    document.getElementById('standardSelect').value = this.standard;
                    return;
                }
            }
            state.selectedStandard = value;
            try { localStorage.setItem('v_standard', value); } catch (e) {}
            await this.fetchData();
            this.loadBookOptions();
        },

        // Builds the 22-column header, grouped like the printed register.
        buildHeader() {
            const head = document.getElementById('ledgerHead');
            if (!head) return;
            const groups = [
                { key: 'std', label: '' },
                { key: 'vendor', label: 'Vendor Details' },
                { key: 'invoice', label: 'Invoice' },
                { key: 'article', label: 'Article Details' },
                { key: 'qty', label: 'Quantity' },
                { key: 'money', label: 'Rate & Amount' },
                { key: 'issue', label: 'Issue / Balance' }
            ];
            let top = `<tr class="group-row"><th class="col-idx" rowspan="2">#</th>`;
            groups.forEach(g => {
                const span = this.schema.filter(c => c.group === g.key).length;
                if (!span) return;
                if (g.label) top += `<th colspan="${span}" class="group-head group-${g.key}">${g.label}</th>`;
                else top += `<th rowspan="2" class="col-std">Standard</th>`;
            });
            top += `</tr><tr>`;
            this.schema.forEach(col => {
                if (col.group === 'std') return;
                const cls = [col.type === 'readonly' ? 'col-derived' : '',
                             ['number','decimal','readonly'].includes(col.type) ? 'col-num' : ''].join(' ');
                top += `<th class="${cls}">${col.label}</th>`;
            });
            top += `</tr>`;
            head.innerHTML = top;
        },

        async fetchData() {
            try {
                const res = await apiCall(`/ledger/class/${state.selectedSchool.id}/${state.selectedClass.id}`);
                this.classInfo = { className: res.className, locked: !!res.locked, standard: res.standard };
                state.selectedStandard = res.standard || this.standard;
                state.ledgerData = res.rows || [];
                state.standardStrength = res.strength || 0;
                const stdSel = document.getElementById('standardSelect');
                if (stdSel) { stdSel.value = this.standard; stdSel.disabled = this.isLocked(); }
                state.dirtyRecords.clear(); state.deletedIds.clear();
                this.updateLabels();
                this.updateSaveButton(); this.render();
            } catch (e) { ui.showToast('Failed to load ledger records.', 'error'); }
        },

        updateLabels() {
            const lbl = document.getElementById('lblClassName');
            const cname = (this.classInfo && this.classInfo.className) || (state.selectedClass && state.selectedClass.name) || '';
            if (lbl) lbl.textContent = cname || this.standardLabel(this.standard);
            const strLbl = document.getElementById('lblClassStrength');
            if (strLbl) {
                strLbl.textContent = this.isAllView()
                    ? `${state.ledgerData.filter(r => !r._deleted).length} rows`
                    : `Strength: ${state.standardStrength || 0} Students`;
            }
        },

        visibleRows() {
            const cat = (document.getElementById('categorySelect') || {}).value || 'ALL';
            return state.ledgerData.filter(r => {
                if (r._deleted) return false;
                if (cat !== 'ALL' && String(r.category || '').toUpperCase() !== cat) return false;
                return true;
            });
        },

        render() {
            const tbody = document.getElementById('ledgerBody'); tbody.innerHTML = '';
            const rows = this.visibleRows();
            if (!rows.length) {
                tbody.innerHTML = `<tr class="empty-row"><td colspan="${this.schema.length + 1}">
                    No records for ${this.isAllView() ? 'any standard' : this.standardLabel(this.standard)} yet.
                    Use “+ Add from Catalog” to pick books for this standard.</td></tr>`;
                return;
            }
            let lastStd = null;
            rows.forEach((row, index) => {
                // In the all-standards view, insert a divider whenever the standard changes.
                if (this.isAllView() && row.standard !== lastStd) {
                    lastStd = row.standard;
                    const sep = document.createElement('tr');
                    sep.className = 'standard-divider';
                    sep.innerHTML = `<td colspan="${this.schema.length + 1}">${this.standardLabel(lastStd)}</td>`;
                    tbody.appendChild(sep);
                }
                const tr = document.createElement('tr'); tr.dataset.id = row.id;
                let html = `<td class="col-idx">${index + 1}</td>`;
                this.schema.forEach(col => {
                    const val = this.cellValue(row, col);
                    const dirty = state.dirtyRecords.has(row.id) && state.dirtyRecords.get(row.id)[col.key] !== undefined;
                    const alignClass = ['number', 'decimal', 'readonly'].includes(col.type) ? 'col-num' : '';
                    if (col.type === 'readonly') {
                        html += `<td data-key="${col.key}" class="${alignClass} col-derived ${this.derivedClass(col.key, val)}">${ui.escapeHTML(this.fmt(col, val))}</td>`;
                    } else {
                        html += `<td class="editable ${alignClass} ${dirty ? 'dirty' : ''}" data-key="${col.key}" data-type="${col.type}" contenteditable="true">${ui.escapeHTML(this.fmt(col, val))}</td>`;
                    }
                });
                if (state.selectedRowId && String(row.id) === String(state.selectedRowId)) tr.classList.add('row-selected');
                tr.innerHTML = html;
                tbody.appendChild(tr);
            });
        },
        cellValue(row, col) {
            if (col.key === 'strength' && (row.strength === undefined || row.strength === null || row.strength === '' || row.strength === 0)) {
                return this.isAllView() ? (row.strength ?? '') : (state.standardStrength || '');
            }
            return row[col.key] ?? '';
        },
        fmt(col, val) {
            if (col.type === 'decimal' || (col.type === 'readonly' && ['discountAmount', 'totalAmount'].includes(col.key))) {
                const n = parseFloat(val);
                return isNaN(n) ? '' : n.toFixed(2);
            }
            return String(val);
        },
        derivedClass(key, val) {
            const n = parseFloat(val) || 0;
            if (key === 'closingBalance') return n <= 0 ? 'text-danger' : 'text-success';
            if (key === 'booksRequired') return n > 0 ? 'text-warning' : '';
            return '';
        },

        cellEdited(rowId, td) {
            const key = td.dataset.key, type = td.dataset.type;
            let val = td.innerText.trim();
            if (type === 'number') val = Math.max(parseInt(val) || 0, 0);
            else if (type === 'decimal') val = Math.max(parseFloat(val) || 0, 0).toFixed(2);
            if (key === 'standard') {
                val = this.guessStandard(val) || val.toUpperCase() || 'OTHERS';
                if (this.isLocked() && val !== this.standard) {
                    ui.showToast(`${this.classInfo.className} only accepts ${this.standardLabel(this.standard)} data.`, 'error');
                    val = this.standard;
                }
            }
            if (key === 'category') val = val.toUpperCase();
            td.innerText = val;

            const row = state.ledgerData.find(r => String(r.id) === String(rowId));
            if (!row) return;
            row[key] = val;
            if (!state.dirtyRecords.has(rowId)) state.dirtyRecords.set(rowId, {});
            state.dirtyRecords.get(rowId)[key] = val;
            td.classList.add('dirty');
            this.recalcRow(row, td.parentElement);
            this.updateSaveButton();
        },

        // Mirrors the server-side maths so edits show live totals before saving.
        recalcRow(row, tr) {
            const num = k => parseInt(row[k]) || 0;
            const dec = k => parseFloat(row[k]) || 0;
            const p = num('purchased'), d = num('distributed'), r = num('returned'), ob = num('openingBalance');
            const s = num('strength') || (this.isAllView() ? 0 : (parseInt(state.standardStrength) || 0));
            const qty = p > 0 ? p : 1;

            row.balance = p - d + r;
            row.booksRequired = Math.max(s - p, 0);
            row.closingBalance = ob + p - d - r;
            row.discountAmount = +(dec('baseRate') * qty * dec('discountPercent') / 100).toFixed(2);
            row.totalAmount = +(dec('baseRate') * qty + dec('gstAmount') - row.discountAmount).toFixed(2);

            if (!tr) return;
            ['booksRequired', 'closingBalance', 'discountAmount', 'totalAmount'].forEach(k => {
                const cell = tr.querySelector(`td[data-key="${k}"]`);
                if (!cell) return;
                const isMoney = k === 'discountAmount' || k === 'totalAmount';
                cell.textContent = isMoney ? Number(row[k]).toFixed(2) : row[k];
                cell.className = `col-num col-derived ${this.derivedClass(k, row[k])}`;
            });
        },

        /* ---- Catalog picker: choose books from the standard's master list ---- */
        async openCatalog() {
            if (this.isAllView()) {
                return ui.showToast('Pick a standard first — the catalog is per standard.', 'info');
            }
            const modal = document.getElementById('catalogModal');
            const body = document.getElementById('catalogList');
            document.getElementById('catalogTitle').textContent = `Catalog — ${this.standardLabel(this.standard)}`;
            modal.classList.add('active');
            body.innerHTML = '<p class="muted">Loading catalog…</p>';
            try {
                if (this.catalogStandard !== this.standard) {
                    this.catalog = await apiCall(`/catalog?standard=${encodeURIComponent(this.standard)}`);
                    this.catalogStandard = this.standard;
                }
                this.renderCatalog();
            } catch (e) {
                body.innerHTML = '<p class="text-danger">Could not load the catalog.</p>';
            }
        },
        closeCatalog() { document.getElementById('catalogModal').classList.remove('active'); },
        renderCatalog() {
            const body = document.getElementById('catalogList');
            const term = (document.getElementById('catalogSearch').value || '').toLowerCase();
            const cat = document.getElementById('catalogCategory').value;
            const items = this.catalog.filter(it => {
                if (cat !== 'ALL' && String(it.category || '').toUpperCase() !== cat) return false;
                return !term || `${it.title} ${it.subject} ${it.publication}`.toLowerCase().includes(term);
            });
            if (!items.length) { body.innerHTML = '<p class="muted">No catalog items match.</p>'; return; }
            body.innerHTML = items.map((it, i) => `
                <label class="catalog-item">
                    <input type="checkbox" data-idx="${this.catalog.indexOf(it)}">
                    <span class="badge badge-${String(it.category || '').toLowerCase()}">${it.category || '-'}</span>
                    <span class="catalog-title">${ui.escapeHTML(it.title)}</span>
                    <span class="muted">${ui.escapeHTML(it.subject || '')}${it.publication ? ' · ' + ui.escapeHTML(it.publication) : ''}</span>
                </label>`).join('');
        },
        addSelectedFromCatalog() {
            const picked = Array.from(document.querySelectorAll('#catalogList input:checked'))
                .map(cb => this.catalog[parseInt(cb.dataset.idx, 10)]).filter(Boolean);
            if (!picked.length) return ui.showToast('Select at least one item.', 'info');
            picked.forEach(it => this.addRecord({
                bookName: it.title, subject: it.subject || '', publication: it.publication || '',
                category: it.category || '', standard: it.standard || this.standard
            }, false));
            this.closeCatalog();
            this.render(); this.updateSaveButton();
            ui.showToast(`${picked.length} row(s) added. Press Save Changes to confirm.`, 'success');
        },

        addRecord(preset, doRender = true) {
            const newId = 'new_' + Date.now() + '_' + Math.random().toString(36).slice(2, 6);
            let std = (preset && preset.standard) || this.standard;
            if (this.isLocked() && (this.guessStandard(std) || std) !== this.standard) {
                ui.showToast(`${this.classInfo.className} only accepts ${this.standardLabel(this.standard)} data.`, 'error');
                return;
            }
            if (this.isLocked()) std = this.standard;
            const strength = this.isAllView() ? 0 : (parseInt(state.standardStrength) || 0);
            const newRow = Object.assign({
                id: newId, standard: std, bookName: 'New Item', category: '', subject: '', publication: '',
                edition: '', vendorId: '', vendor: '', vendorContact: '', vendorGst: '',
                invoiceDate: '', invoiceRef: '',
                openingBalance: 0, purchased: 0, approvedRate: 0, baseRate: 0, gstAmount: 0,
                discountPercent: 0, discountAmount: 0, totalAmount: 0,
                strength: strength, booksRequired: strength, distributed: 0, returned: 0,
                closingBalance: 0, balance: 0, remarks: '', _isNew: true
            }, preset || {});
            state.ledgerData.unshift(newRow);
            const payload = {};
            this.schema.forEach(c => { if (c.type !== 'readonly') payload[c.key] = newRow[c.key]; });
            state.dirtyRecords.set(newId, payload);
            if (doRender) { this.render(); this.updateSaveButton(); }
        },

        async downloadCSV() {
            try {
                ui.showToast('Generating CSV...', 'info');
                const res = await fetch(`${API_BASE_URL}/ledger/${state.selectedSchool.id}/${state.selectedClass.id}/download`, {
                    headers: { 'Authorization': `Bearer ${state.token}` }
                });
                if (!res.ok) throw new Error("Download failed");
                const blob = await res.blob();
                const url = window.URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                const cd = res.headers.get('Content-Disposition');
                let filename = 'Register.csv';
                if (cd && cd.includes('filename=')) filename = cd.split('filename=')[1].replace(/["']/g, '');
                a.download = filename;
                document.body.appendChild(a); a.click(); a.remove();
                window.URL.revokeObjectURL(url);
                ui.showToast('Download complete.', 'success');
            } catch (e) { ui.showToast('Failed to download CSV.', 'error'); }
        },
        // Wide register: the ledger fills the whole browser window.
        // We deliberately do NOT use the native Fullscreen API here — it paints a
        // black backdrop, clips modals/dropdowns rendered outside the element and
        // shows the browser's "press Esc" banner. A CSS overlay behaves better.
        toggleFullScreen(force) {
            const btn = document.getElementById('btnFullScreen');
            const on = typeof force === 'boolean'
                ? force
                : !document.body.classList.contains('ledger-fullscreen');

            // Leave any native fullscreen a previous version may have entered.
            if (document.fullscreenElement && document.exitFullscreen) {
                try { document.exitFullscreen(); } catch (e) { /* ignore */ }
            }

            document.body.classList.toggle('ledger-fullscreen', on);
            if (btn) {
                btn.innerHTML = on ? '\u2715 Exit Full Screen' : '\u26F6 Full Screen';
                btn.setAttribute('aria-pressed', on ? 'true' : 'false');
                btn.title = on ? 'Back to the normal layout' : 'Show the ledger full screen';
            }

            if (!this._fsBound) {
                this._fsBound = true;
                document.addEventListener('keydown', (e) => {
                    if (e.key === 'Escape' && document.body.classList.contains('ledger-fullscreen')) {
                        // Don't steal Esc from an open dialog / editing cell.
                        if (document.querySelector('.modal.show, .modal[style*="display: flex"], .modal[style*="display:flex"]')) return;
                        if (document.activeElement && document.activeElement.classList.contains('editable')) return;
                        this.toggleFullScreen(false);
                    }
                });
            }
            // Recalculate any sticky header / virtual scroll sizes.
            window.dispatchEvent(new Event('resize'));
        },

        handleKeydown(e) {
            if (!e.target.classList.contains('editable')) return;
            const td = e.target; const tr = td.parentElement;
            if (e.key === 'Enter') {
                e.preventDefault(); td.blur();
                const nextTr = tr.nextElementSibling;
                if (nextTr) { const nextTd = nextTr.children[Array.from(tr.children).indexOf(td)]; if (nextTd && nextTd.classList.contains('editable')) nextTd.focus(); }
            }
        },
        selectRow(id) {
            state.selectedRowId = String(id);
            document.querySelectorAll('#ledgerBody tr').forEach(tr => {
                tr.classList.toggle('row-selected', String(tr.dataset.id) === state.selectedRowId);
            });
        },
        deleteSelected() {
            const id = state.selectedRowId;
            const exists = id && state.ledgerData.some(r => String(r.id) === String(id) && !r._deleted);
            if (!exists) return ui.showToast('Click a row in the table first, then press Delete.', 'info');
            const row = state.ledgerData.find(r => String(r.id) === String(id));
            const name = (row && row.bookName) ? row.bookName : 'this row';
            if (!confirm(`Delete "${name}"? This is confirmed when you press Save Changes.`)) return;
            state.selectedRowId = null;
            this.deleteRecord(id);
        },
        deleteRecord(id) {
            if (id === undefined || id === null || id === '') return;
            const sid = String(id);
            if (sid.startsWith('new_')) {
                state.ledgerData = state.ledgerData.filter(r => String(r.id) !== sid);
                state.dirtyRecords.delete(sid);
                state.dirtyRecords.delete(id);
            } else {
                const row = state.ledgerData.find(r => String(r.id) === sid);
                if (row) row._deleted = true;
                state.deletedIds.add(sid);
            }
            this.render(); this.updateSaveButton(); this.updateLabels();
            ui.showToast('Row removed. Press Save Changes to confirm.', 'info');
        },
        async saveChanges() {
            if (state.dirtyRecords.size === 0 && state.deletedIds.size === 0) return ui.showToast('No unsaved changes.', 'info');
            const btn = document.getElementById('btnSaveLedger'); btn.textContent = 'Saving...'; btn.disabled = true;
            try {
                await apiCall(`/ledger/sync`, 'POST', {
                    schoolId: state.selectedSchool.id,
                    classId: (state.selectedClass && state.selectedClass.id) || 0,
                    standard: this.isAllView() ? 'OTHERS' : this.standard,
                    updates: Array.from(state.dirtyRecords.entries()).map(([id, changes]) => ({ id, ...changes })),
                    deletes: Array.from(state.deletedIds)
                });
                ui.showToast('Changes saved successfully.', 'success'); await this.fetchData();
                document.dispatchEvent(new CustomEvent('v-data-changed'));
                try { localStorage.setItem('v_data_version', String(Date.now())); } catch (e) {}
            } catch (err) { ui.showToast(err.message, 'error'); }
            finally { this.updateSaveButton(); }
        },
        updateSaveButton() {
            const btn = document.getElementById('btnSaveLedger'); if (!btn) return;
            const count = state.dirtyRecords.size + state.deletedIds.size;
            btn.textContent = `Save Changes (${count})`; btn.disabled = count === 0;
            btn.style.backgroundColor = count > 0 ? 'var(--warning)' : 'var(--primary-color)';
            btn.style.borderColor = count > 0 ? 'var(--warning)' : 'var(--primary-color)';
        },
        filterTable(term) {
            term = term.toLowerCase();
            document.querySelectorAll('#ledgerBody tr').forEach(row => {
                if (row.classList.contains('standard-divider')) { row.style.display = term ? 'none' : ''; return; }
                row.style.display = row.innerText.toLowerCase().includes(term) ? '' : 'none';
            });
        }
    };



    /* ---------------- User Administration (admin only) ---------------- */
    const users = {
        all: [], schools: [], editing: null,
        meta: { canCreate: false, creatableRoles: [], canDelete: false, schoolLocked: true },
        async init() {
            if (!isManager()) {
                document.getElementById('usersBody').innerHTML =
                    '<tr><td colspan="8" style="padding:20px;text-align:center;">Staff access or above required.</td></tr>';
                const nb = document.getElementById('btnNewUser'); if (nb) nb.style.display = 'none';
                return;
            }
            try { this.meta = await apiCall('/users/meta'); } catch (e) { /* keep defaults */ }
            const nb = document.getElementById('btnNewUser');
            if (nb) nb.style.display = this.meta.canCreate ? '' : 'none';
            this.fillRoleSelects();
            try { this.schools = await apiCall('/schools'); } catch (e) { this.schools = []; }
            this.fillSchoolSelects();
            await this.load();
            const search = document.getElementById('userSearch');
            if (search) search.addEventListener('input', () => this.render());
            const filter = document.getElementById('userStatusFilter');
            if (filter) filter.addEventListener('change', () => this.render());
        },
        fillRoleSelects() {
            // Each account may only hand out the roles below its own level.
            const roles = (this.meta.creatableRoles || []);
            if (!roles.length) return;
            const opts = roles.map(r => `<option value="${r.value}">${ui.escapeHTML(r.label)}</option>`).join('');
            ['newUserRole', 'editUserRole'].forEach(id => {
                const el = document.getElementById(id); if (el) el.innerHTML = opts;
            });
        },
        fillSchoolSelects() {
            // Admin and Staff can only place accounts in their own schools.
            const head = this.meta.schoolLocked
                ? '<option value="">— Select a school —</option>'
                : '<option value="">— No specific school (all access) —</option>';
            const opts = head +
                this.schools.map(s => `<option value="${s.id}">${ui.escapeHTML(s.name)}</option>`).join('');
            ['newUserSchool', 'editUserSchool'].forEach(id => {
                const el = document.getElementById(id); if (el) el.innerHTML = opts;
            });
        },
        async load() {
            try { this.all = await apiCall('/users'); this.render(); }
            catch (e) {
                document.getElementById('usersBody').innerHTML =
                    `<tr><td colspan="8" style="padding:20px;text-align:center;" class="text-danger">${ui.escapeHTML(e.message)}</td></tr>`;
            }
        },
        visible() {
            const term = (document.getElementById('userSearch')?.value || '').toLowerCase();
            const status = document.getElementById('userStatusFilter')?.value || '';
            return this.all.filter(u => {
                const hay = `${u.username} ${u.fullName} ${u.email} ${u.school_name} ${u.role}`.toLowerCase();
                return hay.includes(term) && (!status || u.status === status);
            });
        },
        roleLabel(role) {
            if (role === 'super_admin') return 'Super Admin';
            if (role === 'admin') return 'Admin';
            if (role === 'staff') return 'Staff';
            return 'User';
        },
        badge(text, cls) { return `<span class="badge ${cls}">${ui.escapeHTML(text)}</span>`; },
        render() {
            const list = this.visible();
            const pending = this.all.filter(u => u.status === 'Pending').length;
            const banner = document.getElementById('pendingBanner');
            if (banner) {
                banner.style.display = pending ? '' : 'none';
                banner.textContent = `${pending} account request${pending === 1 ? '' : 's'} waiting for approval.`;
            }
            const body = document.getElementById('usersBody');
            if (!list.length) { body.innerHTML = '<tr><td colspan="8" style="padding:20px;text-align:center;">No users match your filters.</td></tr>'; return; }
            body.innerHTML = list.map(u => {
                const statusCls = u.status === 'Active' ? 'badge-active' : (u.status === 'Pending' ? 'badge-pending' : 'badge-disabled');
                const roleCls = (u.role === 'super_admin' || u.role === 'admin') ? 'badge-admin' : 'badge-role';
                const onlineCls = u.online ? 'badge-online' : 'badge-offline';
                const uname = ui.escapeHTML(u.username);
                const unameAttr = ui.escapeHTML(u.username);
                let actions = '';
                if (u.status === 'Pending') actions += `<button class="btn-mini approve" data-act="status" data-status="Active" data-user="${unameAttr}">Approve</button>`;
                // The one and only Super Admin account is locked: never
                // disabled, never deleted, never demoted.
                const locked = String(u.username || '').toLowerCase() === 'admin' || u.role === 'super_admin';
                if (u.status === 'Active' && u.username !== state.username && !locked) actions += `<button class="btn-mini" data-act="status" data-status="Disabled" data-user="${unameAttr}">Disable</button>`;
                if (u.status === 'Disabled') actions += `<button class="btn-mini approve" data-act="status" data-status="Active" data-user="${unameAttr}">Enable</button>`;
                actions += `<button class="btn-mini" data-act="edit" data-user="${unameAttr}">Edit</button>`;
                if (u.username !== state.username && !locked && this.meta.canDelete)
                    actions += `<button class="btn-mini danger" data-act="delete" data-user="${unameAttr}">Delete</button>`;

                return `<tr>
                    <td><strong>${uname}</strong><div class="muted-small">${ui.escapeHTML(u.fullName || '—')}</div></td>
                    <td>${this.badge(this.roleLabel(u.role), roleCls)}</td>
                    <td>${ui.escapeHTML(u.school_name || (u.school_id ? 'School #' + u.school_id : '—'))}</td>
                    <td>${ui.escapeHTML(u.email || '—')}</td>
                    <td>${ui.escapeHTML(u.lastLogin || 'Never')}</td>
                    <td>${this.badge(u.online ? 'Online' : 'Offline', onlineCls)}</td>
                    <td>${this.badge(u.status, statusCls)}</td>
                    <td class="user-actions-cell"><div class="user-actions">${actions}</div></td>
                </tr>`;
            }).join('');
            // Row actions are delegated once.
            // username contains characters that clash with attribute escaping,
            if (!body.dataset.bound) {
                body.dataset.bound = '1';
                body.addEventListener('click', (ev) => {
                    const b = ev.target.closest('button[data-act]');
                    if (!b) return;
                    const u = b.dataset.user;
                    const act = b.dataset.act;
                    if (act === 'status') this.setStatus(u, b.dataset.status);
                    else if (act === 'edit') this.openEdit(u);
                    else if (act === 'delete') this.confirmDelete(u);
                });
            }
        },
        openCreate() {
            ['newUserPassword', 'newUserPassword2'].forEach(id => {
                const el = document.getElementById(id); if (el) el.value = '';
            });
            ui.openModal('modalNewUser');
        },
        async submitCreate(e) {
            e.preventDefault();
            const pwd = (document.getElementById('newUserPassword')?.value || '');
            const pwd2 = (document.getElementById('newUserPassword2')?.value || '');
            if (pwd || pwd2) {
                if (pwd !== pwd2) return ui.showToast('The two passwords do not match.', 'error');
                if (pwd.length < 8) return ui.showToast('Use at least 8 characters for the password.', 'error');
                if (!/[A-Z]/.test(pwd) || !/[a-z]/.test(pwd) || !/[0-9]/.test(pwd))
                    return ui.showToast('Password needs an uppercase letter, a lowercase letter and a number.', 'error');
            }
            const btn = document.getElementById('btnSaveUser'); btn.disabled = true; btn.textContent = 'Creating...';
            try {
                await apiCall('/users', 'POST', {
                    username: document.getElementById('newUserName').value.trim(),
                    password: pwd || undefined,
                    role: document.getElementById('newUserRole').value,
                    fullName: document.getElementById('newUserFullName').value.trim(),
                    email: document.getElementById('newUserEmail').value.trim(),
                    school_id: document.getElementById('newUserSchool').value,
                    status: document.getElementById('newUserStatus').value
                });
                ui.showToast(pwd ? 'User created with the password you set.' : 'User created with an auto-generated password.', 'success');
                ui.closeModal('modalNewUser');
                await this.load();
            } catch (err) { ui.showToast(err.message, 'error'); }
            finally { btn.disabled = false; btn.textContent = 'Create User'; }
        },
        openEdit(username) {
            const u = this.all.find(x => x.username === username); if (!u) return;
            this.editing = username;
            document.getElementById('editUserTitle').textContent = `Edit ${username}`;
            document.getElementById('editUserFullName').value = u.fullName || '';
            document.getElementById('editUserEmail').value = u.email || '';
            const roleSel = document.getElementById('editUserRole');
            if (roleSel) {
                if (!Array.prototype.some.call(roleSel.options, o => o.value === u.role)) {
                    roleSel.insertAdjacentHTML('afterbegin',
                        `<option value="${u.role}">${ui.escapeHTML(this.roleLabel(u.role))}</option>`);
                }
                roleSel.value = u.role;
                // A locked Super Admin account can never be demoted.
                roleSel.disabled = (u.role === 'super_admin');
            }
            document.getElementById('editUserStatus').value = u.status;
            document.getElementById('editUserSchool').value = u.school_id || '';
            // Password fields always start blank; blank means "keep current".
            ['editUserPassword', 'editUserPassword2'].forEach(id => {
                const el = document.getElementById(id); if (el) el.value = '';
            });
            // Staff may only change passwords for ordinary user accounts;
            // nobody but the Super Admin touches the Super Admin account.
            const canSetPwd = isSuperAdmin() || u.username === state.username
                || (isAdmin() && u.role !== 'super_admin')
                || (isStaff() && u.role === 'user');
            ['editUserPassword', 'editUserPassword2'].forEach(id => {
                const el = document.getElementById(id);
                if (el && el.closest('.field')) el.closest('.field').style.display = canSetPwd ? '' : 'none';
            });
            const note = document.getElementById('editUserPwdNote');
            if (note) note.style.display = canSetPwd ? '' : 'none';
            ui.openModal('modalEditUser');
        },
        async submitEdit(e) {
            e.preventDefault();
            const pwd = (document.getElementById('editUserPassword')?.value || '');
            const pwd2 = (document.getElementById('editUserPassword2')?.value || '');
            if (pwd || pwd2) {
                if (pwd !== pwd2) return ui.showToast('The two passwords do not match.', 'error');
                if (pwd.length < 8) return ui.showToast('Use at least 8 characters for the new password.', 'error');
            }
            const btn = document.getElementById('btnUpdateUser'); btn.disabled = true; btn.textContent = 'Saving...';
            try {
                await apiCall(`/users/${encodeURIComponent(this.editing)}`, 'PUT', {
                    fullName: document.getElementById('editUserFullName').value.trim(),
                    email: document.getElementById('editUserEmail').value.trim(),
                    role: document.getElementById('editUserRole').value,
                    status: document.getElementById('editUserStatus').value,
                    school_id: document.getElementById('editUserSchool').value
                });
                if (pwd) {
                    await apiCall(`/users/${encodeURIComponent(this.editing)}/password`, 'POST', { newPassword: pwd });
                    ui.showToast('User updated and password changed.', 'success');
                } else {
                    ui.showToast('User updated.', 'success');
                }
                ui.closeModal('modalEditUser');
                await this.load();
            } catch (err) { ui.showToast(err.message, 'error'); }
            finally { btn.disabled = false; btn.textContent = 'Save Changes'; }
        },
        async setStatus(username, status) {
            try {
                await apiCall(`/users/${encodeURIComponent(username)}`, 'PUT', { status });
                ui.showToast(`${username} is now ${status}.`, 'success');
                await this.load();
            } catch (err) { ui.showToast(err.message, 'error'); }
        },
        confirmDelete(username) {
            if (username === state.username) {
                return ui.showToast("You cannot delete the account you're signed in with. Sign in as another admin first.", 'error');
            }
            this.editing = username;
            document.getElementById('lblDeleteUser').textContent = username;
            ui.openModal('modalDeleteUser');
        },
        async executeDelete() {
            try {
                await apiCall(`/users/${encodeURIComponent(this.editing)}`, 'DELETE');
                ui.showToast('User deleted.', 'success');
                ui.closeModal('modalDeleteUser');
                await this.load();
            } catch (err) { ui.showToast(err.message, 'error'); ui.closeModal('modalDeleteUser'); }
        }
    };

    /* ---------------- Activity Log (admin only) ---------------- */
    const activity = {
        rows: [],
        async init() {
            // Super Admin: everyone. Staff: the users of their own schools.
            // Ordinary user accounts have no access to the activity log.
            if (!isManager()) {
                document.getElementById('auditBody').innerHTML =
                    '<tr><td colspan="5" style="padding:20px;text-align:center;">Staff access or above required.</td></tr>';
                return;
            }
            await this.load();
            ['auditUser', 'auditAction'].forEach(id => {
                const el = document.getElementById(id);
                if (el) el.addEventListener('input', () => this.render());
                if (el && el.tagName === 'SELECT') el.addEventListener('change', () => this.render());
            });
        },
        async load() {
            try {
                this.rows = await apiCall('/audit?limit=500');
                this.render();
            } catch (e) {
                document.getElementById('auditBody').innerHTML =
                    `<tr><td colspan="5" style="padding:20px;text-align:center;" class="text-danger">${ui.escapeHTML(e.message)}</td></tr>`;
            }
        },
        render() {
            const u = (document.getElementById('auditUser')?.value || '').toLowerCase();
            const a = document.getElementById('auditAction')?.value || '';
            const list = this.rows.filter(r =>
                (!u || (r.username || '').toLowerCase().includes(u)) && (!a || r.action === a));
            const body = document.getElementById('auditBody');
            const count = document.getElementById('auditCount');
            if (count) count.textContent = `${list.length} entries`;
            if (!list.length) { body.innerHTML = '<tr><td colspan="5" style="padding:20px;text-align:center;">No activity recorded yet.</td></tr>'; return; }
            body.innerHTML = list.map(r => `<tr>
                <td style="white-space:nowrap;">${ui.escapeHTML(r.timestamp)}</td>
                <td><strong>${ui.escapeHTML(r.username)}</strong><div class="muted-small">${ui.escapeHTML(r.role || '')}</div></td>
                <td><span class="badge badge-role">${ui.escapeHTML(r.action)}</span></td>
                <td>${ui.escapeHTML(r.entity || '—')}${r.entity_id ? `<div class="muted-small">${ui.escapeHTML(r.entity_id)}</div>` : ''}</td>
                <td>${ui.escapeHTML(r.details || '')}</td>
            </tr>`).join('');
        },
        async download() {
            try {
                const res = await fetch(`${API_BASE_URL}/audit/download`, { headers: { 'Authorization': `Bearer ${state.token}` } });
                if (!res.ok) throw new Error('Download failed');
                const blob = await res.blob();
                const url = window.URL.createObjectURL(blob);
                const link = document.createElement('a');
                link.href = url; link.download = 'Vedritam_Activity_Log.csv';
                document.body.appendChild(link); link.click(); link.remove();
                window.URL.revokeObjectURL(url);
            } catch (e) { ui.showToast('Failed to download the activity log.', 'error'); }
        }
    };

    async function init() {
        // Show why the previous session ended (e.g. signed in on another device).
        try {
            const reason = sessionStorage.getItem('v_signout_reason');
            const box = document.getElementById('signoutReason');
            if (reason && box) { box.textContent = reason; box.style.display = ''; }
            sessionStorage.removeItem('v_signout_reason');
        } catch (e) {}
        const ok = await checkAuth();
        if (!ok) return;
        const p = window.location.pathname;
        if (p.endsWith('index.html') || p === '/' || p === '') { auth.init(); }
        else if (p.endsWith('schools.html')) { schools.init(); }
        else if (p.endsWith('users.html')) { users.init(); }
        else if (p.endsWith('activity.html')) { activity.init(); }
        // ledger.html is served by the enterprise Ledger module in ledger.js.
        else if (p.endsWith('ledger.html')) { /* handled by window.Ledger */ }
    }

    document.addEventListener('DOMContentLoaded', init);
    return { ui, state, logout, schools, ledger, login, auth, users, activity, apiCall, applyRoleVisibility,
             isSuperAdmin, isAdmin, isStaff, isManager, canManageSchools, API_BASE_URL };
})();
// Expose the app namespace to other scripts.
window.App = App;
