'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'chrome-extension/popup/popup.html'), 'utf8');
const css = fs.readFileSync(path.join(root, 'chrome-extension/popup/popup.css'), 'utf8');
const js = fs.readFileSync(path.join(root, 'chrome-extension/popup/popup.js'), 'utf8');
const visualMock = fs.readFileSync(path.join(root, 'tests/fixtures/popup_visual_mock.html'), 'utf8');
const settingsDetailIds = [
  'agent-settings',
  'api-settings',
  'video-settings',
  'vault-settings',
  'cookie-settings',
  'task-settings'
];

assert.match(html, /class="status-strip"[\s\S]*id="status-agent"[\s\S]*id="status-api"[\s\S]*id="status-cookie"[\s\S]*id="status-vault"/);
assert.match(html, /<h1>Agent-wiki 控制台<\/h1>/);
assert.doesNotMatch(html, new RegExp(['知识库', '控制台'].join('')));
assert.equal((html.match(/<h1\b/g) || []).length, 1, 'all popup views must share one brand heading');
for (const id of ['status-agent', 'status-api', 'status-cookie', 'status-vault']) {
  assert.match(html, new RegExp(`<span class="status-indicator" id="${id}"`), `${id} must use display-only markup`);
  assert.doesNotMatch(html, new RegExp(`<button[^>]+id="${id}"`), `${id} must be display-only`);
  assert.doesNotMatch(html, new RegExp(`id="${id}"[^>]+(?:tabindex|role="button")`), `${id} must not be interactive`);
}
for (const [id, label] of Object.entries({
  agent: 'Agent',
  api: 'API',
  cookie: 'Cookie',
  vault: '知识库'
})) {
  assert.match(
    html,
    new RegExp(`id="status-${id}"[\\s\\S]*?<b>${label}</b>[\\s\\S]*?id="${id}-status-dot"[\\s\\S]*?id="${id}-status-text"`),
    `${label} status must stay on one compact display row`
  );
}
assert.doesNotMatch(html, /system-summary|系统就绪/);
assert.match(html, /<button class="task-activity-entry" id="status-tasks"/);
assert.match(html, /id="back-home-from-index"[^>]*>← 首页</);
assert.match(html, /id="back-settings-index"[^>]*>← 设置</);
for (const id of settingsDetailIds) {
  assert.match(html, new RegExp(`class="settings-card"[^>]+data-target="${id}"`));
  assert.match(html, new RegExp(`class="settings-group detail-section" id="${id}"`));
}

const apiSettingsSection = html.match(/<section class="settings-group detail-section" id="api-settings"[\s\S]*?<\/section>/)?.[0] || '';
const arkApiKeyLink = apiSettingsSection.match(/<a class="api-key-link icon-text-button" id="ark-api-key-link"[^>]*>/)?.[0] || '';
assert.match(
  arkApiKeyLink,
  /href="https:\/\/console\.volcengine\.com\/ark\/region:ark\+cn-beijing\/apiKey"/,
  'API settings must link directly to the official Ark API Key page'
);
assert.match(arkApiKeyLink, /target="_blank"/);
assert.match(arkApiKeyLink, /rel="noopener noreferrer"/);
assert.match(arkApiKeyLink, /aria-label="点这里获取火山方舟 API Key（在新标签页打开）"/);
assert.doesNotMatch(arkApiKeyLink, /onclick=|data-(?:key|value|action)=/);
assert.match(apiSettingsSection, />点这里获取火山方舟 API Key</);
assert.match(apiSettingsSection, /data-lucide-icon="external-link"[\s\S]*?aria-hidden="true"/);

