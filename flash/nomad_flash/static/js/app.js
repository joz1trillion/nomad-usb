// nomad-flash — wizard SPA.
//
// Deliberately framework-free: this is a five-step wizard, not a
// SaaS product. Vanilla JS keeps the install lightweight (no node,
// no build step) and the source easy to follow.
//
// State machine:
//
//   welcome → select-iso → select-device → select-apps → confirm → progress → done
//
// Each step is a render function. They share a single `state` object.
// Navigating calls render(stepName) which clears the root and rebuilds.

const root = document.getElementById('app');

const STEPS = [
    'welcome',
    'select-iso',
    'select-device',
    'select-apps',
    'confirm',
    'progress',
    'done',
];

const state = {
    current: 'welcome',
    iso: null,            // path string
    device: null,         // {name, size, model, ...}
    apps: new Set(),      // selected optional app keys
    fromArchive: null,    // optional pre-built tarball path
    // Backend version, populated from /api/health on startup. Shown
    // in the page header so we (and the user) can tell at a glance
    // whether the latest build is actually loaded — handy after deploys.
    version: 'loading…',
};

// ---------- API helpers ----------

async function api(path, opts = {}) {
    const res = await fetch(`/api${path}`, opts);
    if (!res.ok) throw new Error(`API ${path}: HTTP ${res.status}`);
    return res.json();
}

// ---------- shared chrome ----------

function header() {
    return `
        <div class="header">
            <h1>Nomad Flash</h1>
            <span class="version">v${state.version}</span>
        </div>
        <div class="steps">
            ${STEPS.filter(s => s !== 'done').map(name => {
                const idx = STEPS.indexOf(name);
                const cur = STEPS.indexOf(state.current);
                let cls = '';
                if (idx < cur) cls = 'done';
                if (idx === cur) cls = 'current';
                return `<div class="step ${cls}"></div>`;
            }).join('')}
        </div>
    `;
}

// ---------- steps ----------

function renderWelcome() {
    // Reflect root state captured during bootstrap. If we don't have
    // root, the flash will fail at the partition step — warn early
    // rather than letting them go through the whole wizard first.
    const rootWarning = (window.__nomad_health && !window.__nomad_health.is_root)
        ? `<div class="alert warn">
              <strong>Not running as root.</strong>
              Flashing requires root privileges. Quit and re-run with sudo:
              <code style="display:block; margin-top:0.5rem; opacity:0.8;">
                sudo $(which nomad-flash)
              </code>
           </div>`
        : '';

    root.innerHTML = `
        ${header()}
        ${rootWarning}
        <div class="card">
            <h2>Welcome</h2>
            <p>This tool will write a Project Nomad ISO to a USB drive
               and pre-populate it with everything Nomad needs to run
               offline. The target USB will be <strong>completely
               wiped</strong>.</p>
            <p>You'll need: an ISO file, a USB drive, and a working
               internet connection (for the docker pull) — unless you
               have a pre-built image archive.</p>
            <div class="actions">
                <span></span>
                <button class="primary" id="next">Get started</button>
            </div>
        </div>
    `;
    document.getElementById('next').onclick = () => go('select-iso');
}

