/* Vedritam Assistant — floating chat.
   Renders markdown, shows which data tools were used, and turns assistant
   file links into working downloads / inline images (the auth token is
   appended so <img> and <a> requests are still permission-checked server side). */
(function () {
    if (window.VAssistant && typeof window.VAssistant.open === 'function') return;

    const CSS = `
    :root{--cb-surface:#fff;--cb-log:#f5f7fa;--cb-text:#1d2430;--cb-muted:#7c8798;
        --cb-border:#e3e7ee;--cb-brand:#0f3a61;--cb-brand-text:#fff;--cb-soft:#eef4fb;
        --cb-code:#eef1f6;--cb-input:#fff}
    html[data-theme="dark"]{--cb-surface:#1E293B;--cb-log:#0F172A;--cb-text:#E2E8F0;
        --cb-muted:#94A3B8;--cb-border:rgba(148,163,184,.22);--cb-brand:#2563EB;
        --cb-brand-text:#fff;--cb-soft:rgba(37,99,235,.16);--cb-code:rgba(148,163,184,.16);
        --cb-input:#0F172A}
    #cbBtn{position:fixed;bottom:20px;right:20px;width:56px;height:56px;border-radius:50%;
        background:var(--cb-brand);color:var(--cb-brand-text);border:none;font-size:24px;cursor:pointer;
        box-shadow:0 6px 18px rgba(15,58,97,.35);z-index:9999;transition:transform .15s}
    #cbBtn:hover{transform:scale(1.06)}
    #cbBox{position:fixed;bottom:88px;right:20px;width:430px;max-width:calc(100vw - 32px);
        height:600px;max-height:calc(100vh - 120px);background:var(--cb-surface);color:var(--cb-text);
        border:1px solid var(--cb-border);border-radius:14px;
        box-shadow:0 12px 40px rgba(0,0,0,.28);display:none;flex-direction:column;z-index:9999;
        font-family:inherit;overflow:hidden}
    #cbBox.open{display:flex}
    #cbHead{background:var(--cb-brand);color:var(--cb-brand-text);padding:11px 14px;font-weight:600;display:flex;
        justify-content:space-between;align-items:center}
    #cbHead .cbSub{font-weight:400;font-size:.72rem;opacity:.85;display:block;margin-top:2px}
    #cbHead span.x{cursor:pointer;font-size:22px;line-height:1}
    #cbLog{flex:1;overflow-y:auto;padding:14px;background:var(--cb-log);color:var(--cb-text);
        font-size:.9rem;line-height:1.55}
    .cbMsg{margin-bottom:12px;padding:9px 13px;border-radius:12px;max-width:92%;
        word-wrap:break-word;overflow-wrap:anywhere}
    .cbMe{background:var(--cb-brand);color:var(--cb-brand-text);margin-left:auto;
        border-bottom-right-radius:3px;white-space:pre-wrap}
    .cbAi{background:var(--cb-surface);color:var(--cb-text);border:1px solid var(--cb-border);
        border-bottom-left-radius:3px}
    .cbSys{background:transparent;color:var(--cb-muted);font-size:.79rem;text-align:center;
        font-style:italic;max-width:100%}
    .cbAi h1,.cbAi h2,.cbAi h3,.cbAi h4{font-size:.98rem;margin:.6em 0 .3em;font-weight:700;color:inherit}
    .cbAi p{margin:.45em 0}
    .cbAi ul,.cbAi ol{margin:.4em 0 .4em 1.1em;padding:0}
    .cbAi li{margin:.16em 0}
    .cbAi code{background:var(--cb-code);padding:1px 5px;border-radius:4px;font-size:.84em}
    .cbAi pre{background:#12233a;color:#e6edf6;padding:10px;border-radius:8px;overflow-x:auto;font-size:.8rem}
    .cbAi pre code{background:none;color:inherit;padding:0}
    .cbAi table{border-collapse:collapse;width:100%;margin:.5em 0;font-size:.82rem;display:block;overflow-x:auto}
    .cbAi th,.cbAi td{border:1px solid var(--cb-border);padding:5px 8px;text-align:left}
    .cbAi th{background:var(--cb-soft);font-weight:600}
    .cbAi a{color:var(--cb-brand);font-weight:600}
    html[data-theme="dark"] .cbAi a{color:#93C5FD}
    .cbAi img{max-width:100%;border-radius:8px;margin:.4em 0;display:block}
    .cbAi blockquote{border-left:3px solid var(--cb-border);margin:.4em 0;padding:.1em .8em;color:var(--cb-muted)}
    .cbAi hr{border:none;border-top:1px solid var(--cb-border);margin:.6em 0}
    .cbFile{display:inline-flex;align-items:center;gap:6px;background:var(--cb-soft);
        border:1px solid var(--cb-border);color:var(--cb-text);
        border-radius:8px;padding:5px 10px;margin:.25em 0;text-decoration:none;font-size:.84rem}
    .cbTools{margin-top:8px;font-size:.72rem;color:var(--cb-muted);border-top:1px dashed var(--cb-border);padding-top:5px}
    #cbSuggest{display:flex;flex-wrap:wrap;gap:6px;padding:8px 10px 0;background:var(--cb-log)}
    #cbSuggest button{border:1px solid var(--cb-border);background:var(--cb-surface);color:var(--cb-text);
        border-radius:14px;padding:4px 10px;font-size:.76rem;cursor:pointer}
    #cbSuggest button:hover{background:var(--cb-soft)}
    #cbAttached{display:none;flex-wrap:wrap;gap:6px;padding:8px 10px 0;background:var(--cb-surface)}
    #cbAttached.on{display:flex}
    .cbChip{display:inline-flex;align-items:center;gap:6px;background:var(--cb-soft);color:var(--cb-text);
        border:1px solid var(--cb-border);border-radius:14px;padding:3px 9px;font-size:.75rem;max-width:100%}
    .cbChip span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:170px}
    .cbChip b{cursor:pointer;font-weight:700;opacity:.7}
    .cbChip b:hover{opacity:1}
    #cbForm{display:flex;padding:9px;border-top:1px solid var(--cb-border);background:var(--cb-surface);
        gap:6px;align-items:flex-end}
    #cbInput{flex:1;border:1px solid var(--cb-border);background:var(--cb-input);color:var(--cb-text);
        border-radius:9px;padding:9px;font:inherit;outline:none;resize:none;max-height:110px;line-height:1.35}
    #cbInput::placeholder{color:var(--cb-muted)}
    #cbInput:focus{border-color:var(--cb-brand)}
    #cbAttach{background:var(--cb-soft);color:var(--cb-text);border:1px solid var(--cb-border);
        border-radius:9px;width:38px;height:38px;font-size:16px;cursor:pointer;flex:0 0 auto}
    #cbAttach:hover{border-color:var(--cb-brand)}
    #cbAttach:disabled{opacity:.5;cursor:wait}
    #cbSend{background:var(--cb-brand);color:var(--cb-brand-text);border:none;border-radius:9px;
        padding:0 15px;height:38px;cursor:pointer;font-weight:600}
    #cbSend:disabled{opacity:.5;cursor:wait}
    .cbDots:after{content:'';animation:cbd 1.2s steps(4,end) infinite}
    @keyframes cbd{0%{content:''}25%{content:'.'}50%{content:'..'}75%{content:'...'}}
    @media(max-width:520px){#cbBox{width:calc(100vw - 24px);right:12px;bottom:80px}}
    `;
    const style = document.createElement('style'); style.textContent = CSS; document.head.appendChild(style);

    const token = () => localStorage.getItem('v_token') || '';
    const authed = (url) => url + (url.indexOf('?') > -1 ? '&' : '?') + 't=' + encodeURIComponent(token());

    /* ---------- tiny, safe markdown renderer ---------- */
    const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    const isAiFile = (u) => /^\/api\/v1\/ai\/files\//.test(u);
    const safeUrl = (u) => (/^(https?:\/\/|\/)/i.test(u) ? u : '#');

    function inline(t) {
        t = esc(t);
        t = t.replace(/`([^`]+)`/g, (m, c) => '<code>' + c + '</code>');
        t = t.replace(/!\[([^\]]*)\]\(([^)\s]+)\)/g, (m, alt, url) => {
            const u = safeUrl(url);
            return '<img src="' + (isAiFile(u) ? authed(u) : u) + '" alt="' + alt + '"/>';
        });
        t = t.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (m, txt, url) => {
            const u = safeUrl(url);
            if (isAiFile(u)) return '<a class="cbFile" href="' + authed(u) + '" download>📎 ' + txt + '</a>';
            return '<a href="' + u + '" target="_blank" rel="noopener">' + txt + '</a>';
        });
        t = t.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
        t = t.replace(/__([^_]+)__/g, '<strong>$1</strong>');
        t = t.replace(/~~([^~]+)~~/g, '<s>$1</s>');
        t = t.replace(/(^|[\s(])\*([^*\n]+)\*/g, '$1<em>$2</em>');
        return t;
    }

    function markdown(src) {
        const lines = String(src || '').replace(/\r/g, '').split('\n');
        let out = '', list = null, inCode = false, code = '', para = [];
        const closeList = () => { if (list) { out += '</' + list + '>'; list = null; } };
        /* consecutive plain lines belong to ONE paragraph - emitting a <p> per
           line is what used to chop sentences into fragments mid-word. */
        const closePara = () => { if (para.length) { out += '<p>' + inline(para.join(' ')) + '</p>'; para = []; } };

        for (let i = 0; i < lines.length; i++) {
            const raw = lines[i], line = raw.trim();

            if (/^```/.test(line)) {
                closePara();
                if (inCode) { out += '<pre><code>' + esc(code) + '</code></pre>'; code = ''; inCode = false; }
                else { closeList(); inCode = true; }
                continue;
            }
            if (inCode) { code += raw + '\n'; continue; }

            /* table */
            if (/^\|.*\|$/.test(line) && /^\|[\s:|-]+\|$/.test((lines[i + 1] || '').trim())) {
                closePara(); closeList();
                const cells = (r) => r.trim().replace(/^\||\|$/g, '').split('|').map(c => c.trim());
                let html = '<table><thead><tr>' +
                    cells(line).map(c => '<th>' + inline(c) + '</th>').join('') + '</tr></thead><tbody>';
                i++;
                while (i + 1 < lines.length && /^\|.*\|$/.test((lines[i + 1] || '').trim())) {
                    i++;
                    html += '<tr>' + cells(lines[i]).map(c => '<td>' + inline(c) + '</td>').join('') + '</tr>';
                }
                out += html + '</tbody></table>';
                continue;
            }

            if (!line) { closePara(); closeList(); continue; }

            let m;
            if ((m = line.match(/^(#{1,6})\s+(.*)$/))) {
                closePara(); closeList();
                const lvl = Math.min(m[1].length + 1, 4);
                out += '<h' + lvl + '>' + inline(m[2]) + '</h' + lvl + '>';
            } else if (/^(---|\*\*\*|___)$/.test(line)) {
                closePara(); closeList(); out += '<hr/>';
            } else if ((m = line.match(/^>\s?(.*)$/))) {
                closePara(); closeList(); out += '<blockquote>' + inline(m[1]) + '</blockquote>';
            } else if ((m = line.match(/^[-*•]\s+(.*)$/))) {
                closePara();
                if (list !== 'ul') { closeList(); out += '<ul>'; list = 'ul'; }
                out += '<li>' + inline(m[1]) + '</li>';
            } else if ((m = line.match(/^\d+[.)]\s+(.*)$/))) {
                closePara();
                if (list !== 'ol') { closeList(); out += '<ol>'; list = 'ol'; }
                out += '<li>' + inline(m[1]) + '</li>';
            } else {
                closeList(); para.push(line);
            }
        }
        if (inCode && code) out += '<pre><code>' + esc(code) + '</code></pre>';
        closePara(); closeList();
        return out;
    }

    /* ---------- shell ---------- */
    const btn = document.createElement('button');
    btn.id = 'cbBtn'; btn.innerHTML = '💬'; btn.title = 'Vedritam Assistant';

    const box = document.createElement('div'); box.id = 'cbBox';
    box.innerHTML =
        '<div id="cbHead"><div><div id="cbTitle">Vedritam Assistant</div>' +
        '<span class="cbSub" id="cbScope">Working with the data you are allowed to see</span></div>' +
        '<span class="x" id="cbClose">×</span></div>' +
        '<div id="cbLog"></div><div id="cbSuggest"></div><div id="cbAttached"></div>' +
        '<form id="cbForm">' +
        '<input type="file" id="cbFileInput" multiple hidden ' +
        'accept=".txt,.md,.csv,.tsv,.json,.xml,.html,.log,.yml,.yaml,.ini,.pdf,.png,.jpg,.jpeg,.webp,.gif">' +
        '<button id="cbAttach" type="button" title="Attach a file">📎</button>' +
        '<textarea id="cbInput" rows="1" placeholder="Ask about stock, attach a file, write a note…"></textarea>' +
        '<button id="cbSend" type="submit">Send</button></form>';
    document.body.appendChild(btn); document.body.appendChild(box);

    const log = box.querySelector('#cbLog');
    const input = box.querySelector('#cbInput');
    const form = box.querySelector('#cbForm');
    const send = box.querySelector('#cbSend');
    const chips = box.querySelector('#cbSuggest');
    const attachBtn = box.querySelector('#cbAttach');
    const fileInput = box.querySelector('#cbFileInput');
    const attachBar = box.querySelector('#cbAttached');

    /* ---------- attachments ---------- */
    let attachments = [];   // [{filename, readable}]

    function renderAttachments() {
        attachBar.innerHTML = attachments.map((a, i) =>
            '<span class="cbChip"><span>📎 ' + esc(a.filename) + '</span>' +
            '<b data-i="' + i + '">×</b></span>').join('');
        attachBar.classList.toggle('on', attachments.length > 0);
    }
    attachBar.onclick = (e) => {
        const i = e.target && e.target.getAttribute && e.target.getAttribute('data-i');
        if (i === null || i === undefined) return;
        attachments.splice(Number(i), 1); renderAttachments();
    };
    const readAsBase64 = (file) => new Promise((resolve, reject) => {
        const fr = new FileReader();
        fr.onload = () => resolve(String(fr.result).split(',').pop());
        fr.onerror = () => reject(new Error('Could not read that file.'));
        fr.readAsDataURL(file);
    });
    async function uploadFiles(files) {
        attachBtn.disabled = true;
        for (const file of files) {
            if (file.size > 8 * 1024 * 1024) { add('cbSys', '"' + file.name + '" is larger than 8 MB.'); continue; }
            try {
                const content = await readAsBase64(file);
                const r = await fetch('/api/v1/ai/upload', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token() },
                    body: JSON.stringify({ filename: file.name, content: content })
                });
                const j = await r.json().catch(() => ({}));
                if (!r.ok) throw new Error(j.detail || ('Upload failed (HTTP ' + r.status + ')'));
                attachments.push({ filename: j.filename, readable: j.readable });
            } catch (err) { add('cbSys', '⚠️ ' + err.message); }
        }
        attachBtn.disabled = false;
        renderAttachments();
    }
    attachBtn.onclick = () => fileInput.click();
    fileInput.onchange = () => { const f = [...fileInput.files]; fileInput.value = ''; if (f.length) uploadFiles(f); };
    box.addEventListener('dragover', (e) => { e.preventDefault(); });
    box.addEventListener('drop', (e) => {
        e.preventDefault();
        const f = [...(e.dataTransfer && e.dataTransfer.files ? e.dataTransfer.files : [])];
        if (f.length) uploadFiles(f);
    });

    function openBox() { box.classList.add('open'); setTimeout(() => input.focus(), 0); }
    function closeBox() { box.classList.remove('open'); }
    function toggleBox() { box.classList.contains('open') ? closeBox() : openBox(); }
    btn.onclick = toggleBox;
    box.querySelector('#cbClose').onclick = closeBox;
    window.VAssistant = { open: openBox, close: closeBox, toggle: toggleBox };

    input.addEventListener('input', () => {
        input.style.height = 'auto';
        input.style.height = Math.min(input.scrollHeight, 110) + 'px';
    });
    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); form.requestSubmit(); }
    });

    function add(cls, text) {
        const d = document.createElement('div');
        d.className = 'cbMsg ' + cls;
        if (cls === 'cbAi') d.innerHTML = markdown(text); else d.textContent = text;
        log.appendChild(d); log.scrollTop = log.scrollHeight; return d;
    }
    function setAi(node, text, tools) {
        node.innerHTML = markdown(text);
        if (tools && tools.length) {
            const t = document.createElement('div');
            t.className = 'cbTools';
            t.textContent = '🔎 Checked: ' + [...new Set(tools)].join(', ').replace(/_/g, ' ');
            node.appendChild(t);
        }
        log.scrollTop = log.scrollHeight;
    }

    const SUGGESTIONS = [
        'Summarise my stock',
        'Which books are low on stock?',
        'Export my ledger to CSV',
        'Write a short indent note'
    ];
    SUGGESTIONS.forEach(s => {
        const b = document.createElement('button');
        b.type = 'button'; b.textContent = s;
        b.onclick = () => { input.value = s; form.requestSubmit(); };
        chips.appendChild(b);
    });

    const history = [];
    add('cbSys', 'Hi! I can read your ledger, add rows, write files, make images, read files you attach with 📎 and look things up live — always limited to the data your account is allowed to see.');

    fetch('/api/v1/settings/ai', { headers: { 'Authorization': 'Bearer ' + token() } })
        .then(r => (r.ok ? r.json() : null))
        .then(cfg => {
            if (cfg && !cfg.configured) {
                add('cbSys', cfg.canEdit
                    ? 'No API key saved yet — add one in Settings to enable the assistant.'
                    : 'The assistant is not set up yet. Ask your administrator to add an API key.');
            }
        })
        .catch(() => { });

    form.onsubmit = async (e) => {
        e.preventDefault();
        const q = input.value.trim();
        if (!q && !attachments.length) return;
        const names = attachments.map(a => a.filename);
        const shown = q || 'Please look at the attached file' + (names.length > 1 ? 's' : '') + '.';
        add('cbMe', shown + (names.length ? '\n📎 ' + names.join(', ') : ''));
        const sent = names.length
            ? shown + '\n\n[Attached file' + (names.length > 1 ? 's' : '') + ' in my workspace: ' +
              names.join(', ') + '. Use read_file to open ' + (names.length > 1 ? 'them' : 'it') + '.]'
            : shown;
        attachments = []; renderAttachments();
        input.value = ''; input.style.height = 'auto'; send.disabled = true;
        chips.style.display = 'none';
        const thinking = add('cbAi', '');
        thinking.innerHTML = '<span class="cbDots">Working</span>';
        try {
            const r = await fetch('/api/v1/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token() },
                body: JSON.stringify({ question: sent, history })
            });
            let j = {};
            try { j = await r.json(); } catch (_) { }
            if (r.status === 401) throw new Error('Session expired — please log in again.');
            if (!r.ok) throw new Error(j.detail || (j.error && j.error.message) || ('HTTP ' + r.status));
            const answer = j.answer || '(no response)';
            setAi(thinking, answer, j.tools_used);
            history.push({ role: 'user', content: sent }, { role: 'assistant', content: answer });
            if (history.length > 12) history.splice(0, history.length - 12);
        } catch (err) {
            thinking.innerHTML = '';
            thinking.textContent = '⚠️ ' + (err.message === 'Failed to fetch'
                ? 'Cannot reach the server. Is the app running?' : err.message);
        } finally { send.disabled = false; input.focus(); }
    };
})();
