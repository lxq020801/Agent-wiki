// popup.js - Obsidian Librarian control console
// 扩展只做辅助控制台：Agent 状态、知识库识别、模型配置、Cookie 回传。
// 视频拆解由 Agent 会话触发，不在扩展里启动。

const WS_URL = 'ws://127.0.0.1:8765';
const PROVIDERS = {
  doubao: {
    label: '豆包 / 火山方舟 API',
    shortLabel: '方舟 API',
    endpoint: 'https://ark.cn-beijing.volces.com/api/v3',
    model: 'doubao-seed-2-0-lite-260428',
    keyPlaceholder: '普通方舟 API Key',
    modelPlaceholder: 'doubao-seed-2-0-lite-260428',
    note: '普通 API Key · 按量计费接口'
  },
  volcengine_agent_plan: {
    label: '火山 Agent Plan',
    shortLabel: 'Agent Plan',
    endpoint: 'https://ark.cn-beijing.volces.com/api/plan/v3',
    model: 'doubao-seed-2.0-lite',
    keyPlaceholder: 'Agent Plan 专属 API Key',
    modelPlaceholder: 'doubao-seed-2.0-lite / ark-code-latest',
    note: '专属 Plan Key · /api/plan/v3'
  }
};
const DEFAULT_PROVIDER = 'doubao';
const DEFAULT_ENDPOINT = PROVIDERS[DEFAULT_PROVIDER].endpoint;
const DEFAULT_MODEL = PROVIDERS[DEFAULT_PROVIDER].model;
const MODEL_CHECK_INTERVAL_MS = 10 * 60 * 1000;
const PENDING_COOKIE_KEYS = [
  'pendingCookieText',
  'pendingCookieCount',
  'pendingCookieNames',
  'pendingCookieGrabbedAt'
];

let ws = null;
let isAgentConnected = false;
let reconnectTimer = null;
let modelCheckTimer = null;

const PANELS = [
  ['agent', 'agent-section', 'agent-toggle', 'agent-panel'],
  ['vault', 'vault-section', 'vault-toggle', 'vault-panel'],
  ['model', 'model-section', 'model-toggle', 'model-panel'],
  ['cookie', 'cookie-section', 'cookie-toggle', 'cookie-panel']
];

function normalizeProvider(value) {
  return PROVIDERS[value] ? value : DEFAULT_PROVIDER;
}

function providerInfo(value) {
  return PROVIDERS[normalizeProvider(value)];
}

function providerStorageKeys(value) {
  const provider = normalizeProvider(value);
  if (provider === 'volcengine_agent_plan') {
    return {
      apiKey: 'agentPlanApiKey',
      model: 'agentPlanModel'
    };
  }
  return {
    apiKey: 'arkApiKey',
    model: 'arkModel'
  };
}

// ─────────────────────────────────────────
// WebSocket
// ─────────────────────────────────────────

function connectWebSocket() {
  clearTimeout(reconnectTimer);

  try {
    ws = new WebSocket(WS_URL);

    ws.onopen = () => {
      updateConnectionStatus(true);
      ws.send(JSON.stringify({
        type: 'handshake',
        client: 'obsidian-librarian-extension',
        version: '0.1.0'
      }));
      requestStatus();
      flushPendingSync();
      scheduleModelHealthCheck();
    };

    ws.onmessage = (event) => {
      try {
        handleAgentMessage(JSON.parse(event.data));
      } catch (err) {
        console.error('[Librarian] 消息解析失败:', err);
      }
    };

    ws.onclose = () => {
      updateConnectionStatus(false);
      ws = null;
      reconnectTimer = setTimeout(connectWebSocket, 3000);
    };

    ws.onerror = () => {
      updateConnectionStatus(false);
    };
  } catch (err) {
    updateConnectionStatus(false);
    reconnectTimer = setTimeout(connectWebSocket, 3000);
  }
}

function sendToAgent(data) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(data));
    return true;
  }
  return false;
}

function requestStatus() {
  sendToAgent({ type: 'status_request' });
}

function transientStorage() {
  return chrome.storage.session || chrome.storage.local;
}

async function flushPendingSync() {
  await sendConfigToAgent({ silent: true });

  const pending = await transientStorage().get(PENDING_COOKIE_KEYS);
  if (!pending.pendingCookieText) return;

  const sent = sendToAgent({
    type: 'cookie_update',
    platform: 'douyin',
    data: pending.pendingCookieText
  });
  if (sent) {
    const count = pending.pendingCookieCount || 0;
    setStatus('cookie', `已抓取 ${count} 条，正在同步`, 'warning', formatTime(pending.pendingCookieGrabbedAt));
  }
}

