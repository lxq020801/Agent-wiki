'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const SCRIPT_PATH = path.resolve(
  __dirname,
  '..',
  'chrome-extension',
  'content',
  'douyin-current-video.js'
);

class FakeText {
  constructor(text) {
    this.nodeType = 3;
    this.textContent = text;
    this.parentElement = null;
  }
}

function attributeValue(element, name) {
  if (name === 'class') return element.className;
  return element.attrs[name] ?? null;
}

function matchesSimpleSelector(element, selector) {
  const value = selector.trim();
  if (!value) return false;
  if (value === '*') return true;

  const tag = value.match(/^[a-z][a-z0-9-]*/i)?.[0];
  if (tag && element.tagName !== tag.toUpperCase()) return false;

  for (const classMatch of value.matchAll(/\.([a-z0-9_-]+)/gi)) {
    const classes = element.className.split(/\s+/).filter(Boolean);
    if (!classes.includes(classMatch[1])) return false;
  }

  for (const attrMatch of value.matchAll(/\[([^\]\s=*]+)\s*(\*=|=)?\s*(?:"([^"]*)"|'([^']*)')?\]/g)) {
    const [, name, operator, doubleQuoted, singleQuoted] = attrMatch;
    const actual = attributeValue(element, name);
    if (!operator && actual === null) return false;
    const expected = doubleQuoted ?? singleQuoted ?? '';
    if (operator === '=' && String(actual) !== expected) return false;
    if (operator === '*=' && !String(actual || '').includes(expected)) return false;
  }

  return true;
}

class FakeElement {
  constructor(tagName, attrs = {}, children = [], rect = {}) {
    this.nodeType = 1;
    this.tagName = tagName.toUpperCase();
    this.attrs = { ...attrs };
    this.className = String(attrs.class || '');
    this.parentElement = null;
    this.childNodes = [];
    this.rect = {
      left: rect.left ?? 100,
      top: rect.top ?? 100,
      width: rect.width ?? 300,
      height: rect.height ?? 80
    };
    this.dataset = {};
    for (const [name, value] of Object.entries(attrs)) {
      if (!name.startsWith('data-')) continue;
      const key = name
        .slice(5)
        .replace(/-([a-z])/g, (_match, letter) => letter.toUpperCase());
      this.dataset[key] = String(value);
    }
    this.href = attrs.href || '';
    this.src = attrs.src || '';
    this.currentSrc = attrs.currentSrc || this.src;
    this.poster = attrs.poster || '';
    this.paused = attrs.paused ?? true;
    this.ended = attrs.ended ?? false;
    this.readyState = attrs.readyState ?? 4;
    this.currentTime = attrs.currentTime ?? 0;
    this.append(...children);
  }

  append(...children) {
    for (const child of children) {
      const node = typeof child === 'string' ? new FakeText(child) : child;
      node.parentElement = this;
      this.childNodes.push(node);
    }
  }

  get textContent() {
    return this.childNodes.map((node) => node.textContent || '').join('');
  }

  get attributes() {
    return Object.entries(this.attrs).map(([name, value]) => ({ name, value: String(value) }));
  }

  getAttribute(name) {
    return attributeValue(this, name);
  }

  getBoundingClientRect() {
    return {
      ...this.rect,
      right: this.rect.left + this.rect.width,
      bottom: this.rect.top + this.rect.height
    };
  }

  matches(selector) {
    return selector.split(',').some((part) => matchesSimpleSelector(this, part));
  }

  closest(selector) {
    let current = this;
    while (current) {
      if (current.matches(selector)) return current;
      current = current.parentElement;
    }
    return null;
  }

  querySelectorAll(selector) {
    const matches = [];
    const visit = (node) => {
      if (!(node instanceof FakeElement)) return;
      if (node.matches(selector)) matches.push(node);
      for (const child of node.childNodes) visit(child);
    };
    for (const child of this.childNodes) visit(child);
    return matches;
  }

  querySelector(selector) {
    return this.querySelectorAll(selector)[0] || null;
  }
}

class FakeDocument {
  constructor(roots, title = '抖音 - 记录美好生活') {
    this.roots = roots;
    this.title = title;
  }

  querySelectorAll(selector) {
    const matches = [];
    for (const root of this.roots) {
      if (root.matches(selector)) matches.push(root);
      matches.push(...root.querySelectorAll(selector));
    }
    return matches;
  }

  querySelector(selector) {
    return this.querySelectorAll(selector)[0] || null;
  }

  addEventListener() {}
}

function el(tagName, attrs, children, rect) {
  return new FakeElement(tagName, attrs, children, rect);
}

function currentVideoRoot({
  id,
  titleNodes = [],
  extraDescriptionNodes = [],
  extraNodes = [],
  active = true,
  poster = `https://p3.douyinpic.com/${id}.jpeg`,
  rect = { left: 120, top: 80, width: 760, height: 620 }
}) {
  const description = el(
    'div',
    { 'data-e2e': 'video-desc' },
    [...titleNodes, ...extraDescriptionNodes],
    { left: rect.left + 20, top: rect.top + rect.height - 120, width: 520, height: 70 }
  );
  const video = el(
    'video',
    { poster, paused: false, readyState: 4, currentTime: 26 },
    [],
    { left: rect.left, top: rect.top, width: rect.width, height: rect.height }
  );
  return el(
    'article',
    {
      'data-e2e': active ? 'feed-active-video' : 'feed-video',
      'data-e2e-vid': id
    },
    [video, description, ...extraNodes],
    rect
  );
}

