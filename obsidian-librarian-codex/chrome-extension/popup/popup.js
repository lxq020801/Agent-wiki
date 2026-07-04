// popup.js - Obsidian Librarian control console
// 首页只负责提交拆解任务；设置页负责 API、Cookie、Vault 与拆解偏好。

const WS_URL = 'ws://127.0.0.1:8765';
const DEBUG_LOGS = false;
const PROVIDERS = {
  doubao: {
    label: '字节跳动',
    shortLabel: '字节跳动方舟 API',
    endpoint: 'https://ark.cn-beijing.volces.com/api/v3',
    strategyModel: 'doubao-seed-2-0-mini-260428',
    keyPlaceholder: 'Ark API Key'
  }
};
const MODEL_PRESETS = {
  lite: 'doubao-seed-2-0-lite-260428',
  mini: 'doubao-seed-2-0-mini-260428'
};
const DEFAULT_PROVIDER = 'doubao';
const DEFAULT_MODEL_PRESET = 'lite';
const DEFAULT_TASK_CONCURRENCY = 2;
const DEFAULT_CHUNK_CONCURRENCY = 2;
const TRUSTED_ARK_HOSTS = new Set(['ark.cn-beijing.volces.com']);
const SETTINGS_DETAIL_TITLES = {
  'agent-settings': 'Agent 连接',
  'api-settings': 'API 设置',
  'video-settings': '模型与并发',
  'vault-settings': '知识库',
  'cookie-settings': '抖音 Cookie',
  'task-settings': '任务状态'
};
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
let previewPollTimer = null;
let previewInputTimer = null;
let previewRequestSeq = 0;
let lastPreviewKey = '';
let lastSettingsTrigger = null;

function debugLog(...args) {
  if (DEBUG_LOGS) console.log(...args);
}

function hasExtensionApis() {
  return typeof chrome !== 'undefined' && !!chrome.runtime?.sendMessage && !!chrome.storage?.local;
}

function normalizeProvider(value) {
  return PROVIDERS[value] ? value : DEFAULT_PROVIDER;
}

function providerInfo(value) {
  return PROVIDERS[normalizeProvider(value)];
}

function normalizeModelPreset(value) {
  if (value === 'custom') return 'custom';
  return MODEL_PRESETS[value] ? value : DEFAULT_MODEL_PRESET;
}

function presetFromModel(model) {
  const value = String(model || '').trim();
  if (value === MODEL_PRESETS.mini) return 'mini';
  if (value === MODEL_PRESETS.lite) return 'lite';
  return value ? 'custom' : DEFAULT_MODEL_PRESET;
}

function normalizeModelId(value) {
  const model = String(value || '').trim();
  return model || MODEL_PRESETS[DEFAULT_MODEL_PRESET];
}

function normalizeBoundedInt(value, fallback) {
  const parsed = Number.parseInt(value, 10);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.max(1, Math.min(4, parsed));
}

function normalizeTaskConcurrency(value) {
  return normalizeBoundedInt(value, DEFAULT_TASK_CONCURRENCY);
}

function normalizeChunkConcurrency(value) {
  return normalizeBoundedInt(value, DEFAULT_CHUNK_CONCURRENCY);
}

function normalizeEndpoint(value, provider) {
  const endpoint = String(value || providerInfo(provider).endpoint).trim().replace(/\/+$/, '');
  try {
    const url = new URL(endpoint);
    if (url.protocol !== 'https:' || !url.hostname || url.username || url.password) {
      return { ok: false, endpoint, message: 'Endpoint 必须是有效 HTTPS 地址，且不能包含账号密码' };
    }
    if (!TRUSTED_ARK_HOSTS.has(url.hostname.toLowerCase())) {
      return { ok: false, endpoint, message: 'Endpoint 必须使用可信 Ark 官方域名' };
    }
    if (endpoint.endsWith('/api/plan/v3')) {
      return { ok: false, endpoint, message: 'Agent Plan endpoint 不能作为普通 Ark API 使用' };
    }
    return { ok: true, endpoint };
  } catch (_err) {
    return { ok: false, endpoint, message: 'Endpoint URL 格式不正确' };
  }
}

function providerStorageKeys() {
  return {
    apiKey: 'arkApiKey',
    model: 'arkModel',
    strategyModel: 'arkStrategyModel'
  };
}

function ownValue(source, key) {
  return Object.prototype.hasOwnProperty.call(source, key) ? source[key] : undefined;
}

function readStoredApiKey(source) {
  const keys = providerStorageKeys();
  const providerValue = ownValue(source, keys.apiKey);
  if (providerValue !== undefined && providerValue !== null) {
    return String(providerValue).trim();
  }
  return String(source.apiKey || '').trim();
}

function readStoredModelPreset(source) {
  const explicit = source.videoAnalysisModel || source.arkModel || source.model || '';
  if (String(explicit || '').trim()) {
    return presetFromModel(explicit);
  }
  if (source.videoAnalysisModelPreset) {
    return normalizeModelPreset(source.videoAnalysisModelPreset);
  }
  return DEFAULT_MODEL_PRESET;
}