// ─────────────────────────────────────────
// Agent messages
// ─────────────────────────────────────────

function handleAgentMessage(msg) {
  switch (msg.type) {
    case 'agent_ready':
      updateConnectionStatus(true);
      requestStatus();
      flushPendingSync();
      break;

    case 'status_snapshot':
      applyStatusSnapshot(msg.status || {});
      break;

    case 'config_synced':
      chrome.storage.local.set({ configSyncedAt: new Date().toISOString() });
      showHint('config-hint', '配置已同步到 Agent', 'success');
      requestStatus();
      break;

    case 'cookie_synced':
      chrome.storage.local.set({ cookieSyncedAt: new Date().toISOString() });
      transientStorage().remove(PENDING_COOKIE_KEYS);
      setStatus('cookie', 'Cookie 已同步', 'online', formatTime(msg.timestamp || new Date().toISOString()));
      break;

    case 'vault_status':
      applyVaultStatus(msg.status || {});
      break;

    case 'model_status':
      applyModelStatus(msg.status || {});
      break;

    case 'task_rejected':
      showHint('notification-area', '请在 Agent 会话中发送链接入库', 'warning');
      break;

    default:
      console.log('[Librarian] 未知消息类型:', msg.type);
  }
}

function applyStatusSnapshot(status) {
  applyVaultStatus(status.vault || {});
  applyModelStatus(status.model || {});
  applyCookieStatus(status.cookie || {});
}

function applyVaultStatus(status) {
  if (status.ok || status.state === 'ready') {
    const path = status.path || '';
    document.getElementById('vault-path').value = path;
    chrome.storage.local.set({ vaultPath: path, vaultSyncedAt: new Date().toISOString() });
    setStatus('vault', compactPath(path) || '知识库已识别', 'online');
    showHint('vault-hint', `来源：${status.source || 'Agent 自动识别'}`, 'success');
    return;
  }

  setStatus('vault', '等待识别知识库', 'warning');
  if (status.message) {
    showHint('vault-hint', status.message, 'warning');
  }
}

function applyModelStatus(status) {
  const provider = normalizeProvider(status.provider || document.getElementById('provider').value);
  const info = providerInfo(provider);
  const model = status.model || document.getElementById('model').value || info.model;
  document.getElementById('provider').value = provider;
  updateProviderCopy(provider, { preserveModel: true });
  if (status.state === 'missing') {
    setStatus('model', '缺少 API Key', 'warning', formatTime(status.checkedAt));
  } else if (status.ok || status.state === 'ready') {
    setStatus('model', `${info.shortLabel} 已连接`, 'online', formatTime(status.checkedAt));
  } else if (status.state === 'configured') {
    setStatus('model', `${info.shortLabel} 待测试`, 'warning', formatTime(status.checkedAt));
  } else {
    setStatus('model', status.message || '模型连接异常', 'offline', formatTime(status.checkedAt));
  }
  if (model) {
    document.getElementById('model').value = model;
  }
  if (status.message) {
    showHint('model-hint', status.message, status.ok ? 'success' : 'warning');
  }
}

function applyCookieStatus(status) {
  if (status.ok || status.state === 'ready') {
    setStatus('cookie', 'Cookie 已同步', 'online', formatTime(status.updatedAt));
  } else {
    refreshCookieStatusFromStorage();
  }
}

// ─────────────────────────────────────────
// Config
// ─────────────────────────────────────────

async function loadConfig() {
  const result = await chrome.storage.local.get([
    'apiKey',
    'arkApiKey',
    'agentPlanApiKey',
    'provider',
    'vaultPath',
    'model',
    'arkModel',
    'agentPlanModel',
    'cookieSyncedAt',
    'modelStatus'
  ]);
  const provider = normalizeProvider(result.provider);
  const info = providerInfo(provider);
  const keys = providerStorageKeys(provider);
  document.getElementById('api-key').value = result[keys.apiKey] || result.apiKey || '';
  document.getElementById('provider').value = provider;
  document.getElementById('vault-path').value = result.vaultPath || '';
  document.getElementById('model').value = result[keys.model] || result.model || info.model;
  updateProviderCopy(provider, { preserveModel: true });

  if (result.modelStatus) {
    applyModelStatus(result.modelStatus);
  } else if (result.apiKey || result[keys.apiKey]) {
    setStatus('model', `${info.shortLabel} 待测试`, 'warning');
  }
}

