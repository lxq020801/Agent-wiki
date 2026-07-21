'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'chrome-extension/popup/popup.html'), 'utf8');
const js = fs.readFileSync(path.join(root, 'chrome-extension/popup/popup.js'), 'utf8');
const css = fs.readFileSync(path.join(root, 'chrome-extension/popup/popup.css'), 'utf8');

for (const id of [
  'open-github',
  'home-github-summary',
  'github-view',
  'back-home-from-github',
  'github-account-copy',
  'github-login',
  'github-device-panel',
  'github-copy-code',
  'github-device-expiry',
  'github-auto-star',
  'github-stars-list',
  'github-select-all',
  'github-import-selected',
  'github-cancel-import',
  'github-refresh-panel',
  'github-confirm-refresh',
  'github-cancel-refresh'
]) {
  assert.match(html, new RegExp(`id="${id}"`), `missing GitHub control: ${id}`);
}

assert.match(html, /<main class="view" id="github-view"[\s\S]*id="github-stars-tool"/);
assert.match(html, /data-lucide-icon="github"/);
assert.match(html, />GitHub 资产</);
assert.match(html, />GitHub 资产管理</);
assert.match(html, />资产创建后自动 Star</);
assert.doesNotMatch(html, /id="github-search-(?:tool|query|results)"|id="github-search"|>仓库搜索</);
assert.doesNotMatch(html + js, /GitHub 项目|项目资料|项目刷新/);
assert.doesNotMatch(html, /class="settings-card"[^>]*data-target="github-settings"/);
assert.doesNotMatch(html, /id="github-settings"/);
const settingsIndexMarkup = html.slice(html.indexOf('id="settings-index-view"'), html.indexOf('id="settings-detail-view"'));
const settingsDetailMarkup = html.slice(html.indexOf('id="settings-detail-view"'), html.indexOf('id="github-view"'));
// 配置向导仅展示 GitHub 可选状态并跳转独立 GitHub 页面；设置页仍不得包含 GitHub 配置入口
const settingsIndexWithoutWizard = settingsIndexMarkup.replace(/<section class="onboarding-panel"[\s\S]*?<\/section>/, '');
assert.doesNotMatch(settingsIndexWithoutWizard, /GitHub|github/i);
assert.doesNotMatch(settingsDetailMarkup, /github-stars-tool/);

for (const type of [
  'github_auth_start',
  'github_auth_cancel',
  'github_logout',
  'github_stars_request',
  'github_import_stars',
  'github_import_status',
  'github_import_cancel',
  'github_refresh_check',
  'github_refresh_confirm',
  'github_refresh_cancel'
]) {
  assert.match(js, new RegExp(`\\b[A-Z_]+: '${type}'`), `missing GitHub message constant: ${type}`);
}