function readStoredModelId(source) {
  const explicit = source.videoAnalysisModel || source.arkModel || source.model || '';
  if (String(explicit || '').trim()) {
    return normalizeModelId(explicit);
  }
  const preset = readStoredModelPreset(source);
  return MODEL_PRESETS[preset] || MODEL_PRESETS[DEFAULT_MODEL_PRESET];
}

function readStoredStrategyModel(source) {
  const keys = providerStorageKeys();
  const configured = String(source.videoStrategyModel || source[keys.strategyModel] || source.strategyModel || '').trim();
  return configured === providerInfo(DEFAULT_PROVIDER).strategyModel
    ? configured
    : providerInfo(DEFAULT_PROVIDER).strategyModel;
}

function setControlValue(id, value, { notify = false } = {}) {
  const input = document.getElementById(id);
  if (!input) return;
  const normalizedValue = String(value ?? '');
  input.value = normalizedValue;
  document.querySelectorAll(`[data-control="${id}"]`).forEach(button => {
    const selected = String(button.dataset.value) === normalizedValue;
    button.classList.toggle('selected', selected);
    if (button.getAttribute('role') === 'radio') {
      button.setAttribute('aria-checked', selected ? 'true' : 'false');
    }
  });
  if (notify) input.dispatchEvent(new Event('change', { bubbles: true }));
}

function selectedVideoConfig() {
  const analyzerModel = normalizeModelId(document.getElementById('analysis-model-id').value);
  const preset = presetFromModel(analyzerModel);
  return {
    modelPreset: preset,
    analyzerModel,
    strategyModel: providerInfo(DEFAULT_PROVIDER).strategyModel,
    chunkConcurrency: normalizeChunkConcurrency(document.getElementById('chunk-concurrency').value)
  };
}

async function buildAgentConfig({ requireApiKey = false } = {}) {
  const stored = await chrome.storage.local.get([
    'llmProvider',
    'provider',
    'apiKey',
    'arkApiKey',
    'arkEndpoint',
    'endpoint',
    'videoAnalysisModelPreset',
    'videoAnalysisModel',
    'model',
    'arkModel',
    'videoStrategyModel',
    'strategyModel',
    'arkStrategyModel',
    'serverTaskConcurrency',
    'taskConcurrency',
    'videoChunkConcurrency',
    'vaultPath'
  ]);
  const provider = normalizeProvider(stored.llmProvider || stored.provider);
  const keys = providerStorageKeys(provider);
  const apiKey = readStoredApiKey(stored);
  if (requireApiKey && !apiKey) return null;
  const endpointResult = normalizeEndpoint(stored.arkEndpoint || stored.endpoint, provider);
  if (!endpointResult.ok) {
    throw new Error(endpointResult.message);
  }
  const analyzerModel = readStoredModelId(stored);
  const preset = presetFromModel(analyzerModel);
  const strategyModel = readStoredStrategyModel(stored);
  const taskConcurrency = normalizeTaskConcurrency(stored.serverTaskConcurrency || stored.taskConcurrency);
  const chunkConcurrency = normalizeChunkConcurrency(stored.videoChunkConcurrency);
  const data = {
    llm: {
      provider,
      apiKey,
      endpoint: endpointResult.endpoint
    },
    videoAnalysis: {
      modelPreset: preset,
      analyzerModel,
      strategyModel,
      chunkConcurrency
    },
    server: {
      taskConcurrency
    },
    provider,
    apiKey,
    [keys.apiKey]: apiKey,
    model: analyzerModel,
    [keys.model]: analyzerModel,
    strategyModel,
    [keys.strategyModel]: strategyModel,
    taskConcurrency,
    serverTaskConcurrency: taskConcurrency,
    videoChunkConcurrency: chunkConcurrency,
    endpoint: endpointResult.endpoint,
    arkEndpoint: endpointResult.endpoint
  };
  if (stored.vaultPath) data.vaultPath = stored.vaultPath;
  return data;
}

// WebSocket
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
    ws.onerror = () => updateConnectionStatus(false);
  } catch (_err) {
    updateConnectionStatus(false);
    reconnectTimer = setTimeout(connectWebSocket, 3000);
  }
}

