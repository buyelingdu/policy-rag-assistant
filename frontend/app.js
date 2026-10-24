/**
 * ============================================================
 * 政策通 · 中小企业政策助手 —— 前端主脚本（阶段2）
 *   · 输入问题 → 后端 SSE 流式返回（status / citations / delta / done / error）
 *   · 引用卡片来自后端真实检索（文件名 + 页码 + 章节 + 原文片段）
 *   · 点击引用卡片 → 右侧侧边栏展示原文，命中片段淡黄高亮
 * 阶段1 文档上传/列表/删除逻辑原样保留。
 * ============================================================
 */

/* ==================== 1. 全局配置 ==================== */
// 后端地址：本地开发默认连本地 8000；部署时在 index.html 设置 window.APP_API_BASE
// 为 Render 后端地址（例如 https://your-app.onrender.com），这里会自动优先采用。
const CONFIG = {
  API_BASE: (typeof window !== 'undefined' && window.APP_API_BASE) || 'http://127.0.0.1:8000',
};

/* ==================== 1.5 对话历史 & 示例问题 ==================== */
// 多轮对话：保存最近轮次，发送时带给后端，让模型理解"它/这项政策"等指代
let chatHistory = [];
const HISTORY_LIMIT = 10; // 只带最近10条（约5轮），避免请求越来越长

// 空状态快捷示例问题（点击即填入并发送）
const SAMPLE_QUESTIONS = [
  '自治区对中小企业融资、贷款、担保有哪些支持措施？',
  '中小企业上市挂牌、直接融资有什么奖励政策？',
  '减轻中小企业负担、清理拖欠账款有哪些规定？',
  '对吸纳就业和创业补贴有哪些扶持？',
];

/* ==================== 2. DOM 引用 ==================== */
const chatMessages      = document.getElementById('chatMessages');
const emptyState        = document.getElementById('emptyState');
const sampleQuestions   = document.getElementById('sampleQuestions');
const chatInput         = document.getElementById('chatInput');
const sendBtn           = document.getElementById('sendBtn');
const sourcePanel       = document.getElementById('sourcePanel');
const sourcePlaceholder = document.getElementById('sourcePlaceholder');
const sourceDetail      = document.getElementById('sourceDetail');
const drawerMask        = document.getElementById('drawerMask');
const newChatBtn        = document.getElementById('newChatBtn');
const docManagerBtn     = document.getElementById('docManagerBtn');
const docModal          = document.getElementById('docModal');
const closeDocModalBtn  = document.getElementById('closeDocModalBtn');

// 记录当前问题，供侧边栏只标黄与问题相关的词句，避免整段铺满高亮
let currentQuestion = '';

// ---- 政策匹配弹窗 ----
const matchBtn          = document.getElementById('matchBtn');
const matchModal        = document.getElementById('matchModal');
const closeMatchModalBtn= document.getElementById('closeMatchModalBtn');
const cancelMatchBtn    = document.getElementById('cancelMatchBtn');
const submitMatchBtn    = document.getElementById('submitMatchBtn');
const mIndustry = document.getElementById('mIndustry');
const mSize     = document.getElementById('mSize');
const mRegion   = document.getElementById('mRegion');
const mYears    = document.getElementById('mYears');
const mNeed     = document.getElementById('mNeed');

// ---- 数据看板弹窗 ----
const statsBtn          = document.getElementById('statsBtn');
const statsModal        = document.getElementById('statsModal');
const closeStatsModalBtn= document.getElementById('closeStatsModalBtn');
const statQuestions = document.getElementById('statQuestions');
const statDocs     = document.getElementById('statDocs');
const statUseful   = document.getElementById('statUseful');
const statUseless  = document.getElementById('statUseless');
const topQuestions = document.getElementById('topQuestions');
const statsEmpty   = document.getElementById('statsEmpty');

// ---- 文档管理（上传区 / 状态条 / 列表） ----
const dropZone      = document.getElementById('dropZone');
const fileInput     = document.getElementById('fileInput');
const uploadStatus  = document.getElementById('uploadStatus');
const docList       = document.getElementById('docList');
const docEmpty      = document.getElementById('docEmpty');

/* ==================== 3. 工具函数 ==================== */

// 内联 SVG 图标库：统一线性风格（避免重复写长 SVG 字符串）
const ICON_PATHS = {
  file:  '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/>',
  close: '<path d="M18 6L6 18M6 6l12 12"/>',
};
function svgIcon(name, size = 16) {
  return '<svg viewBox="0 0 24 24" width="' + size + '" height="' + size +
    '" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" ' +
    'stroke-linejoin="round" aria-hidden="true">' + (ICON_PATHS[name] || '') + '</svg>';
}

