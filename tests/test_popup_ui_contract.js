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

assert.match(html, /class="status-strip"[\s\S]*id="status-agent"[\s\S]*id="status-api"[\s\S]*id="status-cookie"[\s\S]*id="status-vault"/);
for (const id of ['status-agent', 'status-api', 'status-cookie', 'status-vault']) {
  assert.doesNotMatch(html, new RegExp(`<button[^>]+id="${id}"`), `${id} must be display-only`);
}
assert.match(html, /<button class="task-activity-entry" id="status-tasks"/);

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
