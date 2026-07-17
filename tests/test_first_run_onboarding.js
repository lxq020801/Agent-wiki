'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'chrome-extension/popup/popup.html'), 'utf8');
const css = fs.readFileSync(path.join(root, 'chrome-extension/popup/popup.css'), 'utf8');
const js = fs.readFileSync(path.join(root, 'chrome-extension/popup/popup.js'), 'utf8');

for (const id of [
  'first-run-guide',
  'toggle-first-run',
  'first-run-reminder',
  'first-run-next-action',
  'agent-start-command',
  'copy-agent-start-command',
  'retry-agent-connection'
]) {
  assert.match(html, new RegExp(`id="${id}"`), `missing first-run control: ${id}`);
}
for (const step of ['agent', 'api', 'vault', 'cookie', 'github']) {
  assert.match(html, new RegExp(`data-onboarding-step="${step}"`));
  assert.match(html, new RegExp(`data-onboarding-action="${step}"`));
}
assert.match(html, /GitHub <em>可选<\/em>/);
assert.match(html, /python3\.11 server\/launcher\.py start/);
assert.doesNotMatch(html + js, /\/Users\/|\.codex\/worktrees|obsidian-librarian/);
assert.match(css, /\.onboarding-steps li,[\s\S]*?grid-template-columns:\s*auto minmax\(0, 1fr\) auto/);
assert.match(css, /\.onboarding-step-copy small,[\s\S]*?overflow-wrap:\s*anywhere/);
assert.match(css, /\.command-row code\s*\{[\s\S]*?overflow-wrap:\s*anywhere/);
assert.match(css, /button:disabled\s*\{[\s\S]*?cursor:\s*not-allowed/);

async function main() {
  const stored = {};
  const sent = [];
  const statusCalls = [];
  const elements = {};
  const chrome = {
    storage: {
      local: {
        async get(keys) {
          return Object.fromEntries((Array.isArray(keys) ? keys : [keys])
            .filter(key => key in stored)
            .map(key => [key, stored[key]]));
        },
        async set(values) {
          Object.assign(stored, JSON.parse(JSON.stringify(values)));
        }
      },
      session: {
        async get() { return {}; },
        async set() {},
        async remove() {}
      }
    }
  };
  const context = vm.createContext({
    AgentWikiRuntime: {
      PROTOCOL_VERSION: 1,
      extensionVersion: () => '0.3.1',
      canSendMessage: () => true,
      buildHandshake: () => ({ type: 'handshake' }),
      evaluateRuntimeCompatibility: () => ({
        canOperate: true,
        tone: 'online',
        state: 'compatible',
        message: '版本一致',
        runtime: {
          productVersion: '0.3.1',
          protocolVersion: 1,
          sourceRevision: 'abcdef123456'
        }
      })
    },
    WebSocket: { CONNECTING: 0, OPEN: 1 },
    chrome,
    console: { log() {}, error() {} },
    Date,
    navigator: {},
    document: {
      body: { dataset: { view: 'home-view' } },
      documentElement: { dataset: {} },
      addEventListener() {},
      getElementById(id) {
        if (!elements[id]) {
          elements[id] = {
            className: '',
            textContent: '',
            title: '',
            hidden: false,
            disabled: false
          };
        }
        return elements[id];
      }
    },
    setTimeout,
    clearTimeout,
    setInterval,
    clearInterval
  });
  vm.runInContext(js, context, { filename: 'popup.js' });

  const view = expression => JSON.parse(vm.runInContext(`JSON.stringify(${expression})`, context));
  const initial = view('onboardingStepViews()');
  assert.deepEqual(initial.slice(0, 4).map(step => step.ready), [false, false, false, false]);
  assert.equal(initial[4].optional, true);

  vm.runInContext(`
    isAgentConnected = true;
    runtimeCompatibility = { canOperate: true };
    setupState.api = { configured: true, verified: true };
    setupState.vault = { configured: true, verified: true };
    setupState.cookie = { configured: true, pending: false, verified: true };
    setupState.github = { configured: false, verified: false };
  `, context);
  const requiredReady = view('onboardingStepViews()');
  assert.equal(requiredReady.filter(step => !step.optional).every(step => step.ready), true);
  assert.equal(requiredReady.at(-1).ready, false, 'GitHub must not block first-run completion');

  vm.runInContext('renderFirstRunGuide = () => {};', context);
  await vm.runInContext('toggleFirstRunGuide()', context);
  assert.equal(stored.firstRunGuideCollapsed, true);
  await vm.runInContext('toggleFirstRunGuide()', context);
  assert.equal(stored.firstRunGuideCollapsed, false);
  assert.deepEqual(Object.keys(stored), ['firstRunGuideCollapsed']);

  context.statusCalls = statusCalls;
  vm.runInContext(`
    setStatus = (kind, text, type) => statusCalls.push({ kind, text, type });
    applyGithubStatus = status => {
      setupState.github.verified = false;
      statusCalls.push({ kind: 'github', text: status.state, type: 'warning' });
    };
    refreshCookieStatusFromStorage = () => Promise.resolve();
    setupState.api = { configured: true, verified: true };
    setupState.vault = { configured: true, verified: true };
    setupState.cookie = { configured: true, pending: false, verified: true };
    setupState.github = { configured: true, verified: true };
    updateConnectionStatus(false);
  `, context);
  assert.equal(view('setupState').api.verified, false);
  assert.equal(view('setupState').vault.verified, false);
  assert.equal(view('setupState').cookie.verified, false);
  assert.equal(view('setupState').github.verified, false);
  assert.deepEqual(
    statusCalls.filter(call => ['api', 'vault', 'cookie'].includes(call.kind)).map(call => [call.kind, call.text, call.type]),
    [
      ['api', '已配置，待检查', 'warning'],
      ['vault', '已配置，待检查', 'warning'],
      ['cookie', '已同步，待检查', 'warning']
    ]
  );

  context.fakeSocket = {
    readyState: 1,
    send(payload) { sent.push(JSON.parse(payload)); }
  };
  vm.runInContext(`
    ws = fakeSocket;
    updateConnectionStatus = connected => { isAgentConnected = connected; };
    applyRuntimeCompatibility = () => {
      runtimeCompatibility = { canOperate: true };
      return runtimeCompatibility;
    };
    flushPendingSync = () => Promise.resolve();
    handleAgentMessage({ type: 'handshake_ack' });
  `, context);
  assert.equal(sent.some(message => message.type === 'status_request'), true);
  assert.equal(sent.some(message => message.type === 'github_status_request' && message.validate === true), true);
  vm.runInContext(`
    applyModelStatus({ state: 'ready', ok: true });
    applyVaultStatus({ state: 'ready', path: '/tmp/mock-vault' });
    applyCookieStatus({ state: 'ready', ok: true });
  `, context);
  assert.deepEqual(
    view('onboardingStepViews()').slice(0, 4).map(step => step.ready),
    [true, true, true, true],
    'a compatible handshake followed by fresh service statuses must restore required readiness'
  );

  console.log('First-run onboarding contract checks passed');
}

main().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
