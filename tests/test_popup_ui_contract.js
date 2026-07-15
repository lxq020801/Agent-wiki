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
for (const id of ['status-agent', 'status-api', 'status-cookie', 'status-vault']) {
  assert.doesNotMatch(html, new RegExp(`<button[^>]+id="${id}"`), `${id} must be display-only`);
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

for (const id of [
  'scan-knowledge-bases',
  'create-knowledge-base',
  'switch-knowledge-base',
  'migrate-knowledge-base',
  'vault-workflow-modal',
  'vault-candidate-select',
  'vault-name',
  'vault-migration-summary',
  'confirm-vault-workflow',
  'rollback-vault-migration'
]) {
  assert.match(html, new RegExp(`id="${id}"`), `missing knowledge base control: ${id}`);
}
assert.match(html, />新建知识库</);
assert.match(html, />切换知识库</);
assert.match(html, />迁移现有知识库</);
assert.doesNotMatch(html, /Git 仓库/);

assert.match(css, /--popup-height:\s*600px/);
assert.match(css, /:root\s*\{[\s\S]*?color-scheme:\s*dark;[\s\S]*?--bg:\s*#15171c/);
assert.match(css, /@media\s*\(prefers-color-scheme:\s*light\)\s*\{[\s\S]*?color-scheme:\s*light;[\s\S]*?--bg:\s*#f5f7f8/);
assert.match(css, /\.status-strip\s*\{[\s\S]*?grid-template-columns:\s*repeat\(4, minmax\(0, 1fr\)\)/);
assert.match(css, /\.status-indicator\s*\{[\s\S]*?grid-template-columns:\s*auto 6px minmax\(0, 1fr\)/);
const compactStatusRule = css.match(/\.status-indicator b,\s*\.status-indicator em\s*\{([^}]*)\}/)?.[1] || '';
assert.doesNotMatch(compactStatusRule, /text-overflow|white-space:\s*nowrap|overflow:\s*hidden/);
assert.match(css, /\.status-indicator em\s*\{[^}]*overflow-wrap:\s*anywhere/);
assert.match(css, /body\s*\{[\s\S]*?border:\s*0;[\s\S]*?border-radius:\s*0;/);
assert.match(css, /\.view\s*\{[\s\S]*?overflow-y:\s*auto/);
assert.match(css, /\.task-head\s*\{[\s\S]*?grid-template-columns:\s*minmax\(0, 1fr\) auto/);
assert.match(css, /\.task-head strong\s*\{[\s\S]*?overflow-wrap:\s*anywhere/);
assert.match(css, /button\.primary,[\s\S]*?button\.secondary\s*\{[\s\S]*?overflow-wrap:\s*anywhere/);
assert.match(visualMock, /mock-account-with-an-intentionally-very-long-login-name/);
assert.match(visualMock, /https:\/\/mock\.invalid\//);
assert.doesNotMatch(visualMock, /fetch\(|WebSocket|chrome\.(?:runtime|storage|tabs)|github\.com|douyin\.com/);

for (const [name, type] of Object.entries({
  SCAN: 'vault_scan',
  CREATE: 'vault_create',
  SWITCH: 'vault_switch',
  CANDIDATE_CONFIRM: 'vault_candidate_confirm',
  MIGRATION_PREVIEW: 'vault_migration_preview',
  MIGRATION_EXECUTE: 'vault_migration_execute',
  MIGRATION_ROLLBACK: 'vault_migration_rollback'
})) {
  assert.match(js, new RegExp(`${name}: '${type}'`), `missing knowledge base message: ${type}`);
}
assert.match(js, /case 'vault_lifecycle_status':/);
assert.doesNotMatch(js, /knowledge_base_(?:scan|create|switch|migrate)|case 'vault_status':/);
assert.match(js, /function requestVaultLifecycle[\s\S]*?data:\s*payload/);
assert.match(js, /function buildVaultCreatePayload[\s\S]*?userName:\s*name[\s\S]*?obsidianRoot/);
assert.match(js, /function confirmVaultWorkflow[\s\S]*?CANDIDATE_CONFIRM[\s\S]*?MIGRATION_EXECUTE[\s\S]*?MIGRATION_PREVIEW/);
assert.match(js, /function rollbackVaultMigration[\s\S]*?MIGRATION_ROLLBACK/);
assert.match(js, /status\.migration\?\.migrationId/);
assert.match(js, /status\.activeVault\?\.vaultPath/);
assert.match(js, /state === 'ready' && action !== 'scan'/);
assert.match(js, /status\.ok === false \|\| \['failed', 'error', 'rejected'\]/);
assert.match(js, /function setView\(viewId\) \{\s*releaseFocusBeforeViewChange\(viewId\);/);
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

  assert.deepEqual(route(`buildVaultCreatePayload({ id: 'root-1', obsidianRoot: '/tmp/root' }, 'Demo')`), {
    userName: 'Demo',
    obsidianRoot: '/tmp/root'
  });
  assert.deepEqual(route(`buildVaultMigrationPreviewPayload({ id: 'root-2', obsidianRoot: '/tmp/target' }, 'Moved', '/tmp/source')`), {
    sourcePath: '/tmp/source',
    userName: 'Moved',
    obsidianRoot: '/tmp/target'
  });
  assert.deepEqual(route(`(() => {
    vaultWorkflow.mode = 'switch';
    return normalizeVaultCandidates({
      obsidianRoots: [{ candidateId: 'root-1', kind: 'obsidian_root', obsidianRoot: '/tmp/root' }],
      vaultCandidates: [{
        candidateId: 'vault-1', kind: 'agent_wiki_vault', vaultPath: '/tmp/vault',
        vaultId: 'identity-1', userName: 'Demo', supportedActions: ['switch']
      }]
    });
  })()`), [{
    id: 'vault-1',
    path: '/tmp/vault',
    label: 'Demo',
    kind: 'agent_wiki_vault',
    obsidianRoot: '',
    parentDirectory: '',
    vaultPath: '/tmp/vault',
    vaultId: 'identity-1',
    key: 'vault-1',
    requiresConfirmation: false
  }]);

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