async function collectConfig() {
  const vaultPath = document.getElementById('vault-path').value.trim();
  const provider = normalizeProvider(document.getElementById('provider').value);
  const info = providerInfo(provider);
  const keys = providerStorageKeys(provider);
  const apiKey = document.getElementById('api-key').value.trim();
  const model = document.getElementById('model').value.trim() || info.model;
  const config = {
    provider,
    apiKey,
    [keys.apiKey]: apiKey,
    model,
    [keys.model]: model,
    endpoint: info.endpoint,
    savedAt: new Date().toISOString()
  };
  if (vaultPath) {
    config.vaultPath = vaultPath;
  }
  return config;
}

async function saveConfig() {
  const config = await collectConfig();
  await chrome.storage.local.set(config);
  const sent = sendToAgent({ type: 'config_update', data: config });

  if (sent) {
    showHint('config-hint', '配置已发送', 'success');
    checkModelHealth();
  } else {
    showHint('config-hint', 'Agent 未连接，配置已本地保存', 'warning');
  }
}

async function sendConfigToAgent({ silent = false } = {}) {
  const stored = await chrome.storage.local.get([
    'apiKey',
    'arkApiKey',
    'agentPlanApiKey',
    'provider',
    'model',
    'arkModel',
    'agentPlanModel',
    'vaultPath'
  ]);
  const provider = normalizeProvider(stored.provider);
  const info = providerInfo(provider);
  const keys = providerStorageKeys(provider);
  const apiKey = stored[keys.apiKey] || stored.apiKey || '';
  const model = stored[keys.model] || stored.model || info.model;
  if (!apiKey && !model && !stored.vaultPath) return false;

  const data = {
    provider,
    apiKey,
    [keys.apiKey]: apiKey,
    model,
    [keys.model]: model,
    endpoint: info.endpoint
  };
  if (stored.vaultPath) data.vaultPath = stored.vaultPath;

  const sent = sendToAgent({ type: 'config_update', data });
  if (!sent && !silent) {
    showHint('config-hint', 'Agent 未连接', 'warning');
  }
  return sent;
}

async function checkModelHealth() {
  const config = await collectConfig();
  await chrome.storage.local.set(config);
  setStatus('model', '正在测试连接', 'warning');
  showHint('model-hint', '正在测试模型连接', 'warning');
  const sent = sendToAgent({ type: 'model_check', data: config });
  if (!sent) {
    setStatus('model', 'Agent 未连接', 'offline');
    showHint('model-hint', '请先启动 Agent 服务', 'warning');
  }
}

async function handleProviderChange() {
  const provider = normalizeProvider(document.getElementById('provider').value);
  const info = providerInfo(provider);
  const keys = providerStorageKeys(provider);
  const stored = await chrome.storage.local.get([keys.apiKey, keys.model]);
  document.getElementById('api-key').value = stored[keys.apiKey] || '';
  document.getElementById('model').value = stored[keys.model] || info.model;
  updateProviderCopy(provider, { preserveModel: true });
  await chrome.storage.local.set({
    provider,
    endpoint: info.endpoint,
    model: document.getElementById('model').value.trim() || info.model
  });
  setStatus('model', `${info.shortLabel} 待测试`, 'warning');
  showHint('model-hint', info.note, 'warning');
}

function updateProviderCopy(providerValue, { preserveModel = true } = {}) {
  const provider = normalizeProvider(providerValue);
  const info = providerInfo(provider);
  const keyInput = document.getElementById('api-key');
  const modelInput = document.getElementById('model');
  const note = document.getElementById('provider-note');
  if (keyInput) keyInput.placeholder = info.keyPlaceholder;
  if (modelInput) {
    modelInput.placeholder = info.modelPlaceholder;
    if (!preserveModel || !modelInput.value.trim()) {
      modelInput.value = info.model;
    }
  }
  if (note) note.textContent = info.note;
}

function scheduleModelHealthCheck() {
  clearInterval(modelCheckTimer);
  modelCheckTimer = setInterval(() => {
    if (isAgentConnected) {
      checkModelHealth();
    }
  }, MODEL_CHECK_INTERVAL_MS);
}

// ─────────────────────────────────────────
// Vault
// ─────────────────────────────────────────

async function discoverVault() {
  const hint = document.getElementById('vault-path').value.trim();
  if (hint) {
    await chrome.storage.local.set({ vaultPath: hint });
  }
  setStatus('vault', '正在识别知识库', 'warning');
  const sent = sendToAgent({ type: 'vault_discover', hint });
  if (!sent) {
    setStatus('vault', 'Agent 未连接', 'offline');
    showHint('vault-hint', '请先启动 Agent 服务', 'warning');
  }
}