// 转义 HTML：防止用户输入 / 文档内容里的特殊字符破坏页面结构
function escapeHtml(str) {
  return String(str == null ? '' : str).replace(/[&<>"']/g, function (ch) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch];
  });
}

// 滚动到消息区底部（新消息出现时调用）
function scrollToBottom() {
  chatMessages.scrollTop = chatMessages.scrollHeight;
}

/* ==================== 4. 渲染：用户消息 / AI 行 / 引用卡片 ==================== */

// 用户消息气泡（右对齐，浅灰底）
function createUserMessage(text) {
  const row = document.createElement('div');
  row.className = 'msg-row msg-user';
  const bubble = document.createElement('div');
  bubble.className = 'bubble';
  bubble.textContent = text; // textContent：天然防注入
  row.appendChild(bubble);
  return row;
}

// 新建一个空的 AI 行（白底气泡 + 待填充的引用卡片列表）
// SSE 先到 citations / 后到 delta，所以返回容器引用，后续边流边填。
function createAiRow() {
  const row = document.createElement('div');
  row.className = 'msg-row msg-ai';
  const bubble = document.createElement('div');
  bubble.className = 'bubble';
  row.appendChild(bubble);
  // 版本切换控件（多次重新生成后出现：‹ 2/3 ›）
  const versionBox = document.createElement('div');
  versionBox.className = 'version-switcher';
  row.appendChild(versionBox);
  const cardList = document.createElement('div');
  cardList.className = 'citation-list';
  row.appendChild(cardList);
  // 反馈按钮容器（有用/无用），流式完成后填充
  const feedbackBox = document.createElement('div');
  feedbackBox.className = 'feedback-row';
  row.appendChild(feedbackBox);
  return { row, bubble, cardList, feedbackBox, versionBox };
}

// 单张引用卡片（小号、浅灰底、可点击），数据来自后端检索结果
function createCitationCard(c, onClick) {
  const card = document.createElement('button');
  card.type = 'button';
  card.className = 'citation-card';
  card.title = '点击查看原文'; // 悬停提示

  const pageBadge = c.page
    ? '<span class="citation-page">第 ' + escapeHtml(String(c.page)) + ' 页</span>'
    : '';

  card.innerHTML =
    '<span class="citation-icon">' + svgIcon('file', 16) + '</span>' +
    '<span class="citation-content">' +
      '<span class="citation-meta">' +
        '<span class="citation-file">' + escapeHtml(c.file_name) + '</span>' +
        pageBadge +
      '</span>' +
      '<span class="citation-snippet">' + escapeHtml(c.snippet || '') + '</span>' +
    '</span>';

  card.addEventListener('click', onClick);
  return card;
}

// 回答完成后渲染「有用 / 无用」反馈小按钮（低调，提交后变为感谢语）
function renderFeedback(box, data) {
  box.innerHTML = '';
  const label = document.createElement('span');
  label.className = 'feedback-label';
  label.textContent = '回答有帮助吗？';

  const yes = document.createElement('button');
  yes.type = 'button';
  yes.className = 'feedback-btn';
  yes.textContent = '有用';
  const no = document.createElement('button');
  no.type = 'button';
  no.className = 'feedback-btn';
  no.textContent = '无用';

  // 重新生成按钮（仅在传入 onRegen 回调时显示）
  let regen = null;
  if (typeof data.onRegen === 'function') {
    regen = document.createElement('button');
    regen.type = 'button';
    regen.className = 'feedback-btn regen-btn';
    regen.textContent = '重新生成';
    regen.addEventListener('click', function () { data.onRegen(); });
  }

  function submit(useful) {
    yes.disabled = true;
    no.disabled = true;
    if (regen) regen.disabled = true;
    // 静默提交反馈，失败也不打扰用户
    fetch(CONFIG.API_BASE + '/api/feedback', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        question: data.question,
        answer: data.answer,
        useful: useful,
        citations_count: data.citations_count,
      }),
    }).catch(function () {});
    box.innerHTML = '<span class="feedback-done">感谢您的反馈</span>';
  }

  yes.addEventListener('click', function () { submit(true); });
  no.addEventListener('click', function () { submit(false); });
  box.append(label, yes, no);
  if (regen) box.appendChild(regen);
}

// 版本切换控件：多次重新生成后显示 ‹ 2/3 ›，点击回看历次正文（仿 DeepSeek）
function renderVersionSwitcher(box, versions, row) {
  box.innerHTML = '';
  if (versions.texts.length < 2) return; // 只有一版不显示控件

  const prev = document.createElement('button');
  prev.type = 'button';
  prev.className = 'version-btn';
  prev.textContent = '‹';
  const counter = document.createElement('span');
  counter.className = 'version-counter';
  const next = document.createElement('button');
  next.type = 'button';
  next.className = 'version-btn';
  next.textContent = '›';

  function go(n) {
    versions.idx = Math.max(0, Math.min(versions.texts.length - 1, n));
    renderAnswer(row.querySelector('.bubble'), versions.texts[versions.idx]);
    counter.textContent = (versions.idx + 1) + '/' + versions.texts.length;
    prev.disabled = versions.idx === 0;
    next.disabled = versions.idx === versions.texts.length - 1;
  }

  prev.addEventListener('click', function () { go(versions.idx - 1); });
  next.addEventListener('click', function () { go(versions.idx + 1); });
  go(versions.idx);
  box.append(prev, counter, next);
}

