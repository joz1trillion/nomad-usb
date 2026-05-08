// nomad-flash — wizard SPA (v0.6.1).

const root = document.getElementById('app');

const STEPS = ['welcome', 'device', 'mode', 'confirm', 'progress', 'done'];

const state = {
    current: 'welcome',
    device: null,
    flashMode: 'full',
    version: null,
    isoPathOverride: '',
    dockerTarOverride: '',
    version_label: 'loading…',
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
            <span class="version">v${state.version_label}</span>
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

function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g,
        c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

function formatBytes(bytes) {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024*1024) return (bytes/1024).toFixed(1) + ' KB';
    if (bytes < 1024*1024*1024) return (bytes/1024/1024).toFixed(1) + ' MB';
    return (bytes/1024/1024/1024).toFixed(2) + ' GB';
}

// ---------- in-app file browser (used in advanced overrides) ----------
//
// Mounts a modal over the wizard. The user navigates the filesystem
// and clicks a file matching `allowedExts` to select it. Selection
// fires `onSelect(path)` and closes the modal.

function openFileBrowser(opts) {
    const allowedExts = opts.allowedExts || [];   // e.g. ['.iso']
    const onSelect = opts.onSelect || (() => {});

    const overlay = document.createElement('div');
    overlay.className = 'modal-overlay';
    const modal = document.createElement('div');
    modal.className = 'modal';
    overlay.appendChild(modal);
    document.body.appendChild(overlay);

    const close = () => overlay.remove();

    overlay.addEventListener('click', (e) => {
        if (e.target === overlay) close();
    });

    async function loadDir(path) {
        try {
            const params = new URLSearchParams();
            if (path) params.set('path', path);
            if (allowedExts.length) params.set('exts', allowedExts.join(','));
            const data = await api(`/picker/browse?${params}`);
            render(data);
        } catch (e) {
            modal.innerHTML = `
                <div class="modal-header">
                    <h2>Browse files</h2>
                    <button class="modal-close">✕</button>
                </div>
                <p style="color: var(--danger);">Browse failed: ${escapeHtml(e.message)}</p>
            `;
            modal.querySelector('.modal-close').onclick = close;
        }
    }

    function render(data) {
        const items = data.entries.map(e => {
            // Style by what they are. Directories: navigable (always).
            // Matching files: selectable. Other files: dim, non-clickable.
            const icon = e.is_dir ? '📁' : (e.is_match ? '💿' : '📄');
            const cls = e.is_dir
                ? 'fb-entry fb-dir'
                : (e.is_match ? 'fb-entry fb-match' : 'fb-entry fb-other');
            return `
                <div class="${cls}" data-path="${escapeHtml(e.path)}"
                     data-is-dir="${e.is_dir}" data-is-match="${e.is_match}">
                    <span class="fb-icon">${icon}</span>
                    <span class="fb-name">${escapeHtml(e.name)}</span>
                    <span class="fb-size">
                        ${e.is_dir ? '' : formatBytes(e.size)}
                    </span>
                </div>
            `;
        }).join('');

        const upEntry = data.parent
            ? `<div class="fb-entry fb-dir" data-path="${escapeHtml(data.parent)}"
                    data-is-dir="true" data-is-match="false">
                 <span class="fb-icon">📁</span>
                 <span class="fb-name">..</span>
                 <span class="fb-size"></span>
               </div>`
            : '';

        modal.innerHTML = `
            <div class="modal-header">
                <h2>Browse files</h2>
                <button class="modal-close">✕</button>
            </div>
            <div class="fb-cwd">${escapeHtml(data.cwd)}</div>
            <div class="fb-list">
                ${upEntry}
                ${items || '<p class="hint" style="padding: 1rem;">empty</p>'}
            </div>
        `;

        modal.querySelector('.modal-close').onclick = close;

        modal.querySelectorAll('.fb-entry').forEach(el => {
            el.onclick = () => {
                const isDir = el.dataset.isDir === 'true';
                const isMatch = el.dataset.isMatch === 'true';
                if (isDir) {
                    loadDir(el.dataset.path);
                } else if (isMatch) {
                    onSelect(el.dataset.path);
                    close();
                }
                // Non-matching files: clicks are no-ops (dim styling
                // signals this; we don't need a popup explaining it).
            };
        });
    }

    loadDir(null);
}

// ---------- step: welcome ----------

