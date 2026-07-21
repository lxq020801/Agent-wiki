// popup.js - Agent-wiki control console
// 首页提供正式功能入口；设置页只负责连接、凭据、知识库与拆解偏好。

const RuntimeVersion = globalThis.AgentWikiRuntime;
const EXTENSION_VERSION = RuntimeVersion.extensionVersion();
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
const POPUP_ROUTE_STORAGE_KEY = 'popupRoute';
const AGENT_START_COMMAND = './agent-wiki start';
const LEGACY_GITHUB_ROUTE = 'github';
const POPUP_VIEWS = Object.freeze({
  HOME: 'home-view',
  SETTINGS_INDEX: 'settings-index-view',
  SETTINGS_DETAIL: 'settings-detail-view',
  GITHUB: 'github-view'
});
const GITHUB_MESSAGE_TYPES = Object.freeze({
  STATUS_REQUEST: 'github_status_request',
  AUTH_START: 'github_auth_start',
  AUTH_CANCEL: 'github_auth_cancel',
  LOGOUT: 'github_logout',
  SETTINGS_UPDATE: 'github_settings_update',
  STARS_REQUEST: 'github_stars_request',
  IMPORT_STARS: 'github_import_stars',
  IMPORT_STATUS: 'github_import_status',
  IMPORT_CANCEL: 'github_import_cancel',
  REFRESH_CHECK: 'github_refresh_check',
  REFRESH_CONFIRM: 'github_refresh_confirm',
  REFRESH_CANCEL: 'github_refresh_cancel'
});
const VAULT_MESSAGE_TYPES = Object.freeze({
  SELECT_FOLDER: 'vault_select_folder',
  SELECT_CONFIRM: 'vault_select_confirm'
});
const TRUSTED_ARK_HOSTS = new Set(['ark.cn-beijing.volces.com']);
const SETTINGS_DETAIL_TITLES = {
  'agent-settings': 'Agent 连接',
  'api-settings': 'API 设置',
  'video-settings': '模型与并发',
  'vault-settings': '知识库',
  'cookie-settings': '抖音 Cookie',
  'task-settings': '任务状态'
};
const POPUP_ROUTE_VIEW_IDS = new Set(Object.values(POPUP_VIEWS));
const POPUP_ROUTE_DETAIL_IDS = new Set(Object.keys(SETTINGS_DETAIL_TITLES));
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
let runtimeCompatibility = null;
let runtimeSyncStarted = false;
let colorSchemeMedia = null;
const pendingDerivedActions = new Map();
let githubAuthFlow = null;
let githubStars = [];
let githubStarsAutoLoaded = false;
let githubStarsPage = 0;
let githubStarsHasNext = false;
let githubSelected = new Set();
let githubImportBatch = null;
let githubRecentImports = [];
let githubRecentTasks = [];
let githubImportStatusRequestedFor = '';
let githubRefresh = null;
let githubIsConfigured = false;
let githubIsAuthenticated = false;
let githubValidationRequestedForConnection = false;
const setupState = {
  api: { configured: false, verified: false, checkState: 'unchecked' },
  vault: { configured: false, verified: false, checkState: 'unchecked' },
  cookie: { configured: false, pending: false, verified: false, checkState: 'unchecked' },
  github: { configured: false, verified: false }
};
let vaultWorkflow = {
  stage: 'idle',
  selectionId: '',
  pendingType: ''
};

function debugLog(...args) {
  if (DEBUG_LOGS) console.log(...args);
}

function applyColorScheme(state) {
  if (!document.documentElement) return;
  document.documentElement.dataset.theme = state?.matches ? 'dark' : 'light';
}

function initColorScheme() {
  if (typeof globalThis.matchMedia !== 'function') return;
  colorSchemeMedia = globalThis.matchMedia('(prefers-color-scheme: dark)');
  applyColorScheme(colorSchemeMedia);
  if (typeof colorSchemeMedia.addEventListener === 'function') {
    colorSchemeMedia.addEventListener('change', applyColorScheme);
  } else if (typeof colorSchemeMedia.addListener === 'function') {
    colorSchemeMedia.addListener(applyColorScheme);
  }
}

function hasExtensionApis() {
  return typeof chrome !== 'undefined' && !!chrome.runtime?.sendMessage && !!chrome.storage?.local;
}

function serviceCanVerify() {
  return Boolean(isAgentConnected && runtimeCompatibility?.canOperate);
}

function onboardingStepViews() {
  const agentReady = serviceCanVerify();
  return [
    {
      id: 'agent',
      label: '本地 Agent 服务',
      ready: agentReady,
      state: agentReady
        ? '已连接并通过版本校验'
        : runtimeCompatibility && !runtimeCompatibility.canOperate
          ? '已连接，版本校验未通过'
          : isAgentConnected ? '已连接，版本待校验' : '未连接，先启动本地服务',
      action: '处理'
    },
    {
      id: 'api',
      label: 'Ark API',
      ready: setupState.api.verified,
      state: setupState.api.verified
        ? '服务已验证连接'
        : setupState.api.configured
          ? !agentReady
            ? '已配置，待 Agent 检查'
            : ['configured', 'unchecked'].includes(setupState.api.checkState)
              ? '已配置，等待测试'
              : '已配置，检查未通过'
          : '未配置',
      action: '配置'
    },
    {
      id: 'vault',
      label: '知识库',
      ready: setupState.vault.verified,
      state: setupState.vault.verified
        ? '服务已确认连接'
        : setupState.vault.configured
          ? agentReady ? '已配置，服务未确认' : '已配置，待 Agent 检查'
          : '未连接',
      action: '选择'
    },
    {
      id: 'cookie',
      label: '抖音 Cookie',
      ready: setupState.cookie.verified,
      state: setupState.cookie.verified
        ? '服务已确认同步'
        : setupState.cookie.pending
          ? '已抓取，待同步'
          : setupState.cookie.configured
            ? !agentReady
              ? '已同步，待 Agent 检查'
              : setupState.cookie.checkState === 'incomplete'
                ? '同步不完整，请重新抓取'
                : '已同步，服务未确认'
            : '未同步',
      action: '同步'
    },
    {
      id: 'github',
      label: 'GitHub（可选）',
      optional: true,
      ready: setupState.github.verified,
      state: setupState.github.verified
        ? '已登录'
        : setupState.github.configured
          ? '待 Agent 检查，不影响完成'
          : '未登录，不影响完成',
      action: '查看'
    }
  ];
}

function renderFirstRunGuide() {
  const guide = document.getElementById('first-run-guide');
  const steps = onboardingStepViews();
  const required = steps.filter(step => !step.optional);
  const next = required.find(step => !step.ready) || null;
  const requiredComplete = !next;
  const summary = requiredComplete
    ? '必填项已完成，GitHub 可稍后连接'
    : `下一步：${next.label}`;
  document.getElementById('first-run-summary').textContent = summary;

  if (guide && typeof guide.querySelector === 'function') {
    for (const step of steps) {
      const row = guide.querySelector(`[data-onboarding-step="${step.id}"]`);
      const state = document.getElementById(`onboarding-${step.id}-state`);
      const action = row?.querySelector('.onboarding-step-action');
      row?.classList.toggle('complete', step.ready);
      if (state) state.textContent = step.state;
      if (action) {
        action.textContent = step.ready ? '完成' : step.action;
        action.disabled = step.ready;
        action.setAttribute('aria-label', `${step.action}${step.label}`);
      }
    }
  }

  const reminder = document.getElementById('first-run-reminder');
  if (reminder) {
    reminder.hidden = requiredComplete;
    if (!requiredComplete) {
      document.getElementById('first-run-reminder-dot').className = 'status-dot inline-dot warning';
      document.getElementById('first-run-reminder-title').textContent = `待完成：${next.label}`;
      document.getElementById('first-run-reminder-copy').textContent = next.state;
      const action = document.getElementById('first-run-next-action');
      action.hidden = false;
      action.disabled = false;
      action.dataset.onboardingAction = next.id;
      action.textContent = next.action;
    }
  }
}

function runOnboardingAction(action) {
  const target = String(action || '');
  if (target === 'agent') openSettingsDetail('agent-settings');
  else if (target === 'api') openSettingsDetail('api-settings');
  else if (target === 'vault') openSettingsDetail('vault-settings');
  else if (target === 'cookie') openSettingsDetail('cookie-settings');
  else if (target === 'github') openGithubPage();
}

async function copyPlainText(text) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const input = document.createElement('textarea');
  input.value = text;
  input.style.position = 'fixed';
  input.style.opacity = '0';
  document.body.appendChild(input);
  input.select();
  const copied = document.execCommand('copy');
  input.remove();
  if (!copied) throw new Error('clipboard unavailable');
}

async function copyAgentStartCommand() {
  try {
    await copyPlainText(AGENT_START_COMMAND);
    showHint('agent-recovery-hint', '启动命令已复制', 'success');
  } catch (_err) {
    showHint('agent-recovery-hint', '复制失败，请手动选择命令', 'error', { persist: true });
  }
}

function retryAgentConnection() {
  const button = document.getElementById('retry-agent-connection');
  button.disabled = true;
  showHint('agent-recovery-hint', '正在重试本地连接', 'warning', { persist: true });
  if (ws?.readyState === WebSocket.OPEN) {
    runtimeCompatibility = null;
    updateConnectionStatus(true);
    ws.send(JSON.stringify(withOperationContext(RuntimeVersion.buildHandshake('agent-wiki-extension'))));
    requestStatus();
  } else if (ws?.readyState !== WebSocket.CONNECTING) {
    connectWebSocket();
  }
  setTimeout(() => {
    button.disabled = false;
    if (serviceCanVerify()) showHint('agent-recovery-hint', 'Agent 已连接', 'success');
  }, 1200);
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
    'videoChunkConcurrency'
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
  return data;
}

