// background.js - control-plane WebSocket background service
// 职责：
//   1. 维护 WebSocket 连接
//   2. 接收配置/Cookie 同步确认
//   3. 安装时初始化

importScripts('runtime-version.js');

const RuntimeVersion = self.AgentWikiRuntime;
const WS_URL = 'ws://127.0.0.1:8765';
const MODEL_HEALTH_ALARM = 'agent-wiki-model-health';
const NOTIFICATION_ICON = 'icons/icon-128.png';
const DEBUG_LOGS = false;
const PROVIDERS = {
  doubao: {
    endpoint: 'https://ark.cn-beijing.volces.com/api/v3',
    model: 'doubao-seed-2-0-lite-260428',
    strategyModel: 'doubao-seed-2-0-mini-260428'
  }
};
const DEFAULT_PROVIDER = 'doubao';
const MODEL_PRESETS = {
  lite: 'doubao-seed-2-0-lite-260428',
  mini: 'doubao-seed-2-0-mini-260428'
};
const DEFAULT_MODEL_PRESET = 'lite';
const DEFAULT_TASK_CONCURRENCY = 2;
const DEFAULT_CHUNK_CONCURRENCY = 2;
const MIN_TASK_CONCURRENCY = 1;
const MAX_TASK_CONCURRENCY = 4;
const TRUSTED_ARK_HOSTS = new Set(['ark.cn-beijing.volces.com']);
const CURRENT_DOUYIN_VIDEO_ACTION = 'getCurrentDouyinVideoV3';
const DOUYIN_URL_PATTERN = /https?:\/\/(?:v\.douyin\.com\/[A-Za-z0-9_-]+\/?|(?:www\.)?(?:douyin|iesdouyin)\.com\/(?:video|share\/video|note|share\/note)\/\d+(?:[/?#][^\s"'<>，。！？、；：）)]*)?)/i;
const DOUYIN_TAB_PATTERNS = ['https://douyin.com/*', 'https://*.douyin.com/*'];
let ws = null;
let reconnectTimer = null;
let pendingModelHealthCheck = false;
let pendingModelConfigSync = false;
let pendingTaskRequests = new Map();
let runtimeCompatibility = null;
let vaultScanRequestedForConnection = false;

function debugLog(...args) {
  if (DEBUG_LOGS) console.log(...args);
}

function notifyUser(title, message) {
  if (!chrome.notifications) {
    debugLog(`[Librarian BG] ${title}: ${message}`);
    return;
  }
  chrome.notifications.create({
    type: 'basic',
    iconUrl: NOTIFICATION_ICON,
    title,
    message
  });
}

function normalizeProvider(value) {
  return PROVIDERS[value] ? value : DEFAULT_PROVIDER;
}

function providerStorageKeys(value) {
  return { apiKey: 'arkApiKey', model: 'arkModel', strategyModel: 'arkStrategyModel' };
}

function normalizeTaskConcurrency(value) {
  const parsed = Number.parseInt(value, 10);
  if (!Number.isFinite(parsed)) return DEFAULT_TASK_CONCURRENCY;
  return Math.max(MIN_TASK_CONCURRENCY, Math.min(MAX_TASK_CONCURRENCY, parsed));
}

function normalizeChunkConcurrency(value) {
  const parsed = Number.parseInt(value, 10);
  if (!Number.isFinite(parsed)) return DEFAULT_CHUNK_CONCURRENCY;
  return Math.max(1, Math.min(4, parsed));
}

function normalizeEndpoint(value, provider) {
  const endpoint = String(value || PROVIDERS[provider].endpoint).trim().replace(/\/+$/, '');
  try {
    const url = new URL(endpoint);
    if (url.protocol !== 'https:' || !url.hostname || url.username || url.password) {
      throw new Error('Endpoint 必须是有效 HTTPS 地址，且不能包含账号密码');
    }
    if (!TRUSTED_ARK_HOSTS.has(url.hostname.toLowerCase())) {
      throw new Error('Endpoint 必须使用可信 Ark 官方域名');
    }
    if (endpoint.endsWith('/api/plan/v3')) {
      throw new Error('Agent Plan endpoint 不能作为普通 Ark API 使用');
    }
    return endpoint;
  } catch (err) {
    if (err instanceof Error && (
      err.message.includes('Endpoint 必须') ||
      err.message.includes('Agent Plan endpoint')
    )) {
      throw err;
    }
    throw new Error('Endpoint URL 格式不正确');
  }
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
  const preset = normalizeModelPreset(source.videoAnalysisModelPreset);
  const explicit = ownValue(source, 'videoAnalysisModel');
  if (explicit !== undefined && explicit !== null && String(explicit).trim()) {
    return String(explicit).trim();
  }
  const providerValue = ownValue(source, keys.model);
  if (providerValue !== undefined && providerValue !== null) {
    const value = String(providerValue).trim();
    return value || MODEL_PRESETS[preset] || MODEL_PRESETS[DEFAULT_MODEL_PRESET];
  }
  const legacy = String(source.model || '').trim();
  return legacy || MODEL_PRESETS[preset] || MODEL_PRESETS[DEFAULT_MODEL_PRESET];
}

function readStoredStrategyModel(source, provider) {
  const keys = providerStorageKeys(provider);
  const providerValue = ownValue(source, keys.strategyModel);
  const defaultStrategy = PROVIDERS[provider].strategyModel;
  if (providerValue !== undefined && providerValue !== null) {
    const value = String(providerValue).trim();
    return value === defaultStrategy ? value : defaultStrategy;
  }
  const legacy = String(source.strategyModel || source.videoStrategyModel || '').trim();
  return legacy === defaultStrategy ? legacy : defaultStrategy;
}

function buildAgentConfig(config) {
  const provider = normalizeProvider(config.llmProvider || config.provider);
  const defaults = PROVIDERS[provider];
  const keys = providerStorageKeys(provider);
  const apiKey = readStoredApiKey(config, provider);
  const model = readStoredModel(config, provider) || defaults.model;
  const strategyModel = readStoredStrategyModel(config, provider) || defaults.strategyModel;
  const modelPreset = presetFromModel(model);
  const endpoint = normalizeEndpoint(config.arkEndpoint || config.endpoint, provider);
  const taskConcurrency = normalizeTaskConcurrency(config.serverTaskConcurrency || config.taskConcurrency || config.task_concurrency);
  const chunkConcurrency = normalizeChunkConcurrency(config.videoChunkConcurrency || config.chunkConcurrency);
  const data = {
    llm: {
      provider,
      apiKey,
      endpoint
    },
    videoAnalysis: {
      modelPreset,
      analyzerModel: model,
      strategyModel,
      chunkConcurrency
    },
    server: {
      taskConcurrency
    },
    // Flat fields are kept for old servers/clients.
    provider,
    apiKey,
    [keys.apiKey]: apiKey,
    model,
    [keys.model]: model,
    strategyModel,
    [keys.strategyModel]: strategyModel,
    taskConcurrency,
    serverTaskConcurrency: taskConcurrency,
    videoChunkConcurrency: chunkConcurrency,
    endpoint,
    arkEndpoint: endpoint
  };
  return data;
}

async function storedAgentConfig() {
  const config = await chrome.storage.local.get([
    'provider',
    'llmProvider',
    'apiKey',
    'arkApiKey',
    'arkEndpoint',
    'endpoint',
    'model',
    'arkModel',
    'videoAnalysisModelPreset',
    'videoAnalysisModel',
    'strategyModel',
    'arkStrategyModel',
    'videoStrategyModel',
    'taskConcurrency',
    'serverTaskConcurrency',
    'videoChunkConcurrency'
  ]);
  return buildAgentConfig(config);
}

// ─────────────────────────────────────────
// WebSocket 连接管理
// ─────────────────────────────────────────

function connectWebSocket() {
  debugLog('[Librarian BG] 连接 WebSocket:', WS_URL);

  if (ws && (ws.readyState === WebSocket.CONNECTING || ws.readyState === WebSocket.OPEN)) {
    return;
  }

  clearTimeout(reconnectTimer);

  try {
    const socket = new WebSocket(WS_URL);
    ws = socket;

    socket.onopen = () => {
      if (ws !== socket) {
        socket.close();
        return;
      }
      debugLog('[Librarian BG] WebSocket 已连接');
      clearTimeout(reconnectTimer);
      runtimeCompatibility = null;
      vaultScanRequestedForConnection = false;

      socket.send(JSON.stringify(withOperationContext(
        RuntimeVersion.buildHandshake('agent-wiki-background')
      )));
    };

    socket.onmessage = (event) => {
      try {
        handleAgentMessage(JSON.parse(event.data));
      } catch (err) {
        console.error('[Librarian BG] 消息解析失败:', err);
      }
    };

    socket.onclose = () => {
      if (ws !== socket) return;
      debugLog('[Librarian BG] WebSocket 已断开，3秒后重连');
      ws = null;
      runtimeCompatibility = null;
      vaultScanRequestedForConnection = false;
      reconnectTimer = setTimeout(connectWebSocket, 3000);
    };

    socket.onerror = () => {
      debugLog('[Librarian BG] WebSocket 暂不可用，等待 close 后重连');
    };

  } catch (err) {
    debugLog('[Librarian BG] WebSocket 连接失败:', err);
    ws = null;
    reconnectTimer = setTimeout(connectWebSocket, 3000);
  }
}

function sendToAgent(data) {
  const payload = withOperationContext(data);
  if (!RuntimeVersion.canSendMessage(payload?.type, runtimeCompatibility)) {
    return false;
  }
  const socket = ws;
  if (socket && socket.readyState === WebSocket.OPEN) {
    try {
      socket.send(JSON.stringify(payload));
      return true;
    } catch (err) {
      debugLog('[Librarian BG] WebSocket 发送失败:', err);
      if (ws === socket) {
        ws = null;
      }
      reconnectTimer = setTimeout(connectWebSocket, 3000);
    }
  }
  return false;
}

function makeRequestId() {
  return `${Date.now()}-${Math.random().toString(16).slice(2, 10)}`;
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
  payload.requestId = payload.requestId || makeRequestId();
  return payload;
}

function waitForTaskAck(requestId, timeoutMs = 10000) {
  return new Promise((resolve) => {
    const timer = setTimeout(() => {
      pendingTaskRequests.delete(requestId);
      resolve({
        ok: false,
        message: 'Agent 未确认任务，请稍后查看任务列表。'
      });
    }, timeoutMs);
    pendingTaskRequests.set(requestId, { resolve, timer });
  });
}

function resolveTaskAck(msg) {
  const requestId = msg.requestId || msg.request_id;
  if (!requestId || !pendingTaskRequests.has(requestId)) {
    return false;
  }
  const pending = pendingTaskRequests.get(requestId);
  clearTimeout(pending.timer);
  pendingTaskRequests.delete(requestId);
  if (msg.type === 'task_accepted') {
    pending.resolve({
      ok: true,
      task: msg.task,
      message: msg.message || '任务已进入队列'
    });
  } else {
    pending.resolve({
      ok: false,
      reason: msg.reason,
      message: msg.message || 'Agent 拒绝了任务'
    });
  }
  return true;
}

async function sendModelHealthCheck() {
  let data = null;
  try {
    data = await storedAgentConfig();
    if (!data) {
      pendingModelHealthCheck = false;
      return false;
    }
  } catch (err) {
    pendingModelHealthCheck = false;
    chrome.storage.local.set({
      modelStatus: {
        ok: false,
        state: 'error',
        message: err.message || '模型配置无效',
        checkedAt: new Date().toISOString()
      }
    });
    return false;
  }

  if (!ws || ws.readyState !== WebSocket.OPEN) {
    pendingModelHealthCheck = true;
    connectWebSocket();
    return true;
  }

  pendingModelHealthCheck = false;
  return sendToAgent({ type: 'model_check', data });
}

async function sendModelConfigAndHealthCheck() {
  let data = null;
  try {
    data = await storedAgentConfig();
    if (!data) {
      pendingModelConfigSync = false;
      pendingModelHealthCheck = false;
      return false;
    }
  } catch (err) {
    pendingModelConfigSync = false;
    pendingModelHealthCheck = false;
    chrome.storage.local.set({
      modelStatus: {
        ok: false,
        state: 'error',
        message: err.message || '模型配置无效',
        checkedAt: new Date().toISOString()
      }
    });
    return false;
  }

  if (!ws || ws.readyState !== WebSocket.OPEN) {
    pendingModelConfigSync = true;
    connectWebSocket();
    return true;
  }

  pendingModelConfigSync = false;
  pendingModelHealthCheck = false;
  const configSent = sendToAgent({ type: 'config_update', data });
  const healthSent = sendToAgent({ type: 'model_check', data });
  return configSent && healthSent;
}

function ensureModelHealthAlarm() {
  chrome.alarms.create(MODEL_HEALTH_ALARM, {
    periodInMinutes: 10
  });
}

function cleanExtractedUrl(value) {
  return String(value || '')
    .trim()
    .replace(/[，。,.!！)）\]】>]+$/g, '');
}

function isPotentialIngestDouyinUrl(value) {
  try {
    const url = new URL(value);
    const host = url.hostname.toLowerCase();
    const path = url.pathname.toLowerCase();
    if (host === 'v.douyin.com') return true;
    if (
      host === 'douyin.com' ||
      host.endsWith('.douyin.com') ||
      host === 'iesdouyin.com' ||
      host.endsWith('.iesdouyin.com')
    ) {
      return /\/(?:share\/)?(?:video|note)\/\d{8,}/i.test(path) ||
        url.searchParams.has('modal_id') ||
        url.searchParams.has('aweme_id') ||
        url.searchParams.has('item_id') ||
        url.searchParams.has('itemId') ||
        url.searchParams.has('awemeId');
    }
  } catch (_err) {
    // ignore
  }
  return false;
}

function inferDouyinUrlType(value) {
  try {
    const pathname = new URL(value).pathname.toLowerCase();
    if (pathname.includes('/note/')) return 'note';
    if (pathname.includes('/video/')) return 'video';
  } catch (_err) {
    // ignore
  }
  return 'share';
}

function makeDouyinUrlCandidate(url, method, score, raw) {
  const videoMatch = url.match(/(?:https?:\/\/[^/\s]+)?\/(?:share\/)?video\/(\d{8,})/i);
  if (videoMatch) {
    return makeDouyinCandidate(videoMatch[1], 'video', method, score, raw || url);
  }
  const noteMatch = url.match(/(?:https?:\/\/[^/\s]+)?\/(?:share\/)?note\/(\d{8,})/i);
  if (noteMatch) {
    return makeDouyinCandidate(noteMatch[1], 'note', method, score, raw || url);
  }
  const queryMatch = url.match(/[?&](?:modal_id|aweme_id|item_id|itemId|awemeId)=(\d{8,})/i);
  if (queryMatch) {
    return makeDouyinCandidate(queryMatch[1], 'video', method, score, raw || url);
  }
  return {
    ok: true,
    awemeId: '',
    type: inferDouyinUrlType(url),
    url,
    method,
    score,
    raw: String(raw || url).slice(0, 300)
  };
}

function candidateFromText(value, method, score) {
  const text = String(value || '').trim();
  if (!text) return null;

  if (/^\d{8,}$/.test(text)) {
    return makeDouyinCandidate(text, 'video', method, score, text);
  }

  const videoMatch = text.match(/(?:https?:\/\/[^/\s]+)?\/(?:share\/)?video\/(\d{8,})/i);
  if (videoMatch) {
    return makeDouyinCandidate(videoMatch[1], 'video', method, score, text);
  }

  const noteMatch = text.match(/(?:https?:\/\/[^/\s]+)?\/(?:share\/)?note\/(\d{8,})/i);
  if (noteMatch) {
    return makeDouyinCandidate(noteMatch[1], 'note', method, score, text);
  }

  const queryMatch = text.match(/[?&](?:modal_id|aweme_id|item_id|itemId|awemeId)=(\d{8,})/i);
  if (queryMatch) {
    return makeDouyinCandidate(queryMatch[1], 'video', method, score, text);
  }

  const douyinUrlMatch = text.match(DOUYIN_URL_PATTERN);
  if (douyinUrlMatch) {
    return makeDouyinUrlCandidate(cleanExtractedUrl(douyinUrlMatch[0]), method, score, text);
  }

  const urls = text.match(/https?:\/\/[^\s"'<>]+/g) || [];
  for (const item of urls) {
    const url = cleanExtractedUrl(item);
    if (isPotentialIngestDouyinUrl(url)) {
      return makeDouyinUrlCandidate(url, method, score, text);
    }
  }

  return null;
}

function makeDouyinCandidate(id, type, method, score, raw) {
  const safeType = type === 'note' ? 'note' : 'video';
  return {
    ok: true,
    awemeId: id,
    type: safeType,
    url: `https://www.douyin.com/${safeType}/${id}`,
    method,
    score,
    raw: String(raw || '').slice(0, 300)
  };
}

function decodeHtmlEntities(value) {
  return String(value || '')
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&#(\d+);/g, (_match, code) => String.fromCharCode(Number(code)))
    .replace(/&#x([0-9a-f]+);/gi, (_match, code) => String.fromCharCode(Number.parseInt(code, 16)));
}

function cleanPreviewTitle(value) {
  let text = decodeHtmlEntities(value)
    .replace(/\s+/g, ' ')
    .replace(/\s*复制此链接.*$/i, '')
    .replace(/\s*打开Dou音搜索.*$/i, '')
    .replace(/\s*打开抖音搜索.*$/i, '')
    .replace(/^抖音[-—\s]*/, '')
    .replace(/[-—\s]*抖音[-—\s]*记录美好生活$/i, '')
    .replace(/[-—\s]*抖音$/i, '')
    .trim();
  if (!text || text === '抖音-记录美好生活' || text === '抖音') return '';
  return text.length > 180 ? `${text.slice(0, 180).trim()}...` : text;
}

function extractShareTitle(text) {
  let value = String(text || '')
    .replace(/https?:\/\/[^\s"'<>]+/g, ' ')
    .replace(/\s*复制此链接.*$/i, ' ')
    .replace(/\s*打开Dou音搜索.*$/i, ' ')
    .replace(/\s*打开抖音搜索.*$/i, ' ')
    .replace(/\s+/g, ' ')
    .trim();
  const afterDate = value.match(/(?:^|\s)\d{1,2}\/\d{1,2}\s+(.+)$/);
  if (afterDate) value = afterDate[1].trim();
  value = value.split(/[＃#]/)[0].trim();
  return cleanPreviewTitle(value);
}

function previewSourceLabel(source, type) {
  if (source === 'share') return '分享链接';
  return type === 'note' ? '当前图文预览' : '当前画面';
}

function normalizePreviewCandidate(candidate, source, message) {
  if (!candidate?.ok || !candidate.url) {
    return {
      ok: false,
      source,
      message: message || '未识别到抖音内容'
    };
  }
  const type = candidate.type === 'note' ? 'note' : 'video';
  const title = cleanPreviewTitle(candidate.title || candidate.pageTitle || '') ||
    (source === 'share' ? '已识别分享链接' : '已识别当前抖音内容');
  return {
    ok: true,
    source,
    sourceLabel: previewSourceLabel(source, type),
    type,
    url: candidate.url,
    awemeId: candidate.awemeId || '',
    title,
    coverUrl: candidate.coverUrl || '',
    pageTitle: candidate.pageTitle || '',
    method: candidate.method || ''
  };
}

function bestCandidate(candidates) {
  return candidates
    .filter((item) => item && item.ok)
    .sort((a, b) => (b.score || 0) - (a.score || 0))[0] || null;
}

function candidateFromContextInfo(info, tab) {
  return bestCandidate([
    candidateFromText(info?.linkUrl, 'context-link', 1200),
    candidateFromText(info?.pageUrl, 'context-page', 1000),
    candidateFromText(tab?.url, 'tab-url', 950),
    candidateFromText(info?.srcUrl, 'context-src', 400)
  ]);
}

function sendTabMessage(tabId, message) {
  return new Promise((resolve, reject) => {
    chrome.tabs.sendMessage(tabId, message, (response) => {
      if (chrome.runtime.lastError) {
        reject(new Error(chrome.runtime.lastError.message));
        return;
      }
      resolve(response);
    });
  });
}

function injectDouyinContentScript(tabId) {
  return chrome.scripting.executeScript({
    target: { tabId },
    files: ['content/douyin-current-video.js']
  });
}

async function currentDouyinCandidate(info, tab) {
  const hint = candidateFromContextInfo(info, tab);
  let pageCandidate = null;

  if (tab?.id) {
    try {
      pageCandidate = await sendTabMessage(tab.id, { action: CURRENT_DOUYIN_VIDEO_ACTION });
    } catch (_err) {
      try {
        await injectDouyinContentScript(tab.id);
        pageCandidate = await sendTabMessage(tab.id, { action: CURRENT_DOUYIN_VIDEO_ACTION });
      } catch (err) {
        console.warn('[Librarian BG] 抖音页面识别脚本不可用:', err.message);
        try {
          pageCandidate = await sendTabMessage(tab.id, { action: 'getCurrentDouyinVideo' });
        } catch (_fallbackErr) {
          // Old content script is unavailable too.
        }
      }
    }
  }

  if (
    pageCandidate?.ok &&
    pageCandidate.url &&
    (pageCandidate.awemeId || pageCandidate.title || pageCandidate.coverUrl)
  ) {
    return pageCandidate;
  }

  return bestCandidate([
    pageCandidate && pageCandidate.ok ? pageCandidate : null,
    hint
  ]) || pageCandidate || hint || {
    ok: false,
    reason: 'douyin_current_video_not_found',
    pageUrl: info?.pageUrl || tab?.url || ''
  };
}

async function waitForAgentConnection(timeoutMs = 3000) {
  if (ws && ws.readyState === WebSocket.OPEN && runtimeCompatibility?.canOperate) {
    return true;
  }

  connectWebSocket();
  const start = Date.now();
  return new Promise((resolve) => {
    const timer = setInterval(() => {
      if (ws && ws.readyState === WebSocket.OPEN && runtimeCompatibility?.canOperate) {
        clearInterval(timer);
        resolve(true);
      } else if (runtimeCompatibility && !runtimeCompatibility.canOperate) {
        clearInterval(timer);
        resolve(false);
      } else if (Date.now() - start >= timeoutMs) {
        clearInterval(timer);
        resolve(false);
      }
    }, 100);
  });
}

async function submitDouyinIngestTask(candidate, info, tab) {
  const connected = await waitForAgentConnection();
  if (!connected) {
    const message = runtimeCompatibility?.message || '已识别视频链接，但本地 Agent 服务暂时连接不上。';
    notifyUser(runtimeCompatibility ? '版本校验未通过' : 'Agent 未连接', message);
    return { ok: false, message };
  }

  const requestId = makeRequestId();
  const payload = {
    type: 'task_request',
    requestId,
    source: info?.source || 'extension_popup',
    taskType: 'douyin_ingest',
    url: candidate.url,
    awemeId: candidate.awemeId,
    videoType: candidate.type || 'video',
    title: candidate.title || '',
    coverUrl: candidate.coverUrl || '',
    pageTitle: candidate.pageTitle || tab?.title || '',
    pageUrl: candidate.pageUrl || info?.pageUrl || tab?.url || '',
    detectedBy: candidate.method || 'unknown',
    requestedAt: new Date().toISOString()
  };

  const sent = sendToAgent(payload);
  if (sent) {
    const ack = await waitForTaskAck(requestId);
    await chrome.storage.local.set({
      lastDouyinTaskRequest: {
        url: payload.url,
        awemeId: payload.awemeId,
        pageTitle: payload.pageTitle,
        requestedAt: payload.requestedAt,
        detectedBy: payload.detectedBy,
        taskId: ack.task?.id || ''
      }
    });
    if (ack.ok) {
      notifyUser('Agent 已接收任务', '知识入库任务已进入队列。');
    } else {
      notifyUser('发送失败', ack.message || 'Agent 未接收任务。');
    }
    return ack;
  } else {
    notifyUser('发送失败', '已识别视频链接，但 WebSocket 发送失败。');
  }
  return { ok: false, message: 'WebSocket 发送失败' };
}

async function activeTab() {
  const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  return tabs[0] || null;
}

async function submitDouyinIngestFromPopup(request) {
  const shareText = String(request?.shareText || '').trim();
  const tab = await activeTab();
  const pastedCandidate = shareText
    ? candidateFromText(shareText, 'popup-share-text', 1300)
    : null;
  if (pastedCandidate) {
    pastedCandidate.title = extractShareTitle(shareText);
  }
  const candidate = pastedCandidate || await currentDouyinCandidate({
    pageUrl: tab?.url || ''
  }, tab);

  if (!candidate?.ok || !candidate.url) {
    return {
      ok: false,
      message: shareText
        ? '没有从分享文案里识别到抖音链接'
        : '没有识别到当前抖音视频，请粘贴分享链接'
    };
  }

  return submitDouyinIngestTask(candidate, {
    pageUrl: tab?.url || '',
    source: 'extension_popup'
  }, tab);
}

async function previewDouyinIngest(request) {
  const shareText = String(request?.shareText || '').trim();
  const tab = await activeTab();
  if (shareText) {
    const candidate = candidateFromText(shareText, 'popup-preview-share-text', 1300);
    if (!candidate?.ok || !candidate.url) {
      return normalizePreviewCandidate(null, 'share', '没有从输入内容里识别到抖音链接');
    }
    const shareTitle = extractShareTitle(shareText);
    return normalizePreviewCandidate({
      ...candidate,
      title: shareTitle || candidate.title
    }, 'share');
  }

  const candidate = await currentDouyinCandidate({
    pageUrl: tab?.url || ''
  }, tab);
  if (!candidate?.ok || !candidate.url) {
    return normalizePreviewCandidate(candidate, 'current', '没有识别到当前抖音视频');
  }
  return normalizePreviewCandidate(candidate, 'current');
}

// ─────────────────────────────────────────
// 处理 Agent 推送的消息
// ─────────────────────────────────────────

function handleAgentMessage(msg) {
  switch (msg.type) {
    case 'config_synced':
    case 'cookie_synced':
      debugLog('[Librarian BG] 控制面同步:', msg.type);
      break;

    case 'status_snapshot':
      applyRuntimeCompatibility(msg);
      if (msg.status?.model || msg.status?.llm || msg.status?.videoAnalysis) {
        const modelStatus = msg.status.llm || msg.status.model || {};
        const videoStatus = msg.status.videoAnalysis || {};
        chrome.storage.local.set({
          modelStatus,
          videoAnalysisStatus: videoStatus,
          ...(modelStatus.endpoint ? { arkEndpoint: modelStatus.endpoint } : {}),
          ...(videoStatus.modelPreset ? { videoAnalysisModelPreset: videoStatus.modelPreset } : {}),
          ...(videoStatus.analyzerModel ? { videoAnalysisModel: videoStatus.analyzerModel } : {}),
          ...(videoStatus.strategyModel ? { videoStrategyModel: videoStatus.strategyModel } : {}),
          ...(videoStatus.chunkConcurrency ? { videoChunkConcurrency: normalizeChunkConcurrency(videoStatus.chunkConcurrency) } : {})
        });
      }
      if (msg.status?.tasks?.taskConcurrency) {
        chrome.storage.local.set({
          taskConcurrency: normalizeTaskConcurrency(msg.status.tasks.taskConcurrency),
          serverTaskConcurrency: normalizeTaskConcurrency(msg.status.tasks.taskConcurrency)
        });
      }
      break;

    case 'model_status':
      chrome.storage.local.set({
        modelStatus: msg.status,
        ...(msg.status?.endpoint ? { arkEndpoint: msg.status.endpoint } : {}),
        ...(msg.status?.taskConcurrency ? {
          taskConcurrency: normalizeTaskConcurrency(msg.status.taskConcurrency),
          serverTaskConcurrency: normalizeTaskConcurrency(msg.status.taskConcurrency)
        } : {}),
        ...(msg.status?.chunkConcurrency ? {
          videoChunkConcurrency: normalizeChunkConcurrency(msg.status.chunkConcurrency)
        } : {})
      });
      break;

    case 'vault_status':
      if (msg.status?.path) {
        chrome.storage.local.set({ vaultPath: msg.status.path });
      }
      break;

    case 'vault_lifecycle_status': {
      const result = msg.result || msg.status || {};
      const path = result.activeVault?.vaultPath || '';
      if (path && result.ok) {
        chrome.storage.local.set({ vaultPath: path });
      } else if (result.state === 'selection_required') {
        chrome.storage.local.set({ vaultPath: '' });
      }
      break;
    }
      
    case 'agent_ready':
    case 'handshake_ack': {
      const compatibility = applyRuntimeCompatibility(msg);
      debugLog('[Librarian BG] Agent 版本状态:', compatibility.state);
      if (compatibility.canOperate) {
        if (msg.type === 'handshake_ack' && !vaultScanRequestedForConnection) {
          vaultScanRequestedForConnection = sendToAgent({
            type: 'vault_scan',
            data: { source: 'extension_handshake' }
          });
        }
        if (pendingModelConfigSync) {
          sendModelConfigAndHealthCheck();
        } else if (pendingModelHealthCheck) {
          sendModelHealthCheck();
        }
      }
      break;
    }

    case 'protocol_rejected':
      applyRuntimeCompatibility(msg, {
        state: msg.reason || 'protocol_rejected',
        canOperate: false,
        tone: 'offline',
        message: msg.message || '版本握手未通过，服务已拒绝写操作。'
      });
      notifyUser('版本校验未通过', msg.message || '服务已拒绝写操作。');
      break;

    case 'task_rejected':
      debugLog('[Librarian BG] Agent 拒绝任务:', msg.reason);
      if (!resolveTaskAck(msg)) {
        notifyUser('Agent 暂未接收任务', msg.message || 'Agent 未接收任务。');
      }
      break;

    case 'task_accepted':
      debugLog('[Librarian BG] Agent 已接收任务:', msg.task?.id || '');
      if (!resolveTaskAck(msg)) {
        notifyUser('Agent 已接收任务', '入库任务已进入队列。');
      }
      break;
      
    default:
      debugLog('[Librarian BG] 未知消息类型:', msg.type);
  }
}

function applyRuntimeCompatibility(msg, override = null) {
  const evaluated = RuntimeVersion.evaluateRuntimeCompatibility(msg);
  runtimeCompatibility = override ? { ...evaluated, ...override } : evaluated;
  chrome.storage.local.set({
    runtimeCompatibility,
    agentRuntime: runtimeCompatibility.runtime
  });
  return runtimeCompatibility;
}

// ─────────────────────────────────────────
// 安装时初始化
// ─────────────────────────────────────────

chrome.runtime.onInstalled.addListener((details) => {
  debugLog('[Librarian BG] Extension installed');
  ensureModelHealthAlarm();
  connectWebSocket();
});

// 启动时连接
chrome.runtime.onStartup.addListener(() => {
  debugLog('[Librarian BG] Extension started');
  ensureModelHealthAlarm();
  connectWebSocket();
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === MODEL_HEALTH_ALARM) {
    sendModelHealthCheck();
  }
});

// 扩展激活时连接（如果还没连）
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.action === 'ensureConnected') {
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      connectWebSocket();
    }
    sendResponse({ connected: ws && ws.readyState === WebSocket.OPEN });
    return false;
  } else if (request.action === 'modelHealthCheck') {
    sendModelHealthCheck().then((accepted) => {
      sendResponse({ accepted, message: accepted ? '' : runtimeCompatibility?.message || 'Agent 未连接' });
    });
    return true;
  } else if (request.action === 'syncModelConfigAndCheck') {
    sendModelConfigAndHealthCheck().then((accepted) => {
      sendResponse({ accepted, message: accepted ? '' : runtimeCompatibility?.message || 'Agent 未连接' });
    });
    return true;
  } else if (request.action === 'submitDouyinIngestFromPopup') {
    submitDouyinIngestFromPopup(request).then((result) => {
      sendResponse({
        ok: Boolean(result?.ok),
        task: result?.task || null,
        message: result?.message || (result?.ok ? '任务已进入队列。' : '发送失败，请确认 Agent 已连接。')
      });
    }).catch((err) => {
      sendResponse({
        ok: false,
        message: err.message || '发送失败，请确认 Agent 已连接。'
      });
    });
    return true;
  } else if (request.action === 'previewDouyinIngest') {
    previewDouyinIngest(request).then((result) => {
      sendResponse(result);
    }).catch((err) => {
      sendResponse({
        ok: false,
        source: request.shareText ? 'share' : 'current',
        message: err.message || '预览识别失败'
      });
    });
    return true;
  }
  return false;
});