// 状态消息（正在检索… / 正在生成…）：小号文字 + 旋转加载圈
function createStatusMessage(label) {
  const row = document.createElement('div');
  row.className = 'msg-row msg-ai msg-status';
  row.innerHTML = '<span class="spinner"></span><span class="status-label">' +
    escapeHtml(label) + '</span>';
  return row;
}

// 聊天区内的错误提示行（密钥未配置 / 无文档 / 网络失败等）
function showChatError(message) {
  const row = document.createElement('div');
  row.className = 'msg-row msg-ai msg-error';
  row.textContent = message;
  chatMessages.appendChild(row);
  scrollToBottom();
}

/* ---- 流式文本写入：维护一个末尾闪烁光标，新片段插到光标前 ---- */
function ensureCursor(bubble) {
  let cursor = bubble.querySelector('.typing-cursor');
  if (!cursor) {
    cursor = document.createElement('span');
    cursor.className = 'typing-cursor';
    bubble.appendChild(cursor);
  }
  return cursor;
}
function appendStreamText(bubble, text) {
  const cursor = ensureCursor(bubble);
  cursor.insertAdjacentText('beforebegin', text);
}
function finishStream(bubble) {
  const cursor = bubble.querySelector('.typing-cursor');
  if (cursor) cursor.remove();
}

// 轻量 Markdown 渲染：先转义防注入，再把 **加粗** 变成 <strong>，换行变成 <br>
// 只处理加粗和换行，去掉裸露的星号，避免大模型原样输出 **文字**
function mdToHtml(text) {
  let html = escapeHtml(text || '');
  html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  html = html.replace(/\n/g, '<br>');
  return html;
}
// 回答完成 / 版本回看时，用渲染后的 HTML 整体替换气泡内容
function renderAnswer(bubble, text) {
  const cursor = bubble.querySelector('.typing-cursor');
  if (cursor) cursor.remove();
  bubble.innerHTML = mdToHtml(text);
}

/* ==================== 5. 侧边栏：原文溯源 ==================== */

// 宽松包含：去掉两边所有空白后再比较，兼容 PDF 提取时字与字之间插入的空格（如“担 保”）
function looseIncludes(seg, w) {
  return seg.replace(/\s+/g, '').indexOf(w.replace(/\s+/g, '')) >= 0;
}

// 泛词停用表：这些词在政策文件里句句都有，单独出现不构成“相关”信号，
// 剔除后避免每个句子都命中、导致整段发黄
const STOP_GRAMS = new Set([
  '企业', '贷款', '政策', '支持', '融资', '小微', '小型', '微型', '公司',
  '项目', '服务', '发展', '经济', '资金', '补贴', '申请', '条件', '需要',
  '什么', '可以', '如何', '怎么', '多少', '我们', '你们', '进行', '通过',
  '给予', '予以', '以及', '有关', '相关', '地方', '国家', '银行', '借款',
  '利息', '措施', '内容', '方面', '规定', '办法', '机制', '体系',
]);

// 从问题里提取有区分度的命中词：中文两字组合，剔除上面的泛词
function extractCandidates(question) {
  const set = new Set();
  (question.match(/[\u4e00-\u9fa5]+/g) || []).forEach(function (seg) {
    for (let i = 0; i + 2 <= seg.length; i++) {
      const g = seg.slice(i, i + 2);
      if (!STOP_GRAMS.has(g)) set.add(g);
    }
  });
  (question.match(/[A-Za-z0-9]{2,}/g) || []).forEach(function (w) { set.add(w); });
  return Array.from(set);
}