const videoSettingsSection = html.match(/<section class="settings-group detail-section" id="video-settings"[\s\S]*?<\/section>/)?.[0] || '';
assert.match(videoSettingsSection, /id="analysis-model-preset" value="lite"/);
assert.match(videoSettingsSection, /data-value="lite" data-model-id="doubao-seed-2-0-lite-260428"/);
assert.match(videoSettingsSection, /data-value="mini" data-model-id="doubao-seed-2-0-mini-260428"/);
assert.match(videoSettingsSection, /id="task-concurrency" value="2"/);
assert.match(videoSettingsSection, /id="chunk-concurrency" value="2"/);

for (const id of [
  'select-knowledge-base',
  'vault-confirmation-modal',
  'vault-selection-folder',
  'confirm-vault-selection',
  'cancel-vault-confirmation'
]) {
  assert.match(html, new RegExp(`id="${id}"`), `missing knowledge base control: ${id}`);
}
const vaultSection = html.match(/<section class="settings-group detail-section" id="vault-settings"[\s\S]*?<\/section>/)?.[0] || '';
assert.match(vaultSection, />选择知识库</);
assert.doesNotMatch(vaultSection, /<input|<select|知识库名称|根目录|父目录/);
for (const removed of [
  'scan-knowledge-bases',
  'create-knowledge-base',
  'switch-knowledge-base',
  'migrate-knowledge-base',
  'vault-candidate-select',
  'vault-name',
  'vault-migration-summary'
]) {
  assert.doesNotMatch(html, new RegExp(`id="${removed}"`));
}
assert.doesNotMatch(html, />新建知识库|>切换知识库|>迁移现有知识库/);
assert.doesNotMatch(html, /Git 仓库/);

