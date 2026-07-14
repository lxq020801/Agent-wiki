'use strict';

const assert = require('node:assert/strict');

global.chrome = {
  runtime: {
    getManifest: () => ({ version: '0.1.0' })
  }
};

const runtimeVersion = require('../chrome-extension/runtime-version.js');

function currentRuntime(overrides = {}) {
  return {
    product: 'agent-wiki',
    productVersion: '0.1.0',
    protocolVersion: 1,
    sourceRevision: 'abcdef123456',
    buildId: 'src-1234567890abcdef',
    deployment: { state: 'current', code: 'source_checkout' },
    ...overrides
  };
}

const handshake = runtimeVersion.buildHandshake('agent-wiki-extension');
assert.deepEqual(handshake, {
  type: 'handshake',
  client: 'agent-wiki-extension',
  product: 'agent-wiki',
  version: '0.1.0',
  protocolVersion: 1
});

const matching = runtimeVersion.evaluateRuntimeCompatibility({ runtime: currentRuntime() });
assert.equal(matching.state, 'compatible');
assert.equal(matching.canOperate, true);
assert.equal(runtimeVersion.canSendMessage('task_request', matching), true);

const versionMismatch = runtimeVersion.evaluateRuntimeCompatibility({
  runtime: currentRuntime({ productVersion: '0.0.9' })
});
assert.equal(versionMismatch.state, 'version_mismatch');
assert.equal(versionMismatch.canOperate, false);
assert.match(versionMismatch.message, /扩展 v0\.1\.0 与服务 v0\.0\.9 不一致/);

const protocolMismatch = runtimeVersion.evaluateRuntimeCompatibility({
  runtime: currentRuntime({ protocolVersion: 2 })
});
assert.equal(protocolMismatch.state, 'protocol_mismatch');
assert.equal(protocolMismatch.canOperate, false);
assert.match(protocolMismatch.message, /扩展协议 v1 与服务协议 v2 不一致/);

const missingVersion = runtimeVersion.evaluateRuntimeCompatibility({
  runtime: currentRuntime({ productVersion: '' })
});
assert.equal(missingVersion.state, 'legacy_server');
assert.equal(missingVersion.canOperate, false);

const oldService = runtimeVersion.evaluateRuntimeCompatibility({
  type: 'agent_ready',
  version: '0.1.0'
});
assert.equal(oldService.state, 'legacy_server');
assert.equal(oldService.runtime.productVersion, '0.1.0');
assert.equal(runtimeVersion.canSendMessage('status_request', oldService), true);
assert.equal(runtimeVersion.canSendMessage('task_request', oldService), false);

const legacyDeployment = runtimeVersion.evaluateRuntimeCompatibility({
  runtime: currentRuntime({
    deployment: { state: 'legacy_path', code: 'legacy_source_path' }
  })
});
assert.equal(legacyDeployment.state, 'legacy_deployment');
assert.match(legacyDeployment.message, /不要创建旧路径兼容目录/);

const untrusted = runtimeVersion.evaluateRuntimeCompatibility({
  runtime: {
    ...currentRuntime(),
    sourceRevision: '/Users/private/api-key-secret',
    localPath: '/Users/private/Agent-wiki',
    apiKey: 'should-not-leak',
    buildId: 'src-1234567890abcdef'
  }
});
const serialized = JSON.stringify(untrusted);
assert.equal(untrusted.state, 'compatible');
assert.doesNotMatch(serialized, /Users|api-key-secret|should-not-leak|localPath|apiKey/);

console.log('Extension runtime version protocol checks passed');