// 块级高亮：优先标后端给的相关块（可多块一起标）；匹配不到退回按问题词标相关块，始终不整段铺满
function renderHighlightedText(text, question, highlightBlocks) {
  const safe = escapeHtml(text || '');

  // 把后端给的块（数组或单字符串）统一成去空格数组
  let blocks = [];
  if (Array.isArray(highlightBlocks)) {
    blocks = highlightBlocks.map(function (h) { return (h || '').replace(/\s+/g, ''); }).filter(Boolean);
  } else if (typeof highlightBlocks === 'string' && highlightBlocks.trim()) {
    blocks = [highlightBlocks.replace(/\s+/g, '')];
  }

  // 1) 后端给了相关块：按句号/分号/换行切块，前端块去空格后包含后端块即标黄，多块一起标
  if (blocks.length) {
    const parts = safe.split(/([。！？；;])/);
    let matched = false;
    const html = parts.map(function (seg, i) {
      if (i % 2 === 1 || !seg.trim()) return seg;
      const c = seg.replace(/\s+/g, '');
      const hit = blocks.some(function (hh) { return c && c.includes(hh); });
      if (hit) matched = true;
      return hit ? '<mark class="hit-sentence">' + seg + '</mark>' : seg;
    }).join('');
    if (matched) return html;
  }

  // 2) 退回：按问题核心词命中的块标黄
  if (!question) return safe;
  const words = extractCandidates(question);
  const parts = safe.split(/([。！？；;\n])/);
  return parts.map(function (seg, i) {
    if (i % 2 === 1 || !seg.trim()) return seg;
    const hit = words.some(function (w) { return looseIncludes(seg, w); });
    return hit ? '<mark class="hit-sentence">' + seg + '</mark>' : seg;
  }).join('');
}

// 打开引用详情：文件名 + 页码 + 章节 + 相关关键词标黄（q 为该卡片对应的原始问题）
function openSource(c, q) {
  const query = q || c._question || currentQuestion;
  const pageBadge = c.page
    ? '<span class="page-badge">第 ' + escapeHtml(String(c.page)) + ' 页</span>'
    : '';
  const chapterText = c.chapter || '原文片段';

  sourceDetail.innerHTML =
    '<div class="source-header">' +
      '<div class="source-file-wrap">' +
        '<div class="source-file">' + svgIcon('file', 16) +
          escapeHtml(c.file_name) + pageBadge +
        '</div>' +
        '<div class="source-chapter">' + escapeHtml(chapterText) + '</div>' +
      '</div>' +
      '<button class="icon-btn close-source-btn" type="button" aria-label="关闭">' +
        svgIcon('close', 16) +
      '</button>' +
    '</div>' +
    '<div class="source-body">' +
      '<div class="source-segment"><span class="seg-page">P.' +
        escapeHtml(String(c.page || '—')) + '</span>' +
        renderHighlightedText(c.text, query, c.highlights || c.highlight) +
      '</div>' +
    '</div>';

  // 绑定关闭按钮（详情是动态生成的，需在填充后绑定）
  sourceDetail.querySelector('.close-source-btn')
    .addEventListener('click', closeSource);

  // 切换 占位态 → 详情态
  sourcePlaceholder.hidden = true;
  sourceDetail.hidden = false;

  // 右侧栏滑出（桌面端常驻，.open 仅在移动端控制抽屉位移）
  sourcePanel.classList.add('open');
  // 遮罩只在移动端（<768px）需要；桌面端右栏常驻，不压暗页面
  if (window.innerWidth < 768) drawerMask.hidden = false;
}

// 关闭侧边栏（回到占位态）
function closeSource() {
  sourcePanel.classList.remove('open');
  drawerMask.hidden = true;
  sourcePlaceholder.hidden = false;
  sourceDetail.hidden = true;
}

/* ==================== 6. 发送：后端 SSE 流式问答 ==================== */

/* ==================== 6. 发送：后端 SSE 流式问答 ==================== */

// 通用流式请求：封装 fetch + SSE 拆帧 + 首字等待提示，界面更新交给 hooks 回调
// hooks: onStatus / onWait / onCitations / onDelta / onDone / onError
async function streamChat(payload, hooks, endpoint) {
  let answerText = '';
  // 首字等待提示：6 秒还没出第一个字，提示首次连接稍慢（出字后立即取消）
  const waitTimer = setTimeout(function () {
    if (!answerText.trim() && hooks.onWait) hooks.onWait();
  }, 6000);

  try {
    const res = await fetch(CONFIG.API_BASE + (endpoint || '/api/chat/stream'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!res.ok || !res.body) throw new Error('网络响应异常（HTTP ' + res.status + '）');

    // 读取 SSE 流：按 \n\n 拆帧，data: 后是 JSON
    const reader = res.body.getReader();
    const decoder = new TextDecoder('utf-8');
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let frameEnd;
      while ((frameEnd = buffer.indexOf('\n\n')) >= 0) {
        const frame = buffer.slice(0, frameEnd);
        buffer = buffer.slice(frameEnd + 2);
        const dataLine = frame.split('\n').find(function (l) {
          return l.startsWith('data:');
        });
        if (!dataLine) continue;

        let evt;
        try {
          evt = JSON.parse(dataLine.slice(5).trim());
        } catch { continue; }

        if (evt.type === 'status') {
          hooks.onStatus && hooks.onStatus(evt.text);
        } else if (evt.type === 'citations') {
          hooks.onCitations && hooks.onCitations(evt.items || []);
        } else if (evt.type === 'clear_citations') {
          // 后端判定为「资料中未涉及/未找到」，撤掉已渲染的无关引用卡片
          hooks.onClearCitations && hooks.onClearCitations();
        } else if (evt.type === 'delta') {
          clearTimeout(waitTimer);
          answerText += evt.text;
          hooks.onDelta && hooks.onDelta(evt.text);
        } else if (evt.type === 'done') {
          clearTimeout(waitTimer);
          hooks.onDone && hooks.onDone(answerText.trim());
        } else if (evt.type === 'error') {
          hooks.onError && hooks.onError(evt.message || '回答生成失败，请稍后重试');
        }
      }
    }
  } catch (err) {
    hooks.onError && hooks.onError('连接后端失败：' + err.message + '（请确认后端已启动 uvicorn）');
  } finally {
    clearTimeout(waitTimer);
  }
}