async function renderWelcome() {
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
            <p>This tool writes a Project Nomad ISO to a USB drive
               and pre-populates it with everything Nomad needs to
               run offline. The target USB will be
               <strong>completely wiped</strong>.</p>
            <p>By default, the latest release is downloaded automatically
               from GitHub. You'll need internet for that download.</p>

            <details class="advanced" id="advanced-toggle">
                <summary>Advanced options</summary>
                <div class="advanced-body" id="advanced-body">Loading…</div>
            </details>

            <div class="actions">
                <span></span>
                <button class="primary" id="next">Get started</button>
            </div>
        </div>
    `;

    document.getElementById('next').onclick = () => go('device');

    const adv = document.getElementById('advanced-toggle');
    let hydrated = false;
    adv.addEventListener('toggle', async () => {
        if (adv.open && !hydrated) {
            hydrated = true;
            await renderAdvanced();
        }
    });
}

async function renderAdvanced() {
    const body = document.getElementById('advanced-body');

    const [relsRes, cacheRes] = await Promise.allSettled([
        api('/releases'),
        api('/cache'),
    ]);

    const releases = relsRes.status === 'fulfilled' ? relsRes.value : { releases: [], error: relsRes.reason?.message };
    const cache = cacheRes.status === 'fulfilled' ? cacheRes.value : { versions: [], total_bytes: 0 };

    const releaseOptions = ['<option value="">latest (recommended)</option>']
        .concat(releases.releases.map(r =>
            `<option value="${escapeHtml(r.tag)}"
                     ${state.version === r.tag ? 'selected' : ''}>
                 ${escapeHtml(r.tag)}
             </option>`
        )).join('');

    const releaseError = releases.error
        ? `<div class="hint" style="color: var(--warn);">
               (couldn't fetch releases: ${escapeHtml(releases.error)})
           </div>`
        : '';

    const cacheRows = cache.versions.length
        ? cache.versions.map(v => `
            <div class="cache-row">
                <span class="cache-tag">${escapeHtml(v.tag)}</span>
                <span class="cache-size">${formatBytes(v.bytes)}</span>
                <button class="clear-cache-btn" data-tag="${escapeHtml(v.tag)}">
                    Clear
                </button>
            </div>
        `).join('')
        : `<div class="hint">cache is empty</div>`;

    const cacheTotal = cache.total_bytes
        ? `<div class="hint" style="margin-top:0.5rem;">
               Total: ${formatBytes(cache.total_bytes)}
           </div>`
        : '';

    body.innerHTML = `
        <div class="adv-section">
            <label for="adv-version">Release version</label>
            <select id="adv-version">${releaseOptions}</select>
            ${releaseError}
            <div class="hint">
                Pick a specific release if you need to reproduce an exact
                build. Latest is right for almost everyone.
            </div>
        </div>

        <div class="adv-section">
            <label for="adv-iso">Local ISO override</label>
            <div class="input-with-button">
                <input type="text" id="adv-iso"
                       placeholder="(empty = download from release)"
                       value="${escapeHtml(state.isoPathOverride)}">
                <button id="adv-iso-browse">Browse…</button>
            </div>
            <div class="hint">
                Path to a local ISO file. When set, skips downloading.
            </div>
        </div>

        <div class="adv-section">
            <label for="adv-docker">Local docker tarball override</label>
            <div class="input-with-button">
                <input type="text" id="adv-docker"
                       placeholder="(empty = download from release)"
                       value="${escapeHtml(state.dockerTarOverride)}">
                <button id="adv-docker-browse">Browse…</button>
            </div>
            <div class="hint">
                Path to a local <code>.tar.xz</code> or <code>.tar.gz</code>
                of <code>docker/</code>. Only used in Full mode.
            </div>
        </div>

        <div class="adv-section">
            <label>Download cache</label>
            <div class="cache-list">${cacheRows}</div>
            ${cacheTotal}
            <button id="clear-all-cache" style="margin-top:0.5rem;"
                    ${cache.versions.length === 0 ? 'disabled' : ''}>
                Clear all cached downloads
            </button>
        </div>
    `;

    const verSel = document.getElementById('adv-version');
    verSel.onchange = () => { state.version = verSel.value || null; };

    const isoIn = document.getElementById('adv-iso');
    isoIn.oninput = () => { state.isoPathOverride = isoIn.value.trim(); };

    document.getElementById('adv-iso-browse').onclick = () => {
        openFileBrowser({
            allowedExts: ['.iso'],
            onSelect: (path) => {
                isoIn.value = path;
                state.isoPathOverride = path;
            },
        });
    };

    const dockerIn = document.getElementById('adv-docker');
    dockerIn.oninput = () => { state.dockerTarOverride = dockerIn.value.trim(); };

    document.getElementById('adv-docker-browse').onclick = () => {
        openFileBrowser({
            // We accept either compressed format. The backend sniffs
            // magic bytes anyway, so the extension is just a hint to
            // make selection feel right.
            allowedExts: ['.xz', '.gz', '.tar.xz', '.tar.gz'],
            onSelect: (path) => {
                dockerIn.value = path;
                state.dockerTarOverride = path;
            },
        });
    };

    body.querySelectorAll('.clear-cache-btn').forEach(btn => {
        btn.onclick = async () => {
            const tag = btn.dataset.tag;
            btn.disabled = true;
            btn.textContent = 'Clearing…';
            try {
                await fetch(`/api/cache?version=${encodeURIComponent(tag)}`,
                    { method: 'DELETE' });
                await renderAdvanced();
            } catch (e) {
                btn.textContent = 'Failed';
            }
        };
    });

    const clearAll = document.getElementById('clear-all-cache');
    clearAll.onclick = async () => {
        if (!confirm('Delete all cached downloads? They will be re-downloaded on the next flash.')) {
            return;
        }
        clearAll.disabled = true;
        clearAll.textContent = 'Clearing…';
        try {
            await fetch('/api/cache', { method: 'DELETE' });
            await renderAdvanced();
        } catch (e) {
            clearAll.textContent = 'Failed';
        }
    };
}

// ---------- step: device ----------

async function renderDevice() {
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

    document.getElementById('back').onclick = () => go('welcome');
    document.getElementById('refresh').onclick = () => renderDevice();

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

    if (state.device) {
        const prev = list.querySelector(`.device[data-name="${state.device.name}"]`);
        if (prev) {
            prev.classList.add('selected');
            document.getElementById('next').disabled = false;
        }
    }

    document.getElementById('next').onclick = () => go('mode');
}

// ---------- step: mode ----------

function renderMode() {
    root.innerHTML = `
        ${header()}
        <div class="card">
            <h2>Choose a mode</h2>
            <p>Both modes give you the same Nomad. They differ only
               in when the container images are downloaded.</p>

            <div class="mode-grid">
                <div class="mode-card ${state.flashMode === 'full' ? 'selected' : ''}"
                     data-mode="full">
                    <div class="mode-card-title">Full</div>
                    <div class="mode-card-tag">recommended</div>
                    <div class="mode-card-desc">
                        Downloads everything now (~4.4 GB). The USB will
                        boot and Nomad will be ready in a few minutes.
                        <strong>Works fully offline</strong> after flashing.
                    </div>
                </div>

                <div class="mode-card ${state.flashMode === 'base' ? 'selected' : ''}"
                     data-mode="base">
                    <div class="mode-card-title">Base only</div>
                    <div class="mode-card-tag">smaller download</div>
                    <div class="mode-card-desc">
                        Downloads just the ISO (~1 GB). Container images
                        will be pulled on first boot, so the laptop you
                        boot the USB on <strong>needs internet</strong>
                        the first time.
                    </div>
                </div>
            </div>

            <div class="actions">
                <button id="back">Back</button>
                <button class="primary" id="next">Next</button>
            </div>
        </div>
    `;

    document.getElementById('back').onclick = () => go('device');
    document.getElementById('next').onclick = () => go('confirm');

    root.querySelectorAll('.mode-card').forEach(el => {
        el.onclick = () => {
            root.querySelectorAll('.mode-card').forEach(e => e.classList.remove('selected'));
            el.classList.add('selected');
            state.flashMode = el.dataset.mode;
        };
    });
}

// ---------- step: confirm ----------

function renderConfirm() {
    const dev = state.device;
    const versionLabel = state.version || 'latest';
    const sourceLabel = state.flashMode === 'full'
        ? 'ISO + prebuilt docker tree'
        : 'ISO only (containers pull on first boot)';

    const overrides = [];
    if (state.isoPathOverride) {
        overrides.push(`<li>Using local ISO: <code>${escapeHtml(state.isoPathOverride)}</code></li>`);
    }
    if (state.dockerTarOverride && state.flashMode === 'full') {
        overrides.push(`<li>Using local docker tarball: <code>${escapeHtml(state.dockerTarOverride)}</code></li>`);
    }
    const overrideHtml = overrides.length
        ? `<div class="hint" style="margin-top:0.75rem;">
               <strong>Overrides:</strong>
               <ul style="margin:0.25rem 0 0 1rem; padding:0;">${overrides.join('')}</ul>
           </div>`
        : '';

    root.innerHTML = `
        ${header()}
        <div class="card">
            <h2>Confirm</h2>
            <div class="alert danger">
                <strong>This will erase /dev/${dev?.name || '?'} completely.</strong>
                All existing data on the drive will be lost.
            </div>
            <p><strong>Device:</strong> /dev/${dev?.name || '?'}
               (${dev?.size || '?'} ${dev?.model || ''})</p>
            <p><strong>Mode:</strong> ${state.flashMode}
               <span style="color:var(--text-dim);">— ${sourceLabel}</span></p>
            <p><strong>Release:</strong> ${escapeHtml(versionLabel)}</p>
            ${overrideHtml}
            <div class="actions">
                <button id="back">Back</button>
                <button class="danger" id="flash">Wipe and flash</button>
            </div>
        </div>
    `;
    document.getElementById('back').onclick = () => go('mode');
    document.getElementById('flash').onclick = () => go('progress');
}

// ---------- step: progress ----------
//
// New design (v0.6.1):
//   - Big visual progress bar at the top showing overall completion
//   - Step name + step counter shown alongside
//   - Logs collapsed by default in <details>, auto-expands on error
//   - Log buffer capped to LOG_BUFFER_LINES so we can't blow out
//     browser memory if a step gets chatty (this is what fixed the
//     v0.6.0 25GB-RAM bug)

const LOG_BUFFER_LINES = 500;   // keep last N lines only

function renderProgress() {
    root.innerHTML = `
        ${header()}
        <div class="card">
            <h2 id="step-title">Starting…</h2>
            <p>Don't unplug the drive or close this window.</p>

            <div class="overall-progress">
                <div class="overall-progress-bar">
                    <div class="overall-progress-fill" id="overall-fill"
                         style="width: 0%;"></div>
                </div>
                <div class="overall-progress-text" id="overall-text">0%</div>
            </div>

            <details id="log-details">
                <summary>Show logs</summary>
                <div class="log" id="log"></div>
            </details>

            <div class="actions" id="actions" style="display:none;">
                <span></span>
                <button class="primary" id="continue">Continue</button>
            </div>
        </div>
    `;

    const logEl = document.getElementById('log');
    const titleEl = document.getElementById('step-title');
    const fillEl = document.getElementById('overall-fill');
    const textEl = document.getElementById('overall-text');
    const detailsEl = document.getElementById('log-details');
    const actionsEl = document.getElementById('actions');

    // Log buffer — array of strings, bounded length. Only the visible
    // text in the DOM is the joined buffer, so we don't grow textNodes
    // forever.
    const buf = [];
    const append = (text) => {
        // Split multi-line text and push each line so the buffer cap
        // is line-based, not write-based.
        const lines = String(text).split('\n');
        for (const line of lines) {
            buf.push(line);
        }
        // Keep only the last N lines. Splice from the front when over.
        if (buf.length > LOG_BUFFER_LINES) {
            buf.splice(0, buf.length - LOG_BUFFER_LINES);
        }
        // Single textContent assignment is the one expensive op here,
        // but we only do it once per message, not per line.
        logEl.textContent = buf.join('\n');
        logEl.scrollTop = logEl.scrollHeight;
    };

    // Track step counters so we can compute overall progress.
    let totalSteps = 0;
    let completedSteps = 0;
    let currentStepFraction = 0;  // 0..1, fraction of current step done

    const updateBar = () => {
        if (totalSteps === 0) return;
        // Each step is worth (100/totalSteps) percent of the overall bar.
        // completedSteps counts steps that have FINISHED (so the next
        // ::step:: marker increments it). currentStepFraction is the
        // partial progress within the running step.
        const overall = ((completedSteps + currentStepFraction) / totalSteps) * 100;
        const clamped = Math.max(0, Math.min(100, overall));
        fillEl.style.width = clamped.toFixed(1) + '%';
        textEl.textContent = clamped.toFixed(0) + '%';
    };

    const wsUrl = (location.protocol === 'https:' ? 'wss://' : 'ws://')
                  + location.host + '/ws/flash';
    const ws = new WebSocket(wsUrl);
    let failed = false;

    ws.onopen = () => {
        ws.send(JSON.stringify({
            device: '/dev/' + state.device.name,
            flash_mode: state.flashMode,
            version: state.version,
            iso_path: state.isoPathOverride || null,
            prebuilt_docker_path: state.dockerTarOverride || null,
        }));
        append('Connected. Starting…');
    };

    ws.onmessage = (ev) => {
        const line = ev.data;

        // ---- ::step::N/TOTAL::NAME — step boundary marker ----
        // The first such marker tells us totalSteps. Every subsequent
        // marker advances the completed counter.
        if (line.startsWith('::step::')) {
            const parts = line.split('::').slice(2);
            const [counter, ...nameParts] = parts;
            const name = nameParts.join('::');
            const m = counter.match(/^(\d+)\/(\d+)$/);
            if (m) {
                const stepNum = parseInt(m[1], 10);
                totalSteps = parseInt(m[2], 10);
                // The marker says "starting step N", so completed = N-1.
                // Reset within-step fraction since we're entering a new step.
                completedSteps = stepNum - 1;
                currentStepFraction = 0;
            }
            titleEl.textContent = `Step ${counter} — ${name}`;
            updateBar();
            append(`\n--- ${name} ---`);
            return;
        }

        // ---- ::progress::N — within-step progress (0-100) ----
        if (line.startsWith('::progress::')) {
            const pct = parseInt(line.slice('::progress::'.length), 10);
            if (!isNaN(pct)) {
                currentStepFraction = Math.max(0, Math.min(100, pct)) / 100;
                updateBar();
            }
            return;
        }

        // ---- ::done:: — pipeline finished successfully ----
        if (line === '::done::') {
            titleEl.textContent = 'Done';
            completedSteps = totalSteps;
            currentStepFraction = 0;
            updateBar();
            return;
        }

        // ---- ::error::msg — pipeline failed ----
        if (line.startsWith('::error::')) {
            failed = true;
            append('\n!! ' + line.slice('::error::'.length));
            titleEl.textContent = 'Failed';
            // Auto-open the logs panel so the user can see what happened
            // without having to click into it.
            detailsEl.open = true;
            return;
        }

        // ---- regular log line ----
        append(line);
    };

    ws.onclose = () => {
        if (failed) {
            append('\n[connection closed after error]');
            actionsEl.style.display = 'flex';
            const btn = document.getElementById('continue');
            btn.textContent = 'Start over';
            btn.onclick = () => {
                resetState();
                go('welcome');
            };
        } else {
            append('\n[done]');
            // Make sure the bar lands at 100% even if the last
            // ::done:: marker arrived before any progress markers.
            completedSteps = totalSteps || 1;
            currentStepFraction = 0;
            updateBar();
            actionsEl.style.display = 'flex';
            document.getElementById('continue').onclick = () => go('done');
        }
    };

    ws.onerror = (ev) => {
        append('\n!! WebSocket error');
        // Open logs so the error is visible.
        detailsEl.open = true;
        console.error('ws error', ev);
    };
}

// ---------- step: done ----------

function renderDone() {
    root.innerHTML = `
        ${header()}
        <div class="card">
            <h2>Done</h2>
            <p>Your Nomad USB is ready. Boot it on the target machine.</p>
            <p style="color:var(--text-dim); font-size:0.9rem;">
                On first boot, log in as <code>nomad</code> /
                <code>nomad</code>. The MOTD shows the URL to access
                Nomad and how to start a hotspot if needed.
            </p>
            <div class="actions">
                <span></span>
                <button class="primary" id="restart">Flash another</button>
            </div>
        </div>
    `;
    document.getElementById('restart').onclick = () => {
        resetState();
        go('welcome');
    };
}

function resetState() {
    state.device = null;
    state.flashMode = 'full';
}

// ---------- router ----------

const STEP_RENDERERS = {
    'welcome':  renderWelcome,
    'device':   renderDevice,
    'mode':     renderMode,
    'confirm':  renderConfirm,
    'progress': renderProgress,
    'done':     renderDone,
};

function go(step) {
    state.current = step;
    STEP_RENDERERS[step]();
}

// ---------- bootstrap ----------

(async () => {
    try {
        const health = await api('/health');
        window.__nomad_health = health;
        state.version_label = health.version || '?';
        console.log('backend ok', health);
    } catch (e) {
        root.innerHTML = `<div class="alert danger">
            Backend unreachable: ${e.message}
        </div>`;
        return;
    }
    go('welcome');
})();