// WebSocket
function connectWebSocket() {
  clearTimeout(reconnectTimer);
  try {
    ws = new WebSocket(WS_URL);
    ws.onopen = () => {
      runtimeCompatibility = null;
      runtimeSyncStarted = false;
      githubValidationRequestedForConnection = false;
      githubImportStatusRequestedFor = '';
      updateConnectionStatus(true);
      ws.send(JSON.stringify(withOperationContext(
        RuntimeVersion.buildHandshake('agent-wiki-extension')
      )));
      requestStatus();
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
      runtimeCompatibility = null;
      runtimeSyncStarted = false;
      githubValidationRequestedForConnection = false;
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
  const payload = withOperationContext(data);
  if (!RuntimeVersion.canSendMessage(payload?.type, runtimeCompatibility)) {
    return false;
  }
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(payload));
    return true;
  }
  return false;
}

function makeOperationId(type = 'operation') {
  const prefix = String(type || 'operation').replace(/[^a-z0-9_-]+/gi, '-').toLowerCase();
  const suffix = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `${prefix}-${suffix}`;
}

function withOperationContext(data) {
  const payload = { ...(data || {}) };
  payload.operationId = payload.operationId || makeOperationId(payload.type);
  payload.taskId = payload.taskId || payload.batchId || payload.flowId || '';
  payload.parentId = payload.parentId || payload.parentTaskId || '';
  payload.requestId = payload.requestId || `${payload.type || 'request'}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return payload;
}

function requestStatus() {
  sendToAgent({ type: 'status_request' });
}

function requestTaskStatus() {
  sendToAgent({ type: 'task_status_request' });
}

function requestGithubStatus({ validate = false } = {}) {
  return sendToAgent({
    type: GITHUB_MESSAGE_TYPES.STATUS_REQUEST,
    requestId: `github-status-${Date.now()}`,
    ...(validate ? { validate: true } : {})
  });
}

function requestGithubImportStatus(batchId) {
  const id = String(batchId || '');
  if (!id) return false;
  const sent = sendToAgent({
    type: GITHUB_MESSAGE_TYPES.IMPORT_STATUS,
    requestId: `github-import-status-${Date.now()}`,
    batchId: id
  });
  if (sent) githubImportStatusRequestedFor = id;
  return sent;
}

function requestGithubValidationAfterHandshake(compatibility) {
  if (
    githubValidationRequestedForConnection ||
    !compatibility?.canOperate
  ) return false;
  const sent = requestGithubStatus({ validate: true });
  if (sent) githubValidationRequestedForConnection = true;
  return sent;
}

function derivedActionKey(taskId, candidateId) {
  return `${taskId || ''}:${candidateId || ''}`;
}

function clearPendingDerivedAction(taskId, candidateId) {
  const id = candidateId || '';
  pendingDerivedActions.delete(derivedActionKey(taskId, id));
  pendingDerivedActions.delete(derivedActionKey('', id));
  if (!taskId && id) {
    for (const key of Array.from(pendingDerivedActions.keys())) {
      if (key.endsWith(`:${id}`)) pendingDerivedActions.delete(key);
    }
  }
}

function submitDerivedAction(row, action) {
  if (!row) return;
  const taskId = row.dataset.taskId || '';
  const candidateId = row.dataset.candidateId || '';
  const status = row.dataset.derivedStatus || '';
  const targetType = row.dataset.targetType || '';
  const input = row.querySelector('[data-role="derived-url"]');
  let targetUrl = action === 'confirm' && input ? input.value.trim() : '';
  if (action === 'confirm' && !targetUrl && (status === 'needs_target' || ['official_doc', 'web_research'].includes(targetType))) {
    showHint('task-hint', '请先补充目标 HTTPS 链接', 'warning');
    input?.focus();
    return;
  }
  if (action === 'confirm' && targetUrl) {
    try {
      const url = new URL(targetUrl);
      if (url.protocol !== 'https:') throw new Error('URL 必须是 HTTPS');
    } catch (err) {
      showHint('task-hint', err.message || '目标链接格式不正确', 'error');
      return;
    }
  }
  if (action !== 'confirm') targetUrl = '';
  if (!taskId || !candidateId) return;
  const key = derivedActionKey(taskId, candidateId);
  pendingDerivedActions.set(key, action);
  setDerivedRowPending(row, true);
  const sent = sendToAgent({
    type: 'derived_task_action',
    requestId: `derived-${Date.now()}-${Math.random().toString(16).slice(2)}`,
    taskId,
    derivedTaskId: candidateId,
    action,
    targetUrl
  });
  if (!sent) {
    pendingDerivedActions.delete(key);
    setDerivedRowPending(row, false);
    showHint('task-hint', 'Agent 未连接，无法操作派生候选', 'warning');
    return;
  }
  requestTaskStatus();
}

function setDerivedRowPending(row, pending) {
  row?.querySelectorAll?.('.derived-action').forEach(button => {
    button.disabled = pending;
    if (pending && button.dataset.action === 'confirm') button.textContent = '处理中';
  });
}

function transientStorage() {
  return chrome.storage.session || chrome.storage.local;
}

function popupRouteStorage() {
  return typeof chrome === 'undefined' ? null : chrome.storage?.session || null;
}

function sanitizePopupRoute(route) {
  if (route === LEGACY_GITHUB_ROUTE) return { view: POPUP_VIEWS.GITHUB };
  if (!route || typeof route !== 'object' || Array.isArray(route)) {
    return { view: POPUP_VIEWS.HOME };
  }
  const view = POPUP_ROUTE_VIEW_IDS.has(route.view) ? route.view : POPUP_VIEWS.HOME;
  if (view !== POPUP_VIEWS.SETTINGS_DETAIL) return { view };
  if (!POPUP_ROUTE_DETAIL_IDS.has(route.detailId)) {
    return { view: POPUP_VIEWS.SETTINGS_INDEX };
  }
  return { view, detailId: route.detailId };
}

async function persistPopupRoute(route) {
  const storage = popupRouteStorage();
  if (!storage) return;
  try {
    await storage.set({ [POPUP_ROUTE_STORAGE_KEY]: sanitizePopupRoute(route) });
  } catch (err) {
    debugLog('[Agent-wiki] 无法保存 popup route:', err);
  }
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
    setStatus('cookie', '待确认', 'warning', formatDateTime(pending.pendingCookieGrabbedAt));
  }
}

function handleAgentMessage(msg) {
  switch (msg.type) {
    case 'agent_ready':
    case 'handshake_ack': {
      updateConnectionStatus(true);
      const compatibility = applyRuntimeCompatibility(msg);
      requestStatus();
      requestGithubValidationAfterHandshake(compatibility);
      if (compatibility.canOperate && !runtimeSyncStarted) {
        runtimeSyncStarted = true;
        flushPendingSync();
      }
      break;
    }
    case 'status_snapshot':
      applyRuntimeCompatibility(msg);
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
        setupState.cookie.configured = true;
        setupState.cookie.pending = false;
        setupState.cookie.verified = serviceCanVerify();
        setStatus('cookie', '已同步', 'online', formatDateTime(msg.timestamp || new Date().toISOString()));
        renderFirstRunGuide();
      }
      break;
    case 'vault_lifecycle_status':
      applyVaultLifecycleStatus(msg.status || msg.result || {});
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
    case 'derived_task_action_done':
      clearPendingDerivedAction(msg.parentTaskId || msg.taskId, msg.candidateId || msg.derivedTaskId);
      requestTaskStatus();
      break;
    case 'derived_task_action_rejected':
      clearPendingDerivedAction(msg.parentTaskId || msg.taskId, msg.candidateId || msg.derivedTaskId);
      showHint('task-hint', msg.message || '派生操作失败', 'error');
      requestTaskStatus();
      break;
    case 'github_status':
      applyGithubStatus(msg.result || {});
      break;
    case 'github_auth_state':
      applyGithubAuthState(msg.result || {});
      break;
    case 'github_stars_results':
      applyGithubStarsResults(msg.result || {});
      break;
    case 'github_import_accepted':
    case 'github_import_progress':
      applyGithubImportProgress(msg.result || {});
      break;
    case 'github_refresh_state':
      applyGithubRefreshState(msg.result || {});
      break;
    case 'github_error':
      applyGithubError(msg.result || {});
      break;
    case 'error':
      showHint('config-hint', msg.message || msg.error || 'Agent 返回错误', 'error', { persist: true });
      break;
    case 'protocol_rejected':
      applyRuntimeCompatibility(msg, {
        state: msg.reason || 'protocol_rejected',
        tone: 'offline',
        canOperate: false,
        message: msg.message || '版本握手未通过，服务已拒绝写操作。'
      });
      break;
    default:
      debugLog('[Librarian] 未知消息类型:', msg.type);
  }
}

function applyRuntimeCompatibility(msg, override = null) {
  const evaluated = RuntimeVersion.evaluateRuntimeCompatibility(msg, EXTENSION_VERSION);
  runtimeCompatibility = override ? { ...evaluated, ...override } : evaluated;
  const runtime = runtimeCompatibility.runtime;
  const serviceVersion = runtime.productVersion ? `v${runtime.productVersion}` : '旧服务 / 未提供';
  const serviceProtocol = runtime.protocolVersion ? `v${runtime.protocolVersion}` : '未提供';
  const sourceIdentity = runtime.sourceRevision || runtime.buildId || '未提供';
  const versionLabel = runtimeCompatibility.canOperate
    ? `服务 ${serviceVersion}`
    : runtimeCompatibility.state === 'legacy_server'
      ? '检测到旧服务'
      : '版本不一致';

  setStatus('agent', versionLabel, runtimeCompatibility.tone);
  document.getElementById('extension-version').textContent = `v${EXTENSION_VERSION || '未知'}`;
  document.getElementById('service-version').textContent = serviceVersion;
  document.getElementById('runtime-protocol-version').textContent = `扩展 v${RuntimeVersion.PROTOCOL_VERSION} · 服务 ${serviceProtocol}`;
  document.getElementById('runtime-source-version').textContent = sourceIdentity;
  document.getElementById('agent-settings-copy').textContent = runtimeCompatibility.canOperate
    ? `本地服务已连接，版本校验通过`
    : '本地服务已连接，但版本校验未通过';
  showHint(
    'runtime-version-hint',
    runtimeCompatibility.message,
    runtimeCompatibility.canOperate ? 'success' : runtimeCompatibility.tone === 'offline' ? 'error' : 'warning',
    { persist: true }
  );
  renderFirstRunGuide();
  return runtimeCompatibility;
}

function runtimeUnavailableMessage() {
  return runtimeCompatibility?.message || '请先启动 Agent 服务';
}

function applyStatusSnapshot(status) {
  applyVaultStatus(status.vault || {});
  applyModelStatus(status.llm || status.model || {});
  applyVideoStatus(status.videoAnalysis || {});
  applyCookieStatus(status.cookie || {});
  applyTaskStatus(status.tasks || {});
  if (status.github && typeof status.github === 'object') {
    applyGithubStatus(status.github);
  }
}

function applyVaultStatus(status) {
  const currentPath = document.getElementById('vault-current-path');
  const activeVault = status.activeVault || {};
  const state = String(status.state || '').toLowerCase();
  const path = activeVault.vaultPath || status.path || status.vaultPath || '';
  setupState.vault.configured = Boolean(path);
  setupState.vault.checkState = state || 'unchecked';
  const connected = Boolean(
    serviceCanVerify() &&
    path &&
    status.ok !== false &&
    ['ready', 'selected', 'initialized', 'reconnected', 'switched', 'created'].includes(state)
  );
  setupState.vault.verified = connected;
  if (connected) {
    if (currentPath) {
      currentPath.textContent = path || '已连接';
      currentPath.title = path;
    }
    chrome.storage.local.set({ vaultPath: path, vaultSyncedAt: new Date().toISOString() });
    setStatus('vault', '已连接', 'online');
    showHint('vault-hint', status.message || '知识库已连接。', 'success', { persist: true });
    renderFirstRunGuide();
    return;
  }
  if (path) {
    if (currentPath) {
      currentPath.textContent = '已配置，待 Agent 检查';
      currentPath.title = path;
    }
    setStatus('vault', '已配置，待检查', 'warning');
    renderFirstRunGuide();
    return;
  }
  if (currentPath) {
    currentPath.textContent = '尚未连接';
    currentPath.title = '';
  }
  if (serviceCanVerify()) chrome.storage.local.set({ vaultPath: '' });
  setStatus('vault', '待识别', 'warning');
  if (status.message) showHint('vault-hint', status.message, 'warning', { persist: true });
  renderFirstRunGuide();
}

function applyModelStatus(status) {
  const provider = normalizeProvider(status.provider || document.getElementById('provider').value);
  const info = providerInfo(provider);
  if (status.endpoint) {
    document.getElementById('endpoint-url').value = status.endpoint;
    chrome.storage.local.set({ arkEndpoint: status.endpoint, endpoint: status.endpoint });
  }
  const state = String(status.state || '').toLowerCase();
  setupState.api.checkState = state || 'unchecked';
  const ready = serviceCanVerify() && Boolean(status.ok || state === 'ready');
  setupState.api.verified = ready;
  if (state === 'missing') setupState.api.configured = false;
  else if (status.ok || state) setupState.api.configured = true;
  if (state === 'missing') {
    setStatus('api', '缺少 Key', 'warning', formatDateTime(status.checkedAt));
  } else if (ready) {
    setStatus('api', '已连接', 'online', formatDateTime(status.checkedAt));
  } else if (setupState.api.configured && !serviceCanVerify()) {
    setStatus('api', '已配置，待检查', 'warning', formatDateTime(status.checkedAt));
  } else if (state === 'configured') {
    setStatus('api', '待测试', 'warning', formatDateTime(status.checkedAt));
  } else {
    setStatus('api', status.message || '连接异常', 'offline', formatDateTime(status.checkedAt));
  }
  if (status.message) {
    const type = status.ok ? 'success' : status.state === 'missing' ? 'warning' : 'error';
    showHint('model-hint', `${info.shortLabel}: ${status.message}；该检查不等于视频端到端验证。`, type, { persist: true });
  }
  renderFirstRunGuide();
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
  const state = String(status.state || '').toLowerCase();
  setupState.cookie.checkState = state || 'unchecked';
  const ready = serviceCanVerify() && Boolean(status.ok || state === 'ready');
  setupState.cookie.verified = ready;
  setupState.cookie.pending = false;
  if (state === 'missing') setupState.cookie.configured = false;
  else if (status.ok || state) setupState.cookie.configured = true;
  if (ready) {
    setStatus('cookie', '已同步', 'online', formatDateTime(status.updatedAt));
    document.getElementById('cookie-settings-copy').textContent = `上次同步：${formatDateTime(status.updatedAt) || '刚刚'}`;
  } else if (setupState.cookie.configured && !serviceCanVerify()) {
    setStatus('cookie', '已同步，待检查', 'warning', formatDateTime(status.updatedAt));
  } else if (state === 'incomplete') {
    setStatus('cookie', '不完整', 'warning', formatDateTime(status.updatedAt));
    showHint('cookie-hint', status.message || '请登录抖音后重新抓取', 'warning', { persist: true });
  } else if (state === 'missing') {
    setStatus('cookie', '未同步', isAgentConnected ? 'warning' : 'offline', formatDateTime(status.updatedAt));
    showHint('cookie-hint', status.message || '请打开抖音网页版登录后抓取 Cookie', 'warning', { persist: true });
  } else {
    setStatus('cookie', status.message || '状态未知', 'warning', formatDateTime(status.updatedAt));
  }
  renderFirstRunGuide();
}

function githubConfigured(status) {
  return Boolean(typeof status.configured === 'object' ? status.configured.configured : status.configured);
}

function applyGithubStatus(status) {
  const configured = githubConfigured(status);
  const authenticated = status.state !== 'unavailable' && Boolean(status.authenticated);
  const pending = ['checking', 'unchecked', 'unavailable'].includes(status.state);
  githubIsConfigured = configured;
  githubIsAuthenticated = authenticated;
  setupState.github.configured = configured;
  setupState.github.verified = authenticated;
  const account = status.account || {};
  const login = account.login || '';
  const homeSummary = document.getElementById('home-github-summary');
  const homeDot = document.getElementById('home-github-dot');
  const accountDot = document.getElementById('github-account-dot');
  const copy = document.getElementById('github-account-copy');
  const loginButton = document.getElementById('github-login');
  const logoutButton = document.getElementById('github-logout');

  if (authenticated) {
    homeSummary.textContent = login ? `@${login}` : '已登录';
    homeSummary.title = homeSummary.textContent;
    homeDot.className = 'status-dot inline-dot online';
    accountDot.className = 'status-dot inline-dot online';
    copy.textContent = login ? `@${login}` : '已登录';
  } else if (pending) {
    homeSummary.textContent = '待检查';
    homeSummary.title = '等待 Agent 确认 GitHub 登录状态';
    homeDot.className = 'status-dot inline-dot warning';
    accountDot.className = 'status-dot inline-dot warning';
    copy.textContent = status.state === 'unavailable'
      ? 'Agent 未连接，暂无法检查 GitHub'
      : '正在检查 GitHub 登录状态';
  } else if (!configured) {
    homeSummary.textContent = '未登录';
    homeSummary.title = '未登录';
    homeDot.className = 'status-dot inline-dot offline';
    accountDot.className = 'status-dot inline-dot offline';
    copy.textContent = '未登录 · GitHub App 尚未配置';
  } else {
    homeSummary.textContent = '未登录';
    homeSummary.title = '未登录';
    homeDot.className = 'status-dot inline-dot warning';
    accountDot.className = 'status-dot inline-dot warning';
    copy.textContent = '尚未登录 GitHub';
  }

  loginButton.hidden = authenticated;
  loginButton.disabled = !configured;
  logoutButton.hidden = !authenticated;
  document.getElementById('github-auto-star').checked = Boolean(status.settings?.autoStar);
  document.getElementById('github-auto-star').disabled = !authenticated;
  document.getElementById('github-load-stars').disabled = !authenticated;
  document.getElementById('github-select-all').disabled = !authenticated || !githubStars.length;
  updateGithubSelection();
  githubRecentImports = Array.isArray(status.recentImports) ? status.recentImports : [];
  githubRecentTasks = Array.isArray(status.recentTasks) ? status.recentTasks : [];
  const importSnapshot = status.activeImport || githubRecentImports[0] || null;
  if (importSnapshot) {
    applyGithubImportProgress(importSnapshot, { notify: false, refreshStars: false });
  } else {
    renderGithubPersistentHistory(null);
  }
  if (
    status.activeImport?.id &&
    status.activeImport.id !== githubImportStatusRequestedFor
  ) {
    requestGithubImportStatus(status.activeImport.id);
  }
  if (authenticated) {
    clearGithubAuthFlow();
    if (!githubStarsAutoLoaded) {
      githubStarsAutoLoaded = true;
      loadGithubStars({ auto: true });
    }
  } else {
    githubStarsAutoLoaded = false;
    if (status.activeAuthorization?.flowId) {
      showGithubAuthorization(status.activeAuthorization);
    } else if (githubAuthFlow) {
      clearGithubAuthFlow();
    }
  }

  if (pending && !githubAuthFlow) {
    showHint(
      'github-hint',
      status.state === 'unavailable' ? '连接 Agent 后将自动检查 GitHub 登录状态' : '正在检查 GitHub 登录状态',
      'warning',
      { persist: true }
    );
  } else if (status.message && !authenticated && !githubAuthFlow) {
    showHint('github-hint', status.message, configured ? 'warning' : 'error', { persist: true });
  } else if (authenticated && !githubAuthFlow) {
    showHint('github-hint', login ? `GitHub 已连接：@${login}` : 'GitHub 已连接', 'success', { persist: true });
  }
  renderFirstRunGuide();
}

function clearGithubAuthFlow() {
  githubAuthFlow = null;
  document.getElementById('github-device-panel').hidden = true;
  document.getElementById('github-user-code').textContent = '';
  document.getElementById('github-device-expiry').textContent = '';
  document.getElementById('github-login').disabled = !githubIsConfigured;
}

function githubExpiryCopy(expiresAt) {
  const raw = Number(expiresAt || 0);
  const milliseconds = raw > 0 && raw < 1_000_000_000_000 ? raw * 1000 : raw;
  const expiry = formatDateTime(milliseconds);
  if (!expiry) return '验证码有效期未知';
  if (milliseconds <= Date.now()) return `已于 ${expiry} 过期`;
  return `有效至 ${expiry}`;
}

function showGithubAuthorization(result) {
  githubAuthFlow = {
    flowId: result.flowId,
    userCode: result.userCode || githubAuthFlow?.userCode || '',
    verificationUri: result.verificationUri || githubAuthFlow?.verificationUri || '',
    expiresAt: result.expiresAt || githubAuthFlow?.expiresAt || 0
  };
  document.getElementById('github-user-code').textContent = githubAuthFlow.userCode;
  document.getElementById('github-device-expiry').textContent = githubExpiryCopy(githubAuthFlow.expiresAt);
  document.getElementById('github-device-panel').hidden = false;
  document.getElementById('github-login').disabled = true;
}

async function startGithubAuthorization() {
  await persistPopupRoute({ view: POPUP_VIEWS.GITHUB });
  const button = document.getElementById('github-login');
  button.disabled = true;
  showHint('github-hint', '正在向 GitHub 请求设备授权码', 'warning', { persist: true });
  if (!sendToAgent({ type: GITHUB_MESSAGE_TYPES.AUTH_START, requestId: `github-auth-${Date.now()}` })) {
    button.disabled = false;
    showHint('github-hint', runtimeUnavailableMessage(), 'error', { persist: true });
  }
}

function applyGithubAuthState(result) {
  if (result.state === 'waiting_for_user') {
    showGithubAuthorization(result);
    showHint('github-hint', '等待你在 GitHub 完成授权', 'warning', { persist: true });
    return;
  }
  if (result.state === 'authorization_pending') {
    return;
  }
  if (result.state === 'ready') {
    clearGithubAuthFlow();
    showHint('github-hint', 'GitHub 登录成功', 'success', { persist: true });
    requestGithubStatus();
    return;
  }
  if (result.state === 'cancelled') {
    clearGithubAuthFlow();
    showHint('github-hint', '已取消 GitHub 登录', 'warning', { persist: true });
  }
}

function cancelGithubAuthorization() {
  if (githubAuthFlow?.flowId) {
    sendToAgent({ type: GITHUB_MESSAGE_TYPES.AUTH_CANCEL, flowId: githubAuthFlow.flowId });
  }
  clearGithubAuthFlow();
}

function openGithubAuthorization() {
  const url = githubAuthFlow?.verificationUri;
  if (url) chrome.tabs.create({ url });
}

async function copyGithubAuthorizationCode() {
  const code = String(githubAuthFlow?.userCode || document.getElementById('github-user-code').textContent || '').trim();
  if (!code) {
    showHint('github-hint', '当前没有可复制的验证码', 'warning');
    return;
  }
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(code);
    } else {
      const input = document.createElement('textarea');
      input.value = code;
      input.style.position = 'fixed';
      input.style.opacity = '0';
      document.body.appendChild(input);
      input.select();
      const copied = document.execCommand('copy');
      input.remove();
      if (!copied) throw new Error('clipboard unavailable');
    }
  } catch (_err) {
    showHint('github-hint', '复制失败，请手动选择验证码', 'error');
    return;
  }
  showHint('github-hint', '验证码已复制', 'success');
}

function logoutGithub() {
  showHint('github-hint', '正在退出 GitHub', 'warning', { persist: true });
  sendToAgent({ type: GITHUB_MESSAGE_TYPES.LOGOUT, requestId: `github-logout-${Date.now()}` });
}

function saveGithubAutoStar() {
  const autoStar = document.getElementById('github-auto-star').checked;
  if (!sendToAgent({ type: GITHUB_MESSAGE_TYPES.SETTINGS_UPDATE, autoStar })) {
    showHint('github-hint', runtimeUnavailableMessage(), 'error', { persist: true });
    return;
  }
  showHint('github-hint', autoStar ? '已开启资产创建后自动 Star' : '已关闭自动 Star', 'success');
}

function loadGithubStars({ append = false, auto = false } = {}) {
  const page = append ? githubStarsPage + 1 : 1;
  const button = append ? document.getElementById('github-load-more-stars') : document.getElementById('github-load-stars');
  if (!append) {
    document.getElementById('github-stars-list').replaceChildren(
      makeEmptyGithubRow(auto ? '正在自动读取你的 Stars' : '正在读取你的 Stars')
    );
  }
  button.disabled = true;
  button.textContent = '读取中';
  if (!sendToAgent({ type: GITHUB_MESSAGE_TYPES.STARS_REQUEST, page, perPage: 50 })) {
    button.disabled = false;
    button.textContent = append ? '加载更多' : '读取';
    if (auto) githubStarsAutoLoaded = false;
    showHint('github-hint', runtimeUnavailableMessage(), 'error', { persist: true });
  }
}

function applyGithubStarsResults(result) {
  const page = Number(result.page || 1);
  const incoming = Array.isArray(result.repositories) ? result.repositories : [];
  if (page <= 1) githubSelected.clear();
  githubStars = page > 1 ? githubStars.concat(incoming) : incoming;
  githubStarsPage = page;
  githubStarsHasNext = Boolean(result.hasNext);
  document.getElementById('github-load-stars').disabled = false;
  document.getElementById('github-load-stars').textContent = '重新读取';
  const more = document.getElementById('github-load-more-stars');
  more.disabled = false;
  more.textContent = '加载更多';
  more.hidden = !githubStarsHasNext;
  renderGithubRepositories('github-stars-list', githubStars, { selectable: true });
  document.getElementById('github-select-all').disabled = !githubStars.length;
  updateGithubSelection();
  if (!githubStars.length) showHint('github-hint', '你的 GitHub Stars 列表为空', 'warning');
}

function githubRepositoryKey(repo) {
  return String(repo.id || repo.fullName || '').toLowerCase();
}

function makeEmptyGithubRow(text) {
  const empty = document.createElement('div');
  empty.className = 'empty';
  empty.textContent = text;
  return empty;
}

function renderGithubRepositories(listId, repositories, { selectable }) {
  const list = document.getElementById(listId);
  list.replaceChildren();
  if (!repositories.length) {
    list.appendChild(makeEmptyGithubRow(selectable ? '没有可导入的 Star' : '没有搜索结果'));
    return;
  }
  for (const repo of repositories) {
    const row = document.createElement('div');
    row.className = 'github-repo-row';
    row.dataset.repositoryId = String(repo.id || '');
    row.dataset.fullName = repo.fullName || '';
    if (selectable) {
      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.className = 'github-repo-select';
      checkbox.checked = githubSelected.has(githubRepositoryKey(repo));
      checkbox.setAttribute('aria-label', `选择 ${repo.fullName || '仓库'}`);
      row.appendChild(checkbox);
    } else {
      const badge = document.createElement('span');
      badge.className = repo.ingested ? 'github-ingested-badge' : '';
      badge.textContent = repo.ingested ? '已入库' : 'GitHub';
      row.appendChild(badge);
    }
    const copy = document.createElement('span');
    copy.className = 'github-repo-copy';
    const title = document.createElement('strong');
    title.textContent = repo.fullName || repo.name || '未命名仓库';
    const meta = document.createElement('span');
    meta.textContent = `${repo.language || '未标注语言'} · ${Number(repo.stars || 0).toLocaleString('zh-CN')} Stars`;
    copy.append(title, meta);
    row.appendChild(copy);
    const actions = document.createElement('span');
    actions.className = 'github-repo-actions';
    if (repo.ingested) {
      const refresh = document.createElement('button');
      refresh.type = 'button';
      refresh.className = 'secondary small github-refresh-check';
      refresh.textContent = '检查更新';
      actions.appendChild(refresh);
    }
    row.appendChild(actions);
    list.appendChild(row);
  }
}

function updateGithubSelection() {
  document.getElementById('github-selection-count').textContent = `已选择 ${githubSelected.size} 个`;
  const button = document.getElementById('github-import-selected');
  button.disabled = !githubIsAuthenticated || !githubSelected.size || Boolean(
    githubImportBatch && ['queued', 'running'].includes(githubImportBatch.state)
  );
  const all = document.getElementById('github-select-all');
  const keys = githubStars.map(githubRepositoryKey).filter(Boolean);
  all.checked = Boolean(keys.length && keys.every(key => githubSelected.has(key)));
  all.indeterminate = Boolean(keys.some(key => githubSelected.has(key)) && !all.checked);
}

function importSelectedGithubStars() {
  const repositories = githubStars
    .filter(repo => githubSelected.has(githubRepositoryKey(repo)))
    .map(repo => ({ id: repo.id, fullName: repo.fullName }));
  if (!repositories.length) return;
  document.getElementById('github-import-selected').disabled = true;
  document.getElementById('github-import-progress').hidden = false;
  showHint('github-hint', `正在提交 ${repositories.length} 个仓库`, 'warning', { persist: true });
  if (!sendToAgent({ type: GITHUB_MESSAGE_TYPES.IMPORT_STARS, repositories, requestId: `github-import-${Date.now()}` })) {
    updateGithubSelection();
    showHint('github-hint', runtimeUnavailableMessage(), 'error', { persist: true });
  }
}

function githubBatchItems(batch) {
  if (Array.isArray(batch?.items)) return batch.items;
  return Array.isArray(batch?.results)
    ? batch.results.map((result, index) => ({
        taskId: `legacy-${index}`,
        state: result.state || (result.ok ? 'succeeded' : 'failed'),
        repository: result.repository || {},
        result
      }))
    : [];
}

function githubImportItemView(item) {
  const result = item?.result || {};
  const error = item?.error || {};
  const repository = item?.repository || result.repository || {};
  const name = repository.fullName || repository.id || '仓库';
  const state = item?.state || result.state || 'queued';
  if (state === 'succeeded') return { text: `${name}：已入库`, tone: '' };
  if (state === 'existing') return { text: `${name}：已存在`, tone: '' };
  if (state === 'running') return { text: `${name}：正在入库`, tone: 'warning' };
  if (state === 'queued') return { text: `${name}：等待入库`, tone: 'warning' };
  if (state === 'cancelled') return { text: `${name}：已取消`, tone: 'warning' };
  return { text: `${name}：${error.message || result.message || '导入失败'}`, tone: 'failed' };
}

function appendGithubHistoryLine(container, text, tone = '') {
  const line = document.createElement('div');
  line.className = `github-import-result${tone ? ` ${tone}` : ''}`;
  line.textContent = text;
  container.appendChild(line);
}

function renderGithubPersistentHistory(activeBatch) {
  const panel = document.getElementById('github-import-progress');
  const results = document.getElementById('github-import-results');
  if (!activeBatch) {
    document.getElementById('github-progress-copy').textContent = '最近 GitHub 任务';
    document.getElementById('github-progress-count').textContent = `${githubRecentTasks.length} 条`;
    const bar = document.getElementById('github-progress-bar');
    bar.max = 1;
    bar.value = githubRecentTasks.length ? 1 : 0;
    document.getElementById('github-cancel-import').hidden = true;
  }
  results.replaceChildren();
  const items = githubBatchItems(activeBatch);
  for (const item of items) {
    const view = githubImportItemView(item);
    appendGithubHistoryLine(results, view.text, view.tone);
  }
  for (const batch of githubRecentImports.slice(0, 3)) {
    if (!batch?.id || batch.id === activeBatch?.id) continue;
    const state = batch.state === 'cancelled' ? '已取消' : batch.state === 'completed' ? '已完成' : '进行中';
    appendGithubHistoryLine(
      results,
      `历史批次：${state} · ${batch.completed || 0} / ${batch.total || 0}`,
      batch.failed ? 'warning' : ''
    );
  }
  for (const task of githubRecentTasks.slice(0, 5)) {
    const repository = task?.repository || {};
    const autoStar = task?.autoStar || {};
    const starCopy = autoStar.state === 'failed'
      ? `自动 Star 失败：${autoStar.message || '请检查权限'}`
      : autoStar.state === 'succeeded' ? '已自动 Star' : '资产已登记';
    appendGithubHistoryLine(
      results,
      `${repository.fullName || 'GitHub 资产'}：${starCopy}`,
      autoStar.state === 'failed' ? 'warning' : ''
    );
  }
  panel.hidden = !activeBatch && !githubRecentImports.length && !githubRecentTasks.length;
}

function applyGithubImportProgress(result, { notify = true, refreshStars = true } = {}) {
  githubImportBatch = result;
  const running = ['queued', 'running'].includes(result.state);
  document.getElementById('github-import-progress').hidden = false;
  document.getElementById('github-progress-copy').textContent = result.state === 'completed'
    ? '导入完成'
    : result.state === 'cancelled' ? '导入已取消' : '正在导入';
  document.getElementById('github-progress-count').textContent = `${result.completed || 0} / ${result.total || 0}`;
  const bar = document.getElementById('github-progress-bar');
  bar.max = Math.max(1, Number(result.total || 0));
  bar.value = Number(result.completed || 0);
  document.getElementById('github-cancel-import').hidden = !running;
  renderGithubPersistentHistory(result);
  updateGithubSelection();
  if (!running && notify) {
    const failed = Number(result.failed || 0);
    showHint(
      'github-hint',
      result.state === 'cancelled'
        ? `导入已取消，完成 ${result.completed || 0} 个`
        : `导入完成：成功 ${result.succeeded || 0} 个，失败 ${failed} 个`,
      failed ? 'warning' : 'success',
      { persist: true }
    );
    if (refreshStars) loadGithubStars();
  }
}

function cancelGithubImport() {
  if (githubImportBatch?.id) sendToAgent({ type: GITHUB_MESSAGE_TYPES.IMPORT_CANCEL, batchId: githubImportBatch.id });
}

function checkGithubRefresh(row) {
  if (!row) return;
  const button = row.querySelector('.github-refresh-check');
  if (button) {
    button.disabled = true;
    button.textContent = '检查中';
  }
  const sent = sendToAgent({
    type: GITHUB_MESSAGE_TYPES.REFRESH_CHECK,
    requestId: `github-refresh-${Date.now()}`,
    repository: { id: Number(row.dataset.repositoryId || 0), fullName: row.dataset.fullName || '' }
  });
  if (!sent && button) {
    button.disabled = false;
    button.textContent = '检查更新';
  }
}

function refreshValue(change, side) {
  const value = change[side];
  if (change.field === 'readmeSha256') return side === 'before' ? '原 README' : '新 README';
  if (typeof value === 'boolean') return value ? '是' : '否';
  return String(value ?? '无').slice(0, 48);
}

function applyGithubRefreshState(result) {
  document.querySelectorAll('.github-refresh-check').forEach(button => {
    button.disabled = false;
    button.textContent = '检查更新';
  });
  const panel = document.getElementById('github-refresh-panel');
  document.getElementById('github-confirm-refresh').disabled = false;
  if (result.state === 'confirmation_required') {
    githubRefresh = result;
    panel.hidden = false;
    document.getElementById('github-refresh-title').textContent = `${result.repository?.fullName || '资产'} 有更新`;
    const list = document.getElementById('github-refresh-changes');
    list.replaceChildren();
    for (const change of result.changes || []) {
      const item = document.createElement('li');
      item.textContent = `${change.label}：${refreshValue(change, 'before')} → ${refreshValue(change, 'after')}`;
      list.appendChild(item);
    }
    showHint('github-hint', result.message || '确认后一起更新', 'warning', { persist: true });
    return;
  }
  panel.hidden = true;
  githubRefresh = null;
  if (result.state === 'no_changes') {
    showHint('github-hint', result.message || '资产资料没有变化', 'success', { persist: true });
  } else if (result.state === 'updated') {
    showHint('github-hint', '资产资料已更新', 'success', { persist: true });
    loadGithubStars();
  } else if (result.state === 'cancelled') {
    showHint('github-hint', '已取消资产刷新', 'warning');
  }
}

function confirmGithubRefresh() {
  if (!githubRefresh?.refreshId) return;
  document.getElementById('github-confirm-refresh').disabled = true;
  if (!sendToAgent({ type: GITHUB_MESSAGE_TYPES.REFRESH_CONFIRM, refreshId: githubRefresh.refreshId })) {
    document.getElementById('github-confirm-refresh').disabled = false;
    showHint('github-hint', runtimeUnavailableMessage(), 'error', { persist: true });
  }
}

function cancelGithubRefresh() {
  if (githubRefresh?.refreshId) sendToAgent({ type: GITHUB_MESSAGE_TYPES.REFRESH_CANCEL, refreshId: githubRefresh.refreshId });
  document.getElementById('github-refresh-panel').hidden = true;
  githubRefresh = null;
}

function applyGithubError(result) {
  document.getElementById('github-login').disabled = !githubIsConfigured;
  document.getElementById('github-load-stars').disabled = !githubIsAuthenticated;
  document.getElementById('github-load-stars').textContent = '读取';
  document.getElementById('github-confirm-refresh').disabled = false;
  document.querySelectorAll('.github-refresh-check').forEach(button => {
    button.disabled = false;
    button.textContent = '检查更新';
  });
  if (String(result.requestType || '') === GITHUB_MESSAGE_TYPES.STARS_REQUEST) githubStarsAutoLoaded = false;
  const isAuthError = String(result.requestType || '').startsWith('github_auth');
  if (
    isAuthError &&
    result.flowId &&
    githubAuthFlow?.flowId &&
    result.flowId !== githubAuthFlow.flowId
  ) return;
  if (isAuthError && !result.transient) clearGithubAuthFlow();
  const retry = result.retryAfter ? `（约 ${result.retryAfter} 秒后重试）` : '';
  const message = result.transient
    ? `${result.message || 'GitHub 网络暂时不可用'}，后台会自动重试${retry}`
    : `${result.message || 'GitHub 操作失败'}${retry}`;
  showHint('github-hint', message, result.transient ? 'warning' : 'error', { persist: true });
  if (result.code === 'auth_expired') requestGithubStatus();
}

function applyTaskStatus(snapshot) {
  const items = Array.isArray(snapshot.items) ? snapshot.items : [];
  const running = Number(snapshot.running || 0);
  const failed = Number(snapshot.failed || 0);
  const cancelled = Number(snapshot.cancelled || 0);
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
  } else if (cancelled > 0 && latest?.stage === 'cancelled') {
    setStatus('tasks', '最近取消', 'warning', formatTaskTime(latest.updatedAt));
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
  for (const task of items.slice(0, 10)) {
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
      detail.textContent = task.stage === 'cancelled'
        ? '任务已取消，可重新提交'
        : task.error || task.hint || '任务失败';
    } else if (task.ok === true && (task.stage === 'done' || task.displayStage === 'done')) {
      const star = task.githubIntegration?.autoStar;
      if (star?.attempted && star.ok === false) {
        detail.textContent = `资产已写入；自动 Star 失败：${star.message || '请检查 GitHub 权限'}`;
        detail.classList.add('warning');
      } else {
        detail.textContent = task.vaultPath ? compactPath(task.vaultPath) : '已写入知识库';
      }
    } else {
      detail.textContent = task.url || '任务已进入队列';
    }
    card.append(head, meta, bar, detail);
    const controls = document.createElement('div');
    controls.className = 'task-actions';
    if (task.ok === null || task.ok === undefined) {
      const cancel = document.createElement('button');
      cancel.type = 'button';
      cancel.className = 'small secondary task-control';
      cancel.dataset.action = 'cancel';
      cancel.dataset.taskId = task.id || '';
      cancel.textContent = '取消';
      controls.appendChild(cancel);
    } else if (task.ok === false) {
      const retry = document.createElement('button');
      retry.type = 'button';
      retry.className = 'small secondary task-control';
      retry.dataset.action = 'retry';
      retry.dataset.taskId = task.id || '';
      retry.dataset.parentId = task.operationId || '';
      retry.textContent = '重试';
      controls.appendChild(retry);
    }
    if (controls.childElementCount) card.appendChild(controls);
    appendDerivedPanel(card, task);
    list.appendChild(card);
  }
}

function appendDerivedPanel(card, task) {
  const derived = Array.isArray(task.derivedTasks) ? task.derivedTasks : [];
  if (!derived.length) return;
  const panel = document.createElement('div');
  panel.className = 'derived-panel';
  const title = document.createElement('div');
  title.className = 'derived-panel-title';
  const summary = task.derivedSummary || {};
  const autoCount = Number(summary.auto_ready || summary.autoReady || 0);
  title.textContent = autoCount > 0
    ? `派生候选 · ${derived.length} 个 · ${autoCount} 个自动`
    : `派生候选 · ${derived.length} 个`;
  panel.appendChild(title);
  for (const item of derived) {
    panel.appendChild(renderDerivedItem(task, item));
  }
  card.appendChild(panel);
}

function renderDerivedItem(task, item) {
  const row = document.createElement('div');
  row.className = `derived-item ${derivedTone(item)}`;
  row.dataset.taskId = task.id || '';
  row.dataset.candidateId = item.id || '';
  row.dataset.derivedStatus = item.status || item.candidateStatus || '';
  row.dataset.targetType = item.targetType || '';
  const head = document.createElement('div');
  head.className = 'derived-head';
  const name = document.createElement('strong');
  name.textContent = compactTaskTitle(item.name || item.targetUrl || item.searchQuery || '派生候选');
  name.title = item.name || item.targetUrl || item.searchQuery || '';
  const status = document.createElement('span');
  status.className = `derived-status ${derivedTone(item)}`;
  status.textContent = derivedStatusLabel(item);
  head.append(name, status);

  const meta = document.createElement('div');
  meta.className = 'derived-meta';
  const score = Number.isFinite(Number(item.score)) ? `${Number(item.score)}分` : '';
  meta.textContent = [
    targetTypeLabel(item.targetType),
    score,
    item.targetUrl || item.searchQuery || ''
  ].filter(Boolean).join(' · ');

  const reason = document.createElement('div');
  reason.className = 'derived-reason';
  reason.textContent = item.reason || '';

  row.append(head, meta);
  if (reason.textContent) row.appendChild(reason);

  const actions = renderDerivedActions(task, item);
  if (actions) row.appendChild(actions);
  return row;
}

function renderDerivedActions(task, item) {
  const status = item.status || item.candidateStatus || '';
  if (['done', 'running', 'queued', 'ignored', 'existing_related'].includes(status)) return null;
  if (!isAgentConnected) return null;
  const key = derivedActionKey(task.id || '', item.id || '');
  const pending = pendingDerivedActions.has(key);
  const actions = document.createElement('div');
  actions.className = 'derived-actions';
  let input = null;
  if (status === 'needs_target' || (!item.targetUrl && ['official_doc', 'web_research'].includes(item.targetType))) {
    input = document.createElement('input');
    input.className = 'derived-url-input';
    input.type = 'url';
    input.placeholder = '补充公开 HTTPS 链接';
    input.value = item.targetUrl || '';
    input.dataset.role = 'derived-url';
    input.setAttribute('aria-label', `为 ${item.name || '派生候选'} 补充目标链接`);
    actions.appendChild(input);
  }
  const confirm = document.createElement('button');
  confirm.className = 'small primary derived-action';
  confirm.type = 'button';
  confirm.textContent = pending ? '处理中' : '确认派生';
  confirm.disabled = pending || Boolean(input && !input.value.trim());
  confirm.setAttribute('aria-label', `确认派生 ${item.name || '派生候选'}`);
  confirm.dataset.action = 'confirm';
  const ignore = document.createElement('button');
  ignore.className = 'small secondary derived-action';
  ignore.type = 'button';
  ignore.textContent = '忽略';
  ignore.disabled = pending;
  ignore.setAttribute('aria-label', `忽略 ${item.name || '派生候选'}`);
  ignore.dataset.action = 'ignore';
  actions.append(confirm, ignore);
  if (input) {
    input.addEventListener('input', () => {
      confirm.disabled = pending || !input.value.trim();
    });
  }
  return actions;
}

function derivedTone(item) {
  const status = item.status || item.candidateStatus || '';
  if (status === 'done' || status === 'existing_related') return 'done';
  if (status === 'failed') return 'failed';
  if (status === 'running' || status === 'queued' || status === 'auto_ready') return 'running';
  if (status === 'needs_target') return 'needs-target';
  if (status === 'ignored') return 'muted';
  return 'candidate';
}

function derivedStatusLabel(item) {
  const status = item.status || item.candidateStatus || '';
  return {
    auto_ready: '自动待派生',
    candidate: '待确认',
    needs_target: '需补链接',
    queued: '已排队',
    running: '执行中',
    done: '已完成',
    failed: '失败',
    ignored: '已忽略',
    existing_related: '已有资产'
  }[status] || status || '候选';
}

function targetTypeLabel(type) {
  return {
    github_project: 'GitHub 资产',
    official_doc: '官方/API 文档',
    web_research: '网页研究'
  }[type] || type || '';
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
  const source = task.source === 'extension_popup' ? '扩展' : 'Agent';
  const updatedAt = formatTaskTime(task.updatedAt || task.createdAt);
  return [stage, elapsed, source, updatedAt].filter(Boolean).join(' · ');
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
    source_identified: '识别来源',
    cookie_availability_checked: '检查 Cookie',
    source_metadata_read: '读取来源信息',
    download_file_validated: '校验下载文件',
    download_files_validated: '校验下载文件',
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
    derived_candidates_ready: '派生候选已生成',
    resolving_target: '解析派生目标',
    target_resolved: '派生目标已解析',
    analyzing_derived_target: '分析派生目标',
    writing_vault: '写入知识库',
    concise_summary_generated: '生成简洁概括',
    complete_content_generated: '整理完整内容',
    ai_analysis_generated: '生成 AI 分析',
    asset_structure_parsed: '解析资产结构',
    asset_fields_validated: '校验资产字段',
    asset_title_selected: '确定标题',
    asset_tags_selected: '确定标签',
    asset_filename_selected: '确定文件名',
    asset_file_written: '写入资产',
    asset_index_updated: '更新索引',
    derived_output_validated: '校验派生内容',
    derived_asset_fields_selected: '确定派生字段',
    derived_asset_file_written: '写入派生资产',
    derived_index_updated: '更新派生索引',
    derived_parent_child_linked: '建立父子关系',
    done: '成功',
    cancelled: '已取消',
    retry_queued: '重试已入队',
    failed: '失败',
    config_error: '配置错误',
    task_invalid: '任务无效'
  }[stage] || stage || '处理中';
}

function submitTaskControl(button) {
  const taskId = button?.dataset.taskId || '';
  const action = button?.dataset.action || '';
  if (!taskId || !['cancel', 'retry'].includes(action)) return;
  button.disabled = true;
  const sent = sendToAgent({
    type: action === 'cancel' ? 'task_cancel' : 'task_retry',
    taskId,
    parentId: button.dataset.parentId || ''
  });
  if (!sent) {
    button.disabled = false;
    showHint('task-hint', runtimeUnavailableMessage(), 'warning');
  }
}

async function loadConfig() {
  const result = await chrome.storage.local.get([
    'apiKey',
    'arkApiKey',
    'llmProvider',
    'provider',
    'arkEndpoint',
    'endpoint',
    'videoAnalysisModelPreset',
    'videoAnalysisModel',
    'model',
    'arkModel',
    'videoChunkConcurrency',
    'serverTaskConcurrency',
    'taskConcurrency',
    'cookieSyncedAt',
    'vaultPath',
    'modelStatus',
    'videoAnalysisStatus'
  ]);
  const provider = normalizeProvider(result.llmProvider || result.provider);
  const info = providerInfo(provider);
  document.getElementById('api-key').value = readStoredApiKey(result);
  setControlValue('provider', provider);
  document.getElementById('endpoint-url').value = result.arkEndpoint || result.endpoint || info.endpoint;
  setControlValue('analysis-model-preset', readStoredModelPreset(result));
  setControlValue('analysis-model-id', readStoredModelId(result));
  setControlValue('task-concurrency', normalizeTaskConcurrency(result.serverTaskConcurrency || result.taskConcurrency));
  setControlValue('chunk-concurrency', normalizeChunkConcurrency(result.videoChunkConcurrency));
  updateVideoSettingsSummary();
  setupState.api.configured = Boolean(readStoredApiKey(result));
  setupState.vault.configured = Boolean(result.vaultPath);
  setupState.cookie.configured = Boolean(result.cookieSyncedAt);
  if (result.vaultPath) {
    const currentPath = document.getElementById('vault-current-path');
    currentPath.textContent = '已配置，待 Agent 检查';
    currentPath.title = result.vaultPath;
    setStatus('vault', '已配置，待检查', 'warning');
  }
  if (result.videoAnalysisStatus) applyVideoStatus(result.videoAnalysisStatus);
  if (readStoredApiKey(result)) setStatus('api', '待测试', 'warning');
  renderFirstRunGuide();
}

async function collectConfig() {
  const provider = normalizeProvider(document.getElementById('provider').value);
  const apiKey = document.getElementById('api-key').value.trim();
  const endpointResult = normalizeEndpoint(document.getElementById('endpoint-url').value, provider);
  if (!endpointResult.ok) throw new Error(endpointResult.message);
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
    setupState.api.configured = Boolean(config.apiKey);
    setupState.api.verified = false;
    setupState.api.checkState = 'configured';
    if (config.apiKey) setStatus('api', '已配置，待检查', 'warning');
    renderFirstRunGuide();
    const agentConfig = await buildAgentConfig({ requireApiKey: false });
    const sent = sendToAgent({ type: 'config_update', data: agentConfig });
    if (!config.apiKey) {
      setStatus('api', '缺少 Key', 'warning');
      showHint('config-hint', sent ? 'API 设置已保存，填写 Key 后再测试连接' : 'API 设置已本地保存', 'warning', { persist: true });
      return;
    }
    showHint('config-hint', sent ? 'API 设置已发送到 Agent' : `${runtimeUnavailableMessage()} API 设置仅保存在扩展本地。`, sent ? 'success' : 'warning', { persist: !sent });
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
    showHint('video-config-hint', sent ? '拆解设置已同步' : `${runtimeUnavailableMessage()} 拆解设置仅保存在扩展本地。`, sent ? 'success' : 'warning', { persist: !sent });
  } catch (err) {
    showHint('video-config-hint', err.message || '拆解设置保存失败', 'error', { persist: true });
  }
}

async function sendConfigToAgent({ silent = false, requireApiKey = false } = {}) {
  try {
    const data = await buildAgentConfig({ requireApiKey });
    if (!data) return false;
    const sent = sendToAgent({ type: 'config_update', data });
    if (!sent && !silent) showHint('config-hint', runtimeUnavailableMessage(), 'warning', { persist: true });
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
        setStatus('api', runtimeCompatibility ? '版本未通过' : 'Agent 未连接', 'offline');
        showHint('model-hint', response?.message || runtimeUnavailableMessage(), 'warning', { persist: true });
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

async function submitDouyinIngestFromPopup() {
  const shareInput = document.getElementById('douyin-share-text');
  const shareText = shareInput?.value.trim() || '';
  const hintId = 'ingest-hint';
  const buttons = [document.getElementById('share-knowledge')].filter(Boolean);
  if (!isAgentConnected) {
    showHint(hintId, '请先启动 Agent 服务', 'warning', { persist: true });
    openSettingsDetail('agent-settings');
    return;
  }
  if (!runtimeCompatibility?.canOperate) {
    showHint(hintId, runtimeUnavailableMessage(), runtimeCompatibility?.tone === 'offline' ? 'error' : 'warning', { persist: true });
    openSettingsDetail('agent-settings');
    return;
  }
  buttons.forEach(btn => { btn.disabled = true; });
  showHint(hintId, shareText ? '正在从分享文案提取链接并提交' : '正在识别当前抖音页面', 'warning', { persist: true });
  try {
    const response = await chrome.runtime.sendMessage({
      action: 'submitDouyinIngestFromPopup',
      shareText
    });
    if (response?.ok) {
      showHint(hintId, '知识入库任务已进入队列', 'success');
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

function requestVaultLifecycle(type, payload, pendingCopy) {
  const sent = sendToAgent({
    type,
    requestId: `vault-${type}-${Date.now()}`,
    source: 'extension_popup',
    data: payload
  });
  if (!sent) {
    setStatus('vault', runtimeCompatibility ? '版本未通过' : 'Agent 未连接', 'offline');
    showHint('vault-hint', runtimeUnavailableMessage(), 'warning', { persist: true });
    return false;
  }
  document.getElementById('select-knowledge-base').disabled = true;
  vaultWorkflow.pendingType = type;
  setStatus('vault', pendingCopy, 'warning');
  showHint('vault-hint', pendingCopy, 'warning', { persist: true });
  return true;
}

function selectVaultFolder() {
  vaultWorkflow = { stage: 'selecting', selectionId: '', pendingType: '' };
  setupState.vault.verified = false;
  renderFirstRunGuide();
  requestVaultLifecycle(
    VAULT_MESSAGE_TYPES.SELECT_FOLDER,
    {},
    '正在打开系统文件夹选择窗口'
  );
}

function closeVaultConfirmation() {
  document.getElementById('vault-confirmation-modal').hidden = true;
  vaultWorkflow = { stage: 'idle', selectionId: '', pendingType: '' };
  document.getElementById('select-knowledge-base').disabled = false;
}

function showVaultConfirmation(status) {
  const selection = status.selection || {};
  vaultWorkflow = {
    stage: 'confirming',
    selectionId: String(selection.selectionId || ''),
    pendingType: ''
  };
  document.getElementById('vault-selection-folder').textContent = selection.folderName || '所选文件夹';
  document.getElementById('vault-confirmation-copy').textContent = status.message ||
    '确认后只会补齐缺失的 Agent-wiki 必要结构，已有内容不会被覆盖。';
  showHint('vault-selection-hint', '', 'warning', { persist: true });
  const modal = document.getElementById('vault-confirmation-modal');
  modal.hidden = false;
  const button = document.getElementById('confirm-vault-selection');
  button.disabled = !vaultWorkflow.selectionId;
  requestAnimationFrame(() => button.focus({ preventScroll: true }));
}

function confirmVaultSelection() {
  if (!vaultWorkflow.selectionId) return;
  const sent = requestVaultLifecycle(
    VAULT_MESSAGE_TYPES.SELECT_CONFIRM,
    { selectionId: vaultWorkflow.selectionId },
    '正在安全初始化所选文件夹'
  );
  if (sent) {
    document.getElementById('confirm-vault-selection').disabled = true;
    showHint('vault-selection-hint', '正在确认所选文件夹', 'warning', { persist: true });
  }
}

function applyVaultLifecycleStatus(status) {
  const state = String(status.state || status.status || '').toLowerCase();
  const operation = String(status.operation || '').toLowerCase();
  document.getElementById('select-knowledge-base').disabled = false;

  if (state === 'cancelled') {
    vaultWorkflow = { stage: 'idle', selectionId: '', pendingType: '' };
    showHint('vault-hint', status.message || '已取消选择，知识库保持不变。', 'warning', { persist: true });
    return;
  }

  if (state === 'confirmation_required') {
    showVaultConfirmation(status);
    return;
  }

  if (status.ok && status.activeVault?.vaultPath) {
    applyVaultStatus({ ...status, state: 'ready' });
    closeVaultConfirmation();
    return;
  }

  if (operation === 'scan') {
    if (state === 'selection_required' || state === 'ambiguous') {
      applyVaultStatus(status);
    }
    return;
  }

  const message = status.message || '知识库选择失败，请重试。';
  showHint('vault-hint', message, 'error', { persist: true });
  if (!document.getElementById('vault-confirmation-modal').hidden) {
    showHint('vault-selection-hint', message, 'error', { persist: true });
    document.getElementById('confirm-vault-selection').disabled = false;
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
    setupState.cookie.configured = true;
    setupState.cookie.pending = true;
    setupState.cookie.verified = false;
    setupState.cookie.checkState = 'pending';
    const sent = sendToAgent({
      type: 'cookie_update',
      platform: 'douyin',
      data: cookieText
    });
    setStatus('cookie', sent ? '等待确认' : '待同步', 'warning', formatDateTime(grabbedAt));
    showHint(
      'cookie-hint',
      sent ? 'Cookie 已发送，等待 Agent 确认' : `${runtimeUnavailableMessage()} Cookie 已暂存在扩展中，未发送给服务。`,
      'warning',
      { persist: true }
    );
    renderFirstRunGuide();
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
      setupState.cookie.configured = true;
      setupState.cookie.pending = true;
      setupState.cookie.verified = false;
      setupState.cookie.checkState = 'pending';
      setStatus('cookie', '待同步', 'warning', formatDateTime(pending.pendingCookieGrabbedAt));
      renderFirstRunGuide();
      return;
    }
  }
  if (local.cookieSyncedAt) {
    setupState.cookie.configured = true;
    setupState.cookie.pending = false;
    if (!serviceCanVerify()) setupState.cookie.verified = false;
    setStatus(
      'cookie',
      setupState.cookie.verified ? '已同步' : '已同步，待检查',
      setupState.cookie.verified ? 'online' : 'warning',
      formatDateTime(local.cookieSyncedAt)
    );
    document.getElementById('cookie-settings-copy').textContent = `上次同步：${formatDateTime(local.cookieSyncedAt)}`;
    renderFirstRunGuide();
    return;
  }
  setupState.cookie.configured = false;
  setupState.cookie.pending = false;
  setupState.cookie.verified = false;
  setStatus('cookie', '未同步', isAgentConnected ? 'warning' : 'offline');
  renderFirstRunGuide();
}

function invalidateServiceDependentStatuses() {
  setupState.api.verified = false;
  setupState.vault.verified = false;
  setupState.cookie.verified = false;
  setupState.github.verified = false;
  setupState.api.checkState = 'unchecked';
  setupState.vault.checkState = 'unchecked';
  if (!setupState.cookie.pending) setupState.cookie.checkState = 'unchecked';
  setStatus('api', setupState.api.configured ? '已配置，待检查' : '未配置', 'warning');
  setStatus('vault', setupState.vault.configured ? '已配置，待检查' : '待识别', 'warning');
  setStatus(
    'cookie',
    setupState.cookie.pending ? '已抓取，待同步' : setupState.cookie.configured ? '已同步，待检查' : '未同步',
    'warning'
  );
  applyGithubStatus({
    state: 'unavailable',
    configured: githubIsConfigured,
    authenticated: false
  });
  renderFirstRunGuide();
}

function updateConnectionStatus(connected) {
  isAgentConnected = connected;
  if (connected && !runtimeCompatibility) {
    invalidateServiceDependentStatuses();
    setStatus('agent', '正在校验版本', 'warning');
    document.getElementById('agent-settings-copy').textContent = '本地服务已连接，正在校验版本';
  } else if (!connected) {
    invalidateServiceDependentStatuses();
    setStatus('agent', '未连接', 'offline');
    document.getElementById('agent-settings-copy').textContent = '本地 Agent 未连接';
    document.getElementById('service-version').textContent = '待连接';
    document.getElementById('runtime-protocol-version').textContent = '待校验';
    document.getElementById('runtime-source-version').textContent = '待校验';
    showHint('runtime-version-hint', '', '');
  }
  refreshCookieStatusFromStorage();
  renderFirstRunGuide();
}

function compactStatusText(kind, type, detail = '') {
  const status = type || 'warning';
  if (kind === 'cookie') {
    if (status === 'online') return '已同步';
    if (status === 'offline') return '未同步';
    if (String(detail).includes('待检查')) return '待检查';
    return '待同步';
  }
  if (status === 'online') return '已连接';
  if (status === 'offline') return '未连接';
  if (kind === 'agent') return '检查中';
  if (kind === 'vault') return String(detail).includes('待检查') ? '待检查' : '待识别';
  return '待检查';
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
    label.textContent = compactStatusText(kind, normalized, text);
    label.className = normalized;
  }
  if (menuLabel) {
    menuLabel.textContent = time ? `${text} · ${time}` : text;
    menuLabel.className = normalized;
  }
}

function releaseFocusBeforeViewChange(viewId) {
  const activeElement = document.activeElement;
  const activeView = activeElement?.closest?.('.view');
  if (activeView && activeView.id !== viewId && typeof activeElement.blur === 'function') {
    activeElement.blur();
  }
}

function setView(viewId) {
  releaseFocusBeforeViewChange(viewId);
  document.body.dataset.view = viewId;
  Object.values(POPUP_VIEWS).forEach(id => {
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

function openSettingsIndex({ persist = true, focus = true } = {}) {
  lastSettingsTrigger = document.activeElement;
  hideAllDetailSections();
  updateVideoSettingsSummary();
  setView(POPUP_VIEWS.SETTINGS_INDEX);
  if (persist) void persistPopupRoute({ view: POPUP_VIEWS.SETTINGS_INDEX });
  if (focus) requestAnimationFrame(() => document.getElementById(POPUP_VIEWS.SETTINGS_INDEX)?.focus({ preventScroll: true }));
}

function openSettingsDetail(targetId, { persist = true, focus = true } = {}) {
  lastSettingsTrigger = document.activeElement;
  const target = document.getElementById(targetId || 'api-settings') || document.getElementById('api-settings');
  hideAllDetailSections();
  target.hidden = false;
  target.classList.add('active-detail');
  const title = target.dataset.title || SETTINGS_DETAIL_TITLES[target.id] || '设置';
  document.getElementById('settings-detail-title').textContent = title;
  setView(POPUP_VIEWS.SETTINGS_DETAIL);
  if (persist) void persistPopupRoute({ view: POPUP_VIEWS.SETTINGS_DETAIL, detailId: target.id });
  if (focus) requestAnimationFrame(() => target.focus({ preventScroll: true }));
}

function openGithubPage({ persist = true, focus = true } = {}) {
  lastSettingsTrigger = document.activeElement;
  hideAllDetailSections();
  setView(POPUP_VIEWS.GITHUB);
  requestGithubStatus();
  if (persist) void persistPopupRoute({ view: POPUP_VIEWS.GITHUB });
  if (focus) requestAnimationFrame(() => document.getElementById(POPUP_VIEWS.GITHUB)?.focus({ preventScroll: true }));
}

async function restorePopupRoute() {
  const storage = popupRouteStorage();
  if (!storage) return;
  try {
    const stored = await storage.get(POPUP_ROUTE_STORAGE_KEY);
    const route = sanitizePopupRoute(stored[POPUP_ROUTE_STORAGE_KEY]);
    if (route.view === POPUP_VIEWS.SETTINGS_INDEX) {
      openSettingsIndex({ persist: false, focus: false });
    } else if (route.view === POPUP_VIEWS.SETTINGS_DETAIL) {
      openSettingsDetail(route.detailId, { persist: false, focus: false });
    } else if (route.view === POPUP_VIEWS.GITHUB) {
      openGithubPage({ persist: false, focus: false });
    }
  } catch (err) {
    debugLog('[Agent-wiki] 无法恢复 popup route:', err);
  }
}

function closeToHome() {
  hideAllDetailSections();
  setView(POPUP_VIEWS.HOME);
  void persistPopupRoute({ view: POPUP_VIEWS.HOME });
  const triggerIsHiddenMenu = lastSettingsTrigger?.closest?.('#settings-index-view');
  if (lastSettingsTrigger && !triggerIsHiddenMenu && typeof lastSettingsTrigger.focus === 'function') {
    lastSettingsTrigger.focus();
  } else {
    document.getElementById('open-settings').focus();
  }
}

function closeSettingsDetailToIndex({ focus = true } = {}) {
  const trigger = lastSettingsTrigger?.closest?.('#settings-index-view') ? lastSettingsTrigger : null;
  openSettingsIndex({ focus: false });
  if (!focus) return;
  requestAnimationFrame(() => {
    (trigger || document.getElementById(POPUP_VIEWS.SETTINGS_INDEX))?.focus({ preventScroll: true });
  });
}

function closeGithubToHome() {
  lastSettingsTrigger = document.getElementById('open-github');
  closeToHome();
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

function dateFromTimestamp(value) {
  if (!value) return '';
  const raw = typeof value === 'number' ? value : Number.NaN;
  const normalized = Number.isFinite(raw) && raw > 0 && raw < 1_000_000_000_000 ? raw * 1000 : value;
  const date = new Date(normalized);
  return Number.isNaN(date.getTime()) ? null : date;
}

function formatDateTime(value, now = new Date()) {
  const date = dateFromTimestamp(value);
  if (!date) return '';
  const pad = number => String(number).padStart(2, '0');
  const year = date.getFullYear() !== now.getFullYear() ? `${date.getFullYear()}年` : '';
  return `${year}${pad(date.getMonth() + 1)}月${pad(date.getDate())}日 ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function formatTaskTime(value) {
  return formatDateTime(value);
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

function bindDerivedTaskActions() {
  const list = document.getElementById('settings-task-list');
  if (!list) return;
  list.addEventListener('click', event => {
    const taskControl = event.target.closest('.task-control');
    if (taskControl) {
      submitTaskControl(taskControl);
      return;
    }
    const button = event.target.closest('.derived-action');
    if (!button) return;
    const row = button.closest('.derived-item');
    submitDerivedAction(row, button.dataset.action);
  });
}

function bindGithubControls() {
  bindClick('refresh-github-status', requestGithubStatus);
  bindClick('github-login', startGithubAuthorization);
  bindClick('github-logout', logoutGithub);
  bindClick('github-open-authorization', openGithubAuthorization);
  bindClick('github-copy-code', copyGithubAuthorizationCode);
  bindClick('github-cancel-authorization', cancelGithubAuthorization);
  bindClick('github-load-stars', () => loadGithubStars());
  bindClick('github-load-more-stars', () => loadGithubStars({ append: true }));
  bindClick('github-import-selected', importSelectedGithubStars);
  bindClick('github-cancel-import', cancelGithubImport);
  bindClick('github-confirm-refresh', confirmGithubRefresh);
  bindClick('github-cancel-refresh', cancelGithubRefresh);
  document.getElementById('github-auto-star').addEventListener('change', saveGithubAutoStar);
  document.getElementById('github-select-all').addEventListener('change', event => {
    for (const repo of githubStars) {
      const key = githubRepositoryKey(repo);
      if (!key) continue;
      if (event.target.checked) githubSelected.add(key);
      else githubSelected.delete(key);
    }
    renderGithubRepositories('github-stars-list', githubStars, { selectable: true });
    updateGithubSelection();
  });
  document.getElementById('github-stars-list').addEventListener('change', event => {
    const checkbox = event.target.closest('.github-repo-select');
    if (!checkbox) return;
    const row = checkbox.closest('.github-repo-row');
    const key = String(row?.dataset.repositoryId || row?.dataset.fullName || '').toLowerCase();
    if (!key) return;
    if (checkbox.checked) githubSelected.add(key);
    else githubSelected.delete(key);
    updateGithubSelection();
  });
  document.getElementById('github-view').addEventListener('click', event => {
    const button = event.target.closest('.github-refresh-check');
    if (button) checkGithubRefresh(button.closest('.github-repo-row'));
  });
}

function syncModelPresetFromInput() {
  setControlValue('analysis-model-preset', presetFromModel(document.getElementById('analysis-model-id').value));
  updateVideoSettingsSummary();
}

document.addEventListener('DOMContentLoaded', async () => {
  initColorScheme();
  document.getElementById('extension-version').textContent = `v${EXTENSION_VERSION || '未知'}`;
  document.getElementById('agent-start-command').textContent = AGENT_START_COMMAND;
  document.getElementById('open-settings').addEventListener('click', openSettingsIndex);
  document.getElementById('open-github').addEventListener('click', () => openGithubPage());
  document.getElementById('back-settings-index').addEventListener('click', closeSettingsDetailToIndex);
  document.getElementById('back-home-from-index').addEventListener('click', closeToHome);
  document.getElementById('back-home-from-github').addEventListener('click', closeGithubToHome);
  document.getElementById('status-tasks').addEventListener('click', () => openSettingsDetail('task-settings'));
  for (const onboardingRoot of ['first-run-guide', 'first-run-reminder']) {
    document.getElementById(onboardingRoot)?.addEventListener('click', event => {
      const button = event.target.closest('[data-onboarding-action]');
      if (button && !button.disabled && button.dataset.onboardingAction) {
        runOnboardingAction(button.dataset.onboardingAction);
      }
    });
  }
  bindClick('copy-agent-start-command', copyAgentStartCommand);
  bindClick('retry-agent-connection', retryAgentConnection);
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
  bindClick('select-knowledge-base', selectVaultFolder);
  bindClick('close-vault-confirmation', closeVaultConfirmation);
  bindClick('cancel-vault-confirmation', closeVaultConfirmation);
  bindClick('confirm-vault-selection', confirmVaultSelection);
  document.getElementById('vault-confirmation-modal').addEventListener('click', event => {
    if (event.target === event.currentTarget) closeVaultConfirmation();
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !document.getElementById('vault-confirmation-modal').hidden) closeVaultConfirmation();
  });
  document.getElementById('grab-cookie').addEventListener('click', grabCookie);
  bindClick('share-knowledge', submitDouyinIngestFromPopup);
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
  bindDerivedTaskActions();
  bindGithubControls();
  if (!hasExtensionApis()) {
    renderDouyinPreview({
      ok: false,
      source: 'current',
      message: '打开扩展后自动识别抖音内容'
    });
    return;
  }
  await restorePopupRoute();
  renderFirstRunGuide();
  await loadConfig();
  await refreshCookieStatusFromStorage();
  startDouyinPreviewLoop();
  connectWebSocket();
});