async function handleSend() {
  const text = chatInput.value.trim();
  if (!text) return; // 空内容不发送

  // 发送后：清空输入框、恢复高度、临时禁用发送按钮（防连点）
  chatInput.value = '';
  chatInput.style.height = 'auto';
  sendBtn.disabled = true;
  emptyState.hidden = true; // 进入对话，隐藏空状态

  // 1) 追加用户消息
  currentQuestion = text; // 记录当前问题，供侧边栏关键词标黄
  chatMessages.appendChild(createUserMessage(text));
  scrollToBottom();

  // 2) 状态提示行（检索/生成阶段共用一条，随事件更新文字）
  const status = createStatusMessage('正在准备回答…');
  chatMessages.appendChild(status);
  scrollToBottom();

  // AI 行容器（citations / delta 到达时惰性创建）
  let aiRow = null, bubble = null, cardList = null, feedbackBox = null, versionBox = null;

  // 先取历史快照（不含本轮问题），再把本轮问题记入历史，避免当前问题重复发给后端
  const recentHistory = chatHistory.slice(-HISTORY_LIMIT);
  chatHistory.push({ role: 'user', content: text });

  await streamChat({ question: text, history: recentHistory }, {
    onStatus: function (t) {
      const label = status.querySelector('.status-label');
      if (label) label.textContent = t;
      scrollToBottom();
    },
    onWait: function () {
      const label = status.querySelector('.status-label');
      if (label && label.textContent.indexOf('正在生成') < 0) {
        label.textContent = '首次连接大模型稍慢，请耐心等待…';
      }
    },
    onCitations: function (items) {
      // 卡片在答案生成后到达：若 AI 行已由 onDelta 建好则复用，避免重复建行
      if (!aiRow) {
        const ai = createAiRow();
        aiRow = ai.row; bubble = ai.bubble; cardList = ai.cardList; feedbackBox = ai.feedbackBox; versionBox = ai.versionBox;
        chatMessages.appendChild(aiRow);
      }
      items.forEach(function (c) {
        c._question = text; // 记录该卡片对应的原始问题，供侧边栏精准标黄
        cardList.appendChild(createCitationCard(c, function () { openSource(c); }));
      });
      scrollToBottom();
    },
    onClearCitations: function () {
      // 资料未涉及：清空已渲染的引用卡片，AI 回答文字保留
      if (cardList) cardList.innerHTML = '';
    },
    onDelta: function (t) {
      // 首字出现即移除等待提示；若 citations 没先到，这里再建行
      if (status.parentNode) status.remove();
      if (!aiRow) {
        const ai = createAiRow();
        aiRow = ai.row; bubble = ai.bubble; cardList = ai.cardList; feedbackBox = ai.feedbackBox; versionBox = ai.versionBox;
        chatMessages.appendChild(aiRow);
      }
      appendStreamText(bubble, t);
      scrollToBottom();
    },
    onDone: function (answerText) {
      if (bubble && answerText) renderAnswer(bubble, answerText);
      // 回答完成，写入历史并渲染反馈按钮（含重新生成）
      if (answerText) {
        chatHistory.push({ role: 'assistant', content: answerText });
        // 版本记录：第一版
        const versions = { texts: [answerText], idx: 0 };
        aiRow._versions = versions;
        if (versionBox) renderVersionSwitcher(versionBox, versions, aiRow);
        if (feedbackBox) {
          renderFeedback(feedbackBox, {
            question: text,
            answer: answerText,
            citations_count: cardList ? cardList.children.length : 0,
            onRegen: function () { regenerate(aiRow, text, recentHistory, versions); },
          });
        }
      }
    },
    onError: function (msg) {
      status.remove();
      showChatError(msg);
    },
  });

  sendBtn.disabled = false;
}