assert.doesNotMatch(js, /chrome\.storage\.(?:local|session)\.(?:set|get)\([^\n]*(?:githubToken|accessToken|deviceCode)/i);
assert.doesNotMatch(html + js, /github_pat_[A-Za-z0-9_]{20,}|ghp_[A-Za-z0-9]{20,}/);
assert.match(js, /const POPUP_ROUTE_STORAGE_KEY = 'popupRoute'/);
assert.match(js, /function popupRouteStorage\(\)[\s\S]*?chrome\.storage\?\.session/);
assert.match(js, /storage\.set\(\{ \[POPUP_ROUTE_STORAGE_KEY\]: sanitizePopupRoute\(route\) \}\)/);
assert.match(js, /function restorePopupRoute\(\)[\s\S]*?sanitizePopupRoute\(stored\[POPUP_ROUTE_STORAGE_KEY\]\)/);
assert.match(js, /function closeToHome\(\)[\s\S]*?persistPopupRoute\(\{ view: POPUP_VIEWS\.HOME \}\)/);
assert.match(js, /function openGithubPage\([\s\S]*?persistPopupRoute\(\{ view: POPUP_VIEWS\.GITHUB \}\)/);
assert.match(js, /async function startGithubAuthorization\(\)[\s\S]*?persistPopupRoute\(\{ view: POPUP_VIEWS\.GITHUB \}\)/);
const routeStorageContract = js.slice(js.indexOf('function popupRouteStorage'), js.indexOf('async function flushPendingSync'));
assert.doesNotMatch(routeStorageContract, /storage\.local|transientStorage|apiKey|token|deviceCode|userCode|verification/i);
assert.doesNotMatch(js, /openSettingsDetail\(['"]github-settings/);
assert.match(js, /HOME: 'home-view'[\s\S]*SETTINGS_INDEX: 'settings-index-view'[\s\S]*SETTINGS_DETAIL: 'settings-detail-view'[\s\S]*GITHUB: 'github-view'/);
assert.match(js, /homeSummary\.textContent = login \? `@\$\{login\}` : '已登录'/);
assert.match(js, /copy\.textContent = login \? `@\$\{login\}` : '已登录'/);
assert.match(js, /case 'handshake_ack':[\s\S]*?requestStatus\(\);[\s\S]*?requestGithubValidationAfterHandshake\(compatibility\)/);
assert.match(js, /ws\.onopen = \(\) => \{[\s\S]*?githubValidationRequestedForConnection = false/);
assert.match(js, /function applyGithubStatus[\s\S]*\['checking', 'unchecked', 'unavailable'\]\.includes\(status\.state\)/);
assert.match(js, /status\.activeAuthorization/);
assert.match(js, /status\.activeImport/);
assert.match(js, /status\.recentImports/);
assert.match(js, /status\.recentTasks/);
assert.match(js, /Array\.isArray\(batch\?\.items\)/);
assert.match(js, /navigator\.clipboard\?\.writeText/);
assert.match(js, /document\.execCommand\('copy'\)/);
assert.doesNotMatch(js, /type: 'github_auth_poll'/);
assert.match(js, /isAuthError && !result\.transient/);
assert.match(js, /后台会自动重试/);
assert.doesNotMatch(js, /function applyModelStatus[\s\S]*?status\.state === 'unchecked'[\s\S]*?function applyVideoStatus/);
assert.match(js, /githubIntegration\?\.autoStar/);
assert.match(js, /if \(page <= 1\) githubSelected\.clear\(\)/);
assert.match(css, /\[hidden\]\s*\{\s*display:\s*none\s*!important;/);

const sentMessages = [];
const behaviorContext = vm.createContext({
  AgentWikiRuntime: {
    extensionVersion: () => 'test',
    canSendMessage: () => true,
    buildHandshake: () => ({ type: 'handshake' })
  },
  WebSocket: { OPEN: 1 },
  console: { log() {}, error() {} },
  document: {
    body: { dataset: { view: 'home-view' } },
    addEventListener() {},
    createElement() { return { className: '', textContent: '' }; }
  },
  setTimeout,
  clearTimeout,
  setInterval,
  clearInterval
});
behaviorContext.fakeSocket = {
  readyState: 1,
  send(payload) {
    sentMessages.push(JSON.parse(payload));
  }
};
vm.runInContext(js, behaviorContext);
vm.runInContext(`
  ws = fakeSocket;
  requestGithubValidationAfterHandshake({ canOperate: true });
  requestGithubValidationAfterHandshake({ canOperate: true });
`, behaviorContext);
assert.equal(sentMessages.length, 1, 'handshake must trigger at most one GitHub validation per connection');
assert.equal(sentMessages[0].type, 'github_status_request');
assert.equal(sentMessages[0].validate, true);

const githubElements = new Proxy({}, {
  get(target, id) {
    if (!target[id]) {
      target[id] = {
        className: '',
        textContent: '',
        title: '',
        hidden: false,
        disabled: false,
        checked: false,
        indeterminate: false,
        max: 0,
        value: 0,
        dataset: {},
        replaceChildren() {},
        appendChild() {}
      };
    }
    return target[id];
  }
});
behaviorContext.document.getElementById = id => githubElements[id];
vm.runInContext(`handleAgentMessage({
  type: 'github_status',
  result: {
    configured: true,
    authenticated: true,
    account: { login: 'existing-user' },
    settings: { autoStar: true }
  }
})`, behaviorContext);
assert.equal(githubElements['home-github-summary'].textContent, '@existing-user');
assert.equal(githubElements['home-github-dot'].className, 'status-dot inline-dot online');
assert.equal(githubElements['github-account-copy'].textContent, '@existing-user');
const starsRequests = () => sentMessages.filter(message => message.type === 'github_stars_request');
assert.equal(starsRequests().length, 1, 'authenticated status must auto-load Stars once');
assert.equal(starsRequests()[0].page, 1);

vm.runInContext(`handleAgentMessage({
  type: 'github_status',
  result: { configured: true, authenticated: true, account: { login: 'existing-user' } }
})`, behaviorContext);
assert.equal(starsRequests().length, 1, 'repeated authenticated status must not reload Stars automatically');

vm.runInContext(`handleAgentMessage({
  type: 'github_status',
  result: { configured: true, authenticated: false, state: 'ready' }
})`, behaviorContext);
assert.equal(githubElements['home-github-summary'].textContent, '未登录');
assert.equal(githubElements['home-github-dot'].className, 'status-dot inline-dot warning');
assert.equal(githubElements['github-login'].hidden, false);
assert.equal(githubElements['github-logout'].hidden, true);

vm.runInContext(`applyGithubStatus({
  configured: true,
  authenticated: false,
  state: 'unavailable'
})`, behaviorContext);
assert.equal(githubElements['home-github-summary'].textContent, '待检查');
assert.equal(githubElements['github-account-copy'].textContent, 'Agent 未连接，暂无法检查 GitHub');

vm.runInContext(`applyGithubStatus({
  configured: true,
  authenticated: true,
  state: 'ready',
  account: { login: 'existing-user' }
})`, behaviorContext);
assert.equal(starsRequests().length, 2, 're-authentication after logout must auto-load Stars again');

sentMessages.length = 0;
vm.runInContext(`requestGithubImportStatus('batch-restore')`, behaviorContext);
assert.equal(sentMessages[0].type, 'github_import_status');
assert.equal(sentMessages[0].batchId, 'batch-restore');

const recoveredViews = JSON.parse(vm.runInContext(`JSON.stringify(githubBatchItems({
  items: [
    { state: 'running', repository: { fullName: 'openai/running' } },
    { state: 'failed', repository: { fullName: 'openai/failed' }, error: { message: 'mock failure' } }
  ]
}).map(githubImportItemView))`, behaviorContext));
assert.equal(recoveredViews[0].text, 'openai/running：正在入库');
assert.equal(recoveredViews[1].text, 'openai/failed：mock failure');

sentMessages.length = 0;
behaviorContext.document.body.dataset.view = 'settings-detail-view';
vm.runInContext(`
  githubValidationRequestedForConnection = false;
  requestGithubValidationAfterHandshake({ canOperate: true });
`, behaviorContext);
assert.equal(sentMessages.length, 1, 'handshake must validate GitHub regardless of the restored view');
assert.equal(sentMessages[0].type, 'github_status_request');
assert.equal(sentMessages[0].validate, true);

console.log('GitHub extension contract checks passed');