function pickVault() {
  setStatus('vault', '等待选择文件夹', 'warning');
  const sent = sendToAgent({ type: 'vault_pick' });
  if (!sent) {
    setStatus('vault', 'Agent 未连接', 'offline');
    showHint('vault-hint', '请先启动 Agent 服务', 'warning');
  }
}

// ─────────────────────────────────────────
// Cookie
// ─────────────────────────────────────────

async function grabCookie() {
  const btn = document.getElementById('grab-cookie');
  btn.disabled = true;
  btn.textContent = '抓取中...';

  try {
    if (!chrome.cookies) {
      throw new Error('缺少 chrome.cookies 权限');
    }

    let cookies = await chrome.cookies.getAll({ domain: 'www.douyin.com' });
    if (cookies.length === 0) cookies = await chrome.cookies.getAll({ domain: '.douyin.com' });
    if (cookies.length === 0) cookies = await chrome.cookies.getAll({ domain: 'douyin.com' });
    if (cookies.length === 0) cookies = await chrome.cookies.getAll({ url: 'https://www.douyin.com' });
    if (cookies.length === 0) {
      const allCookies = await chrome.cookies.getAll({});
      cookies = allCookies.filter(c => (
        c.domain.includes('douyin.com') || c.domain.includes('iesdouyin.com')
      ));
    }

    if (cookies.length === 0) {
      setStatus('cookie', '未找到抖音 Cookie', 'offline');
      showHint('cookie-hint', '请先打开抖音网页版并登录', 'warning');
      return;
    }

    const cookieText = cookies.map(c =>
      `${c.domain}\t${c.domain.startsWith('.') ? 'TRUE' : 'FALSE'}\t${c.path}\t${c.secure ? 'TRUE' : 'FALSE'}\t${c.expirationDate ? Math.floor(c.expirationDate) : '0'}\t${c.name}\t${c.value}`
    ).join('\n');
    const grabbedAt = new Date().toISOString();

    await transientStorage().set({
      pendingCookieText: cookieText,
      pendingCookieCount: cookies.length,
      pendingCookieNames: cookies.map(c => c.name).join(', '),
      pendingCookieGrabbedAt: grabbedAt
    });

    const sent = sendToAgent({
      type: 'cookie_update',
      platform: 'douyin',
      data: cookieText
    });

    if (sent) {
      setStatus('cookie', `已抓取 ${cookies.length} 条，已发送`, 'online', formatTime(grabbedAt));
    } else {
      setStatus('cookie', `已抓取 ${cookies.length} 条，待同步`, 'warning', formatTime(grabbedAt));
    }
  } catch (err) {
    setStatus('cookie', `抓取失败: ${err.message}`, 'offline');
  } finally {
    btn.disabled = false;
    btn.textContent = '抓取抖音 Cookie';
  }
}

async function refreshCookieStatusFromStorage() {
  const local = await chrome.storage.local.get(['cookieSyncedAt']);
  const pending = await transientStorage().get([
    'pendingCookieText',
    'pendingCookieCount',
    'pendingCookieGrabbedAt'
  ]);

  if (pending.pendingCookieText) {
    const pendingAt = new Date(pending.pendingCookieGrabbedAt || 0).getTime();
    const syncedAt = new Date(local.cookieSyncedAt || 0).getTime();
    if (!local.cookieSyncedAt || pendingAt >= syncedAt) {
      const count = pending.pendingCookieCount || 0;
      setStatus('cookie', `已抓取 ${count} 条，待同步`, 'warning', formatTime(pending.pendingCookieGrabbedAt));
      return;
    }
  }
  if (local.cookieSyncedAt) {
    setStatus('cookie', 'Cookie 已同步', 'online', formatTime(local.cookieSyncedAt));
    return;
  }
  setStatus('cookie', 'Cookie 未同步', isAgentConnected ? 'warning' : 'offline');
}

// ─────────────────────────────────────────
// Status UI
// ─────────────────────────────────────────

function updateConnectionStatus(connected) {
  isAgentConnected = connected;
  setStatus('agent', connected ? '已连接' : '未连接', connected ? 'online' : 'offline');
  const topDot = document.getElementById('connection-status');
  if (topDot) {
    topDot.className = connected ? 'status-dot online' : 'status-dot offline';
    topDot.title = connected ? 'Agent 已连接' : 'Agent 未连接';
  }
  refreshCookieStatusFromStorage();
}

