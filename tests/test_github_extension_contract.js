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
  'github-search-results',
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

assert.match(html, /<main class="view" id="github-view"[\s\S]*id="github-search-tool"[\s\S]*id="github-stars-tool"/);
assert.doesNotMatch(html, /class="settings-card"[^>]*data-target="github-settings"/);
assert.doesNotMatch(html, /id="github-settings"/);
const settingsIndexMarkup = html.slice(html.indexOf('id="settings-index-view"'), html.indexOf('id="settings-detail-view"'));
const settingsDetailMarkup = html.slice(html.indexOf('id="settings-detail-view"'), html.indexOf('id="github-view"'));
assert.doesNotMatch(settingsIndexMarkup, /GitHub|github/i);
assert.doesNotMatch(settingsDetailMarkup, /github-search-tool|github-stars-tool/);

for (const type of [
  'github_auth_start',
  'github_auth_cancel',
  'github_logout',
  'github_repository_search',
  'github_stars_request',
  'github_import_stars',
  'github_import_cancel',
  'github_refresh_check',
  'github_refresh_confirm',
  'github_refresh_cancel'
]) {
  assert.match(js, new RegExp(`type: '${type}'`), `missing GitHub message: ${type}`);
}

assert.doesNotMatch(js, /chrome\.storage\.(?:local|session)\.(?:set|get)\([^\n]*(?:githubToken|accessToken|deviceCode)/i);
assert.doesNotMatch(html + js, /github_pat_[A-Za-z0-9_]{20,}|ghp_[A-Za-z0-9]{20,}/);
assert.match(js, /const POPUP_ROUTE_STORAGE_KEY = 'popupRoute'/);
assert.match(js, /function popupRouteStorage\(\)[\s\S]*?chrome\.storage\?\.session/);
assert.match(js, /storage\.set\(\{ \[POPUP_ROUTE_STORAGE_KEY\]: GITHUB_ROUTE \}\)/);
assert.match(js, /storage\.remove\(POPUP_ROUTE_STORAGE_KEY\)/);
assert.match(js, /function restorePopupRoute\(\)[\s\S]*?openGithubPage\(\{ persist: false, focus: false \}\)/);
assert.match(js, /function closeToHome\(\)[\s\S]*?clearPopupRoute\(\)/);
assert.match(js, /function openGithubPage\([\s\S]*?if \(persist\) void persistGithubRoute\(\)/);
assert.match(js, /async function startGithubAuthorization\(\)[\s\S]*?await persistGithubRoute\(\)/);
const routeStorageContract = js.slice(js.indexOf('function popupRouteStorage'), js.indexOf('async function flushPendingSync'));
assert.doesNotMatch(routeStorageContract, /storage\.local|transientStorage|token|deviceCode|userCode/i);
assert.doesNotMatch(js, /openSettingsDetail\(['"]github-settings/);
assert.match(js, /\['home-view', 'settings-index-view', 'settings-detail-view', 'github-view'\]/);
assert.match(js, /homeSummary\.textContent = login \? `@\$\{login\}` : '已登录'/);
assert.match(js, /copy\.textContent = login \? `@\$\{login\}` : '已登录'/);
assert.match(js, /case 'handshake_ack':[\s\S]*?requestStatus\(\);[\s\S]*?requestGithubValidationAfterHandshake\(compatibility\)/);
assert.match(js, /ws\.onopen = \(\) => \{[\s\S]*?githubValidationRequestedForConnection = false/);
assert.match(js, /function applyGithubStatus[\s\S]*status\.state === 'unchecked'/);
assert.match(js, /status\.activeAuthorization/);
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
    body: { dataset: { view: 'github-view' } },
    addEventListener() {}
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

sentMessages.length = 0;
behaviorContext.document.body.dataset.view = 'home-view';
vm.runInContext(`
  githubValidationRequestedForConnection = false;
  requestGithubValidationAfterHandshake({ canOperate: true });
`, behaviorContext);
assert.equal(sentMessages.length, 0, 'handshake must not validate GitHub while another view is active');

console.log('GitHub extension contract checks passed');