// 重新生成：用同一问题+历史，在原行内重新流式；保留历次版本可切换，不强制滚到底部
async function regenerate(row, question, history, versions) {
  // 重置原行：清空正文/卡片/反馈/版本控件，插入等待提示
  const bubble = row.querySelector('.bubble');
  const cardList = row.querySelector('.citation-list');
  const feedbackBox = row.querySelector('.feedback-row');
  const versionBox = row.querySelector('.version-switcher');
  bubble.textContent = '';
  cardList.innerHTML = '';
  feedbackBox.innerHTML = '';
  versionBox.innerHTML = '';

  const status = createStatusMessage('正在重新生成…');
  row.appendChild(status);
  // 只把该行滚动到可视区域，不跳到聊天最底部
  row.scrollIntoView({ block: 'nearest' });

  await streamChat({ question: question, history: history }, {
    onStatus: function (t) {
      const label = status.querySelector('.status-label');
      if (label) label.textContent = t;
    },
    onWait: function () {
      const label = status.querySelector('.status-label');
      if (label && label.textContent.indexOf('正在生成') < 0) {
        label.textContent = '首次连接稍慢，请耐心等待…';
      }
    },
    onCitations: function (items) {
      // 等待提示保留到首字出现；此处只填引用卡片
      items.forEach(function (c) {
        c._question = question; // 重新生成也绑定原问题，供侧边栏精准标黄
        cardList.appendChild(createCitationCard(c, function () { openSource(c); }));
      });
    },
    onClearCitations: function () {
      if (cardList) cardList.innerHTML = '';
    },
    onDelta: function (t) {
      if (status.parentNode) status.remove(); // 首字出现即移除等待提示
      appendStreamText(bubble, t);
    },
    onDone: function (answerText) {
      if (answerText) renderAnswer(bubble, answerText);
      if (answerText) {
        // 追加新版本（不覆盖旧版），并切到最新版
        versions.texts.push(answerText);
        versions.idx = versions.texts.length - 1;
        // 同步聊天历史最后一条 assistant 为最新版
        const last = chatHistory[chatHistory.length - 1];
        if (last && last.role === 'assistant') last.content = answerText;
        renderVersionSwitcher(versionBox, versions, row);
        renderFeedback(feedbackBox, {
          question: question,
          answer: answerText,
          citations_count: cardList ? cardList.children.length : 0,
          onRegen: function () { regenerate(row, question, history, versions); },
        });
      }
    },
    onError: function (msg) {
      status.remove();
      showChatError(msg);
    },
  });
}

/* ==================== 7. 示例问题 & 新建会话 ==================== */

// 渲染空状态的示例问题胶囊按钮
function renderSampleQuestions() {
  sampleQuestions.innerHTML = '';
  SAMPLE_QUESTIONS.forEach(function (q) {
    const chip = document.createElement('button');
    chip.type = 'button';
    chip.className = 'sample-chip';
    chip.textContent = q;
    chip.addEventListener('click', function () { askQuestion(q); });
    sampleQuestions.appendChild(chip);
  });
}

// 点击示例：填入输入框并直接发送
function askQuestion(q) {
  chatInput.value = q;
  handleSend();
}

function newChat() {
  chatMessages.innerHTML = '';   // 清空所有消息
  chatHistory = [];             // 清空多轮历史
  emptyState.hidden = false;    // 显示空状态引导
  renderSampleQuestions();       // 重新渲染示例问题
  closeSource();
  sendBtn.disabled = false;
}

/* ==================== 8. 事件绑定 ==================== */

// 发送：点击按钮 或 按 Enter（Shift+Enter 换行）
sendBtn.addEventListener('click', handleSend);
chatInput.addEventListener('keydown', function (e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    handleSend();
  }
});

// 输入框自动增高（最多 3 行）
chatInput.addEventListener('input', function () {
  chatInput.style.height = 'auto';
  chatInput.style.height = Math.min(chatInput.scrollHeight, 96) + 'px';
});

// 新建会话
newChatBtn.addEventListener('click', newChat);

// 文档管理弹窗：打开（同时刷新文档列表）/ 关闭 / 点遮罩关闭
docManagerBtn.addEventListener('click', function () {
  docModal.hidden = false;
  loadDocList();
});
closeDocModalBtn.addEventListener('click', function () { docModal.hidden = true; });
docModal.addEventListener('click', function (e) {
  if (e.target === docModal) docModal.hidden = true; // 点遮罩空白处关闭
});

// ===== 政策匹配弹窗：打开 / 关闭 / 点遮罩关闭 =====
matchBtn.addEventListener('click', function () { matchModal.hidden = false; });
closeMatchModalBtn.addEventListener('click', function () { matchModal.hidden = true; });
cancelMatchBtn.addEventListener('click', function () { matchModal.hidden = true; });
matchModal.addEventListener('click', function (e) {
  if (e.target === matchModal) matchModal.hidden = true;
});