async function renderSelectIso() {
    // Probe the backend for zenity support — we only show "Browse…"
    // if there's actually a native dialog available.
    let zenityOk = false;
    try {
        const res = await api('/picker/zenity-available');
        zenityOk = !!res.available;
    } catch (e) {
        // Quietly fall back to the in-app browser if the probe fails.
    }

    // Build the split-button HTML. If zenity isn't available, the
    // primary action just falls back to the in-app browser, and we
    // skip the dropdown entirely (nothing to switch between).
    //
    // Note: menu visibility is controlled by the .open class on the
    // wrapper, NOT the [hidden] attribute — the CSS .split-btn-menu
    // rule sets display:flex which would override [hidden] anyway.
    const browseButton = zenityOk
        ? `
            <div class="split-btn" id="browse-split">
                <button id="browse-primary" class="split-btn-main">Browse…</button>
                <button id="browse-toggle" class="split-btn-arrow"
                        aria-label="Other options">▾</button>
                <div class="split-btn-menu" id="browse-menu">
                    <button id="browse-app-menu">Browse files (in-app)…</button>
                </div>
            </div>
        `
        : `<button id="browse-primary">Browse files…</button>`;

    root.innerHTML = `
        ${header()}
        <div class="card">
            <h2>Select ISO</h2>
            <p>Path to the Nomad live ISO file you built or downloaded.</p>
            <label for="iso">ISO path</label>
            <div style="display:flex; gap:0.5rem;">
                <input type="text" id="iso" placeholder="/path/to/live-image-amd64.hybrid.iso"
                       value="${state.iso || ''}" style="flex:1;">
                ${browseButton}
            </div>
            <div class="actions">
                <button id="back">Back</button>
                <button class="primary" id="next" disabled>Next</button>
            </div>
        </div>
    `;

    const input = document.getElementById('iso');
    const next = document.getElementById('next');
    const refresh = () => { next.disabled = !input.value.trim(); };
    input.oninput = refresh;
    refresh();

    document.getElementById('back').onclick = () => go('welcome');
    next.onclick = () => {
        state.iso = input.value.trim();
        go('select-device');
    };

    // Helper: take a path (string or null), set the input field if non-null.
    const acceptPath = (p) => {
        if (p) {
            input.value = p;
            refresh();
        }
    };

    // Native (zenity) picker.
    const useNative = async () => {
        try {
            const res = await fetch('/api/picker/zenity', { method: 'POST' });
            const data = await res.json();
            acceptPath(data.path);
        } catch (e) {
            console.error('zenity pick failed', e);
        }
    };

    // In-app browser.
    const useInApp = () => openInAppBrowser(acceptPath);

    if (zenityOk) {
        // Primary button uses zenity, dropdown opens in-app browser.
        document.getElementById('browse-primary').onclick = useNative;

        const split = document.getElementById('browse-split');
        const toggle = document.getElementById('browse-toggle');

        toggle.onclick = (e) => {
            e.stopPropagation();
            split.classList.toggle('open');
        };

        document.getElementById('browse-app-menu').onclick = (e) => {
            e.stopPropagation();
            split.classList.remove('open');
            useInApp();
        };

        // Click anywhere else closes the menu. Persistent listener
        // (not once:true) so it works after multiple open/close cycles.
        // We just check whether the click was inside our split-btn.
        document.addEventListener('click', (e) => {
            if (!split.contains(e.target)) {
                split.classList.remove('open');
            }
        });
    } else {
        // Single button — straight to in-app browser.
        document.getElementById('browse-primary').onclick = useInApp;
    }
}


// In-app file browser. Mounts an overlay over the wizard, lets the
// user navigate the filesystem to find an ISO, returns the path via
// callback.
function openInAppBrowser(onSelect) {
    const overlay = document.createElement('div');
    overlay.style.cssText = `
        position: fixed; inset: 0; z-index: 100;
        background: rgba(0,0,0,0.7);
        display: flex; align-items: center; justify-content: center;
    `;

    const modal = document.createElement('div');
    modal.style.cssText = `
        background: var(--bg-card); border: 1px solid var(--border);
        border-radius: var(--radius); padding: 1.5rem;
        width: min(640px, 95vw); max-height: 85vh;
        display: flex; flex-direction: column; gap: 0.75rem;
    `;
    overlay.appendChild(modal);
    document.body.appendChild(overlay);

    const close = () => overlay.remove();

    // Click outside the modal closes it.
    overlay.addEventListener('click', (e) => {
        if (e.target === overlay) close();
    });

    async function loadDir(path) {
        try {
            const qs = path ? `?path=${encodeURIComponent(path)}` : '';
            const data = await api(`/picker/browse${qs}`);
            render(data);
        } catch (e) {
            modal.innerHTML = `<p style="color: var(--danger);">
                Browse failed: ${escapeHtml(e.message)}
            </p>
            <div style="text-align:right;"><button id="bf-close">Close</button></div>`;
            document.getElementById('bf-close').onclick = close;
        }
    }

    function render(data) {
        const headerHtml = `
            <div style="display:flex; justify-content:space-between; align-items:center;">
                <h2 style="margin:0; font-size:1.05rem;">Browse files</h2>
                <button id="bf-close" style="padding:0.3rem 0.75rem;">✕</button>
            </div>
            <div style="font-family:ui-monospace,monospace; font-size:0.85rem;
                        color:var(--text-dim); word-break:break-all;">
                ${escapeHtml(data.cwd)}
            </div>
        `;

        const items = data.entries.map(e => {
            // Style by what they are. Directories: navigable. ISOs: selectable.
            // Other files: shown in dim color, non-clickable.
            const icon = e.is_dir ? '📁' : (e.is_iso ? '💿' : '📄');
            const dim = (!e.is_dir && !e.is_iso) ? 'opacity:0.4;' : '';
            const cursor = (e.is_dir || e.is_iso) ? 'cursor:pointer;' : '';
            return `
                <div class="bf-entry" data-path="${escapeHtml(e.path)}"
                     data-is-dir="${e.is_dir}" data-is-iso="${e.is_iso}"
                     style="display:flex; gap:0.5rem; padding:0.4rem 0.5rem;
                            border-radius:4px; ${dim} ${cursor}">
                    <span>${icon}</span>
                    <span style="flex:1;">${escapeHtml(e.name)}</span>
                    <span style="color:var(--text-dim); font-size:0.8rem;">
                        ${e.is_dir ? '' : formatSize(e.size)}
                    </span>
                </div>
            `;
        }).join('');

        const upEntry = data.parent
            ? `<div class="bf-entry" data-path="${escapeHtml(data.parent)}"
                    data-is-dir="true" data-is-iso="false"
                    style="display:flex; gap:0.5rem; padding:0.4rem 0.5rem;
                           border-radius:4px; cursor:pointer;">
                <span>📁</span><span>..</span></div>`
            : '';

        modal.innerHTML = `
            ${headerHtml}
            <div style="overflow-y:auto; flex:1; min-height:0;
                        background:var(--bg); border-radius:6px;
                        padding:0.5rem;">
                ${upEntry}
                ${items || '<p style="color:var(--text-dim);">empty</p>'}
            </div>
        `;

        document.getElementById('bf-close').onclick = close;

        modal.querySelectorAll('.bf-entry').forEach(el => {
            el.addEventListener('mouseenter', () => {
                el.style.background = 'var(--bg-elev)';
            });
            el.addEventListener('mouseleave', () => {
                el.style.background = '';
            });
            el.onclick = () => {
                const isDir = el.dataset.isDir === 'true';
                const isIso = el.dataset.isIso === 'true';
                if (isDir) {
                    loadDir(el.dataset.path);
                } else if (isIso) {
                    onSelect(el.dataset.path);
                    close();
                }
            };
        });
    }

    loadDir(null);  // start at default (~/Downloads)
}

