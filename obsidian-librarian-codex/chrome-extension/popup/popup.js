// popup.js - Obsidian Librarian control console
// 扩展只做辅助控制台：状态、配置、Cookie 回传、任务入口与进度观察。
// 视频拆解仍由 Agent 本地执行层完成，扩展只提交任务。

const WS_URL = 'ws://127.0.0.1:8765';
const PROVIDERS = {
  doubao: {
    label: '豆包 / 火山方舟 API',
    shortLabel: '方舟 API',
    endpoint: 'https://ark.cn-beijing.volces.com/api/v3',
    model: 'doubao-seed-2-0-lite-260428',
    keyPlaceholder: '普通方舟 API Key',
    modelPlaceholder: 'doubao-seed-2-0-lite-260428',
    note: '普通 Ark API Key · Files API 上传后走 Responses 拆解'
  }
};
const DEFAULT_PROVIDER = 'doubao';
const DEFAULT_ENDPOINT = PROVIDERS[DEFAULT_PROVIDER].endpoint;
const DEFAULT_MODEL = PROVIDERS[DEFAULT_PROVIDER].model;
const INGEST_INTENT_LABELS = {
  knowledge_ingest: '知识入库',
  viral_breakdown: '爆款拆解',
  knowledge_and_viral: '完整入库'
};
const PENDING_COOKIE_KEYS = [
  'pendingCookieText',
  'pendingCookieCount',
  'pendingCookieNames',
  'pendingCookieGrabbedAt'
];
const DOUYIN_COOKIE_QUERIES = [
  { domain: 'douyin.com' },
  { domain: '.douyin.com' },
  { domain: 'www.douyin.com' },
  { url: 'https://www.douyin.com/' },
  { url: 'https://v.douyin.com/' },
  { domain: 'iesdouyin.com' },
  { domain: '.iesdouyin.com' }
];

let ws = null;
let isAgentConnected = false;
let reconnectTimer = null;
let statusPollTimer = null;

const PANELS = [
  ['agent', 'agent-section', 'agent-toggle', 'agent-panel'],
  ['ingest', 'ingest-section', 'ingest-toggle', 'ingest-panel'],
  ['tasks', 'tasks-section', 'tasks-toggle', 'tasks-panel'],
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
  return {
    apiKey: 'arkApiKey',
    model: 'arkModel'
  };
}

function ownValue(source, key) {
  return Object.prototype.hasOwnProperty.call(source, key) ? source[key] : undefined;
}

function readStoredApiKey(source, provider) {
  const keys = providerStorageKeys(provider);
  const providerValue = ownValue(source, keys.apiKey);
  if (providerValue !== undefined && providerValue !== null) {
    return String(providerValue).trim();
  }
  return String(source.apiKey || '').trim();
}

function readStoredModel(source, provider) {
  const keys = providerStorageKeys(provider);
  const providerValue = ownValue(source, keys.model);
  if (providerValue !== undefined && providerValue !== null) {
    return String(providerValue).trim();
  }
  return String(source.model || '').trim();
}