assert.match(css, /--popup-width:\s*390px/);
assert.match(css, /--popup-height:\s*600px/);
assert.match(css, /:root\s*\{[\s\S]*?color-scheme:\s*dark;[\s\S]*?--bg:\s*#15171c/);
assert.match(css, /@media\s*\(prefers-color-scheme:\s*light\)\s*\{[\s\S]*?color-scheme:\s*light;[\s\S]*?--bg:\s*#f5f7f8/);
assert.match(css, /:root\s*\{[\s\S]*?--github-mark-bg:\s*#0d1117;[\s\S]*?--github-mark-text:\s*#f0f6fc;[\s\S]*?--github-mark-border:\s*#30363d/);
assert.match(css, /@media\s*\(prefers-color-scheme:\s*light\)\s*\{[\s\S]*?--github-mark-bg:\s*#ffffff;[\s\S]*?--github-mark-text:\s*#24292f;[\s\S]*?--github-mark-border:\s*#d0d7de/);
assert.match(css, /:root\[data-theme="dark"\]\s*\{[\s\S]*?--github-mark-bg:\s*#0d1117;[\s\S]*?--github-mark-text:\s*#f0f6fc/);
assert.match(css, /:root\[data-theme="light"\]\s*\{[\s\S]*?--github-mark-bg:\s*#ffffff;[\s\S]*?--github-mark-text:\s*#24292f/);
assert.match(css, /\.github-feature-mark\s*\{[\s\S]*?border:\s*1px solid var\(--github-mark-border\);[\s\S]*?background:\s*var\(--github-mark-bg\);[\s\S]*?color:\s*var\(--github-mark-text\)/);
assert.match(css, /\.github-feature-mark svg\s*\{[\s\S]*?stroke:\s*currentColor/);
assert.match(css, /\.github-feature-entry:hover,[\s\S]*?\.github-feature-entry:focus-visible\s*\{[\s\S]*?background:\s*var\(--panel-hover\)/);
assert.match(css, /\.github-feature-entry:hover \.github-feature-mark,[\s\S]*?\.github-feature-entry:focus-visible \.github-feature-mark\s*\{[\s\S]*?background:\s*var\(--github-mark-bg\);[\s\S]*?color:\s*var\(--github-mark-text\)/);
assert.match(css, /\.topbar\s*\{[\s\S]*?display:\s*grid;[\s\S]*?grid-template-columns:\s*34px minmax\(0, 1fr\) 34px/);
assert.match(css, /\.topbar-copy\s*\{[^}]*display:\s*contents/);
assert.match(css, /\.topbar-title-row\s*\{[\s\S]*?grid-column:\s*2;[\s\S]*?justify-content:\s*center/);
const statusStripRule = css.match(/\.status-strip\s*\{([^}]*)\}/)?.[1] || '';
assert.match(statusStripRule, /display:\s*flex/);
assert.match(statusStripRule, /justify-content:\s*space-between/);
assert.match(statusStripRule, /gap:\s*6px/);
assert.match(statusStripRule, /flex-wrap:\s*nowrap/);
assert.match(statusStripRule, /grid-column:\s*1 \/ -1/);
assert.match(statusStripRule, /width:\s*100%/);
assert.match(statusStripRule, /max-width:\s*100%/);
assert.doesNotMatch(statusStripRule, /grid-template-columns/);
assert.match(css, /\.topbar > \.icon-button\s*\{[\s\S]*?grid-column:\s*3;[\s\S]*?grid-row:\s*1/);
assert.match(css, /body\[data-view="settings-index-view"\] #open-settings,[\s\S]*?body\[data-view="settings-detail-view"\] #open-settings,[\s\S]*?body\[data-view="github-view"\] #open-settings\s*\{[\s\S]*?visibility:\s*hidden;[\s\S]*?pointer-events:\s*none/);
const statusIndicatorRule = css.match(/\.status-indicator\s*\{([^}]*)\}/)?.[1] || '';
assert.match(statusIndicatorRule, /display:\s*inline-flex/);
assert.match(statusIndicatorRule, /flex:\s*0 1 auto/);
assert.match(statusIndicatorRule, /min-width:\s*0/);
assert.match(statusIndicatorRule, /max-width:\s*100%/);
const compactStatusRule = css.match(/\.status-indicator b,\s*\.status-indicator em\s*\{([^}]*)\}/)?.[1] || '';
assert.doesNotMatch(compactStatusRule, /text-overflow|white-space:\s*nowrap|overflow:\s*hidden/);
const statusTextRule = css.match(/\.status-indicator b\s*\{[^}]*\}\s*\.status-indicator em\s*\{([^}]*)\}/)?.[1] || '';
assert.match(statusTextRule, /flex:\s*0 1 auto/);
assert.match(statusTextRule, /overflow-wrap:\s*anywhere/);
assert.match(statusTextRule, /word-break:\s*break-word/);
assert.doesNotMatch(statusTextRule, /text-overflow|white-space:\s*nowrap|overflow:\s*hidden/);
assert.match(css, /body\s*\{[\s\S]*?border:\s*0;[\s\S]*?border-radius:\s*0;/);
assert.match(css, /\.view\s*\{[\s\S]*?overflow-y:\s*auto/);
assert.match(css, /\.task-head\s*\{[\s\S]*?grid-template-columns:\s*minmax\(0, 1fr\) auto/);
assert.match(css, /\.task-head strong\s*\{[\s\S]*?overflow-wrap:\s*anywhere/);
assert.match(css, /button\.primary,[\s\S]*?button\.secondary\s*\{[\s\S]*?overflow-wrap:\s*anywhere/);
const apiKeyLinkRule = css.match(/\.api-key-link\s*\{([^}]*)\}/)?.[1] || '';
assert.match(apiKeyLinkRule, /display:\s*inline-flex/);
assert.match(apiKeyLinkRule, /max-width:\s*100%/);
assert.match(apiKeyLinkRule, /min-width:\s*0/);
assert.match(apiKeyLinkRule, /overflow-wrap:\s*anywhere/);
assert.match(apiKeyLinkRule, /color:\s*var\(--primary\)/);
assert.match(css, /\.api-key-link:focus-visible\s*\{[\s\S]*?box-shadow:\s*0 0 0 3px var\(--focus\)/);
const derivedTitleRule = css.match(/\.derived-head strong\s*\{([^}]*)\}/)?.[1] || '';
assert.match(derivedTitleRule, /flex:\s*1 1 0/);
assert.match(derivedTitleRule, /overflow-wrap:\s*anywhere/);
assert.match(derivedTitleRule, /white-space:\s*normal/);
assert.doesNotMatch(derivedTitleRule, /overflow:\s*hidden|text-overflow:\s*ellipsis|white-space:\s*nowrap/);
assert.match(css, /\.derived-actions\s*\{[^}]*flex-wrap:\s*wrap/);
assert.match(visualMock, /mock-account-with-an-intentionally-very-long-login-name/);
assert.match(visualMock, /scenario === 'api'[\s\S]*?openSettingsDetail\('api-settings'/);
assert.match(visualMock, /超长派生候选名称也必须稳定换行且不挤压确认与忽略按钮/);
assert.match(visualMock, /scenario === 'header-long-home' \|\| scenario === 'header-long-settings'/);
assert.match(visualMock, /等待浏览器同步较长状态/);
assert.match(visualMock, /正在识别很长的知识库状态/);
assert.match(visualMock, /https:\/\/mock\.invalid\//);
assert.doesNotMatch(visualMock, /fetch\(|WebSocket|chrome\.(?:runtime|storage|tabs)|github\.com|douyin\.com/);

for (const [name, type] of Object.entries({
  SELECT_FOLDER: 'vault_select_folder',
  SELECT_CONFIRM: 'vault_select_confirm'
})) {
  assert.match(js, new RegExp(`${name}: '${type}'`), `missing knowledge base message: ${type}`);
}
assert.match(js, /case 'vault_lifecycle_status':/);
assert.doesNotMatch(js, /knowledge_base_(?:scan|create|switch|migrate)|case 'vault_status':/);
assert.match(js, /function requestVaultLifecycle[\s\S]*?data:\s*payload/);
assert.match(js, /function selectVaultFolder[\s\S]*?SELECT_FOLDER/);
assert.match(js, /function confirmVaultSelection[\s\S]*?SELECT_CONFIRM[\s\S]*?selectionId/);
assert.match(js, /status\.activeVault\?\.vaultPath/);
assert.doesNotMatch(js, /vault_create|vault_migration_|vault_candidate_confirm|buildVaultCreatePayload|normalizeVaultCandidates/);
assert.match(js, /function setView\(viewId\) \{\s*releaseFocusBeforeViewChange\(viewId\);/);
assert.match(js, /DOMContentLoaded[\s\S]*?initColorScheme\(\)/);
assert.match(js, /back-settings-index'\)\.addEventListener\('click', closeSettingsDetailToIndex\)/);
assert.doesNotMatch(js, /system-summary|系统就绪/);

async function main() {
  const stored = {};
  const chrome = {
    storage: {
      session: {
        async get(key) {
          return { [key]: stored[key] };
        },
        async set(values) {
          Object.assign(stored, JSON.parse(JSON.stringify(values)));
        },
        async remove(key) {
          delete stored[key];
        }
      }
    }
  };
  const context = vm.createContext({
    AgentWikiRuntime: {
      extensionVersion: () => 'test',
      canSendMessage: () => true,
      buildHandshake: () => ({ type: 'handshake' })
    },
    WebSocket: { OPEN: 1 },
    chrome,
    console: { log() {}, error() {} },
    Date,
    document: {
      body: { dataset: { view: 'home-view' } },
      documentElement: { dataset: {} },
      addEventListener() {}
    },
    setTimeout,
    clearTimeout,
    setInterval,
    clearInterval
  });
  vm.runInContext(js, context, { filename: 'popup.js' });

  const route = expression => JSON.parse(vm.runInContext(`JSON.stringify(${expression})`, context));
  assert.deepEqual(route(`sanitizePopupRoute({ view: 'settings-index-view', apiKey: 'secret' })`), {
    view: 'settings-index-view'
  });
  assert.deepEqual(route(`sanitizePopupRoute({ view: 'settings-detail-view', detailId: 'vault-settings', token: 'secret' })`), {
    view: 'settings-detail-view',
    detailId: 'vault-settings'
  });
  assert.deepEqual(route(`sanitizePopupRoute({ view: 'settings-detail-view', detailId: 'unknown', userCode: 'secret' })`), {
    view: 'settings-index-view'
  });
  assert.deepEqual(route(`sanitizePopupRoute({ view: 'not-a-view', fields: { endpoint: 'secret' } })`), {
    view: 'home-view'
  });
  assert.deepEqual(route(`sanitizePopupRoute('github')`), { view: 'github-view' });

  const focusTransition = route(`(() => {
    const operations = [];
    const views = Object.fromEntries([
      'home-view', 'settings-index-view', 'settings-detail-view', 'github-view'
    ].map(id => [id, {
      id,
      inert: false,
      classList: { toggle() {} },
      setAttribute(name, value) {
        if (id === 'settings-detail-view' && name === 'aria-hidden' && value === 'true') {
          operations.push('hide-detail');
        }
      }
    }]));
    const focusedBackButton = {
      closest(selector) {
        return selector === '.view' ? views['settings-detail-view'] : null;
      },
      blur() {
        operations.push('blur');
      }
    };
    document.activeElement = focusedBackButton;
    document.getElementById = id => views[id];
    document.body.scrollTop = 42;
    setView('home-view');
    return { operations, scrollTop: document.body.scrollTop };
  })()`);
  assert.deepEqual(focusTransition, {
    operations: ['blur', 'hide-detail'],
    scrollTop: 0
  });

  const sameViewFocus = route(`(() => {
    let blurCount = 0;
    document.activeElement = {
      closest(selector) {
        return selector === '.view' ? { id: 'home-view' } : null;
      },
      blur() {
        blurCount += 1;
      }
    };
    releaseFocusBeforeViewChange('home-view');
    return { blurCount };
  })()`);
  assert.deepEqual(sameViewFocus, { blurCount: 0 });

  const themeTransition = route(`(() => {
    let changeListener = null;
    let requestedQuery = '';
    globalThis.matchMedia = query => {
      requestedQuery = query;
      return {
        matches: true,
        addEventListener(type, listener) {
          if (type === 'change') changeListener = listener;
        }
      };
    };
    document.documentElement.dataset = {};
    initColorScheme();
    const dark = document.documentElement.dataset.theme;
    changeListener({ matches: false });
    const light = document.documentElement.dataset.theme;
    changeListener({ matches: true });
    return { requestedQuery, dark, light, darkAgain: document.documentElement.dataset.theme };
  })()`);
  assert.deepEqual(themeTransition, {
    requestedQuery: '(prefers-color-scheme: dark)',
    dark: 'dark',
    light: 'light',
    darkAgain: 'dark'
  });

  const compactStatuses = route(`({
    agentOnline: compactStatusText('agent', 'online'),
    agentOffline: compactStatusText('agent', 'offline'),
    apiWarning: compactStatusText('api', 'warning'),
    cookieOnline: compactStatusText('cookie', 'online'),
    cookieWarning: compactStatusText('cookie', 'warning'),
    vaultWarning: compactStatusText('vault', 'warning')
  })`);
  assert.deepEqual(compactStatuses, {
    agentOnline: '已连接',
    agentOffline: '未连接',
    apiWarning: '待检查',
    cookieOnline: '已同步',
    cookieWarning: '待同步',
    vaultWarning: '待识别'
  });

  const statusDetailSeparation = route(`(() => {
    const elements = Object.fromEntries([
      'agent-status-dot',
      'agent-status-text',
      'settings-agent-dot',
      'settings-agent-summary'
    ].map(id => [id, { className: '', textContent: '' }]));
    document.getElementById = id => elements[id] || null;
    setStatus('agent', '服务 v9.9.9', 'online', '07月16日 12:34');
    return {
      top: elements['agent-status-text'].textContent,
      topTone: elements['agent-status-text'].className,
      settings: elements['settings-agent-summary'].textContent,
      settingsTone: elements['settings-agent-summary'].className
    };
  })()`);
  assert.deepEqual(statusDetailSeparation, {
    top: '已连接',
    topTone: 'online',
    settings: '服务 v9.9.9 · 07月16日 12:34',
    settingsTone: 'online'
  });

  const detailNavigation = route(`(() => {
    const makeClassList = () => {
      const values = new Set();
      return {
        add(value) { values.add(value); },
        remove(value) { values.delete(value); },
        toggle(value, enabled) { enabled ? values.add(value) : values.delete(value); },
        contains(value) { return values.has(value); }
      };
    };
    const views = Object.fromEntries(Object.values(POPUP_VIEWS).map(id => [id, {
      id,
      inert: false,
      classList: makeClassList(),
      setAttribute() {}
    }]));
    const sections = Object.fromEntries(Object.keys(SETTINGS_DETAIL_TITLES).map(id => [id, {
      id,
      hidden: true,
      dataset: { title: SETTINGS_DETAIL_TITLES[id] },
      classList: makeClassList()
    }]));
    const title = { textContent: '' };
    const elements = { ...views, ...sections, 'settings-detail-title': title };
    document.getElementById = id => elements[id] || null;
    document.querySelectorAll = selector => selector === '.detail-section' ? Object.values(sections) : [];
    document.activeElement = null;

    return Object.keys(SETTINGS_DETAIL_TITLES).map(id => {
      openSettingsDetail(id, { persist: false, focus: false });
      const detailView = document.body.dataset.view;
      const onlyTargetVisible = Object.values(sections).every(section => section.hidden === (section.id !== id));
      closeSettingsDetailToIndex({ focus: false });
      return {
        id,
        detailView,
        onlyTargetVisible,
        parentView: document.body.dataset.view,
        allDetailsHidden: Object.values(sections).every(section => section.hidden)
      };
    });
  })()`);
  assert.deepEqual(detailNavigation.map(item => item.id), settingsDetailIds);
  for (const result of detailNavigation) {
    assert.equal(result.detailView, 'settings-detail-view', `${result.id} must open in the detail view`);
    assert.equal(result.onlyTargetVisible, true, `${result.id} must be the only visible detail`);
    assert.equal(result.parentView, 'settings-index-view', `${result.id} must return to settings index`);
    assert.equal(result.allDetailsHidden, true, `${result.id} must be hidden after returning`);
  }

  assert.equal(route(`JSON.stringify(VAULT_MESSAGE_TYPES)`), JSON.stringify({
    SELECT_FOLDER: 'vault_select_folder',
    SELECT_CONFIRM: 'vault_select_confirm'
  }));

  await vm.runInContext(`persistPopupRoute({
    view: 'settings-detail-view',
    detailId: 'api-settings',
    apiKey: 'placeholder_for_route_test',
    verificationCode: 'placeholder_for_route_test',
    fields: { endpoint: 'placeholder_for_route_test' }
  })`, context);
  assert.deepEqual(stored.popupRoute, {
    view: 'settings-detail-view',
    detailId: 'api-settings'
  });

  const sameYear = vm.runInContext(
    `formatDateTime(new Date(2026, 6, 15, 9, 5), new Date(2026, 0, 1))`,
    context
  );
  const crossYear = vm.runInContext(
    `formatDateTime(new Date(2025, 11, 31, 23, 59), new Date(2026, 0, 1))`,
    context
  );
  assert.equal(sameYear, '07月15日 09:05');
  assert.equal(crossYear, '2025年12月31日 23:59');

  console.log('Popup UI contract checks passed');
}

main().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
