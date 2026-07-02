// background.js - control-plane WebSocket background service
// 职责：
//   1. 维护 WebSocket 连接
//   2. 接收配置/Cookie 同步确认
//   3. 安装时初始化

const WS_URL = 'ws://127.0.0.1:8765';
const MODEL_HEALTH_ALARM = 'obsidian-librarian-model-health';
const PROVIDERS = {
  doubao: {
    endpoint: 'https://ark.cn-beijing.volces.com/api/v3',
    model: 'doubao-seed-2-0-lite-260428'
  },
  volcengine_agent_plan: {
    endpoint: 'https://ark.cn-beijing.volces.com/api/plan/v3',
    model: 'doubao-seed-2.0-lite'
  }
};
const DEFAULT_PROVIDER = 'doubao';
let ws = null;
let reconnectTimer = null;

function normalizeProvider(value) {
  return PROVIDERS[value] ? value : DEFAULT_PROVIDER;
}

function providerStorageKeys(value) {
  return normalizeProvider(value) === 'volcengine_agent_plan'
    ? { apiKey: 'agentPlanApiKey', model: 'agentPlanModel' }
    : { apiKey: 'arkApiKey', model: 'arkModel' };
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

async function sendModelHealthCheck() {
  const config = await chrome.storage.local.get([
    'provider',
    'apiKey',
    'arkApiKey',
    'agentPlanApiKey',
    'model',
    'arkModel',
    'agentPlanModel'
  ]);
  const provider = normalizeProvider(config.provider);
  const defaults = PROVIDERS[provider];
  const keys = providerStorageKeys(provider);
  const apiKey = config[keys.apiKey] || config.apiKey || '';
  const model = config[keys.model] || config.model || defaults.model;
  if (!apiKey) {
    await chrome.storage.local.set({
      modelStatus: {
        ok: false,
        state: 'missing',
        provider,
        model,
        checkedAt: new Date().toISOString(),
        message: '缺少 API Key'
      }
    });
    return;
  }

  if (!ws || ws.readyState !== WebSocket.OPEN) {
    connectWebSocket();
    return;
  }

  sendToAgent({
    type: 'model_check',
    data: {
      provider,
      apiKey,
      [keys.apiKey]: apiKey,
      model,
      [keys.model]: model,
      endpoint: defaults.endpoint
    }
  });
}

function ensureModelHealthAlarm() {
  chrome.alarms.create(MODEL_HEALTH_ALARM, {
    periodInMinutes: 10
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
      console.log('[Librarian BG] 扩展触发入库不属于 P0:', msg.reason);
      break;
      
    default:
      console.log('[Librarian BG] 未知消息类型:', msg.type);
  }
}

// ─────────────────────────────────────────
// 安装时初始化
// ─────────────────────────────────────────

chrome.runtime.onInstalled.addListener(() => {
  console.log('[Librarian BG] Extension installed');
  ensureModelHealthAlarm();
  connectWebSocket();
});

// 启动时连接
chrome.runtime.onStartup.addListener(() => {
  console.log('[Librarian BG] Extension started');
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
  }
  return true;
});