function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g,
        c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

function formatSize(bytes) {
    if (bytes < 1024) return bytes + 'B';
    if (bytes < 1024*1024) return (bytes/1024).toFixed(1) + 'K';
    if (bytes < 1024*1024*1024) return (bytes/1024/1024).toFixed(1) + 'M';
    return (bytes/1024/1024/1024).toFixed(2) + 'G';
}

async function renderSelectDevice() {
    root.innerHTML = `
        ${header()}
        <div class="card">
            <h2>Select USB</h2>
            <p>Pick the drive to flash. Removable devices are shown first.</p>
            <div class="device-list" id="devices">Loading…</div>
            <div class="actions">
                <button id="back">Back</button>
                <span style="display:flex; gap:0.5rem;">
                    <button id="refresh">Refresh</button>
                    <button class="primary" id="next" disabled>Next</button>
                </span>
            </div>
        </div>
    `;
    document.getElementById('back').onclick = () => go('select-iso');
    document.getElementById('refresh').onclick = () => renderSelectDevice();

    let data;
    try {
        data = await api('/devices');
    } catch (e) {
        document.getElementById('devices').textContent = `Error: ${e.message}`;
        return;
    }

    const list = document.getElementById('devices');
    if (!data.devices.length) {
        list.innerHTML = `<p>No block devices found. Plug in a USB and click Refresh.</p>`;
        return;
    }

    list.innerHTML = data.devices.map(d => `
        <div class="device" data-name="${d.name}">
            <span class="name">/dev/${d.name}</span>
            <span class="meta">${d.size} ${d.model || ''} (${d.tran || 'unknown'})</span>
            <span class="removable ${d.removable ? 'yes' : ''}">
                ${d.removable ? 'removable' : 'fixed'}
            </span>
        </div>
    `).join('');

    list.querySelectorAll('.device').forEach(el => {
        el.onclick = () => {
            list.querySelectorAll('.device').forEach(e => e.classList.remove('selected'));
            el.classList.add('selected');
            state.device = data.devices.find(d => d.name === el.dataset.name);
            document.getElementById('next').disabled = false;
        };
    });

    document.getElementById('next').onclick = () => go('select-apps');
}