function buildAgentConfig(config) {
  const provider = normalizeProvider(config.provider);
  const info = providerInfo(provider);
  const keys = providerStorageKeys(provider);
  const apiKey = readStoredApiKey(config, provider);
  if (!apiKey) return null;

  const model = readStoredModel(config, provider) || info.model;
  const data = {
    provider,
    apiKey,
    [keys.apiKey]: apiKey,
    model,
    [keys.model]: model,
    endpoint: info.endpoint
  };
  if (config.vaultPath) {
    data.vaultPath = config.vaultPath;
  }
  return data;
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
      startStatusPolling();
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
      stopStatusPolling();
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

function startStatusPolling() {
  if (statusPollTimer) return;
  statusPollTimer = setInterval(() => {
    if (ws && ws.readyState === WebSocket.OPEN) {
      requestStatus();
    }
  }, 3000);
}

function stopStatusPolling() {
  if (!statusPollTimer) return;
  clearInterval(statusPollTimer);
  statusPollTimer = null;
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

function requestTaskStatus() {
  sendToAgent({ type: 'task_status_request' });
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

    case 'task_status_snapshot':
      applyTaskStatus(msg.tasks || {});
      break;

    case 'config_synced':
      chrome.storage.local.set({ configSyncedAt: new Date().toISOString() });
      showHint('config-hint', '配置已同步到 Agent', 'success');
      requestStatus();
      break;

    case 'cookie_synced':
      chrome.storage.local.set({ cookieSyncedAt: new Date().toISOString() });
      transientStorage().remove(PENDING_COOKIE_KEYS);
      if (msg.status) {
        applyCookieStatus(msg.status);
      } else {
        setStatus('cookie', 'Cookie 已同步', 'online', formatTime(msg.timestamp || new Date().toISOString()));
      }
      break;

    case 'vault_status':
      applyVaultStatus(msg.status || {});
      break;

    case 'model_status':
      chrome.storage.local.set({ modelStatus: msg.status || {} });
      applyModelStatus(msg.status || {});
      break;

    case 'task_rejected':
      showHint('tasks-hint', msg.message || '任务提交失败', 'warning');
      requestTaskStatus();
      break;

    case 'task_accepted':
      showHint('tasks-hint', msg.message || '任务已进入队列', 'success');
      requestTaskStatus();
      break;

    default:
      console.log('[Librarian] 未知消息类型:', msg.type);
  }
}

function applyStatusSnapshot(status) {
  applyVaultStatus(status.vault || {});
  applyModelStatus(status.model || {});
  applyCookieStatus(status.cookie || {});
  applyTaskStatus(status.tasks || {});
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
  const currentProvider = normalizeProvider(document.getElementById('provider').value);
  const provider = normalizeProvider(status.provider || currentProvider);
  const info = providerInfo(provider);
  const model = status.model || document.getElementById('model').value || info.model;
  if (status.state === 'missing') {
    setStatus('model', '缺少 API Key', 'warning', formatTime(status.checkedAt));
  } else if (status.ok || status.state === 'ready') {
    setStatus('model', `${info.shortLabel} 已连接`, 'online', formatTime(status.checkedAt));
  } else if (status.state === 'configured') {
    setStatus('model', `${info.shortLabel} 待测试`, 'warning', formatTime(status.checkedAt));
  } else {
    setStatus('model', status.message || '模型连接异常', 'offline', formatTime(status.checkedAt));
  }
  if (provider === currentProvider && model) {
    document.getElementById('model').value = model;
  }
  if (status.message) {
    showHint('model-hint', status.message, status.ok ? 'success' : 'warning');
  }
}

function applyCookieStatus(status) {
  if (status.ok || status.state === 'ready') {
    setStatus('cookie', 'Cookie 已同步', 'online', formatTime(status.updatedAt));
  } else if (status.state === 'incomplete') {
    const count = status.cookieCount || 0;
    setStatus('cookie', `Cookie 不完整 ${count} 条`, 'warning', formatTime(status.updatedAt));
    showHint('cookie-hint', status.message || '请打开抖音网页版登录后重新抓取', 'warning');
  } else if (status.state === 'missing') {
    setStatus('cookie', 'Cookie 未同步', isAgentConnected ? 'warning' : 'offline', formatTime(status.updatedAt));
    showHint('cookie-hint', status.message || '请打开抖音网页版登录后抓取 Cookie', 'warning');
  } else {
    setStatus('cookie', status.message || 'Cookie 状态未知', 'warning', formatTime(status.updatedAt));
  }
}

function applyTaskStatus(snapshot) {
  const items = Array.isArray(snapshot.items) ? snapshot.items : [];
  const running = Number(snapshot.running || 0);
  const failed = Number(snapshot.failed || 0);
  const done = Number(snapshot.done || 0);
  const latest = items[0] || null;

  if (running > 0) {
    setStatus('tasks', `${running} 个进行中`, 'warning', formatTaskTime(latest?.updatedAt));
  } else if (failed > 0 && latest?.ok === false) {
    setStatus('tasks', '最近任务失败', 'offline', formatTaskTime(latest.updatedAt));
  } else if (done > 0) {
    setStatus('tasks', '最近任务成功', 'online', formatTaskTime(latest?.updatedAt));
  } else {
    setStatus('tasks', '暂无任务', isAgentConnected ? 'warning' : 'offline');
  }

  renderTaskList(items);
}

function renderTaskList(items) {
  const list = document.getElementById('task-list');
  if (!list) return;
  list.innerHTML = '';
  if (!items.length) {
    const empty = document.createElement('div');
    empty.className = 'empty';
    empty.textContent = '还没有入库任务';
    list.appendChild(empty);
    return;
  }

  for (const task of items.slice(0, 8)) {
    const card = document.createElement('div');
    card.className = `task-card ${taskTone(task)}`;

    const head = document.createElement('div');
    head.className = 'task-head';

    const title = document.createElement('strong');
    title.textContent = compactTaskTitle(task.title || task.url || task.id);
    title.title = task.title || task.url || task.id;

    const badge = document.createElement('span');
    badge.className = 'task-badge';
    badge.textContent = task.stageLabel || stageLabel(task.displayStage || task.stage);

    head.append(title, badge);

    const meta = document.createElement('div');
    meta.className = 'task-meta';
    meta.textContent = taskMetaText(task);

    const bar = document.createElement('div');
    bar.className = 'task-progress';
    const fill = document.createElement('div');
    fill.style.width = `${Math.max(0, Math.min(100, Number(task.progressPercent || 0)))}%`;
    bar.appendChild(fill);

    const detail = document.createElement('div');
    detail.className = 'task-detail';
    if (task.ok === false) {
      detail.textContent = task.error || task.hint || '任务失败';
    } else if (task.ok === true) {
      detail.textContent = Array.isArray(task.assets) && task.assets.length > 1
        ? `已写入 ${task.assets.length} 篇资产`
        : (task.vaultPath ? compactPath(task.vaultPath) : '已写入知识库');
    } else {
      detail.textContent = task.url || '';
    }

    card.append(head, meta, bar, detail);
    list.appendChild(card);
  }
}

function taskTone(task) {
  if (task.ok === true) return 'done';
  if (task.ok === false) return 'failed';
  return 'running';
}

function compactTaskTitle(value) {
  const text = String(value || '').replace(/\s+/g, ' ').trim();
  if (!text) return '入库任务';
  if (text.length <= 42) return text;
  return `${text.slice(0, 40)}…`;
}

function taskMetaText(task) {
  const stage = task.stageLabel || stageLabel(task.displayStage || task.stage);
  const elapsed = formatElapsed(task.elapsedSec);
  const intent = task.ingestIntents?.length > 1
    ? '完整入库'
    : (INGEST_INTENT_LABELS[task.ingestIntent] || '');
  const source = task.source === 'extension_inline_button' ? '页面按钮' :
    task.source === 'extension_context_menu' ? '右键菜单' :
      task.source === 'extension_popup' ? '扩展' : 'Agent';
  return [stage, elapsed, intent, source].filter(Boolean).join(' · ');
}

function stageLabel(stage) {
  return {
    queued: '排队中',
    started: '已开始',
    downloading: '下载中',
    download: '下载中',
    downloaded: '下载完成',
    downloading_images: '下载图片',
    downloaded_images: '图片下载完成',
    probed_duration: '读取视频信息',
    fps_decided: '计算抽帧',
    chunking_plan: '规划切片',
    chunk_uploading: '上传切片',
    chunk_uploaded: '切片上传完成',
    uploading: '上传中',
    uploaded: '上传完成',
    waiting_active: '等待预处理',
    encoding_images: '编码图片',
    analyzing: '分析中',
    analyzing_chunk: '分析切片',
    chunk_done: '切片分析完成',
    synthesizing_chunks: '汇总切片',
    synthesizing_done: '汇总完成',
    analyzing_done: '分析完成',
    analyzed: '分析完成',
    writing_vault: '写入知识库',
    done: '成功',
    failed: '失败',
    config_error: '配置错误',
    task_invalid: '任务无效'
  }[stage] || stage || '处理中';
}

// ─────────────────────────────────────────
// Config
// ─────────────────────────────────────────

async function loadConfig() {
  const result = await chrome.storage.local.get([
    'apiKey',
    'arkApiKey',
    'provider',
    'vaultPath',
    'model',
    'arkModel',
    'cookieSyncedAt',
    'modelStatus'
  ]);
  const provider = normalizeProvider(result.provider);
  const info = providerInfo(provider);
  document.getElementById('api-key').value = readStoredApiKey(result, provider);
  document.getElementById('provider').value = provider;
  document.getElementById('vault-path').value = result.vaultPath || '';
  document.getElementById('model').value = readStoredModel(result, provider) || info.model;
  updateProviderCopy(provider, { preserveModel: true });

  if (result.modelStatus) {
    applyModelStatus(result.modelStatus);
  } else if (readStoredApiKey(result, provider)) {
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
  const agentConfig = buildAgentConfig(config);
  if (!agentConfig) {
    setStatus('model', '缺少 API Key', 'warning');
    showHint('config-hint', '配置已本地保存，未发送空 API Key', 'warning');
    showHint('model-hint', '填写 API Key 后再测试连接', 'warning');
    return;
  }

  const configSent = sendToAgent({ type: 'config_update', data: agentConfig });
  const checkSent = sendToAgent({ type: 'model_check', data: agentConfig });

  if (configSent && checkSent) {
    setStatus('model', '正在测试连接', 'warning');
    showHint('config-hint', '配置已发送，正在测试连接', 'success');
    showHint('model-hint', '正在测试模型连接', 'warning');
  } else {
    await chrome.runtime.sendMessage({ action: 'syncModelConfigAndCheck' }).catch(() => null);
    showHint('config-hint', 'Agent 未连接，配置已本地保存', 'warning');
  }
}

async function sendConfigToAgent({ silent = false } = {}) {
  const stored = await chrome.storage.local.get([
    'apiKey',
    'arkApiKey',
    'provider',
    'model',
    'arkModel',
    'vaultPath'
  ]);
  const provider = normalizeProvider(stored.provider);
  const info = providerInfo(provider);
  const keys = providerStorageKeys(provider);
  const apiKey = readStoredApiKey(stored, provider);
  if (!apiKey) return false;
  const model = readStoredModel(stored, provider) || info.model;

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
  const agentConfig = buildAgentConfig(config);
  if (!agentConfig) {
    setStatus('model', '缺少 API Key', 'warning');
    showHint('model-hint', '填写 API Key 后再测试连接', 'warning');
    return false;
  }

  setStatus('model', '正在测试连接', 'warning');
  showHint('model-hint', '正在测试模型连接', 'warning');
  const sent = sendToAgent({ type: 'model_check', data: agentConfig });
  if (!sent) {
    const response = await chrome.runtime.sendMessage({ action: 'modelHealthCheck' }).catch(() => null);
    if (!response?.accepted) {
      setStatus('model', 'Agent 未连接', 'offline');
      showHint('model-hint', '请先启动 Agent 服务', 'warning');
    }
  }
  return sent;
}

async function handleProviderChange() {
  const provider = normalizeProvider(document.getElementById('provider').value);
  const info = providerInfo(provider);
  const keys = providerStorageKeys(provider);
  const stored = await chrome.storage.local.get([keys.apiKey, keys.model]);
  const apiKey = stored[keys.apiKey] || '';
  const model = stored[keys.model] || info.model;
  document.getElementById('api-key').value = apiKey;
  document.getElementById('model').value = model;
  updateProviderCopy(provider, { preserveModel: true });
  await chrome.storage.local.set({
    provider,
    endpoint: info.endpoint,
    apiKey,
    [keys.apiKey]: apiKey,
    model,
    [keys.model]: model
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

// ─────────────────────────────────────────
// Ingest task entry
// ─────────────────────────────────────────

async function submitDouyinIngestFromPopup(ingestIntent) {
  const shareInput = document.getElementById('douyin-share-text');
  const knowledgeBtn = document.getElementById('ingest-knowledge');
  const viralBtn = document.getElementById('ingest-viral');
  const shareText = shareInput?.value.trim() || '';
  const label = INGEST_INTENT_LABELS[ingestIntent] || '入库';

  if (!isAgentConnected) {
    setStatus('ingest', 'Agent 未连接', 'offline');
    showHint('ingest-hint', '请先启动 Agent 服务', 'warning');
    return;
  }

  knowledgeBtn.disabled = true;
  viralBtn.disabled = true;
  setStatus('ingest', `正在提交${label}`, 'warning');
  showHint('ingest-hint', shareText ? '正在从分享文案提取链接并提交' : '正在识别当前抖音页面', 'warning');

  try {
    const response = await chrome.runtime.sendMessage({
      action: 'submitDouyinIngestFromPopup',
      ingestIntent,
      shareText
    });

    if (response?.ok) {
      setStatus('ingest', `${label}已提交`, 'online', formatTime(new Date().toISOString()));
      showHint('ingest-hint', response.message || '任务已进入队列', 'success');
      requestTaskStatus();
    } else {
      setStatus('ingest', '提交失败', 'offline');
      showHint('ingest-hint', response?.message || '没有识别到可入库的抖音链接', 'warning');
    }
  } catch (err) {
    setStatus('ingest', '提交失败', 'offline');
    showHint('ingest-hint', err.message || '扩展后台未响应', 'warning');
  } finally {
    knowledgeBtn.disabled = false;
    viralBtn.disabled = false;
  }
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

function isDouyinCookie(cookie) {
  const domain = String(cookie.domain || '').toLowerCase().replace(/^\./, '');
  return domain === 'douyin.com' || domain.endsWith('.douyin.com') ||
    domain === 'iesdouyin.com' || domain.endsWith('.iesdouyin.com');
}

function cookieIdentity(cookie) {
  return `${cookie.domain}\t${cookie.path}\t${cookie.name}`;
}

async function collectDouyinCookies() {
  const byKey = new Map();
  const addCookies = (items) => {
    for (const cookie of items || []) {
      if (isDouyinCookie(cookie)) {
        byKey.set(cookieIdentity(cookie), cookie);
      }
    }
  };

  for (const query of DOUYIN_COOKIE_QUERIES) {
    addCookies(await chrome.cookies.getAll(query));
  }

  if (byKey.size < 8) {
    addCookies(await chrome.cookies.getAll({}));
  }

  return Array.from(byKey.values()).sort((a, b) => {
    const domain = a.domain.localeCompare(b.domain);
    if (domain !== 0) return domain;
    return a.name.localeCompare(b.name);
  });
}

async function grabCookie() {
  const btn = document.getElementById('grab-cookie');
  btn.disabled = true;
  btn.textContent = '抓取中...';

  try {
    if (!chrome.cookies) {
      throw new Error('缺少 chrome.cookies 权限');
    }

    const cookies = await collectDouyinCookies();

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
      setStatus('cookie', `已抓取 ${cookies.length} 条，等待确认`, 'warning', formatTime(grabbedAt));
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
  setStatus('ingest', connected ? '当前视频或分享链接' : 'Agent 未连接', connected ? 'warning' : 'offline');
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

function formatTaskTime(value) {
  if (!value) return '';
  if (typeof value === 'number') {
    return new Date(value * 1000).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
  }
  return formatTime(value);
}

function formatElapsed(seconds) {
  if (seconds === null || seconds === undefined || Number.isNaN(Number(seconds))) {
    return '';
  }
  const total = Math.max(0, Math.round(Number(seconds)));
  if (total < 60) return `${total} 秒`;
  const min = Math.floor(total / 60);
  const sec = total % 60;
  if (min < 60) return sec ? `${min} 分 ${sec} 秒` : `${min} 分`;
  const hour = Math.floor(min / 60);
  const rest = min % 60;
  return rest ? `${hour} 小时 ${rest} 分` : `${hour} 小时`;
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
  document.getElementById('ingest-knowledge').addEventListener('click', () => submitDouyinIngestFromPopup('knowledge_ingest'));
  document.getElementById('ingest-viral').addEventListener('click', () => submitDouyinIngestFromPopup('viral_breakdown'));
  document.getElementById('refresh-status').addEventListener('click', requestStatus);
  document.getElementById('refresh-tasks').addEventListener('click', requestTaskStatus);

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

window.addEventListener('unload', stopStatusPolling);