// ===== 数据看板弹窗：打开时拉取 /api/stats 并渲染 =====
statsBtn.addEventListener('click', function () {
  statsModal.hidden = false;
  loadStats();
});
closeStatsModalBtn.addEventListener('click', function () { statsModal.hidden = true; });
statsModal.addEventListener('click', function (e) {
  if (e.target === statsModal) statsModal.hidden = true;
});

async function loadStats() {
  try {
    const res = await fetch(CONFIG.API_BASE + '/api/stats');
    const d = await res.json();
    statQuestions.textContent = d.total_questions || 0;
    statDocs.textContent = d.doc_count || 0;
    statUseful.textContent = (d.feedback && d.feedback.useful) || 0;
    statUseless.textContent = (d.feedback && d.feedback.useless) || 0;

    const top = d.top_questions || [];
    topQuestions.innerHTML = '';
    statsEmpty.hidden = top.length > 0;
    top.forEach(function (it, i) {
      const row = document.createElement('div');
      row.className = 'top-item';
      const rank = document.createElement('span');
      rank.className = 'top-rank';
      rank.textContent = i + 1;
      const q = document.createElement('span');
      q.className = 'top-q';
      q.textContent = it.question;
      const c = document.createElement('span');
      c.className = 'top-count';
      c.textContent = '× ' + it.count;
      row.append(rank, q, c);
      topQuestions.appendChild(row);
    });
  } catch (e) {
    statsEmpty.hidden = false;
    statsEmpty.textContent = '统计数据加载失败，请确认后端已启动';
  }
}

// 提交企业画像 → 调 /api/match，结果流式渲染到聊天区
submitMatchBtn.addEventListener('click', runMatch);

async function runMatch() {
  // 收集表单字段
  const profile = {
    industry: mIndustry.value,
    size: mSize.value,
    region: mRegion.value.trim(),
    years: mYears.value,
    need: mNeed.value,
  };
  // 必选项校验：行业 / 规模 / 年限
  if (!profile.industry || !profile.size || !profile.years) {
    alert('请至少选择：所属行业、企业规模、成立年限');
    return;
  }

  matchModal.hidden = true;
  emptyState.hidden = true;

  // 用户气泡：用一行摘要展示企业画像
  const summary = '【政策匹配】' + [
    profile.industry, profile.size,
    profile.region || '地区未填',
    profile.years, profile.need || '通用诉求',
  ].join(' · ');
  chatMessages.appendChild(createUserMessage(summary));
  scrollToBottom();

  // 等待提示行
  const status = createStatusMessage('正在准备匹配…');
  chatMessages.appendChild(status);

  let aiRow = null, bubble = null, cardList = null;
  await streamChat(profile, {
    onStatus: function (t) {
      const l = status.querySelector('.status-label');
      if (l) l.textContent = t;
      scrollToBottom();
    },
    onWait: function () {
      const l = status.querySelector('.status-label');
      if (l && l.textContent.indexOf('生成') < 0) l.textContent = '首次连接稍慢，请耐心等待…';
    },
    onCitations: function (items) {
      // 卡片在推荐清单生成后到达：AI 行已由 onDelta 建好则复用，避免重复建行
      if (!aiRow) {
        const ai = createAiRow();
        aiRow = ai.row; bubble = ai.bubble; cardList = ai.cardList;
        chatMessages.appendChild(aiRow);
      }
      items.forEach(function (c) {
        c._question = (profile.industry || '') + ' ' + (profile.need || ''); // 匹配用行业+诉求标黄
        cardList.appendChild(createCitationCard(c, function () { openSource(c); }));
      });
      scrollToBottom();
    },
    onClearCitations: function () {
      if (cardList) cardList.innerHTML = '';
    },
    onDelta: function (t) {
      if (status.parentNode) status.remove(); // 首字出现即撤等待提示
      if (!aiRow) {
        const ai = createAiRow();
        aiRow = ai.row; bubble = ai.bubble; cardList = ai.cardList;
        chatMessages.appendChild(aiRow);
      }
      appendStreamText(bubble, t);
      scrollToBottom();
    },
    onDone: function (answerText) {
      if (bubble && answerText) renderAnswer(bubble, answerText);
    },
    onError: function (msg) {
      status.remove();
      showChatError(msg);
    },
  }, '/api/match');
}

// 移动端：点击遮罩关闭侧边栏抽屉
drawerMask.addEventListener('click', closeSource);

// ESC 键：关闭弹窗 / 侧边栏
document.addEventListener('keydown', function (e) {
  if (e.key === 'Escape') {
    docModal.hidden = true;
    matchModal.hidden = true;
    statsModal.hidden = true;
    closeSource();
  }
});

