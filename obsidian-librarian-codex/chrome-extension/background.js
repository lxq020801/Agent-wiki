// background.js - control-plane WebSocket background service
// 职责：
//   1. 维护 WebSocket 连接
//   2. 接收配置/Cookie 同步确认
//   3. 安装时初始化

const WS_URL = 'ws://127.0.0.1:8765';
const MODEL_HEALTH_ALARM = 'obsidian-librarian-model-health';
const INGEST_CONTEXT_MENU_ID = 'obsidian-librarian-ingest-current-douyin';
const NOTIFICATION_ICON = 'icons/icon-128.png';
const PROVIDERS = {
  doubao: {
    endpoint: 'https://ark.cn-beijing.volces.com/api/v3',
    model: 'doubao-seed-2-0-lite-260428'
  }
};
const DEFAULT_PROVIDER = 'doubao';
const INGEST_INTENTS = {
  knowledge_ingest: '知识入库',
  viral_breakdown: '爆款拆解',
  knowledge_and_viral: '完整入库'
};
const DEFAULT_INGEST_INTENT = 'knowledge_ingest';
const DOUYIN_URL_PATTERN = /https?:\/\/(?:v\.douyin\.com\/[A-Za-z0-9_-]+\/?|(?:www\.)?(?:douyin|iesdouyin)\.com\/(?:video|share\/video|note|share\/note)\/\d+(?:[/?#][^\s"'<>，。！？、；：）)]*)?)/i;
const DOUYIN_TAB_PATTERNS = ['https://douyin.com/*', 'https://*.douyin.com/*'];
let ws = null;
let reconnectTimer = null;
let pendingModelHealthCheck = false;
let pendingModelConfigSync = false;
let pendingTaskRequests = new Map();

function notifyUser(title, message) {
  if (!chrome.notifications) {
    console.log(`[Librarian BG] ${title}: ${message}`);
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

function normalizeIngestIntent(value) {
  return INGEST_INTENTS[value] ? value : DEFAULT_INGEST_INTENT;
}

function expandIngestIntents(value) {
  const normalized = normalizeIngestIntent(value);
  if (normalized === 'knowledge_and_viral') {
    return ['knowledge_ingest', 'viral_breakdown'];
  }
  return [normalized];
}

function providerStorageKeys(value) {
  return { apiKey: 'arkApiKey', model: 'arkModel' };
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
  const defaults = PROVIDERS[provider];
  const keys = providerStorageKeys(provider);
  const apiKey = readStoredApiKey(config, provider);
  if (!apiKey) return null;

  const model = readStoredModel(config, provider) || defaults.model;
  const data = {
    provider,
    apiKey,
    [keys.apiKey]: apiKey,
    model,
    [keys.model]: model,
    endpoint: defaults.endpoint
  };
  if (config.vaultPath) {
    data.vaultPath = config.vaultPath;
  }
  return data;
}

async function storedAgentConfig() {
  const config = await chrome.storage.local.get([
    'provider',
    'apiKey',
    'arkApiKey',
    'model',
    'arkModel',
    'vaultPath'
  ]);
  return buildAgentConfig(config);
}

// ─────────────────────────────────────────
// WebSocket 连接管理
// ─────────────────────────────────────────

function connectWebSocket() {
  console.log('[Librarian BG] 连接 WebSocket:', WS_URL);
  
  if (ws) {
    ws.close();
  }
  
  try {
    ws = new WebSocket(WS_URL);
    
    ws.onopen = () => {
      console.log('[Librarian BG] WebSocket 已连接');
      clearTimeout(reconnectTimer);
      
      // 发送握手
      ws.send(JSON.stringify({
        type: 'handshake',
        client: 'obsidian-librarian-background',
        version: '0.1.0'
      }));

      if (pendingModelConfigSync) {
        sendModelConfigAndHealthCheck();
      } else if (pendingModelHealthCheck) {
        sendModelHealthCheck();
      }
    };
    
    ws.onmessage = (event) => {
      try {
        handleAgentMessage(JSON.parse(event.data));
      } catch (err) {
        console.error('[Librarian BG] 消息解析失败:', err);
      }
    };
    
    ws.onclose = () => {
      console.log('[Librarian BG] WebSocket 已断开，3秒后重连');
      ws = null;
      reconnectTimer = setTimeout(connectWebSocket, 3000);
    };
    
    ws.onerror = (err) => {
      console.error('[Librarian BG] WebSocket 错误:', err);
      ws = null;
    };
    
  } catch (err) {
    console.error('[Librarian BG] WebSocket 连接失败:', err);
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

function makeRequestId() {
  return `${Date.now()}-${Math.random().toString(16).slice(2, 10)}`;
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
  const data = await storedAgentConfig();
  if (!data) {
    pendingModelHealthCheck = false;
    return;
  }

  if (!ws || ws.readyState !== WebSocket.OPEN) {
    pendingModelHealthCheck = true;
    connectWebSocket();
    return;
  }

  pendingModelHealthCheck = false;
  sendToAgent({ type: 'model_check', data });
}

async function sendModelConfigAndHealthCheck() {
  const data = await storedAgentConfig();
  if (!data) {
    pendingModelConfigSync = false;
    pendingModelHealthCheck = false;
    return false;
  }

  if (!ws || ws.readyState !== WebSocket.OPEN) {
    pendingModelConfigSync = true;
    connectWebSocket();
    return true;
  }

  pendingModelConfigSync = false;
  pendingModelHealthCheck = false;
  sendToAgent({ type: 'config_update', data });
  sendToAgent({ type: 'model_check', data });
  return true;
}

function ensureModelHealthAlarm() {
  chrome.alarms.create(MODEL_HEALTH_ALARM, {
    periodInMinutes: 10
  });
}

function reloadOpenDouyinTabs(reason = 'extension_updated') {
  if (!chrome.tabs?.query || !chrome.tabs?.reload) return;
  chrome.tabs.query({ url: DOUYIN_TAB_PATTERNS }, (tabs) => {
    if (chrome.runtime.lastError) {
      console.warn('[Librarian BG] 查询抖音标签页失败:', chrome.runtime.lastError.message);
      return;
    }
    for (const tab of tabs || []) {
      if (!tab.id) continue;
      chrome.tabs.reload(tab.id, { bypassCache: false }, () => {
        if (chrome.runtime.lastError) {
          console.warn('[Librarian BG] 刷新抖音标签页失败:', chrome.runtime.lastError.message);
        } else {
          console.log('[Librarian BG] 已刷新抖音标签页，清理旧 content script:', reason);
        }
      });
    }
  });
}

// ─────────────────────────────────────────
// 抖音右键入库入口
// ─────────────────────────────────────────

function setupContextMenu() {
  if (!chrome.contextMenus) return;

  chrome.contextMenus.remove(INGEST_CONTEXT_MENU_ID, () => {
    // 菜单不存在时会有 lastError，这里属于正常初始化路径。
    chrome.runtime.lastError;
    chrome.contextMenus.create({
      id: INGEST_CONTEXT_MENU_ID,
      title: '拆解并收入知识库',
      contexts: ['page', 'video', 'link'],
      documentUrlPatterns: [
        'https://douyin.com/*',
        'https://*.douyin.com/*'
      ]
    }, () => {
      if (chrome.runtime.lastError) {
        console.warn('[Librarian BG] 右键菜单创建失败:', chrome.runtime.lastError.message);
      }
    });
  });
}

function cleanExtractedUrl(value) {
  return String(value || '')
    .trim()
    .replace(/[，。,.!！)）\]】>]+$/g, '');
}

function isSupportedDouyinUrl(value) {
  try {
    const url = new URL(value);
    const host = url.hostname.toLowerCase();
    return url.protocol.startsWith('http') && (
      host === 'douyin.com' ||
      host.endsWith('.douyin.com') ||
      host === 'iesdouyin.com' ||
      host.endsWith('.iesdouyin.com')
    );
  } catch (_err) {
    return false;
  }
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
    if (isSupportedDouyinUrl(url)) {
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
      pageCandidate = await sendTabMessage(tab.id, { action: 'getCurrentDouyinVideo' });
    } catch (_err) {
      try {
        await injectDouyinContentScript(tab.id);
        pageCandidate = await sendTabMessage(tab.id, { action: 'getCurrentDouyinVideo' });
      } catch (err) {
        console.warn('[Librarian BG] 抖音页面识别脚本不可用:', err.message);
      }
    }
  }

  return bestCandidate([
    hint,
    pageCandidate && pageCandidate.ok ? pageCandidate : null
  ]) || pageCandidate || hint || {
    ok: false,
    reason: 'douyin_current_video_not_found',
    pageUrl: info?.pageUrl || tab?.url || ''
  };
}

async function waitForAgentConnection(timeoutMs = 3000) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    return true;
  }

  connectWebSocket();
  const start = Date.now();
  return new Promise((resolve) => {
    const timer = setInterval(() => {
      if (ws && ws.readyState === WebSocket.OPEN) {
        clearInterval(timer);
        resolve(true);
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
    notifyUser('Agent 未连接', '已识别视频链接，但本地 Agent 服务暂时连接不上。');
    return { ok: false, message: 'Agent 未连接' };
  }

  const requestId = makeRequestId();
  const ingestIntent = normalizeIngestIntent(info?.ingestIntent || candidate.ingestIntent);
  const ingestIntents = expandIngestIntents(ingestIntent);
  const payload = {
    type: 'task_request',
    requestId,
    source: info?.source || 'extension_context_menu',
    taskType: 'douyin_ingest',
    ingest_intent: ingestIntents[0],
    ingest_intents: ingestIntents,
    url: candidate.url,
    awemeId: candidate.awemeId,
    videoType: candidate.type || 'video',
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
        ingestIntent,
        ingestIntents,
        requestedAt: payload.requestedAt,
        detectedBy: payload.detectedBy,
        taskId: ack.task?.id || ''
      }
    });
    if (ack.ok) {
      notifyUser('Agent 已接收任务', `${INGEST_INTENTS[ingestIntent]}任务已进入队列。`);
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
  const candidate = pastedCandidate || await currentDouyinCandidate({
    pageUrl: tab?.url || '',
    ingestIntent: request?.ingestIntent
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
    source: 'extension_popup',
    ingestIntent: request?.ingestIntent
  }, tab);
}

async function handleDouyinContextMenu(info, tab) {
  const candidate = await currentDouyinCandidate(info, tab);
  if (!candidate?.ok || !candidate.url) {
    notifyUser('未识别到当前视频', '请进入视频详情页，或复制分享链接后交给 Agent。');
    return;
  }

  await submitDouyinIngestTask(candidate, info, tab);
}

if (chrome.contextMenus?.onClicked) {
  chrome.contextMenus.onClicked.addListener((info, tab) => {
    if (info.menuItemId !== INGEST_CONTEXT_MENU_ID) return;
    handleDouyinContextMenu(info, tab).catch((err) => {
      console.error('[Librarian BG] 右键入库失败:', err);
      notifyUser('发送失败', err.message || '右键入库时发生未知错误。');
    });
  });
}

// ─────────────────────────────────────────
// 处理 Agent 推送的消息
// ─────────────────────────────────────────

function handleAgentMessage(msg) {
  switch (msg.type) {
    case 'config_synced':
    case 'cookie_synced':
      console.log('[Librarian BG] 控制面同步:', msg.type);
      break;

    case 'status_snapshot':
      if (msg.status?.model) {
        chrome.storage.local.set({ modelStatus: msg.status.model });
      }
      break;

    case 'model_status':
      chrome.storage.local.set({ modelStatus: msg.status });
      break;

    case 'vault_status':
      if (msg.status?.path) {
        chrome.storage.local.set({ vaultPath: msg.status.path });
      }
      break;
      
    case 'agent_ready':
      console.log('[Librarian BG] Agent 已就绪');
      break;

    case 'task_rejected':
      console.log('[Librarian BG] Agent 拒绝任务:', msg.reason);
      if (!resolveTaskAck(msg)) {
        notifyUser('Agent 暂未接收任务', msg.message || 'Agent 未接收任务。');
      }
      break;

    case 'task_accepted':
      console.log('[Librarian BG] Agent 已接收任务:', msg.task?.id || '');
      if (!resolveTaskAck(msg)) {
        notifyUser('Agent 已接收任务', '入库任务已进入队列。');
      }
      break;
      
    default:
      console.log('[Librarian BG] 未知消息类型:', msg.type);
  }
}

// ─────────────────────────────────────────
// 安装时初始化
// ─────────────────────────────────────────

chrome.runtime.onInstalled.addListener((details) => {
  console.log('[Librarian BG] Extension installed');
  setupContextMenu();
  ensureModelHealthAlarm();
  if (details?.reason === 'install' || details?.reason === 'update') {
    reloadOpenDouyinTabs(details.reason);
  }
  connectWebSocket();
});

// 启动时连接
chrome.runtime.onStartup.addListener(() => {
  console.log('[Librarian BG] Extension started');
  setupContextMenu();
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
  } else if (request.action === 'modelHealthCheck') {
    sendModelHealthCheck().then(() => {
      sendResponse({ accepted: true });
    });
  } else if (request.action === 'syncModelConfigAndCheck') {
    sendModelConfigAndHealthCheck().then((accepted) => {
      sendResponse({ accepted });
    });
  } else if (request.action === 'submitCurrentDouyinVideo') {
    const candidate = request.candidate || {};
    if (!candidate.ok || !candidate.url) {
      sendResponse({
        ok: false,
        message: '没有识别到当前视频，请进入详情页或复制分享链接。'
      });
      return true;
    }
    submitDouyinIngestTask(
      candidate,
      {
        pageUrl: candidate.pageUrl || sender.tab?.url || '',
        source: 'extension_inline_button',
        ingestIntent: request.ingestIntent
      },
      sender.tab
    ).then((result) => {
      sendResponse({
        ok: Boolean(result?.ok),
        task: result?.task || null,
        url: candidate.url,
        message: result?.message || (result?.ok ? '任务已进入队列。' : '发送失败，请确认 Agent 已连接。')
      });
    }).catch((err) => {
      sendResponse({
        ok: false,
        message: err.message || '发送失败，请确认 Agent 已连接。'
      });
    });
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
  }
  return true;
});
