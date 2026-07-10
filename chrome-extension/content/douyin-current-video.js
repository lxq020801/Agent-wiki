(() => {
  const SCRIPT_VERSION = '2026-07-05-popup-ingest-v1';

  if (
    window.__obsidianLibrarianDouyinCurrentVideoLoaded &&
    window.__obsidianLibrarianDouyinCurrentVideoVersion === SCRIPT_VERSION
  ) {
    return;
  }
  window.__obsidianLibrarianDouyinCurrentVideoLoaded = true;
  window.__obsidianLibrarianDouyinCurrentVideoVersion = SCRIPT_VERSION;

  const MAX_PARENT_DEPTH = 10;
  const CONTEXT_TTL_MS = 15000;
  const COLLECT_CACHE_TTL_MS = 1200;
  let lastContextCandidate = null;
  let currentCandidateCache = null;

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

  function absoluteUrl(value) {
    const text = normalizeText(value);
    if (!text || text.startsWith('data:') || text.startsWith('blob:')) return '';
    try {
      return new URL(text, location.href).href;
    } catch (_err) {
      return '';
    }
  }

  function cleanMediaTitle(value) {
    let text = normalizeText(value).replace(/\s+/g, ' ');
    if (!text) return '';
    text = text
      .replace(/\s*复制此链接.*$/i, '')
      .replace(/\s*打开Dou音搜索.*$/i, '')
      .replace(/\s*打开抖音搜索.*$/i, '')
      .replace(/^抖音[-—\s]*/, '')
      .replace(/[-—\s]*抖音[-—\s]*记录美好生活$/i, '')
      .replace(/[-—\s]*抖音$/i, '')
      .trim();
    if (!text || text === '抖音-记录美好生活' || text === '抖音') return '';
    return text.length > 180 ? `${text.slice(0, 180).trim()}...` : text;
  }

  function isNoiseText(value) {
    const text = normalizeText(value);
    if (!text) return true;
    if (/^(倍速|智能|清屏|连播|发送|通知|私信|投稿|客户端|壁纸|充钻石|听抖音|识别画面|章节要点|下一章)$/.test(text)) return true;
    if (/^(发一条友好的弹幕吧|点击|进入直播间|读屏标签已关闭)$/.test(text)) return true;
    if (/^@/.test(text) || /^·/.test(text)) return true;
    if (/^\d{1,2}:\d{2}(?:\s*\/\s*\d{1,2}:\d{2})?$/.test(text)) return true;
    if (/^[\d.]+万?$/.test(text)) return true;
    if (/^\d+(?:\.\d+)?x$/i.test(text)) return true;
    if (/^相关搜索/.test(text)) return true;
    if (/^[#＃][\s\S]{1,30}$/.test(text)) return true;
    return false;
  }

  function isInsideIgnoredNode(element) {
    if (!isElement(element)) return true;
    return Boolean(element.closest?.('button, input, textarea, select, svg, canvas, [role="button"]'));
  }

  function extractTitleFromRoot(root) {
    if (!isElement(root)) return '';
    const candidates = [];
    const elements = [root, ...Array.from(root.querySelectorAll?.('*') || []).slice(0, 320)];
    for (const parent of elements) {
      if (isInsideIgnoredNode(parent) || !isVisibleElement(parent, 12, 8)) continue;
      for (const node of Array.from(parent.childNodes || [])) {
        if (node.nodeType !== 3) continue;
        const text = cleanMediaTitle(node.textContent);
        if (
          text &&
          text.length >= 4 &&
          text.length <= 260 &&
          !isNoiseText(text)
        ) {
          let score = Math.min(text.length, 80);
          if (text.length >= 8 && text.length <= 80) score += 60;
          if (/[，。！？?!：:]/.test(text)) score += 16;
          if (/[#＃]/.test(text)) score -= 10;
          if (text.length > 100) score -= 80;
          if (text.length > 140) score -= 40;
          if ((text.match(/[。！？?!]/g) || []).length >= 2) score -= 60;
          if (/^第\d+章/.test(text)) score -= 30;
          if (/^原视频|作者声明|汽水音乐/.test(text)) score -= 30;
          candidates.push({ text, score });
        }
      }
    }
    return candidates.sort((a, b) => b.score - a.score)[0]?.text || '';
  }

  function metaContent(selector) {
    return normalizeText(document.querySelector(selector)?.getAttribute('content'));
  }

  function fallbackPageTitle() {
    return cleanMediaTitle(
      metaContent('meta[property="og:title"]') ||
      metaContent('meta[name="twitter:title"]') ||
      document.title
    );
  }

  function extractCoverFromRoot(root, active) {
    const video = active?.tagName === 'VIDEO' ? active : root?.querySelector?.('video');
    const poster = absoluteUrl(video?.poster);
    if (poster) return poster;

    const badImagePattern = /avatar|head|icon|logo|emoji|sodaicon/i;
    const images = Array.from(root?.querySelectorAll?.('img') || [])
      .slice(0, 80)
      .map((img) => {
        const rect = img.getBoundingClientRect();
        const src = absoluteUrl(img.currentSrc || img.src);
        if (!src || /\.svg(?:\?|$)/i.test(src)) return null;
        const markerText = [
          src,
          img.className,
          img.alt,
          Array.from(img.attributes || []).map((attr) => `${attr.name}=${attr.value}`).join(' ')
        ].join(' ');
        const renderedArea = Math.max(0, rect.width) * Math.max(0, rect.height);
        if (renderedArea < 72 * 72 || !isVisibleElement(img, 72, 72)) return null;
        let score = renderedArea + Math.max(0, visibleScore(img));
        if (badImagePattern.test(markerText)) score -= 500000;
        return { src, score };
      })
      .filter(Boolean)
      .sort((a, b) => b.score - a.score);
    if (images[0]?.src) return images[0].src;

    return absoluteUrl(
      metaContent('meta[property="og:image"]') ||
      metaContent('meta[name="twitter:image"]')
    );
  }

  function collectCurrentMetadata() {
    const active = activeVideoElement();
    const root = closestVideoRoot(active) || active;
    const title = extractTitleFromRoot(root) || fallbackPageTitle();
    const coverUrl = extractCoverFromRoot(root, active);
    return {
      ...(title ? { title } : {}),
      ...(coverUrl ? { coverUrl } : {})
    };
  }

  function collectCurrentCandidate() {
    const cacheKey = `${location.href}|${lastContextCandidate?.at || 0}`;
    if (
      currentCandidateCache &&
      currentCandidateCache.key === cacheKey &&
      now() - currentCandidateCache.at < COLLECT_CACHE_TTL_MS
    ) {
      return {
        ...currentCandidateCache.result,
        collectedAt: new Date().toISOString()
      };
    }

    const candidate = best([
      candidateFromContext(),
      candidateFromUrl(),
      candidateFromActiveFeed(),
      candidateFromVideos()
    ]);

    let result;
    if (candidate) {
      result = {
        ...candidate,
        ...collectCurrentMetadata(),
        collectedAt: new Date().toISOString()
      };
    } else {
      result = {
        ok: false,
        reason: 'douyin_current_video_not_found',
        pageUrl: location.href,
        pageTitle: document.title || '',
        collectedAt: new Date().toISOString()
      };
    }
    currentCandidateCache = { key: cacheKey, at: now(), result };
    return result;
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
      .map((element) => ({ element, score: visibleScore(element) }))
      .filter((item) => item.score > 0)
      .sort((a, b) => b.score - a.score);
    if (activeElements[0]) return activeElements[0].element;

    return Array.from(document.querySelectorAll('video'))
      .map((video) => ({
        video,
        score: visibleScore(video) + (!video.paused && !video.ended && video.readyState >= 2 ? 1000000 : 0)
      }))
      .filter((item) => item.score > 0)
      .sort((a, b) => b.score - a.score)[0]?.video || null;
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
    currentCandidateCache = null;
  }, true);

  chrome.runtime.onMessage.addListener((request, _sender, sendResponse) => {
    if (
      request?.action !== 'getCurrentDouyinVideo' &&
      request?.action !== 'getCurrentDouyinVideoV2' &&
      request?.action !== 'getCurrentDouyinVideoV3'
    ) {
      return false;
    }
    sendResponse(collectCurrentCandidate());
    return false;
  });
})();