function startStatusPolling() {
  if (statusPollTimer) return;
  statusPollTimer = setInterval(() => {
    if (ws && ws.readyState === WebSocket.OPEN) requestStatus();
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
  await sendConfigToAgent({ silent: true, requireApiKey: false });
  const pending = await transientStorage().get(PENDING_COOKIE_KEYS);
  if (!pending.pendingCookieText) return;
  const sent = sendToAgent({
    type: 'cookie_update',
    platform: 'douyin',
    data: pending.pendingCookieText
  });
  if (sent) {
    setStatus('cookie', '待确认', 'warning', formatTime(pending.pendingCookieGrabbedAt));
  }
}

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
      showHint('config-hint', '配置已同步到 Agent', 'success', { persist: true });
      showHint('video-config-hint', '配置已同步到 Agent', 'success');
      requestStatus();
      break;
    case 'config_rejected':
      showHint('config-hint', msg.message || '配置被 Agent 拒绝', 'error', { persist: true });
      showHint('video-config-hint', msg.message || '配置被 Agent 拒绝', 'error', { persist: true });
      break;
    case 'cookie_synced':
      chrome.storage.local.set({ cookieSyncedAt: new Date().toISOString() });
      transientStorage().remove(PENDING_COOKIE_KEYS);
      if (msg.status) {
        applyCookieStatus(msg.status);
      } else {
        setStatus('cookie', '已同步', 'online', formatTime(msg.timestamp || new Date().toISOString()));
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
      showHint('ingest-hint', msg.message || '任务提交失败', 'warning', { persist: true });
      requestTaskStatus();
      break;
    case 'task_accepted':
      showHint('ingest-hint', msg.message || '任务已进入队列', 'success');
      requestTaskStatus();
      break;
    case 'error':
      showHint('config-hint', msg.message || msg.error || 'Agent 返回错误', 'error', { persist: true });
      break;
    default:
      debugLog('[Librarian] 未知消息类型:', msg.type);
  }
}

function applyStatusSnapshot(status) {
  applyVaultStatus(status.vault || {});
  applyModelStatus(status.llm || status.model || {});
  applyVideoStatus(status.videoAnalysis || {});
  applyCookieStatus(status.cookie || {});
  applyTaskStatus(status.tasks || {});
}

function applyVaultStatus(status) {
  if (status.ok || status.state === 'ready') {
    const path = status.path || '';
    document.getElementById('vault-path').value = path;
    chrome.storage.local.set({ vaultPath: path, vaultSyncedAt: new Date().toISOString() });
    setStatus('vault', '已连接', 'online');
    showHint('vault-hint', `来源：${status.source || 'Agent 自动识别'}`, 'success', { persist: true });
    return;
  }
  setStatus('vault', '待识别', 'warning');
  if (status.message) showHint('vault-hint', status.message, 'warning', { persist: true });
}

function applyModelStatus(status) {
  const provider = normalizeProvider(status.provider || document.getElementById('provider').value);
  const info = providerInfo(provider);
  if (status.endpoint) {
    document.getElementById('endpoint-url').value = status.endpoint;
    chrome.storage.local.set({ arkEndpoint: status.endpoint, endpoint: status.endpoint });
  }
  if (status.state === 'missing') {
    setStatus('api', '缺少 Key', 'warning', formatTime(status.checkedAt));
  } else if (status.ok || status.state === 'ready') {
    setStatus('api', '已连接', 'online', formatTime(status.checkedAt));
  } else if (status.state === 'configured') {
    setStatus('api', '待测试', 'warning', formatTime(status.checkedAt));
  } else {
    setStatus('api', status.message || '连接异常', 'offline', formatTime(status.checkedAt));
  }
  if (status.message) {
    const type = status.ok ? 'success' : status.state === 'missing' ? 'warning' : 'error';
    showHint('model-hint', `${info.shortLabel}: ${status.message}；该检查不等于视频端到端验证。`, type, { persist: true });
  }
}

function applyVideoStatus(status) {
  if (status.analyzerModel) {
    setControlValue('analysis-model-id', status.analyzerModel);
    setControlValue('analysis-model-preset', presetFromModel(status.analyzerModel));
  } else if (status.modelPreset) {
    const preset = normalizeModelPreset(status.modelPreset);
    setControlValue('analysis-model-preset', preset);
    setControlValue('analysis-model-id', MODEL_PRESETS[preset] || MODEL_PRESETS[DEFAULT_MODEL_PRESET]);
  }
  if (status.taskConcurrency) {
    setControlValue('task-concurrency', normalizeTaskConcurrency(status.taskConcurrency));
  }
  if (status.chunkConcurrency) {
    setControlValue('chunk-concurrency', normalizeChunkConcurrency(status.chunkConcurrency));
  }
  updateVideoSettingsSummary();
}

function applyCookieStatus(status) {
  if (status.ok || status.state === 'ready') {
    setStatus('cookie', '已同步', 'online', formatTime(status.updatedAt));
    document.getElementById('cookie-settings-copy').textContent = `上次同步：${formatTime(status.updatedAt) || '刚刚'}`;
  } else if (status.state === 'incomplete') {
    setStatus('cookie', '不完整', 'warning', formatTime(status.updatedAt));
    showHint('cookie-hint', status.message || '请登录抖音后重新抓取', 'warning', { persist: true });
  } else if (status.state === 'missing') {
    setStatus('cookie', '未同步', isAgentConnected ? 'warning' : 'offline', formatTime(status.updatedAt));
    showHint('cookie-hint', status.message || '请打开抖音网页版登录后抓取 Cookie', 'warning', { persist: true });
  } else {
    setStatus('cookie', status.message || '状态未知', 'warning', formatTime(status.updatedAt));
  }
}

function applyTaskStatus(snapshot) {
  const items = Array.isArray(snapshot.items) ? snapshot.items : [];
  const running = Number(snapshot.running || 0);
  const failed = Number(snapshot.failed || 0);
  const done = Number(snapshot.done || 0);
  const latest = items[0] || null;
  if (snapshot.taskConcurrency) {
    setControlValue('task-concurrency', normalizeTaskConcurrency(snapshot.taskConcurrency));
    chrome.storage.local.set({
      taskConcurrency: normalizeTaskConcurrency(snapshot.taskConcurrency),
      serverTaskConcurrency: normalizeTaskConcurrency(snapshot.taskConcurrency)
    });
    updateVideoSettingsSummary();
  }
  if (running > 0) {
    setStatus('tasks', `${running} 个进行中`, 'warning', formatTaskTime(latest?.updatedAt));
  } else if (failed > 0 && latest?.ok === false) {
    setStatus('tasks', '最近失败', 'offline', formatTaskTime(latest.updatedAt));
  } else if (done > 0 && latest?.ok === true) {
    setStatus('tasks', '最近成功', 'online', formatTaskTime(latest?.updatedAt));
  } else {
    setStatus('tasks', '暂无任务', isAgentConnected ? 'online' : 'offline');
  }
  renderTaskList('settings-task-list', items);
}

function renderTaskList(targetId, items) {
  const list = document.getElementById(targetId);
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
    } else if (task.ok === true && (task.stage === 'done' || task.displayStage === 'done')) {
      detail.textContent = Array.isArray(task.assets) && task.assets.length > 1
        ? `已写入 ${task.assets.length} 篇资产`
        : (task.vaultPath ? compactPath(task.vaultPath) : '已写入知识库');
    } else {
      detail.textContent = task.url || '任务已进入队列';
    }
    card.append(head, meta, bar, detail);
    list.appendChild(card);
  }
}

