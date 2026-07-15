'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'chrome-extension/popup/popup.html'), 'utf8');
const js = fs.readFileSync(path.join(root, 'chrome-extension/popup/popup.js'), 'utf8');
const css = fs.readFileSync(path.join(root, 'chrome-extension/popup/popup.css'), 'utf8');

for (const id of [
  'github-settings',
  'github-login',
  'github-device-panel',
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

for (const type of [
  'github_auth_start',
  'github_auth_poll',
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
assert.match(js, /function applyGithubStatus[\s\S]*status\.state === 'unchecked'/);
assert.doesNotMatch(js, /function applyModelStatus[\s\S]*?status\.state === 'unchecked'[\s\S]*?function applyVideoStatus/);
assert.match(js, /githubIntegration\?\.autoStar/);
assert.match(js, /if \(page <= 1\) githubSelected\.clear\(\)/);
assert.match(css, /\[hidden\]\s*\{\s*display:\s*none\s*!important;/);

console.log('GitHub extension contract checks passed');
