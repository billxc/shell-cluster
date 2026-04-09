// --- State ---
let PEERS = [];
const sessions = new Map();        // tabId -> session state (locally connected)
const remoteSessions = new Map();   // peer.name -> [{id, shell, created_at}]
const collapsedPeers = new Set();   // peer names that are collapsed
let activeTab = null;
let tabCounter = 0;
let loadingSessions = false;
let daemonWsUrl = 'ws://127.0.0.1:9000'; // TODO: make configurable from frontend

// Derive HTTP base URL from daemon WS URL
function daemonHttpUrl() {
  return daemonWsUrl.replace(/^ws(s?):/, 'http$1:');
}

// --- Session names (persisted in localStorage) ---
const SESSION_NAMES_KEY = 'shellcluster-session-names';
function loadSessionNames() {
  try { return JSON.parse(localStorage.getItem(SESSION_NAMES_KEY)) || {}; } catch { return {}; }
}
function saveSessionName(sessionId, name) {
  const names = loadSessionNames();
  if (name) names[sessionId] = name; else delete names[sessionId];
  localStorage.setItem(SESSION_NAMES_KEY, JSON.stringify(names));
}
function getSessionDisplayName(s) {
  const custom = loadSessionNames()[s.sessionId];
  if (custom) return custom;
  return `${s.shell || 'shell'} #${s.sessionId.slice(0,6)}`;
}

function renameSession(tabId) {
  const s = sessions.get(tabId);
  if (!s) return;
  const current = loadSessionNames()[s.sessionId] || '';
  const name = prompt('Rename session:', current || `${s.shell || 'shell'} #${s.sessionId.slice(0,6)}`);
  if (name === null) return; // cancelled
  saveSessionName(s.sessionId, name.trim());
  renderTabs();
  renderPeers();
}

// --- Fetch peers from daemon ---
async function fetchPeers() {
  try {
    const resp = await fetch(daemonHttpUrl() + '/api/peers');
    PEERS = await resp.json();
  } catch (e) {
    console.warn('Failed to fetch peers:', e);
  }
  renderPeers();
}