function collectCandidate(roots, options = {}) {
  let listener = null;
  const context = vm.createContext({
    chrome: {
      runtime: {
        onMessage: {
          addListener(callback) {
            listener = callback;
          }
        }
      }
    },
    console,
    Date,
    Math,
    Node: { ELEMENT_NODE: 1 },
    URL,
    location: {
      href: options.pageUrl || 'https://www.douyin.com/?recommend=1'
    },
    document: new FakeDocument(roots, options.pageTitle),
    setTimeout,
    clearTimeout
  });
  context.window = context;
  context.innerWidth = 1200;
  context.innerHeight = 800;

  vm.runInContext(fs.readFileSync(SCRIPT_PATH, 'utf8'), context, { filename: SCRIPT_PATH });
  assert.equal(typeof listener, 'function');

  let response;
  listener({ action: 'getCurrentDouyinVideoV3' }, {}, (value) => {
    response = value;
  });
  return response;
}

function testScreenshotCase() {
  const id = '7659687854112656680';
  const root = currentVideoRoot({
    id,
    titleNodes: [el('span', {}, ['#明朝']), '明朝第一国师'],
    extraNodes: [
      el('a', { 'data-e2e': 'video-author' }, ['@烽火之城']),
      el('time', {}, ['7月7日']),
      el('div', { 'data-e2e': 'ai-content-label' }, ['作者声明：内容由 AI 生成'])
    ]
  });
  const candidate = collectCandidate([root]);
  assert.equal(candidate.title, '#明朝 明朝第一国师');
  assert.equal(candidate.awemeId, id);
  assert.equal(candidate.url, `https://www.douyin.com/video/${id}`);
  assert.equal(candidate.coverUrl, `https://p3.douyinpic.com/${id}.jpeg`);
  assert.equal(candidate.pageUrl, 'https://www.douyin.com/?recommend=1');
}

function testOrdinaryTitle() {
  const candidate = collectCandidate([
    currentVideoRoot({ id: '7000000000000000001', titleNodes: ['周末去杭州看一场雨'] })
  ]);
  assert.equal(candidate.title, '周末去杭州看一场雨');
}

function testHashtagTitle() {
  const candidate = collectCandidate([
    currentVideoRoot({
      id: '7000000000000000002',
      titleNodes: [el('span', {}, ['#旅行']), el('span', {}, ['#杭州']), '周末路线分享']
    })
  ]);
  assert.equal(candidate.title, '#旅行 #杭州 周末路线分享');
}

function testTitleWinsOverAiDeclarationInSameContainer() {
  const candidate = collectCandidate([
    currentVideoRoot({
      id: '7000000000000000003',
      titleNodes: ['普通作品标题'],
      extraDescriptionNodes: [el('small', {}, ['内容由 AI 生成'])]
    })
  ]);
  assert.equal(candidate.title, '普通作品标题');

  const combinedTextCandidate = collectCandidate([
    currentVideoRoot({
      id: '7000000000000000083',
      titleNodes: ['普通作品标题 作者声明：内容由 AI 生成']
    })
  ]);
  assert.equal(combinedTextCandidate.title, '普通作品标题');
}

function testDeclarationOnlyUsesNeutralFallback() {
  const candidate = collectCandidate([
    currentVideoRoot({
      id: '7000000000000000004',
      extraDescriptionNodes: ['作者声明：内容由 AI 生成'],
      extraNodes: [
        el('a', { class: 'author-name' }, ['@测试作者']),
        el('time', {}, ['2026年7月16日']),
        el('button', {}, ['分享'])
      ]
    })
  ]);
  assert.equal(candidate.title, '当前抖音视频');
}

function testMultilineTitle() {
  const candidate = collectCandidate([
    currentVideoRoot({
      id: '7000000000000000005',
      titleNodes: ['第一行讲清背景\n第二行给出结论\n#历史']
    })
  ]);
  assert.equal(candidate.title, '第一行讲清背景 第二行给出结论 #历史');
}

function testLongTitleIsBounded() {
  const longTitle = `这是一个需要完整识别的长标题${'，后续内容继续说明主题'.repeat(24)}`;
  const candidate = collectCandidate([
    currentVideoRoot({ id: '7000000000000000006', titleNodes: [longTitle] })
  ]);
  assert.ok(candidate.title.startsWith('这是一个需要完整识别的长标题'));
  assert.ok(candidate.title.endsWith('...'));
  assert.ok(candidate.title.length <= 183);
}

function testOnlyActiveVisibleVideoIsUsed() {
  const recommended = currentVideoRoot({
    id: '7000000000000000099',
    titleNodes: ['推荐流里另一个视频'],
    active: false,
    rect: { left: 0, top: 0, width: 1100, height: 760 }
  });
  const active = currentVideoRoot({
    id: '7000000000000000007',
    titleNodes: ['当前正在看的视频'],
    rect: { left: 260, top: 140, width: 620, height: 500 }
  });
  const candidate = collectCandidate([recommended, active]);
  assert.equal(candidate.awemeId, '7000000000000000007');
  assert.equal(candidate.title, '当前正在看的视频');
  assert.equal(candidate.coverUrl, 'https://p3.douyinpic.com/7000000000000000007.jpeg');
}

function main() {
  testScreenshotCase();
  testOrdinaryTitle();
  testHashtagTitle();
  testTitleWinsOverAiDeclarationInSameContainer();
  testDeclarationOnlyUsesNeutralFallback();
  testMultilineTitle();
  testLongTitleIsBounded();
  testOnlyActiveVisibleVideoIsUsed();
  console.log('Douyin current-video title checks passed');
}

main();
