'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function extensionEvent() {
  return { addListener() {} };
}

class FakeWebSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSED = 3;
  static instances = [];

  constructor(url) {
    this.url = url;
    this.readyState = FakeWebSocket.CONNECTING;
    this.sent = [];
    FakeWebSocket.instances.push(this);
  }

  send(message) {
    this.sent.push(JSON.parse(message));
  }

  open() {
    this.readyState = FakeWebSocket.OPEN;
    this.onopen?.();
  }

  close() {
    this.readyState = FakeWebSocket.CLOSED;
    this.onclose?.();
  }
}

async function main() {
  const stored = {};
  const runtimeVersionPath = path.resolve(__dirname, '..', 'chrome-extension', 'runtime-version.js');
  const backgroundPath = path.resolve(__dirname, '..', 'chrome-extension', 'background.js');
  const chrome = {
    alarms: {
      create() {},
      onAlarm: extensionEvent()
    },
    notifications: { create() {} },
    runtime: {
      getManifest: () => ({ version: '0.3.0' }),
      lastError: null,
      onInstalled: extensionEvent(),
      onMessage: extensionEvent(),
      onStartup: extensionEvent()
    },
    scripting: { async executeScript() {} },
    storage: {
      local: {
        async get(keys) {
          return Object.fromEntries(keys.filter((key) => key in stored).map((key) => [key, stored[key]]));
        },
        async set(values) {
          Object.assign(stored, values);
        }
      }
    },
    tabs: {
      async query() { return []; },
      sendMessage() {}
    }
  };
  let context;
  context = vm.createContext({
    chrome,
    console,
    Date,
    Math,
    Promise,
    URL,
    WebSocket: FakeWebSocket,
    clearInterval,
    clearTimeout,
    importScripts(script) {
      assert.equal(script, 'runtime-version.js');
      vm.runInContext(fs.readFileSync(runtimeVersionPath, 'utf8'), context, {
        filename: runtimeVersionPath
      });
    },
    setInterval,
    setTimeout
  });
  context.self = context;
  vm.runInContext(fs.readFileSync(backgroundPath, 'utf8'), context, { filename: backgroundPath });

  vm.runInContext('connectWebSocket()', context);
  assert.equal(FakeWebSocket.instances.length, 1);
  const socket = FakeWebSocket.instances[0];
  assert.equal(socket.url, 'ws://127.0.0.1:8765');
  socket.open();
  assert.deepEqual({
    type: socket.sent[0].type,
    client: socket.sent[0].client,
    product: socket.sent[0].product,
    version: socket.sent[0].version,
    protocolVersion: socket.sent[0].protocolVersion
  }, {
    type: 'handshake',
    client: 'agent-wiki-background',
    product: 'agent-wiki',
    version: '0.3.0',
    protocolVersion: 1
  });
  assert.match(socket.sent[0].operationId, /^handshake-/);
  assert.equal(socket.sent[0].taskId, '');
  assert.equal(socket.sent[0].parentId, '');
  assert.ok(socket.sent[0].requestId);

  const sentBeforeHandshake = vm.runInContext(
    `sendToAgent({ type: 'task_request', requestId: 'blocked-before-handshake' })`,
    context
  );
  assert.equal(sentBeforeHandshake, false);
  assert.equal(socket.sent.length, 1);

  const compatibleRuntime = {
    product: 'agent-wiki',
    productVersion: '0.3.0',
    protocolVersion: 1,
    sourceRevision: 'abcdef123456',
    buildId: 'src-1234567890abcdef',
    deployment: { state: 'current', code: 'source_checkout' }
  };
  vm.runInContext(`handleAgentMessage(${JSON.stringify({
    type: 'handshake_ack',
    runtime: compatibleRuntime,
    compatibility: {
      state: 'compatible',
      canOperate: true,
      clientVersion: '0.3.0',
      clientProtocolVersion: 1
    }
  })})`, context);
  assert.equal(stored.runtimeCompatibility.canOperate, true);
  assert.equal(stored.agentRuntime.productVersion, '0.3.0');

  const candidate = {
    url: 'https://www.douyin.com/video/7390000000000000000',
    awemeId: '7390000000000000000',
    type: 'video',
    title: 'Contract test',
    coverUrl: '',
    pageTitle: 'Douyin page',
    pageUrl: 'https://www.douyin.com/',
    method: 'integration-test'
  };
  const submission = vm.runInContext(
    `submitDouyinIngestTask(${JSON.stringify(candidate)}, { source: 'extension_popup' }, {})`,
    context
  );
  await Promise.resolve();
  const payload = socket.sent.at(-1);
  assert.equal(payload.type, 'task_request');
  assert.equal(payload.taskType, 'douyin_ingest');
  assert.equal(payload.source, 'extension_popup');
  assert.equal(payload.url, candidate.url);
  assert.equal(payload.awemeId, candidate.awemeId);
  assert.equal(payload.pageTitle, candidate.pageTitle);
  assert.equal(payload.pageUrl, candidate.pageUrl);
  assert.equal(payload.detectedBy, candidate.method);
  assert.equal(typeof payload.requestId, 'string');
  assert.ok(!('ingest_intent' in payload));
  assert.ok(!('ingestIntent' in payload));

  vm.runInContext(`handleAgentMessage(${JSON.stringify({
    type: 'task_accepted',
    requestId: payload.requestId,
    task: { id: 'task-contract', ingestIntent: 'knowledge_ingest' },
    message: '任务已进入队列'
  })})`, context);
  const result = await submission;
  assert.equal(result.ok, true);
  assert.equal(result.task.id, 'task-contract');
  assert.equal(stored.lastDouyinTaskRequest.taskId, 'task-contract');
  assert.equal(stored.lastDouyinTaskRequest.url, candidate.url);

  vm.runInContext(`handleAgentMessage(${JSON.stringify({
    type: 'status_snapshot',
    status: {
      runtime: compatibleRuntime,
      llm: { state: 'ready', provider: 'doubao' },
      videoAnalysis: { modelPreset: 'lite', chunkConcurrency: 2 },
      tasks: { taskConcurrency: 3 }
    }
  })})`, context);
  assert.equal(stored.modelStatus.provider, 'doubao');
  assert.equal(stored.videoAnalysisModelPreset, 'lite');
  assert.equal(stored.serverTaskConcurrency, 3);

  console.log('Extension contract checks passed');
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