async function renderSelectApps() {
    root.innerHTML = `
        ${header()}
        <div class="card">
            <h2>Optional apps</h2>
            <p>Bundle additional Nomad app images so they're ready to
               install offline from the Command Center. The base stack
               (admin, mysql, redis, dozzle) is always included.</p>
            <div id="apps-toolbar" class="apps-toolbar"></div>
            <div id="apps-list">Loading…</div>
            <div class="actions">
                <button id="back">Back</button>
                <span style="display:flex; align-items:center; gap:1rem;">
                    <span id="size-hint" style="color:var(--text-dim); font-size:0.85rem;"></span>
                    <button class="primary" id="next">Next</button>
                </span>
            </div>
        </div>
    `;
    document.getElementById('back').onclick = () => go('select-device');
    document.getElementById('next').onclick = () => go('confirm');

    let data;
    try {
        data = await api('/apps');
        // Cache the catalog globally so the confirm screen can show
        // friendly names instead of bare keys (the user's selections
        // are stored as keys in state.apps).
        window.__nomad_apps = data.apps;
    } catch (e) {
        document.getElementById('apps-list').textContent = `Error: ${e.message}`;
        return;
    }

    const list = document.getElementById('apps-list');
    const sizeHint = document.getElementById('size-hint');

    list.innerHTML = data.apps.map(a => {
        // Each app is a row with a checkbox, name (bold), small "powered by"
        // sized hint, and the description below. Clicking anywhere on the
        // row toggles the checkbox — easier target than the box itself.
        const checked = state.apps.has(a.key) ? 'checked' : '';
        const sizeStr = a.approx_mb >= 1000
            ? `${(a.approx_mb / 1000).toFixed(1)} GB`
            : `${a.approx_mb} MB`;
        return `
            <label class="app-row" data-key="${a.key}">
                <input type="checkbox" data-key="${a.key}" ${checked}>
                <div class="app-meta">
                    <div class="app-name">${escapeHtml(a.name)}
                        <span class="app-size">~${sizeStr}</span>
                    </div>
                    <div class="app-desc">${escapeHtml(a.description)}</div>
                </div>
            </label>
        `;
    }).join('');

    // Render the toolbar above the apps list. Two pill buttons that
    // toggle the whole list. They're separate buttons (rather than one
    // toggle that flips between states) because most users want a
    // direct action — "select all now" — and a single toggle button
    // means scanning the label to know what it'll do.
    const toolbar = document.getElementById('apps-toolbar');
    toolbar.innerHTML = `
        <button class="link-btn" id="select-all">Select all</button>
        <span class="toolbar-sep">·</span>
        <button class="link-btn" id="select-none">Clear</button>
    `;

    // Update the running size estimate whenever a checkbox changes,
    // and keep the state.apps Set in sync.
    const updateSize = () => {
        let totalMB = 0;
        for (const a of data.apps) {
            if (state.apps.has(a.key)) totalMB += a.approx_mb;
        }
        if (totalMB === 0) {
            sizeHint.textContent = 'base stack only';
        } else {
            const sz = totalMB >= 1000 ? `${(totalMB/1000).toFixed(1)} GB` : `${totalMB} MB`;
            sizeHint.textContent = `+${sz} of optional apps`;
        }
    };

    // Bulk toggle helper used by both buttons. Avoids triggering the
    // per-checkbox 'change' handler N times — we update state.apps and
    // call updateSize() once at the end.
    const setAll = (checked) => {
        list.querySelectorAll('input[type="checkbox"]').forEach(cb => {
            cb.checked = checked;
            const key = cb.dataset.key;
            if (checked) state.apps.add(key);
            else state.apps.delete(key);
        });
        updateSize();
    };

    document.getElementById('select-all').onclick = () => setAll(true);
    document.getElementById('select-none').onclick = () => setAll(false);

    list.querySelectorAll('input[type="checkbox"]').forEach(cb => {
        cb.addEventListener('change', () => {
            const key = cb.dataset.key;
            if (cb.checked) state.apps.add(key);
            else state.apps.delete(key);
            updateSize();
        });
    });
    updateSize();
}

function renderConfirm() {
    const dev = state.device;

    // Convert the user's selected app keys into friendly display
    // names using the catalog we cached during the apps step.
    const catalog = window.__nomad_apps || [];
    const selectedNames = [...state.apps]
        .map(key => {
            const a = catalog.find(c => c.key === key);
            return a ? a.name : key;
        });
    const appsLine = selectedNames.length
        ? selectedNames.join(', ')
        : '(none — base stack only)';

    root.innerHTML = `
        ${header()}
        <div class="card">
            <h2>Confirm</h2>
            <div class="alert danger">
                <strong>This will erase /dev/${dev?.name || '?'} completely.</strong>
                All existing data on the drive will be lost.
            </div>
            <p><strong>ISO:</strong> ${state.iso}</p>
            <p><strong>Device:</strong> /dev/${dev?.name || '?'}
               (${dev?.size || '?'} ${dev?.model || ''})</p>
            <p><strong>Optional apps:</strong> ${escapeHtml(appsLine)}</p>
            <div class="actions">
                <button id="back">Back</button>
                <button class="danger" id="flash">Wipe and flash</button>
            </div>
        </div>
    `;
    document.getElementById('back').onclick = () => go('select-apps');
    document.getElementById('flash').onclick = () => go('progress');
}