/* ==================== 阶段1：文档上传与索引 ==================== */

// 状态条：type = info(进行中) / success(成功) / error(失败)
function showStatus(type, message) {
  uploadStatus.className = 'upload-status ' + type;
  uploadStatus.textContent = message;
  uploadStatus.hidden = false;
}

// 拉取已索引文档列表并渲染（后端不可达时给友好提示）
async function loadDocList() {
  try {
    const res = await fetch(CONFIG.API_BASE + '/api/documents');
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const data = await res.json();
    renderDocList(data.documents || []);
  } catch (err) {
    renderDocList([]);
    showStatus('error', '无法连接后端服务，请先启动后端（uvicorn）后再试');
  }
}

// 渲染文档列表
function renderDocList(documents) {
  docList.innerHTML = '';
  docEmpty.hidden = documents.length > 0;

  documents.forEach(function (doc) {
    const row = document.createElement('div');
    row.className = 'doc-item';

    const typeLabel = doc.source_type === 'pdf' ? 'PDF' : 'DOCX';
    const pagesText = (doc.source_type === 'pdf' && doc.page_count)
      ? doc.page_count + ' 页' : 'Word 文档';

    row.innerHTML =
      '<span class="doc-item-icon">' + svgIcon('file', 18) + '</span>' +
      '<div class="doc-item-info">' +
        '<div class="doc-item-name">' + escapeHtml(doc.file_name) + '</div>' +
        '<div class="doc-item-meta">' +
          '<span class="type-tag ' + doc.source_type + '">' + typeLabel + '</span>' +
          pagesText + ' · ' + doc.chunk_count + ' 个片段 · ' +
          escapeHtml(formatTime(doc.uploaded_at)) +
        '</div>' +
      '</div>' +
      '<button class="doc-delete" type="button">删除</button>';

    row.querySelector('.doc-delete').addEventListener('click', function () {
      deleteDoc(doc.doc_id, doc.file_name);
    });
    docList.appendChild(row);
  });
}

// 上传单个文件：前端先校验类型，再 POST 到后端解析索引
async function uploadFile(file) {
  if (!/\.(pdf|docx)$/i.test(file.name)) {
    showStatus('error', '仅支持 .pdf 和 .docx 文件：' + file.name);
    return;
  }

  showStatus('info', '正在解析并索引「' + file.name + '」…');
  const form = new FormData();
  form.append('file', file);

  try {
    const res = await fetch(CONFIG.API_BASE + '/api/documents/upload', {
      method: 'POST',
      body: form,
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || '上传失败');
    // 成功提示"已索引 N 个片段"
    showStatus('success', '「' + data.file_name + '」索引完成：共 ' + data.chunk_count + ' 个片段');
    loadDocList(); // 刷新列表
  } catch (err) {
    showStatus('error', '上传失败：' + err.message);
  }
}

// 删除文档（带二次确认）
async function deleteDoc(docId, fileName) {
  if (!confirm('确定删除「' + fileName + '」及其全部索引片段吗？')) return;
  try {
    const res = await fetch(CONFIG.API_BASE + '/api/documents/' + docId, { method: 'DELETE' });
    if (!res.ok) throw new Error('删除失败');
    showStatus('success', '已删除「' + fileName + '」');
    loadDocList();
  } catch (err) {
    showStatus('error', err.message);
  }
}

// 时间格式化：ISO -> "MM-DD HH:MM"
function formatTime(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  const pad = function (n) { return String(n).padStart(2, '0'); };
  return pad(d.getMonth() + 1) + '-' + pad(d.getDate()) + ' ' +
    pad(d.getHours()) + ':' + pad(d.getMinutes());
}

// ---- 上传区交互：点击选择 / 键盘触发 / 拖拽 ----
dropZone.addEventListener('click', function () { fileInput.click(); });
dropZone.addEventListener('keydown', function (e) {
  if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); fileInput.click(); }
});
fileInput.addEventListener('change', function () {
  Array.from(fileInput.files || []).forEach(uploadFile);
  fileInput.value = ''; // 清空，便于重复选择同一文件
});
// 拖入高亮
['dragover', 'dragenter'].forEach(function (evt) {
  dropZone.addEventListener(evt, function (e) {
    e.preventDefault();
    dropZone.classList.add('dragover');
  });
});
// 拖出 / 放下 取消高亮
['dragleave', 'drop'].forEach(function (evt) {
  dropZone.addEventListener(evt, function (e) {
    e.preventDefault();
    dropZone.classList.remove('dragover');
  });
});
dropZone.addEventListener('drop', function (e) {
  Array.from(e.dataTransfer.files || []).forEach(uploadFile);
});

/* ==================== 初始化：显示空状态引导 ==================== */
emptyState.hidden = false;
renderSampleQuestions();
