(() => {
  if (window.__obsidianLibrarianDouyinCurrentVideoLoaded) {
    document.getElementById('obsidian-librarian-douyin-widget')?.remove();
    document.getElementById('obsidian-librarian-douyin-intent-menu')?.remove();
    return;
  }
  window.__obsidianLibrarianDouyinCurrentVideoLoaded = true;

  const MAX_PARENT_DEPTH = 10;
  const CONTEXT_TTL_MS = 15000;
  const NATIVE_HOST_GRACE_MS = 4500;
  const NATIVE_HOST_STICKY_MS = 2000;
  const WIDGET_ID = 'obsidian-librarian-douyin-widget';
  const MENU_ID = 'obsidian-librarian-douyin-intent-menu';
  const TOAST_ID = 'obsidian-librarian-douyin-toast';
  const INGEST_INTENTS = [
    {
      id: 'knowledge_and_viral',
      title: '完整入库',
      desc: '同一来源生成知识资产和创作模式两篇笔记'
    },
    {
      id: 'knowledge_ingest',
      title: '知识入库',
      desc: '沉淀知识、工具、项目、方法和风险'
    },
    {
      id: 'viral_breakdown',
      title: '爆款拆解',
      desc: '沉淀文案、节奏、画面和可迁移创作模式'
    }
  ];
  const scriptStartedAt = now();
  let lastContextCandidate = null;
  let widget = null;
  let widgetButton = null;
  let intentMenu = null;
  let widgetBusy = false;
  let widgetMode = 'floating';
  let widgetRaf = 0;
  let lastWidgetPositionAt = 0;
  let lastNativeHostAt = 0;

  function now() {
    return Date.now();
  }

  function normalizeText(value) {
    return String(value || '').trim();
  }

  function parseCandidateFromText(text, method, score) {
    const value = normalizeText(text);
    if (!value) return null;

    if (/^\d{8,}$/.test(value)) {
      return makeCandidate(value, 'video', method, score, value);
    }

    const videoMatch = value.match(/(?:https?:\/\/[^/\s]+)?\/(?:share\/)?video\/(\d{8,})/i);
    if (videoMatch) {
      return makeCandidate(videoMatch[1], 'video', method, score, value);
    }

    const noteMatch = value.match(/(?:https?:\/\/[^/\s]+)?\/(?:share\/)?note\/(\d{8,})/i);
    if (noteMatch) {
      return makeCandidate(noteMatch[1], 'note', method, score, value);
    }

    const queryMatch = value.match(/[?&](?:modal_id|aweme_id|item_id|itemId|awemeId)=(\d{8,})/i);
    if (queryMatch) {
      return makeCandidate(queryMatch[1], 'video', method, score, value);
    }

    return null;
  }

  function makeCandidate(id, kind, method, score, raw) {
    const type = kind === 'note' ? 'note' : 'video';
    return {
      ok: true,
      awemeId: id,
      type,
      url: `https://www.douyin.com/${type}/${id}`,
      method,
      score,
      raw: raw ? String(raw).slice(0, 300) : '',
      pageUrl: location.href,
      pageTitle: document.title || ''
    };
  }

  function best(candidates) {
    return candidates
      .filter(Boolean)
      .sort((a, b) => (b.score || 0) - (a.score || 0))[0] || null;
  }

  function isElement(node) {
    return node && node.nodeType === Node.ELEMENT_NODE;
  }

  function visibleScore(element) {
    if (!isElement(element)) return 0;
    const rect = element.getBoundingClientRect();
    if (rect.width < 20 || rect.height < 20) return 0;
    if (rect.bottom <= 0 || rect.right <= 0) return 0;
    if (rect.top >= window.innerHeight || rect.left >= window.innerWidth) return 0;

    const visibleWidth = Math.max(0, Math.min(rect.right, window.innerWidth) - Math.max(rect.left, 0));
    const visibleHeight = Math.max(0, Math.min(rect.bottom, window.innerHeight) - Math.max(rect.top, 0));
    const visibleArea = visibleWidth * visibleHeight;
    const centerX = rect.left + rect.width / 2;
    const centerY = rect.top + rect.height / 2;
    const dx = Math.abs(centerX - window.innerWidth / 2) / Math.max(window.innerWidth, 1);
    const dy = Math.abs(centerY - window.innerHeight / 2) / Math.max(window.innerHeight, 1);
    return visibleArea - (dx + dy) * 1000;
  }

  function candidateFromDataset(element, method, score) {
    if (!isElement(element)) return null;
    const keys = [
      'e2eVid',
      'awemeId',
      'awemeid',
      'itemId',
      'itemid',
      'vid',
      'id'
    ];
    const candidates = [];
    for (const key of keys) {
      candidates.push(parseCandidateFromText(element.dataset?.[key], method, score));
    }
    for (const attr of ['data-e2e-vid', 'data-aweme-id', 'data-item-id', 'data-id']) {
      candidates.push(parseCandidateFromText(element.getAttribute(attr), method, score));
    }
    return best(candidates);
  }

  function candidateFromLinks(root, method, score) {
    if (!isElement(root)) return null;
    const links = [];
    if (root.matches?.('a[href]')) links.push(root);
    links.push(...Array.from(root.querySelectorAll?.('a[href]') || []));
    return best(links.map((link) => parseCandidateFromText(link.href, method, score)));
  }

  function candidateFromElement(element, method, baseScore) {
    if (!isElement(element)) return null;
    const candidates = [];
    let node = element;
    for (let depth = 0; node && depth < MAX_PARENT_DEPTH; depth += 1, node = node.parentElement) {
      const score = baseScore - depth * 5;
      candidates.push(candidateFromDataset(node, `${method}:dataset`, score + 30));
      candidates.push(candidateFromLinks(node, `${method}:link`, score));
      candidates.push(parseCandidateFromText(node.getAttribute?.('href'), `${method}:href`, score));
      candidates.push(parseCandidateFromText(node.getAttribute?.('aria-label'), `${method}:aria`, score - 10));
    }
    return best(candidates);
  }

  function candidateFromUrl() {
    return parseCandidateFromText(location.href, 'location', 1000);
  }

  function candidateFromActiveFeed() {
    const selectors = [
      '[data-e2e="feed-active-video"]',
      '[data-e2e-vid]',
      '[data-e2e="feed-video"]',
      '[data-e2e="video-player"]'
    ];
    const candidates = [];
    for (const selector of selectors) {
      for (const element of Array.from(document.querySelectorAll(selector))) {
        const score = 850 + Math.max(0, Math.min(visibleScore(element) / 10000, 100));
        candidates.push(candidateFromElement(element, `active-feed:${selector}`, score));
      }
    }
    return best(candidates);
  }

  function candidateFromVideos() {
    const candidates = [];
    for (const video of Array.from(document.querySelectorAll('video'))) {
      const visibility = visibleScore(video);
      if (visibility <= 0) continue;
      const isPlaying = !video.paused && !video.ended && video.readyState >= 2;
      const timeScore = Math.min(Number(video.currentTime || 0), 120);
      const score = 650 + Math.min(visibility / 10000, 120) + timeScore + (isPlaying ? 220 : 0);
      candidates.push(candidateFromElement(video, isPlaying ? 'playing-video' : 'visible-video', score));
      candidates.push(parseCandidateFromText(video.currentSrc || video.src, 'video-src', score - 100));
    }
    return best(candidates);
  }

  function candidateFromContext() {
    if (!lastContextCandidate) return null;
    if (now() - lastContextCandidate.at > CONTEXT_TTL_MS) return null;
    return lastContextCandidate.candidate;
  }

  function collectCurrentCandidate() {
    const candidate = best([
      candidateFromContext(),
      candidateFromUrl(),
      candidateFromActiveFeed(),
      candidateFromVideos()
    ]);

    if (candidate) {
      return {
        ...candidate,
        collectedAt: new Date().toISOString()
      };
    }

    return {
      ok: false,
      reason: 'douyin_current_video_not_found',
      pageUrl: location.href,
      pageTitle: document.title || '',
      collectedAt: new Date().toISOString()
    };
  }

  function activeVideoElement() {
    const activeSelectors = [
      '[data-e2e="feed-active-video"]',
      '[data-e2e-vid]',
      '[data-e2e="feed-video"]',
      '[data-e2e="video-player"]'
    ];
    const activeElements = activeSelectors
      .flatMap((selector) => Array.from(document.querySelectorAll(selector)))
      .filter((element) => visibleScore(element) > 0)
      .sort((a, b) => visibleScore(b) - visibleScore(a));
    if (activeElements[0]) return activeElements[0];

    return Array.from(document.querySelectorAll('video'))
      .filter((video) => visibleScore(video) > 0)
      .sort((a, b) => {
        const aPlaying = !a.paused && !a.ended && a.readyState >= 2 ? 1000000 : 0;
        const bPlaying = !b.paused && !b.ended && b.readyState >= 2 ? 1000000 : 0;
        return (visibleScore(b) + bPlaying) - (visibleScore(a) + aPlaying);
      })[0] || null;
  }

  function isVisibleElement(element, minWidth = 8, minHeight = 8) {
    if (!isElement(element)) return false;
    const rect = element.getBoundingClientRect();
    if (rect.width < minWidth || rect.height < minHeight) return false;
    if (rect.bottom <= 0 || rect.right <= 0) return false;
    return rect.top < window.innerHeight && rect.left < window.innerWidth;
  }

  function closestVideoRoot(element) {
    if (!isElement(element)) return null;
    return (
      element.closest?.('[data-e2e="feed-active-video"]') ||
      element.closest?.('[data-e2e="feed-video"]') ||
      element.closest?.('[data-e2e="video-player"]') ||
      element.closest?.('.xgplayer') ||
      element.closest?.('[class*="xgplayer"]')
    );
  }

  function findControlBarHost(active) {
    const candidates = [];

    if (active) {
      const activeRoots = [
        active,
        closestVideoRoot(active),
        active.closest?.('.xgplayer'),
        active.querySelector?.('.xgplayer')
      ].filter(Boolean);

      for (const root of activeRoots) {
        candidates.push(
          ...Array.from(root.querySelectorAll?.('xg-right-grid, .xg-right-grid') || [])
        );
        if (root.matches?.('xg-right-grid, .xg-right-grid')) {
          candidates.push(root);
        }
      }
    }

    candidates.push(...Array.from(document.querySelectorAll('xg-right-grid, .xg-right-grid')));

    return candidates
      .filter((host, index, list) => host && list.indexOf(host) === index)
      .filter((host) => isVisibleElement(host, 60, 24))
      .sort((a, b) => {
        const activeRoot = active ? closestVideoRoot(active) : null;
        const aInActive = activeRoot && (a === activeRoot || activeRoot.contains(a)) ? 1000000 : 0;
        const bInActive = activeRoot && (b === activeRoot || activeRoot.contains(b)) ? 1000000 : 0;
        return (visibleScore(b) + bInActive) - (visibleScore(a) + aInActive);
      })[0] || null;
  }

  function stopPlayerEvent(event) {
    event.preventDefault();
    event.stopPropagation();
  }

  function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
  }

  function nativeInsertAnchor(host) {
    const children = Array.from(host.children || [])
      .filter((child) => child.id !== WIDGET_ID);
    const flexDirection = window.getComputedStyle(host).flexDirection || '';
    if (flexDirection.includes('reverse')) {
      return null;
    }

    return children.find((child) => /倍速|智能|清屏|连播/.test(child.innerText || child.textContent || '')) || null;
  }

  function ensureWidget() {
    if (widget && widget.isConnected) {
      return widget;
    }

    widget = document.createElement('div');
    widget.id = WIDGET_ID;
    widget.style.cssText = [
      'position: fixed',
      'z-index: 2147483647',
      'display: none',
      'align-items: center',
      'gap: 6px',
      'pointer-events: auto',
      'font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
    ].join(';');

    widgetButton = document.createElement('button');
    widgetButton.type = 'button';
    widgetButton.textContent = '入库';
    widgetButton.title = '发送当前抖音视频给 Agent 拆解';
    widgetButton.style.cssText = [
      'height: 32px',
      'min-width: 42px',
      'padding: 0 10px',
      'border: 1px solid rgba(255,255,255,.32)',
      'border-radius: 6px',
      'background: rgba(255,255,255,.08)',
      'color: #fff',
      'font-size: 13px',
      'font-weight: 600',
      'line-height: 30px',
      'box-shadow: none',
      'backdrop-filter: blur(12px)',
      '-webkit-backdrop-filter: blur(12px)',
      'cursor: pointer',
      'white-space: nowrap'
    ].join(';');
    widgetButton.addEventListener('mouseenter', () => {
      widgetButton.style.background = widgetMode === 'native' ? 'rgba(255,255,255,.16)' : 'rgba(28,31,38,.88)';
      widgetButton.style.borderColor = 'rgba(255,255,255,.5)';
    });
    widgetButton.addEventListener('mouseleave', () => {
      widgetButton.style.background = widgetMode === 'native' ? 'rgba(255,255,255,.08)' : 'rgba(16,18,22,.72)';
      widgetButton.style.borderColor = 'rgba(255,255,255,.32)';
    });
	    widgetButton.addEventListener('pointerdown', stopPlayerEvent);
	    widgetButton.addEventListener('mousedown', stopPlayerEvent);
	    widgetButton.addEventListener('click', toggleIntentMenu);

	    widget.appendChild(widgetButton);
	    document.documentElement.appendChild(widget);
	    ensureIntentMenu();
	    return widget;
	  }

  function ensureIntentMenu() {
    if (intentMenu) {
      return intentMenu;
    }

    intentMenu = document.createElement('div');
    intentMenu.id = MENU_ID;
    intentMenu.style.cssText = [
      'position: fixed',
      'z-index: 2147483647',
      'display: none',
      'width: 228px',
      'padding: 8px',
      'border: 1px solid rgba(255,255,255,.22)',
      'border-radius: 8px',
      'background: rgba(18,20,26,.94)',
      'box-shadow: 0 14px 34px rgba(0,0,0,.32)',
      'backdrop-filter: blur(18px)',
      '-webkit-backdrop-filter: blur(18px)',
      'font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
      'pointer-events: auto'
    ].join(';');

    for (const intent of INGEST_INTENTS) {
      const card = document.createElement('button');
      card.type = 'button';
      card.dataset.intent = intent.id;
      card.style.cssText = [
        'display: block',
        'width: 100%',
        'padding: 9px 10px',
        'margin: 0',
        'border: 0',
        'border-radius: 6px',
        'background: transparent',
        'color: #fff',
        'text-align: left',
        'cursor: pointer'
      ].join(';');
      card.innerHTML = [
        `<strong style="display:block;font-size:13px;line-height:18px;">${intent.title}</strong>`,
        `<span style="display:block;margin-top:2px;color:rgba(255,255,255,.68);font-size:12px;line-height:16px;">${intent.desc}</span>`
      ].join('');
      card.addEventListener('mouseenter', () => {
        card.style.background = 'rgba(255,255,255,.1)';
      });
      card.addEventListener('mouseleave', () => {
        card.style.background = 'transparent';
      });
      card.addEventListener('pointerdown', stopPlayerEvent);
      card.addEventListener('mousedown', stopPlayerEvent);
      card.addEventListener('click', (event) => submitCurrentVideoFromWidget(event, intent.id));
      intentMenu.appendChild(card);
    }

    intentMenu.addEventListener('pointerdown', stopPlayerEvent);
    intentMenu.addEventListener('mousedown', stopPlayerEvent);
    document.documentElement.appendChild(intentMenu);
    return intentMenu;
  }

  function positionIntentMenu() {
    if (!intentMenu || !widgetButton) return;
    const rect = widgetButton.getBoundingClientRect();
    const width = 228;
    const height = 184;
    const left = clamp(rect.left + rect.width / 2 - width / 2, 12, window.innerWidth - width - 12);
    const top = rect.top > height + 20 ? rect.top - height - 10 : rect.bottom + 10;
    intentMenu.style.left = `${Math.round(left)}px`;
    intentMenu.style.top = `${Math.round(clamp(top, 12, window.innerHeight - height - 12))}px`;
  }

  function hideIntentMenu() {
    if (!intentMenu) return;
    intentMenu.style.display = 'none';
    widgetButton?.setAttribute('aria-expanded', 'false');
  }

  function toggleIntentMenu(event) {
    event.preventDefault();
    event.stopPropagation();
    if (widgetBusy) return;
    ensureIntentMenu();
    if (intentMenu.style.display === 'flex') {
      hideIntentMenu();
      return;
    }
    positionIntentMenu();
    intentMenu.style.display = 'flex';
    intentMenu.style.flexDirection = 'column';
    intentMenu.style.gap = '4px';
    widgetButton?.setAttribute('aria-expanded', 'true');
  }

  function setWidgetNativeMode(host, target) {
    widgetMode = 'native';
    lastNativeHostAt = now();
    const anchor = nativeInsertAnchor(host);
    const shouldAppend = !anchor && target.parentElement === host && target.nextElementSibling !== null;
    if (shouldAppend) {
      host.appendChild(target);
    } else if (
      target.parentElement !== host ||
      (anchor && target.nextElementSibling !== anchor)
    ) {
      host.insertBefore(target, anchor);
    }

    target.style.position = 'static';
    target.style.zIndex = 'auto';
    target.style.display = 'flex';
    target.style.height = '40px';
    target.style.width = '';
    target.style.minWidth = '';
    target.style.flex = '';
    target.style.margin = '0 2px';
    target.style.alignItems = 'center';
    target.style.justifyContent = 'center';
    target.style.overflow = '';
    target.style.pointerEvents = 'auto';
    target.style.left = '';
    target.style.top = '';

    widgetButton.textContent = widgetBusy ? '发送中' : '入库';
    widgetButton.style.position = '';
    widgetButton.style.right = '';
    widgetButton.style.top = '';
    widgetButton.style.transform = '';
    widgetButton.style.width = '';
    widgetButton.style.minWidth = '42px';
    widgetButton.style.height = '32px';
    widgetButton.style.padding = '0 10px';
    widgetButton.style.fontSize = '13px';
    widgetButton.style.lineHeight = '30px';
    widgetButton.style.background = 'rgba(255,255,255,.08)';
    widgetButton.style.boxShadow = 'none';
  }

  function setWidgetFloatingMode(target, rect) {
    widgetMode = 'floating';
    if (target.parentElement !== document.documentElement) {
      document.documentElement.appendChild(target);
    }

    const left = Math.max(12, Math.min(rect.right - 132, window.innerWidth - 148));
    const top = Math.max(12, Math.min(rect.bottom - 56, window.innerHeight - 48));
    target.style.position = 'fixed';
    target.style.zIndex = '2147483647';
    target.style.left = `${Math.round(left)}px`;
    target.style.top = `${Math.round(top)}px`;
    target.style.height = '';
    target.style.margin = '';
    target.style.width = '';
    target.style.minWidth = '';
    target.style.flex = '';
    target.style.alignItems = '';
    target.style.justifyContent = '';
    target.style.overflow = '';
    target.style.display = 'flex';

    widgetButton.textContent = widgetBusy ? '发送中' : '收入知识库';
    widgetButton.style.position = '';
    widgetButton.style.right = '';
    widgetButton.style.top = '';
    widgetButton.style.transform = '';
    widgetButton.style.width = '';
    widgetButton.style.minWidth = '42px';
    widgetButton.style.height = '32px';
    widgetButton.style.padding = '0 10px';
    widgetButton.style.fontSize = '13px';
    widgetButton.style.lineHeight = '30px';
    widgetButton.style.background = 'rgba(16,18,22,.72)';
    widgetButton.style.boxShadow = '0 8px 22px rgba(0,0,0,.28)';
  }

  function positionWidget() {
    const element = activeVideoElement();
    const target = ensureWidget();

    if (!element) {
      target.style.display = 'none';
      return;
    }

    const host = findControlBarHost(element);
    if (host) {
      setWidgetNativeMode(host, target);
      return;
    }

    if (
      now() - scriptStartedAt < NATIVE_HOST_GRACE_MS ||
      now() - lastNativeHostAt < NATIVE_HOST_STICKY_MS
    ) {
      target.style.display = 'none';
      return;
    }

    const rect = element.getBoundingClientRect();
    setWidgetFloatingMode(target, rect);
  }

  function schedulePositionWidget(force = false) {
    if (!force && now() - lastWidgetPositionAt < 500) {
      return;
    }
    if (widgetRaf) {
      return;
    }
    widgetRaf = window.requestAnimationFrame(() => {
      widgetRaf = 0;
      lastWidgetPositionAt = now();
      positionWidget();
    });
  }

  function showToast(message, ok = true) {
    let toast = document.getElementById(TOAST_ID);
    if (!toast) {
      toast = document.createElement('div');
      toast.id = TOAST_ID;
      toast.style.cssText = [
        'position: fixed',
        'left: 50%',
        'bottom: 84px',
        'transform: translateX(-50%)',
        'z-index: 2147483647',
        'max-width: min(420px, calc(100vw - 32px))',
        'padding: 10px 14px',
        'border-radius: 8px',
        'color: #fff',
        'font-size: 13px',
        'font-weight: 600',
        'line-height: 1.4',
        'box-shadow: 0 10px 30px rgba(0,0,0,.28)',
        'backdrop-filter: blur(18px)',
        '-webkit-backdrop-filter: blur(18px)',
        'pointer-events: none',
        'transition: opacity .18s ease'
      ].join(';');
      document.documentElement.appendChild(toast);
    }
    toast.textContent = message;
    toast.style.background = ok ? 'rgba(18,120,86,.92)' : 'rgba(172,54,54,.94)';
    toast.style.opacity = '1';
    window.clearTimeout(toast.__obsidianLibrarianTimer);
    toast.__obsidianLibrarianTimer = window.setTimeout(() => {
      toast.style.opacity = '0';
    }, 2800);
  }

  function sendRuntimeMessage(message) {
    return new Promise((resolve, reject) => {
      chrome.runtime.sendMessage(message, (response) => {
        if (chrome.runtime.lastError) {
          reject(new Error(chrome.runtime.lastError.message));
          return;
        }
        resolve(response);
      });
    });
  }

  function removeInjectedWidget() {
    document.getElementById(WIDGET_ID)?.remove();
    document.getElementById(MENU_ID)?.remove();
  }

  async function submitCurrentVideoFromWidget(event, ingestIntent = 'knowledge_ingest') {
    event.preventDefault();
    event.stopPropagation();
    if (widgetBusy) return;
    hideIntentMenu();

    const candidate = collectCurrentCandidate();
    if (!candidate.ok || !candidate.url) {
      showToast('没有识别到当前视频，请进入详情页或复制分享链接。', false);
      return;
    }

    widgetBusy = true;
    widgetButton.disabled = true;
    widgetButton.textContent = '发送中';
    try {
      const response = await sendRuntimeMessage({
        action: 'submitCurrentDouyinVideo',
        candidate,
        ingestIntent
      });
      if (response?.ok) {
        showToast(response.message || '已发送给 Agent。');
      } else {
        showToast(response?.message || '发送失败，请确认 Agent 已连接。', false);
      }
    } catch (err) {
      showToast(err.message || '发送失败，请确认扩展已启用。', false);
    } finally {
      widgetBusy = false;
      widgetButton.disabled = false;
      widgetButton.textContent = widgetMode === 'native' ? '入库' : '收入知识库';
    }
  }

	  function startWidgetLoop() {
	    ensureWidget();
	    schedulePositionWidget(true);
	    window.setInterval(() => schedulePositionWidget(true), 1500);
	    window.addEventListener('scroll', () => {
	      schedulePositionWidget();
	      hideIntentMenu();
	    }, true);
	    window.addEventListener('resize', () => {
	      schedulePositionWidget(true);
	      hideIntentMenu();
	    }, true);
	    document.addEventListener('visibilitychange', () => schedulePositionWidget(true), true);
	    document.addEventListener('pointerdown', (event) => {
	      if (
	        intentMenu?.style.display === 'flex' &&
	        !intentMenu.contains(event.target) &&
	        !widget?.contains(event.target)
	      ) {
	        hideIntentMenu();
	      }
	    }, true);
    const observer = new MutationObserver(() => schedulePositionWidget());
    observer.observe(document.documentElement, {
      childList: true,
      subtree: true
    });
  }

  document.addEventListener('contextmenu', (event) => {
    const path = typeof event.composedPath === 'function' ? event.composedPath() : [event.target];
    const candidates = path
      .filter(isElement)
      .map((element) => candidateFromElement(element, 'contextmenu', 950));
    const candidate = best(candidates);
    lastContextCandidate = {
      at: now(),
      x: event.clientX,
      y: event.clientY,
      candidate
    };
  }, true);

  chrome.runtime.onMessage.addListener((request, _sender, sendResponse) => {
    if (request?.action !== 'getCurrentDouyinVideo') {
      return false;
    }
    sendResponse(collectCurrentCandidate());
    return false;
  });

  removeInjectedWidget();
})();