function taskTone(task) {
  if (task.ok === true && (task.stage === 'done' || task.displayStage === 'done')) return 'done';
  if (task.ok === false) return 'failed';
  return 'running';
}

function compactTaskTitle(value) {
  const text = String(value || '').replace(/\s+/g, ' ').trim();
  if (!text) return '入库任务';
  return text.length <= 42 ? text : `${text.slice(0, 40)}...`;
}

function taskMetaText(task) {
  const stage = task.stageLabel || stageLabel(task.displayStage || task.stage);
  const elapsed = formatElapsed(task.elapsedSec);
  const intent = task.ingestIntents?.length > 1
    ? '完整入库'
    : (INGEST_INTENT_LABELS[task.ingestIntent] || '');
  const source = task.source === 'extension_popup' ? '扩展' : 'Agent';
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
    overview_uploading: '上传全片概览',
    overview_uploaded: '全片概览上传完成',
    overview_chunking: '规划分片概览',
    overview_chunk_uploading: '上传概览切片',
    overview_chunk_uploaded: '概览切片上传完成',
    analyzing_overview: '分析全片概览',
    analyzing_overview_chunk: '分析概览切片',
    overview_chunk_done: '概览切片完成',
    synthesizing_overview_strategy: '合成精拆策略',
    repairing_overview_strategy: '修复精拆策略',
    overview_strategy_repaired: '精拆策略已修复',
    overview_strategy_decided: '决定精拆策略',
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

async function loadConfig() {
  const result = await chrome.storage.local.get([
    'apiKey',
    'arkApiKey',
    'llmProvider',
    'provider',
    'arkEndpoint',
    'endpoint',
    'vaultPath',
    'videoAnalysisModelPreset',
    'videoAnalysisModel',
    'model',
    'arkModel',
    'videoChunkConcurrency',
    'serverTaskConcurrency',
    'taskConcurrency',
    'cookieSyncedAt',
    'modelStatus',
    'videoAnalysisStatus'
  ]);
  const provider = normalizeProvider(result.llmProvider || result.provider);
  const info = providerInfo(provider);
  document.getElementById('api-key').value = readStoredApiKey(result);
  setControlValue('provider', provider);
  document.getElementById('endpoint-url').value = result.arkEndpoint || result.endpoint || info.endpoint;
  document.getElementById('vault-path').value = result.vaultPath || '';
  setControlValue('analysis-model-preset', readStoredModelPreset(result));
  setControlValue('analysis-model-id', readStoredModelId(result));
  setControlValue('task-concurrency', normalizeTaskConcurrency(result.serverTaskConcurrency || result.taskConcurrency));
  setControlValue('chunk-concurrency', normalizeChunkConcurrency(result.videoChunkConcurrency));
  updateVideoSettingsSummary();
  if (result.modelStatus) applyModelStatus(result.modelStatus);
  if (result.videoAnalysisStatus) applyVideoStatus(result.videoAnalysisStatus);
  if (readStoredApiKey(result)) setStatus('api', '待测试', 'warning');
}

async function collectConfig() {
  const provider = normalizeProvider(document.getElementById('provider').value);
  const apiKey = document.getElementById('api-key').value.trim();
  const endpointResult = normalizeEndpoint(document.getElementById('endpoint-url').value, provider);
  if (!endpointResult.ok) throw new Error(endpointResult.message);
  const vaultPath = document.getElementById('vault-path').value.trim();
  const video = selectedVideoConfig();
  const taskConcurrency = normalizeTaskConcurrency(document.getElementById('task-concurrency').value);
  const keys = providerStorageKeys(provider);
  const config = {
    llmProvider: provider,
    provider,
    apiKey,
    [keys.apiKey]: apiKey,
    arkEndpoint: endpointResult.endpoint,
    endpoint: endpointResult.endpoint,
    videoAnalysisModelPreset: video.modelPreset,
    videoAnalysisModel: video.analyzerModel,
    model: video.analyzerModel,
    [keys.model]: video.analyzerModel,
    videoStrategyModel: video.strategyModel,
    strategyModel: video.strategyModel,
    [keys.strategyModel]: video.strategyModel,
    videoChunkConcurrency: video.chunkConcurrency,
    serverTaskConcurrency: taskConcurrency,
    taskConcurrency,
    savedAt: new Date().toISOString()
  };
  if (vaultPath) config.vaultPath = vaultPath;
  return config;
}

async function persistConfigLocally() {
  const config = await collectConfig();
  await chrome.storage.local.set(config);
  return config;
}

async function saveApiConfig() {
  try {
    const config = await persistConfigLocally();
    const agentConfig = await buildAgentConfig({ requireApiKey: false });
    const sent = sendToAgent({ type: 'config_update', data: agentConfig });
    if (!config.apiKey) {
      setStatus('api', '缺少 Key', 'warning');
      showHint('config-hint', sent ? 'API 设置已保存，填写 Key 后再测试连接' : 'API 设置已本地保存', 'warning', { persist: true });
      return;
    }
    showHint('config-hint', sent ? 'API 设置已发送到 Agent' : 'Agent 未连接，API 设置已本地保存', sent ? 'success' : 'warning', { persist: !sent });
  } catch (err) {
    setStatus('api', '配置错误', 'offline');
    showHint('config-hint', err.message || 'API 设置无效', 'error', { persist: true });
  }
}

async function saveVideoConfig() {
  try {
    await persistConfigLocally();
    const agentConfig = await buildAgentConfig({ requireApiKey: false });
    const sent = sendToAgent({ type: 'config_update', data: agentConfig });
    showHint('video-config-hint', sent ? '拆解设置已同步' : 'Agent 未连接，拆解设置已本地保存', sent ? 'success' : 'warning', { persist: !sent });
  } catch (err) {
    showHint('video-config-hint', err.message || '拆解设置保存失败', 'error', { persist: true });
  }
}

async function sendConfigToAgent({ silent = false, requireApiKey = false } = {}) {
  try {
    const data = await buildAgentConfig({ requireApiKey });
    if (!data) return false;
    const sent = sendToAgent({ type: 'config_update', data });
    if (!sent && !silent) showHint('config-hint', 'Agent 未连接', 'warning', { persist: true });
    return sent;
  } catch (err) {
    if (!silent) showHint('config-hint', err.message, 'error', { persist: true });
    return false;
  }
}

async function checkModelHealth() {
  try {
    await persistConfigLocally();
    const agentConfig = await buildAgentConfig({ requireApiKey: true });
    if (!agentConfig) {
      setStatus('api', '缺少 Key', 'warning');
      showHint('model-hint', '填写 API Key 后再测试连接', 'warning', { persist: true });
      return false;
    }
    setStatus('api', '正在测试', 'warning');
    showHint('model-hint', '正在测试 API 连接；这不等于视频端到端拆解验证', 'warning', { persist: true });
    const sent = sendToAgent({ type: 'model_check', data: agentConfig });
    if (!sent) {
      const response = await chrome.runtime.sendMessage({ action: 'modelHealthCheck' }).catch(() => null);
      if (!response?.accepted) {
        setStatus('api', 'Agent 未连接', 'offline');
        showHint('model-hint', '请先启动 Agent 服务', 'warning', { persist: true });
      }
    }
    return sent;
  } catch (err) {
    setStatus('api', '配置错误', 'offline');
    showHint('model-hint', err.message || 'API 设置无效', 'error', { persist: true });
    return false;
  }
}

function currentShareText() {
  return document.getElementById('douyin-share-text')?.value.trim() || '';
}

function previewTypeLabel(type) {
  return type === 'note' ? '图文' : '视频';
}

function setPreviewImage(url, type) {
  const image = document.getElementById('douyin-preview-image');
  const fallback = document.getElementById('douyin-preview-fallback');
  if (!image || !fallback) return;
  const label = previewTypeLabel(type);
  fallback.textContent = label;
  if (!url) {
    image.hidden = true;
    image.removeAttribute('src');
    fallback.hidden = false;
    return;
  }
  image.hidden = false;
  fallback.hidden = true;
  image.src = url;
}

function renderDouyinPreview(data, options = {}) {
  const preview = document.getElementById('douyin-preview');
  const source = document.getElementById('douyin-preview-source');
  const title = document.getElementById('douyin-preview-title');
  const meta = document.getElementById('douyin-preview-meta');
  if (!preview || !source || !title || !meta) return;

  preview.className = `ingest-preview ${options.loading ? 'loading' : ''}`;
  if (options.loading) {
    source.textContent = currentShareText() ? '分享链接' : '当前画面';
    title.textContent = currentShareText() ? '正在识别分享链接' : '正在识别当前抖音内容';
    title.title = title.textContent;
    meta.textContent = '识别中...';
    setPreviewImage('', 'video');
    return;
  }

  if (!data?.ok) {
    preview.className = 'ingest-preview empty';
    source.textContent = data?.source === 'share' ? '分享链接' : '当前画面';
    title.textContent = data?.message || '未识别到抖音内容';
    title.title = title.textContent;
    meta.textContent = currentShareText()
      ? '请粘贴完整分享文案或抖音链接'
      : '打开抖音页面后自动检测，或在下方粘贴分享链接。';
    setPreviewImage('', data?.type || 'video');
    return;
  }

  const fullTitle = data.title || '已识别抖音内容';
  const typeText = previewTypeLabel(data.type);
  preview.className = 'ingest-preview';
  source.textContent = data.sourceLabel || (data.source === 'share' ? `分享${typeText}` : `当前${typeText}`);
  title.textContent = fullTitle;
  title.title = fullTitle;
  meta.textContent = [
    data.source === 'current' && data.coverUrl ? '当前页面画面' : `抖音${typeText}`,
    data.source === 'share' ? '提交后由 Agent 获取内容' : '',
    data.awemeId ? `ID ${data.awemeId}` : '',
    data.method ? `来源 ${data.method}` : ''
  ].filter(Boolean).join(' · ');
  setPreviewImage(data.coverUrl || '', data.type);
}

async function refreshDouyinPreview({ force = false, silent = false } = {}) {
  const shareText = currentShareText();
  const previewKey = shareText ? `share:${shareText}` : 'current';
  if (!force && shareText && previewKey === lastPreviewKey) return;
  lastPreviewKey = previewKey;
  const seq = ++previewRequestSeq;
  if (!silent) renderDouyinPreview(null, { loading: true });
  try {
    const response = await chrome.runtime.sendMessage({
      action: 'previewDouyinIngest',
      shareText
    });
    if (seq !== previewRequestSeq) return;
    renderDouyinPreview(response || {
      ok: false,
      source: shareText ? 'share' : 'current',
      message: '预览识别失败'
    });
  } catch (err) {
    if (seq !== previewRequestSeq) return;
    renderDouyinPreview({
      ok: false,
      source: shareText ? 'share' : 'current',
      message: err.message || '扩展后台未响应'
    });
  }
}

function scheduleDouyinPreviewRefresh() {
  clearTimeout(previewInputTimer);
  previewInputTimer = setTimeout(() => {
    refreshDouyinPreview({ force: true });
  }, 300);
}

function startDouyinPreviewLoop() {
  refreshDouyinPreview({ force: true });
  clearInterval(previewPollTimer);
  previewPollTimer = setInterval(() => {
    const homeVisible = document.getElementById('home-view')?.classList.contains('active');
    if (homeVisible && !currentShareText()) {
      refreshDouyinPreview({ force: true, silent: true });
    }
  }, 2800);
}

async function handleProviderChange() {
  const provider = normalizeProvider(document.getElementById('provider').value);
  const info = providerInfo(provider);
  document.getElementById('endpoint-url').value = document.getElementById('endpoint-url').value.trim() || info.endpoint;
  document.getElementById('api-key').placeholder = info.keyPlaceholder;
  await saveApiConfig();
}

async function submitDouyinIngestFromPopup(ingestIntent) {
  const shareInput = document.getElementById('douyin-share-text');
  const shareText = shareInput?.value.trim() || '';
  const label = INGEST_INTENT_LABELS[ingestIntent] || '入库';
  const hintId = 'ingest-hint';
  const buttons = [
    'share-knowledge', 'share-viral'
  ].map(id => document.getElementById(id)).filter(Boolean);
  if (!isAgentConnected) {
    showHint(hintId, '请先启动 Agent 服务', 'warning', { persist: true });
    openSettingsDetail('agent-settings');
    return;
  }
  buttons.forEach(btn => { btn.disabled = true; });
  showHint(hintId, shareText ? '正在从分享文案提取链接并提交' : '正在识别当前抖音页面', 'warning', { persist: true });
  try {
    const response = await chrome.runtime.sendMessage({
      action: 'submitDouyinIngestFromPopup',
      ingestIntent,
      shareText
    });
    if (response?.ok) {
      showHint(hintId, `${label}任务已进入队列`, 'success');
      requestTaskStatus();
    } else {
      showHint(hintId, response?.message || '没有识别到可入库的抖音链接', 'warning', { persist: true });
    }
  } catch (err) {
    showHint(hintId, err.message || '扩展后台未响应', 'error', { persist: true });
  } finally {
    buttons.forEach(btn => { btn.disabled = false; });
  }
}

async function discoverVault() {
  const hint = document.getElementById('vault-path').value.trim();
  if (hint) await chrome.storage.local.set({ vaultPath: hint });
  setStatus('vault', '正在识别', 'warning');
  const sent = sendToAgent({ type: 'vault_discover', hint });
  if (!sent) {
    setStatus('vault', 'Agent 未连接', 'offline');
    showHint('vault-hint', '请先启动 Agent 服务', 'warning', { persist: true });
  }
}

function pickVault() {
  setStatus('vault', '等待选择', 'warning');
  const sent = sendToAgent({ type: 'vault_pick' });
  if (!sent) {
    setStatus('vault', 'Agent 未连接', 'offline');
    showHint('vault-hint', '请先启动 Agent 服务', 'warning', { persist: true });
  }
}

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
      if (isDouyinCookie(cookie)) byKey.set(cookieIdentity(cookie), cookie);
    }
  };
  for (const query of DOUYIN_COOKIE_QUERIES) {
    addCookies(await chrome.cookies.getAll(query));
  }
  if (byKey.size < 8) addCookies(await chrome.cookies.getAll({}));
  return Array.from(byKey.values()).sort((a, b) => {
    const domain = a.domain.localeCompare(b.domain);
    return domain !== 0 ? domain : a.name.localeCompare(b.name);
  });
}

