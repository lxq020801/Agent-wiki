(function initAgentWikiRuntime(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) {
    module.exports = api;
  }
  root.AgentWikiRuntime = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function buildAgentWikiRuntime() {
  'use strict';

  const PRODUCT_ID = 'agent-wiki';
  const PROTOCOL_VERSION = 1;
  const READ_ONLY_MESSAGE_TYPES = new Set([
    'handshake',
    'status_request',
    'task_status_request'
  ]);

  function safeMatch(value, pattern, maxLength = 64) {
    const text = String(value || '').trim();
    return text.length <= maxLength && pattern.test(text) ? text : '';
  }

  function extensionVersion() {
    try {
      return safeMatch(
        globalThis.chrome?.runtime?.getManifest?.().version,
        /^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$/
      );
    } catch (_err) {
      return '';
    }
  }

  function protocolNumber(value) {
    const number = Number(value);
    return Number.isInteger(number) && number >= 1 && number <= 999 ? number : null;
  }

  function normalizeDeployment(value) {
    const state = safeMatch(value?.state, /^(?:current|legacy_path|unverified)$/);
    const code = safeMatch(value?.code, /^(?:source_checkout|packaged_source|legacy_source_path|unverified_source)$/);
    return { state, code };
  }

  function normalizeRuntimeIdentity(message) {
    const envelope = message?.runtime || message?.status?.runtime || null;
    const product = safeMatch(envelope?.product, /^[a-z0-9][a-z0-9-]{0,31}$/);
    const productVersion = safeMatch(
      envelope?.productVersion || message?.version,
      /^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$/
    );
    const protocolVersion = protocolNumber(envelope?.protocolVersion ?? message?.protocolVersion);
    const sourceRevision = safeMatch(envelope?.sourceRevision, /^[0-9a-f]{7,40}$/);
    const buildId = safeMatch(envelope?.buildId, /^src-[0-9a-f]{12,64}$/);
    const deployment = normalizeDeployment(envelope?.deployment);
    const complete = Boolean(
      envelope &&
      product &&
      productVersion &&
      protocolVersion &&
      (sourceRevision || buildId) &&
      deployment.state
    );
    return {
      product,
      productVersion,
      protocolVersion,
      sourceRevision,
      buildId,
      deployment,
      complete
    };
  }

  function buildHandshake(client) {
    return {
      type: 'handshake',
      client: safeMatch(client, /^[a-z0-9][a-z0-9-]{0,63}$/) || 'agent-wiki-extension',
      product: PRODUCT_ID,
      version: extensionVersion(),
      protocolVersion: PROTOCOL_VERSION
    };
  }

  function evaluateRuntimeCompatibility(message, clientVersion = extensionVersion()) {
    const runtime = normalizeRuntimeIdentity(message);
    const currentVersion = safeMatch(
      clientVersion,
      /^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$/
    );
    let state = 'compatible';
    let tone = 'online';
    let messageText = '扩展、服务与协议版本一致。';

    if (!runtime.complete) {
      state = 'legacy_server';
      tone = 'warning';
      messageText = '服务未提供完整版本身份，可能仍在运行旧服务或旧部署。已暂停同步与入库，请停止旧服务后从当前 Agent-wiki 源码重新启动。';
    } else if (runtime.product !== PRODUCT_ID) {
      state = 'product_mismatch';
      tone = 'offline';
      messageText = '当前端口连接到的不是 Agent-wiki 服务。已暂停同步与入库，请检查本地服务。';
    } else if (runtime.protocolVersion !== PROTOCOL_VERSION) {
      state = 'protocol_mismatch';
      tone = 'offline';
      messageText = `扩展协议 v${PROTOCOL_VERSION} 与服务协议 v${runtime.protocolVersion} 不一致。已暂停同步与入库，请更新扩展和服务。`;
    } else if (!currentVersion || currentVersion !== runtime.productVersion) {
      state = 'version_mismatch';
      tone = 'offline';
      messageText = `扩展 v${currentVersion || '未知'} 与服务 v${runtime.productVersion} 不一致。已暂停同步与入库，请重新同步扩展并重启当前服务。`;
    } else if (runtime.deployment.state === 'legacy_path') {
      state = 'legacy_deployment';
      tone = 'offline';
      messageText = '服务由旧源码路径启动。已暂停同步与入库，请停止该服务并从当前 Agent-wiki 仓库启动；不要创建旧路径兼容目录。';
    } else if (runtime.deployment.state !== 'current') {
      state = 'unverified_deployment';
      tone = 'warning';
      messageText = '无法确认服务部署来源。已暂停同步与入库，请从当前 Agent-wiki 仓库重新启动服务。';
    }

    return {
      state,
      tone,
      canOperate: state === 'compatible',
      message: messageText,
      extensionVersion: currentVersion,
      runtime
    };
  }

  function canSendMessage(messageType, compatibility) {
    return READ_ONLY_MESSAGE_TYPES.has(messageType) || Boolean(compatibility?.canOperate);
  }

  return {
    PRODUCT_ID,
    PROTOCOL_VERSION,
    extensionVersion,
    buildHandshake,
    normalizeRuntimeIdentity,
    evaluateRuntimeCompatibility,
    canSendMessage
  };
});