// --- Fetch sessions from each peer directly (parallel) ---
async function fetchSessions() {
  loadingSessions = true;
  renderPeers();
  const newRemote = new Map();

  // Query each peer's /sessions HTTP endpoint in parallel
  const promises = PEERS.map(async (peer) => {
    if (!peer.uri) return;
    try {
      // Derive HTTP URL from ws:// URI: ws://localhost:PORT -> http://localhost:PORT/sessions
      const httpUrl = peer.uri.replace(/^ws(s?):\/\//, (_, s) => `http${s}://`) + '/sessions';
      const resp = await fetch(httpUrl, { signal: AbortSignal.timeout(3000) });
      const peerSessions = await resp.json();
      if (!Array.isArray(peerSessions)) return;
      // Filter out sessions we already have connected locally (skip disconnected)
      const connectedIds = new Set();
      for (const [, s] of sessions) {
        if (s.peerName === peer.name && !s._disconnected) connectedIds.add(s.sessionId);
      }
      const remote = peerSessions.filter(s => !connectedIds.has(s.id));
      newRemote.set(peer.name, remote);
    } catch (e) {
      console.debug(`Failed to fetch sessions from ${peer.name}:`, e);
    }
  });

  await Promise.allSettled(promises);

  remoteSessions.clear();
  for (const [k, v] of newRemote) remoteSessions.set(k, v);

  loadingSessions = false;
  renderPeers();
}

// --- Refresh everything ---
async function refreshAll() {
  const btn = document.getElementById('btn-refresh');
  btn.disabled = true;
  btn.textContent = '... Loading';
  try {
    await fetchPeers();
    await fetchSessions();
  } finally {
    btn.disabled = false;
    btn.textContent = '\u21bb Refresh';
  }
}

// --- Trigger discovery refresh (calls tunnel API, slower) ---
async function discoverPeers() {
  if (!confirm('This will query the tunnel API to discover new peers. Continue?')) return;
  const btn = document.getElementById('btn-discover');
  btn.disabled = true;
  btn.textContent = '... Discovering';
  try {
    const resp = await fetch(daemonHttpUrl() + '/api/refresh-peers');
    const data = await resp.json();
    if (data.ok) {
      await fetchPeers();
      await fetchSessions();
    }
  } catch (e) {
    console.warn('Discovery failed:', e);
  } finally {
    btn.disabled = false;
    btn.textContent = '\uD83D\uDD0D Discover';
  }
}

// --- Peer list rendering ---
function renderPeers() {
  const list = document.getElementById('peer-list');
  list.innerHTML = '';

  for (const peer of PEERS) {
    const group = document.createElement('div');
    group.className = 'peer-group';

    const isCollapsed = collapsedPeers.has(peer.name);

    // Peer header
    const header = document.createElement('div');
    header.className = 'peer-header';

    const validStatuses = ['online', 'offline', 'connecting'];
    const statusClass = validStatuses.includes(peer.status) ? peer.status : 'online';
    header.innerHTML = `
      <span class="peer-status ${statusClass}"></span>
      <span class="peer-name">${esc(peer.name)}</span>
      <span class="peer-actions">
        <button class="peer-btn" title="New shell" data-action="new">+</button>
        <button class="peer-btn" title="${isCollapsed ? 'Expand' : 'Collapse'}" data-action="toggle">${isCollapsed ? '\u25B6' : '\u25BC'}</button>
      </span>
    `;
    header.addEventListener('click', (e) => {
      const action = e.target.closest('[data-action]');
      if (action) {
        e.stopPropagation();
        if (action.dataset.action === 'new') {
          createSession(peer);
        } else if (action.dataset.action === 'toggle') {
          togglePeer(peer.name);
        }
      } else {
        togglePeer(peer.name);
      }
    });
    group.appendChild(header);

    // Sessions container
    const sessionsDiv = document.createElement('div');
    sessionsDiv.className = 'peer-sessions' + (isCollapsed ? ' collapsed' : '');

    // Connected sessions (local)
    let hasAny = false;
    for (const [tabId, s] of sessions) {
      if (s.peerName === peer.name) {
        hasAny = true;
        const item = document.createElement('div');
        const isDisconnected = s._disconnected;
        item.className = 'session-item' + (tabId === activeTab ? ' active' : '');
        if (isDisconnected) item.style.opacity = '0.5';
        item.innerHTML = `
          <span class="session-icon">${isDisconnected ? '\u25CB' : '\u25B8'}</span>
          <span class="session-label">${esc(getSessionDisplayName(s))}${isDisconnected ? ' (disconnected)' : ''}</span>
        `;
        item.onclick = (e) => { e.stopPropagation(); switchTab(tabId); };
        item.ondblclick = (e) => { e.stopPropagation(); renameSession(tabId); };
        item.title = 'Double-click to rename';
        sessionsDiv.appendChild(item);
      }
    }

    // Remote sessions (on server but not locally connected)
    const remote = remoteSessions.get(peer.name) || [];
    for (const rs of remote) {
      hasAny = true;
      const item = document.createElement('div');
      item.className = 'session-item remote';
      const age = rs.created_at ? timeAgo(rs.created_at) : '';
      const customName = loadSessionNames()[rs.id];
      const displayName = customName || `${rs.shell || 'shell'} #${rs.id.slice(0,6)}`;
      item.innerHTML = `
        <span class="session-icon" style="color:var(--blue)">\u21bb</span>
        <span class="session-label">${esc(displayName)}</span>
        ${age ? `<span class="session-time">${age}</span>` : ''}
      `;
      item.title = 'Click to reconnect to this session';
      item.onclick = (e) => { e.stopPropagation(); createSession(peer, rs.id); };
      sessionsDiv.appendChild(item);
    }

    // Loading state
    if (loadingSessions && !hasAny) {
      const hint = document.createElement('div');
      hint.className = 'loading-hint';
      hint.textContent = 'Loading sessions...';
      sessionsDiv.appendChild(hint);
    }

    // No sessions hint
    if (!loadingSessions && !hasAny) {
      const hint = document.createElement('div');
      hint.className = 'no-sessions-hint';
      hint.textContent = 'No active sessions';
      sessionsDiv.appendChild(hint);
    }

    group.appendChild(sessionsDiv);
    list.appendChild(group);
  }
}

function togglePeer(name) {
  if (collapsedPeers.has(name)) {
    collapsedPeers.delete(name);
  } else {
    collapsedPeers.add(name);
  }
  renderPeers();
}

// --- Tabs ---
function renderTabs() {
  const bar = document.getElementById('tab-bar');
  bar.innerHTML = '';

  for (const [tabId, s] of sessions) {
    const tab = document.createElement('div');
    tab.className = 'tab' + (tabId === activeTab ? ' active' : '');
    tab.innerHTML = `
      ${esc(getSessionDisplayName(s))}
      <span class="tab-close" onclick="event.stopPropagation(); closeSession('${tabId}')">\u2715</span>
    `;
    tab.onclick = () => switchTab(tabId);
    tab.ondblclick = (e) => { e.stopPropagation(); renameSession(tabId); };
    tab.title = 'Double-click to rename';
    bar.appendChild(tab);
  }
}

function switchTab(tabId) {
  activeTab = tabId;

  document.querySelectorAll('.terminal-pane').forEach(p => p.classList.remove('active'));
  const welcome = document.getElementById('welcome');

  const s = sessions.get(tabId);
  if (s && s.pane) {
    s.pane.classList.add('active');
    welcome.style.display = 'none';
    setTimeout(() => {
      s.fitAddon.fit();
      s.term.focus();
    }, 10);
  }

  renderTabs();
  renderPeers();
}

// --- Session management ---
function createSession(peer, existingSessionId) {
  const tabId = `tab-${++tabCounter}`;
  const isAttach = !!existingSessionId;

  // If reconnecting, remove from remoteSessions
  if (isAttach) {
    const remote = remoteSessions.get(peer.name) || [];
    remoteSessions.set(peer.name, remote.filter(r => r.id !== existingSessionId));
  }

  const pane = document.createElement('div');
  pane.className = 'terminal-pane';
  pane.id = tabId;
  document.getElementById('terminal-container').appendChild(pane);

  const term = new Terminal({
    cursorBlink: true,
    fontSize: 14,
    fontFamily: "'JetBrains Mono', 'Fira Code', 'Cascadia Code', Menlo, monospace",
    theme: {
      background: '#1e1e2e',
      foreground: '#cdd6f4',
      cursor: '#f5e0dc',
      selectionBackground: '#45475a',
      black: '#45475a',
      red: '#f38ba8',
      green: '#a6e3a1',
      yellow: '#f9e2af',
      blue: '#89b4fa',
      magenta: '#cba6f7',
      cyan: '#94e2d5',
      white: '#bac2de',
    },
  });
  const fitAddon = new FitAddon.FitAddon();
  term.loadAddon(fitAddon);
  term.open(pane);
  fitAddon.fit();

  const sessionId = existingSessionId || genId();
  const { cols, rows } = term;
  // Connect directly to the peer's /raw endpoint — no proxy needed
  const rawParams = isAttach
    ? `attach=${sessionId}&cols=${cols}&rows=${rows}`
    : `session=${sessionId}&cols=${cols}&rows=${rows}`;
  const wsUrl = `${peer.uri}/raw?${rawParams}`;
  const ws = new WebSocket(wsUrl);

  const sessionState = {
    ws, term, peerName: peer.name, sessionId, pane, fitAddon,
    shell: '', targetUri: peer.uri, _attachAddon: null,
  };
  sessions.set(tabId, sessionState);

  term.writeln(`\x1b[2m${isAttach ? 'Reconnecting' : 'Connecting'} to ${peer.name}...\x1b[0m`);

  let attached = false;

  ws.onmessage = (event) => {
    const data = event.data;

    // Before attach addon takes over, handle text control messages
    if (!attached && typeof data === 'string') {
      try {
        const msg = JSON.parse(data);
        if (msg.type === 'shell.created' || msg.type === 'shell.attached') {
          sessionState.shell = msg.shell || '?';
          attached = true;
          term.clear();
          renderTabs();
          renderPeers();
          // Now load attach addon — it takes over binary I/O
          const attachAddon = new AttachAddon.AttachAddon(ws, { bidirectional: true });
          term.loadAddon(attachAddon);
          sessionState._attachAddon = attachAddon;
          return;
        }
        if (msg.type === 'shell.closed') {
          term.writeln('\r\n\x1b[2m[Session closed]\x1b[0m');
          return;
        }
        if (msg.type === 'error') {
          term.writeln(`\r\n\x1b[31mError: ${msg.error}\x1b[0m`);
          return;
        }
      } catch (e) {
        // Not JSON — write to terminal
        term.write(data);
      }
    }
  };

  ws.onclose = () => {
    if (!attached) {
      term.writeln('\r\n\x1b[2m[Disconnected]\x1b[0m');
    }
    sessionState._disconnected = true;
    renderPeers();
  };

  ws.onerror = () => {
    term.writeln('\r\n\x1b[31m[Connection error]\x1b[0m');
  };

  // Resize sends JSON text frame (control channel) — works alongside attach addon
  term.onResize(({ cols, rows }) => {
    if (ws.readyState === WebSocket.OPEN && attached) {
      ws.send(JSON.stringify({
        type: 'shell.resize',
        session_id: sessionId,
        cols, rows,
      }));
    }
  });

  const resizeObserver = new ResizeObserver(() => {
    if (activeTab === tabId) {
      fitAddon.fit();
    }
  });
  resizeObserver.observe(pane);
  sessionState._resizeObserver = resizeObserver;

  switchTab(tabId);
}

function closeSession(tabId) {
  const s = sessions.get(tabId);
  if (!s) return;

  if (s.ws.readyState === WebSocket.OPEN) {
    try {
      s.ws.send(JSON.stringify({
        type: 'shell.close',
        session_id: s.sessionId,
      }));
    } catch (e) {}
    s.ws.close();
  }

  s.term.dispose();
  s._resizeObserver?.disconnect();
  s.pane.remove();
  sessions.delete(tabId);

  if (activeTab === tabId) {
    const remaining = [...sessions.keys()];
    if (remaining.length > 0) {
      switchTab(remaining[remaining.length - 1]);
    } else {
      activeTab = null;
      document.getElementById('welcome').style.display = 'flex';
      renderTabs();
    }
  }

  renderPeers();
  renderTabs();
}

// --- Helpers ---
function genId() {
  return Math.random().toString(16).slice(2, 14);
}

function esc(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

function timeAgo(isoStr) {
  try {
    const then = new Date(isoStr);
    const now = new Date();
    const sec = Math.floor((now - then) / 1000);
    if (sec < 60) return 'just now';
    const min = Math.floor(sec / 60);
    if (min < 60) return `${min}m`;
    const hr = Math.floor(min / 60);
    if (hr < 24) return `${hr}h`;
    const day = Math.floor(hr / 24);
    return `${day}d`;
  } catch (e) {
    return '';
  }
}

// --- Keyboard shortcuts ---
document.addEventListener('keydown', (e) => {
  // Alt+[ / Alt+] to switch tabs (Ctrl+Tab is intercepted by browsers)
  if (e.altKey && (e.key === '[' || e.key === ']')) {
    e.preventDefault();
    const tabIds = [...sessions.keys()];
    if (tabIds.length < 2) return;
    const idx = tabIds.indexOf(activeTab);
    const next = e.key === '['
      ? (idx - 1 + tabIds.length) % tabIds.length
      : (idx + 1) % tabIds.length;
    switchTab(tabIds[next]);
  }
});

// --- Init ---
async function init() {
  await fetchPeers();
  await fetchSessions();
  renderTabs();
}
init();

// Retry at startup for late-starting peers
setTimeout(() => fetchPeers().then(fetchSessions), 3000);
setTimeout(() => fetchPeers().then(fetchSessions), 10000);

// Periodic refresh
setInterval(async () => {
  await fetchPeers();
  await fetchSessions();
}, 30000);

// Global resize
window.addEventListener('resize', () => {
  if (activeTab) {
    const s = sessions.get(activeTab);
    if (s) s.fitAddon.fit();
  }
});