function setStatus(kind, text, type, time) {
  const dot = document.getElementById(`${kind}-status-dot`);
  const label = document.getElementById(`${kind}-status-text`);
  const timeEl = document.getElementById(`${kind}-time`);
  const section = document.getElementById(`${kind}-section`);
  const normalized = type || 'warning';

  if (dot) dot.className = `status-dot inline-dot ${normalized}`;
  if (label) {
    label.textContent = text;
    label.className = normalized;
  }
  if (timeEl) timeEl.textContent = time || '';
  if (section) {
    section.dataset.state = normalized;
  }
  updateSystemLight();
}

function updateSystemLight() {
  const dot = document.getElementById('system-status');
  if (!dot) return;
  if (!isAgentConnected) {
    dot.className = 'status-dot offline';
    dot.title = 'Agent 未连接';
    return;
  }
  const blocking = ['vault', 'model', 'cookie'].some(kind => {
    const el = document.getElementById(`${kind}-status-text`);
    return el && el.className === 'offline';
  });
  const waiting = ['vault', 'model', 'cookie'].some(kind => {
    const el = document.getElementById(`${kind}-status-text`);
    return el && el.className === 'warning';
  });
  dot.className = blocking ? 'status-dot offline' : waiting ? 'status-dot warning' : 'status-dot online';
  dot.title = blocking ? '存在异常' : waiting ? '还有项目待配置' : '系统就绪';
}

function bindPanels() {
  for (const [name, sectionId, toggleId, panelId] of PANELS) {
    const toggle = document.getElementById(toggleId);
    if (!toggle) continue;
    toggle.addEventListener('click', async () => {
      const expanded = toggle.getAttribute('aria-expanded') === 'true';
      setPanelExpanded(sectionId, toggleId, panelId, !expanded);
      await chrome.storage.local.set({ [`${name}PanelExpanded`]: !expanded });
    });
  }
}

async function loadPanelState() {
  const keys = PANELS.map(([name]) => `${name}PanelExpanded`);
  const state = await chrome.storage.local.get(keys);
  for (const [name, sectionId, toggleId, panelId] of PANELS) {
    setPanelExpanded(sectionId, toggleId, panelId, Boolean(state[`${name}PanelExpanded`]));
  }
}

function setPanelExpanded(sectionId, toggleId, panelId, expanded) {
  const section = document.getElementById(sectionId);
  const toggle = document.getElementById(toggleId);
  const panel = document.getElementById(panelId);
  if (!section || !toggle || !panel) return;
  section.classList.toggle('expanded', expanded);
  toggle.setAttribute('aria-expanded', expanded ? 'true' : 'false');
  panel.setAttribute('aria-hidden', expanded ? 'false' : 'true');
  if ('inert' in panel) {
    panel.inert = !expanded;
  } else if (expanded) {
    panel.removeAttribute('inert');
  } else {
    panel.setAttribute('inert', '');
  }
}

function showHint(id, text, type) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = text;
  el.className = 'hint ' + (type || '');
  setTimeout(() => {
    el.textContent = '';
    el.className = 'hint';
  }, 4000);
}

function formatTime(value) {
  if (!value) return '';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
}

function compactPath(path) {
  if (!path) return '';
  const parts = path.split('/').filter(Boolean);
  if (parts.length <= 2) return path;
  return `${parts.at(-2)}/${parts.at(-1)}`;
}

// ─────────────────────────────────────────
// Events
// ─────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
  bindPanels();
  document.getElementById('save-config').addEventListener('click', saveConfig);
  document.getElementById('provider').addEventListener('change', handleProviderChange);
  document.getElementById('check-model').addEventListener('click', checkModelHealth);
  document.getElementById('detect-vault').addEventListener('click', discoverVault);
  document.getElementById('pick-vault').addEventListener('click', pickVault);
  document.getElementById('grab-cookie').addEventListener('click', grabCookie);
  document.getElementById('refresh-status').addEventListener('click', requestStatus);

  document.getElementById('toggle-key').addEventListener('click', () => {
    const input = document.getElementById('api-key');
    const btn = document.getElementById('toggle-key');
    input.type = input.type === 'password' ? 'text' : 'password';
    btn.textContent = input.type === 'password' ? '显示' : '隐藏';
    btn.setAttribute('aria-pressed', input.type === 'text' ? 'true' : 'false');
  });

  Promise.all([
    loadPanelState(),
    loadConfig(),
    refreshCookieStatusFromStorage()
  ]).finally(() => {
    connectWebSocket();
  });
});