function renderProgress() {
    root.innerHTML = `
        ${header()}
        <div class="card">
            <h2 id="step-title">Flashing…</h2>
            <p>Don't unplug the drive or close this window.</p>
            <div class="log" id="log"></div>
            <div class="actions" id="actions" style="display:none;">
                <span></span>
                <button class="primary" id="continue">Continue</button>
            </div>
        </div>
    `;

    const logEl = document.getElementById('log');
    const titleEl = document.getElementById('step-title');
    const actionsEl = document.getElementById('actions');

    const append = (text) => {
        logEl.textContent += text + '\n';
        logEl.scrollTop = logEl.scrollHeight;
    };

    // WebSocket URL: derive from the current page so it works on any host/port.
    const wsUrl = (location.protocol === 'https:' ? 'wss://' : 'ws://')
                  + location.host + '/ws/flash';
    const ws = new WebSocket(wsUrl);

    let failed = false;

    ws.onopen = () => {
        // Send config as the first message.
        ws.send(JSON.stringify({
            iso_path: state.iso,
            device: '/dev/' + state.device.name,
            apps: [...state.apps],
            archive_path: state.fromArchive,
            no_cache: false,
        }));
        append('Connected. Starting…\n');
    };

    ws.onmessage = (ev) => {
        const line = ev.data;

        // Sentinel lines control UI state; everything else is just logged.
        if (line.startsWith('::step::')) {
            // Format: ::step::CURRENT/TOTAL::NAME
            const parts = line.split('::').slice(2);
            const [counter, ...nameParts] = parts;
            const name = nameParts.join('::');
            titleEl.textContent = `${counter}  ${name}`;
            append(`\n--- ${name} ---`);
        } else if (line === '::done::') {
            titleEl.textContent = 'Done';
        } else if (line.startsWith('::error::')) {
            failed = true;
            append('\n!! ' + line.slice('::error::'.length));
            titleEl.textContent = 'Failed';
        } else {
            append(line);
        }
    };

    ws.onclose = () => {
        if (failed) {
            append('\n[connection closed after error]');
            // Provide a way to restart the wizard
            actionsEl.style.display = 'flex';
            const btn = document.getElementById('continue');
            btn.textContent = 'Start over';
            btn.onclick = () => go('welcome');
        } else {
            append('\n[done]');
            // Move to the done screen
            actionsEl.style.display = 'flex';
            document.getElementById('continue').onclick = () => go('done');
        }
    };

    ws.onerror = (ev) => {
        append('\n!! WebSocket error');
        console.error('ws error', ev);
    };
}

function renderDone() {
    root.innerHTML = `
        ${header()}
        <div class="card">
            <h2>Done</h2>
            <p>Your Nomad USB is ready. Boot it on the target machine.</p>
            <div class="actions">
                <span></span>
                <button class="primary" id="restart">Flash another</button>
            </div>
        </div>
    `;
    document.getElementById('restart').onclick = () => {
        state.iso = null; state.device = null; state.apps.clear();
        go('welcome');
    };
}

// ---------- router ----------

const STEP_RENDERERS = {
    'welcome':       renderWelcome,
    'select-iso':    renderSelectIso,
    'select-device': renderSelectDevice,
    'select-apps':   renderSelectApps,
    'confirm':       renderConfirm,
    'progress':      renderProgress,
    'done':          renderDone,
};

function go(step) {
    state.current = step;
    STEP_RENDERERS[step]();
}

// ---------- bootstrap ----------

(async () => {
    try {
        const health = await api('/health');
        window.__nomad_health = health;  // for screens that want to react
        // Surface the version into state so header() can render it.
        // Default fallback to "?" if the backend somehow omits it.
        state.version = health.version || '?';
        console.log('backend ok', health);
    } catch (e) {
        root.innerHTML = `<div class="alert danger">
            Backend unreachable: ${e.message}
        </div>`;
        return;
    }
    go('welcome');
})();