async function grabCookie() {
  const btn = document.getElementById('grab-cookie');
  btn.disabled = true;
  btn.textContent = '抓取中...';
  try {
    if (!chrome.cookies) throw new Error('缺少 chrome.cookies 权限');
    const cookies = await collectDouyinCookies();
    if (cookies.length === 0) {
      setStatus('cookie', '未找到', 'offline');
      showHint('cookie-hint', '请先打开抖音网页版并登录', 'warning', { persist: true });
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
    setStatus('cookie', sent ? '等待确认' : '待同步', 'warning', formatTime(grabbedAt));
    showHint('cookie-hint', sent ? 'Cookie 已发送，等待 Agent 确认' : 'Agent 未连接，Cookie 已暂存', sent ? 'warning' : 'warning', { persist: true });
  } catch (err) {
    setStatus('cookie', '抓取失败', 'offline');
    showHint('cookie-hint', err.message || 'Cookie 抓取失败', 'error', { persist: true });
  } finally {
    btn.disabled = false;
    btn.textContent = '抓取抖音 Cookie';
  }
}

async function refreshCookieStatusFromStorage() {
  const local = await chrome.storage.local.get(['cookieSyncedAt']);
  const pending = await transientStorage().get(['pendingCookieText', 'pendingCookieCount', 'pendingCookieGrabbedAt']);
  if (pending.pendingCookieText) {
    const pendingAt = new Date(pending.pendingCookieGrabbedAt || 0).getTime();
    const syncedAt = new Date(local.cookieSyncedAt || 0).getTime();
    if (!local.cookieSyncedAt || pendingAt >= syncedAt) {
      setStatus('cookie', '待同步', 'warning', formatTime(pending.pendingCookieGrabbedAt));
      return;
    }
  }
  if (local.cookieSyncedAt) {
    setStatus('cookie', '已同步', 'online', formatTime(local.cookieSyncedAt));
    document.getElementById('cookie-settings-copy').textContent = `上次同步：${formatTime(local.cookieSyncedAt)}`;
    return;
  }
  setStatus('cookie', '未同步', isAgentConnected ? 'warning' : 'offline');
}

function updateConnectionStatus(connected) {
  isAgentConnected = connected;
  setStatus('agent', connected ? '已连接' : '未连接', connected ? 'online' : 'offline');
  document.getElementById('agent-settings-copy').textContent = connected ? '本地 Agent 已连接' : '本地 Agent 未连接';
  refreshCookieStatusFromStorage();
  updateSystemSummary();
}

function setStatus(kind, text, type, time) {
  const dot = document.getElementById(`${kind}-status-dot`);
  const label = document.getElementById(`${kind}-status-text`);
  const menuDot = document.getElementById(`settings-${kind}-dot`);
  const menuLabel = document.getElementById(`settings-${kind}-summary`);
  const normalized = type || 'warning';
  if (dot) dot.className = `status-dot inline-dot ${normalized}`;
  if (menuDot) menuDot.className = `status-dot inline-dot ${normalized}`;
  if (label) {
    label.textContent = time ? `${text} · ${time}` : text;
    label.className = normalized;
  }
  if (menuLabel) {
    menuLabel.textContent = time ? `${text} · ${time}` : text;
    menuLabel.className = normalized;
  }
  updateSystemSummary();
}

function updateSystemSummary() {
  const el = document.getElementById('system-summary');
  if (!el) return;
  if (!isAgentConnected) {
    el.textContent = 'Agent 未连接';
    return;
  }
  const api = document.getElementById('api-status-text')?.className || '';
  const cookie = document.getElementById('cookie-status-text')?.className || '';
  const vault = document.getElementById('vault-status-text')?.className || '';
  if ([api, cookie, vault].includes('offline')) {
    el.textContent = '存在配置异常';
  } else if ([api, cookie, vault].includes('warning')) {
    el.textContent = '有配置待处理';
  } else {
    el.textContent = '系统就绪';
  }
}

function setView(viewId) {
  document.body.dataset.view = viewId;
  ['home-view', 'settings-index-view', 'settings-detail-view'].forEach(id => {
    const view = document.getElementById(id);
    if (!view) return;
    const active = id === viewId;
    view.classList.toggle('active', active);
    view.setAttribute('aria-hidden', active ? 'false' : 'true');
    if ('inert' in view) view.inert = !active;
  });
  document.body.scrollTop = 0;
}

function hideAllDetailSections() {
  document.querySelectorAll('.detail-section').forEach(section => {
    section.hidden = true;
    section.classList.remove('active-detail');
  });
}

function openSettingsIndex() {
  lastSettingsTrigger = document.activeElement;
  hideAllDetailSections();
  updateVideoSettingsSummary();
  setView('settings-index-view');
  requestAnimationFrame(() => document.getElementById('settings-index-view')?.focus({ preventScroll: true }));
}

function openSettingsDetail(targetId) {
  lastSettingsTrigger = document.activeElement;
  const target = document.getElementById(targetId || 'api-settings') || document.getElementById('api-settings');
  hideAllDetailSections();
  target.hidden = false;
  target.classList.add('active-detail');
  const title = target.dataset.title || SETTINGS_DETAIL_TITLES[target.id] || '设置';
  document.getElementById('settings-detail-title').textContent = title;
  setView('settings-detail-view');
  requestAnimationFrame(() => target.focus({ preventScroll: true }));
}

function closeToHome() {
  hideAllDetailSections();
  setView('home-view');
  const triggerIsHiddenMenu = lastSettingsTrigger?.closest?.('#settings-index-view');
  if (lastSettingsTrigger && !triggerIsHiddenMenu && typeof lastSettingsTrigger.focus === 'function') {
    lastSettingsTrigger.focus();
  } else {
    document.getElementById('open-settings').focus();
  }
}

function updateVideoSettingsSummary() {
  const modelId = normalizeModelId(document.getElementById('analysis-model-id')?.value);
  const modelPreset = presetFromModel(modelId);
  const taskConcurrency = normalizeTaskConcurrency(document.getElementById('task-concurrency')?.value);
  const chunkConcurrency = normalizeChunkConcurrency(document.getElementById('chunk-concurrency')?.value);
  const summary = document.getElementById('settings-video-summary');
  const dot = document.getElementById('settings-video-dot');
  if (summary) {
    const modelLabel = modelPreset === 'custom' ? '自定义模型' : (modelPreset === 'mini' ? 'Mini' : 'Lite');
    summary.textContent = `${modelLabel} · 并发 ${taskConcurrency}/${chunkConcurrency}`;
    summary.className = 'online';
  }
  if (dot) dot.className = 'status-dot inline-dot online';
}

function showHint(id, text, type, options = {}) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = text || '';
  el.className = `hint ${type || ''}${options.persist ? ' persistent' : ''}`;
  if (!options.persist && text) {
    setTimeout(() => {
      el.textContent = '';
      el.className = 'hint';
    }, 4000);
  }
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
  if (seconds === null || seconds === undefined || Number.isNaN(Number(seconds))) return '';
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

function bindClick(id, handler) {
  const el = document.getElementById(id);
  if (el) el.addEventListener('click', handler);
}

function bindOptionControls() {
  document.querySelectorAll('[data-control][data-value]').forEach(button => {
    button.addEventListener('click', () => {
      setControlValue(button.dataset.control, button.dataset.value, { notify: true });
      if (button.dataset.modelId) {
        setControlValue('analysis-model-id', button.dataset.modelId);
        updateVideoSettingsSummary();
      }
    });
  });
}

function syncModelPresetFromInput() {
  setControlValue('analysis-model-preset', presetFromModel(document.getElementById('analysis-model-id').value));
  updateVideoSettingsSummary();
}

document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('open-settings').addEventListener('click', openSettingsIndex);
  document.getElementById('back-home').addEventListener('click', closeToHome);
  document.getElementById('back-home-from-index').addEventListener('click', closeToHome);
  document.querySelectorAll('.status-chip').forEach(button => {
    button.addEventListener('click', () => openSettingsDetail(button.dataset.target));
  });
  document.querySelectorAll('.settings-card').forEach(button => {
    button.addEventListener('click', () => openSettingsDetail(button.dataset.target));
  });
  document.getElementById('save-api-config').addEventListener('click', saveApiConfig);
  document.getElementById('save-video-config').addEventListener('click', saveVideoConfig);
  document.getElementById('provider').addEventListener('change', handleProviderChange);
  document.getElementById('analysis-model-preset').addEventListener('change', updateVideoSettingsSummary);
  document.getElementById('analysis-model-id').addEventListener('input', syncModelPresetFromInput);
  document.getElementById('analysis-model-id').addEventListener('change', syncModelPresetFromInput);
  document.getElementById('task-concurrency').addEventListener('change', updateVideoSettingsSummary);
  document.getElementById('chunk-concurrency').addEventListener('change', updateVideoSettingsSummary);
  document.getElementById('check-model').addEventListener('click', checkModelHealth);
  document.getElementById('detect-vault').addEventListener('click', discoverVault);
  document.getElementById('pick-vault').addEventListener('click', pickVault);
  document.getElementById('grab-cookie').addEventListener('click', grabCookie);
  bindClick('share-knowledge', () => submitDouyinIngestFromPopup('knowledge_ingest'));
  bindClick('share-viral', () => submitDouyinIngestFromPopup('viral_breakdown'));
  document.getElementById('refresh-status').addEventListener('click', requestStatus);
  document.getElementById('refresh-tasks').addEventListener('click', requestTaskStatus);
  document.getElementById('toggle-key').addEventListener('click', () => {
    const input = document.getElementById('api-key');
    const btn = document.getElementById('toggle-key');
    input.type = input.type === 'password' ? 'text' : 'password';
    btn.textContent = input.type === 'password' ? '显示' : '隐藏';
    btn.setAttribute('aria-pressed', input.type === 'text' ? 'true' : 'false');
  });
  document.getElementById('douyin-share-text').addEventListener('input', scheduleDouyinPreviewRefresh);
  document.getElementById('douyin-preview-image').addEventListener('error', () => {
    setPreviewImage('', 'video');
  });
  bindOptionControls();
  if (!hasExtensionApis()) {
    renderDouyinPreview({
      ok: false,
      source: 'current',
      message: '打开扩展后自动识别抖音内容'
    });
    updateSystemSummary();
    return;
  }
  loadConfig();
  refreshCookieStatusFromStorage();
  startDouyinPreviewLoop();
  connectWebSocket();
});
