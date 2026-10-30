/* 知源 — front-end controller. No framework, no CDN. */

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

const state = {
  settings: null,
  embeddingLabel: "",
  reindexRequired: 0,
  kbs: [],
  kbId: null,
  docs: [],
  conversations: [],
  conversationId: null,
  messages: [],
  sources: [],
  mode: "demo",
  streaming: false,
  activeSource: null,
  clearKeys: { llm: false, embedding: false },
  toastTimer: null,
  kbToken: 0,
};

const els = {
  kbList: $("#kbList"),
  docList: $("#docList"),
  convList: $("#convList"),
  thread: $("#thread"),
  stageName: $("#stageName"),
  stageKicker: $("#stageKicker"),
  stageMeta: $("#stageMeta"),
  modeBadge: $("#modeBadge"),
  railFootText: $("#railFootText"),
  reindexBar: $("#reindexBar"),
  evidenceList: $("#evidenceList"),
  evidenceCount: $("#evidenceCount"),
  evidenceSub: $("#evidenceSub"),
  evidenceToggleCount: $("#evidenceToggleCount"),
  question: $("#question"),
  sendBtn: $("#sendBtn"),
  fileInput: $("#fileInput"),
  dropOverlay: $("#dropOverlay"),
  settingsModal: $("#settingsModal"),
  kbModal: $("#kbModal"),
  settingsForm: $("#settingsForm"),
  kbForm: $("#kbForm"),
  scrim: $("#scrim"),
};

/* ---------- helpers ---------- */

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function inlineMarkdown(text) {
  return text
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\[(\d{1,2})\]/g, (_match, number) => {
      return `<button type="button" class="cite" data-cite="${number}" title="查看来源 ${number}">${number}</button>`;
    });
}

function renderMarkdown(text) {
  const lines = escapeHtml(text).split("\n");
  let html = "";
  let list = null;
  let paragraph = [];

  const flushParagraph = () => {
    if (paragraph.length) {
      html += `<p>${paragraph.map(inlineMarkdown).join("<br>")}</p>`;
      paragraph = [];
    }
  };
  const closeList = () => {
    if (list) {
      html += `</${list}>`;
      list = null;
    }
  };

  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed) {
      flushParagraph();
      closeList();
      continue;
    }
    const heading = trimmed.match(/^#{1,6}\s+(.*)$/);
    if (heading) {
      flushParagraph();
      closeList();
      html += `<h3>${inlineMarkdown(heading[1])}</h3>`;
      continue;
    }
    if (/^(-{3,}|\*{3,})$/.test(trimmed)) {
      flushParagraph();
      closeList();
      html += "<hr>";
      continue;
    }
    const bullet = trimmed.match(/^[-*]\s+(.*)$/);
    if (bullet) {
      flushParagraph();
      if (list !== "ul") {
        closeList();
        html += "<ul>";
        list = "ul";
      }
      html += `<li>${inlineMarkdown(bullet[1])}</li>`;
      continue;
    }
    const ordered = trimmed.match(/^\d+[.)]\s+(.*)$/);
    if (ordered) {
      flushParagraph();
      if (list !== "ol") {
        closeList();
        html += "<ol>";
        list = "ol";
      }
      html += `<li>${inlineMarkdown(ordered[1])}</li>`;
      continue;
    }
    const quote = trimmed.match(/^&gt;\s?(.*)$/);
    if (quote) {
      flushParagraph();
      closeList();
      html += `<blockquote>${inlineMarkdown(quote[1])}</blockquote>`;
      continue;
    }
    closeList();
    paragraph.push(trimmed);
  }
  flushParagraph();
  closeList();
  return html || "<p></p>";
}

function formatTime(iso) {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const now = new Date();
  const sameDay =
    date.getFullYear() === now.getFullYear() &&
    date.getMonth() === now.getMonth() &&
    date.getDate() === now.getDate();
  if (sameDay) {
    return date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
  }
  return date.toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" });
}

function formatBytes(bytes) {
  if (!bytes) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function pad2(number) {
  return String(number).padStart(2, "0");
}

function showToast(message, bad = false) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.classList.toggle("bad", bad);
  toast.hidden = false;
  requestAnimationFrame(() => toast.classList.add("show"));
  clearTimeout(state.toastTimer);
  state.toastTimer = setTimeout(() => {
    toast.classList.remove("show");
    setTimeout(() => { toast.hidden = true; }, 220);
  }, bad ? 5200 : 3000);
}

async function api(path, options = {}) {
  const opts = { ...options };
  const headers = { ...(options.headers || {}) };
  if (opts.body && !(opts.body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
  }
  opts.headers = headers;
  const response = await fetch(path, opts);
  let payload = null;
  try {
    payload = await response.json();
  } catch (_error) {
    payload = null;
  }
  if (!response.ok) {
    const detail = payload && payload.detail ? payload.detail : `请求失败（${response.status}）`;
    throw new Error(detail);
  }
  return payload;
}

/* ---------- state helpers ---------- */

function currentKb() {
  return state.kbs.find((item) => item.id === state.kbId) || null;
}

function updateModeBadge() {
  const live = state.mode === "llm";
  els.modeBadge.className = `mode-badge ${live ? "live" : "demo"}`;
  els.modeBadge.textContent = live ? "模型已连接" : "演示模式";
  els.modeBadge.title = live
    ? "已配置大模型 API，回答由模型生成"
    : "未配置大模型 API，当前从检索片段摘录回答";
}

function updateRailFoot() {
  const chunks = state.kbs.reduce((sum, kb) => sum + (kb.chunk_count || 0), 0);
  els.railFootText.textContent = state.kbs.length
    ? `${state.kbs.length} 个知识库 · ${chunks} 个片段`
    : "本地索引 · 数据不出内网";
}

/* ---------- rendering ---------- */

function renderKbs() {
  if (!state.kbs.length) {
    els.kbList.innerHTML = `<p class="empty-note">还没有知识库。<button class="text-btn strong" id="emptyNewKbRail">新建一个</button></p>`;
    return;
  }
  els.kbList.innerHTML = state.kbs
    .map((kb, index) => {
      const active = kb.id === state.kbId ? " active" : "";
      return `
        <div class="kb-item${active}" data-kb="${escapeHtml(kb.id)}" role="button" tabindex="0"
             aria-label="切换到 ${escapeHtml(kb.name)}">
          <span class="kb-index">${pad2(index + 1)}</span>
          <span class="kb-body">
            <strong>${escapeHtml(kb.name)}</strong>
            <em>${kb.document_count} 份文档 · ${kb.chunk_count} 片段</em>
          </span>
          <button class="kb-del" type="button" data-del-kb="${escapeHtml(kb.id)}"
                  aria-label="删除 ${escapeHtml(kb.name)}" title="删除知识库">×</button>
        </div>`;
    })
    .join("");
}

function renderDocs() {
  if (!state.kbId) {
    els.docList.innerHTML = `<p class="empty-note">先选择一个知识库。</p>`;
    return;
  }
  if (!state.docs.length) {
    els.docList.innerHTML = `<p class="empty-note">还没有文档。点击上方「上传」，或把文件拖进右侧窗口。</p>`;
    return;
  }
  els.docList.innerHTML = state.docs
    .map((doc) => {
      const type = (doc.file_type || "doc").toUpperCase().slice(0, 4);
      const meta = [
        `${doc.chunk_count || 0} 片段`,
        doc.page_count ? `${doc.page_count} 页` : "",
        formatBytes(doc.size),
      ].filter(Boolean).join(" · ");
      return `
        <div class="doc-item" title="${escapeHtml(doc.name)}">
          <span class="doc-type">${escapeHtml(type)}</span>
          <span class="doc-info">
            <span class="doc-name">${escapeHtml(doc.name)}</span>
            <span class="doc-meta">${escapeHtml(meta)}</span>
          </span>
          <button class="doc-del" type="button" data-del-doc="${escapeHtml(doc.id)}"
                  aria-label="删除 ${escapeHtml(doc.name)}" title="删除文档">
            <svg><use href="#i-trash"></use></svg>
          </button>
        </div>`;
    })
    .join("");
}

function renderConversations() {
  if (!state.conversations.length) {
    els.convList.innerHTML = `<p class="empty-note">问第一个问题，对话会保存在这里。</p>`;
    return;
  }
  els.convList.innerHTML = state.conversations
    .map((conv) => {
      const active = conv.id === state.conversationId ? " active" : "";
      return `
        <button class="conv-item${active}" type="button" data-conv="${escapeHtml(conv.id)}">
          <span class="conv-title">${escapeHtml(conv.title || "未命名对话")}</span>
          <span class="conv-time">${formatTime(conv.updated_at)} · ${conv.message_count} 条</span>
        </button>`;
    })
    .join("");
}

function renderStage() {
  const kb = currentKb();
  if (!kb) {
    els.stageKicker.textContent = "知识库";
    els.stageName.textContent = "尚未选择知识库";
    els.stageMeta.textContent = "新建一个知识库，然后上传文档。";
    els.evidenceToggleCount.textContent = "0";
    els.reindexBar.hidden = true;
    return;
  }
  els.stageKicker.textContent = "当前知识库";
  els.stageName.textContent = kb.name;
  const bits = [
    `${state.docs.length} 份文档`,
    `${kb.chunk_count} 个片段`,
    state.embeddingLabel,
  ].filter(Boolean);
  els.stageMeta.textContent = bits.join(" · ");
  els.evidenceToggleCount.textContent = String(state.sources.length);
  els.reindexBar.hidden = state.reindexRequired <= 0;
  if (!els.reindexBar.hidden) {
    els.reindexBar.querySelector("span").textContent =
      `Embedding 配置已变更，${state.reindexRequired} 个知识库需要重建索引后才能检索。`;
  }
}

function suggestionsFor(kb) {
  const names = state.docs.map((doc) => doc.name).join(" ");
  const text = `${kb ? kb.name : ""} ${names}`;
  if (text.includes("员工") || text.includes("手册")) {
    return [
      { q: "年假有几天？可以转到明年吗？", src: "员工手册" },
      { q: "报销多久能到账？需要谁审批？", src: "员工手册" },
      { q: "每周可以远程办公几天？", src: "员工手册" },
      { q: "密码多久需要更换一次？", src: "信息安全规范" },
    ];
  }
  if (text.includes("安全") || text.includes("规范")) {
    return [
      { q: "密码多久需要更换一次？", src: "信息安全规范" },
      { q: "客户合同可以发到个人网盘吗？", src: "信息安全规范" },
      { q: "设备丢失后多久内要报告？", src: "信息安全规范" },
      { q: "年假有几天？", src: "员工手册" },
    ];
  }
  if (text.includes("客户") || text.includes("支持")) {
    return [
      { q: "P0 故障的响应和恢复时间是多久？", src: "客户支持流程" },
      { q: "客户退款多久到账？", src: "客户支持流程" },
      { q: "什么情况下需要升级到研发？", src: "客户支持流程" },
      { q: "复盘报告什么时候提交？", src: "客户支持流程" },
    ];
  }
  return [
    { q: "这个知识库主要包含哪些内容？", src: "" },
    { q: "请总结最重要的三条规定。", src: "" },
    { q: "有哪些流程需要审批？", src: "" },
    { q: "遇到问题应该联系谁？", src: "" },
  ];
}

function emptyMarkup() {
  const kb = currentKb();
  if (!kb) {
    return `
      <div class="empty">
        <h1>先建一个知识库。</h1>
        <p class="empty-copy">把企业文档放进来，知源会先检索、再回答，并把每一条结论的来源放在答案旁边。</p>
        <button type="button" class="primary-btn" id="emptyNewKb" style="margin-top:26px">新建知识库</button>
      </div>`;
  }
  const suggestions = suggestionsFor(kb)
    .map(
      (item, index) => `
      <button class="suggestion" type="button" data-question="${escapeHtml(item.q)}">
        <span class="n">${pad2(index + 1)}</span>
        <span class="q">${escapeHtml(item.q)}</span>
        <span class="src">${escapeHtml(item.src)}</span>
      </button>`
    )
    .join("");
  return `
    <div class="empty">
      <h1>让每个回答，<br>都有出处。</h1>
      <p class="empty-copy">上传内部文档后，知源会先从知识库检索相关片段，再交给大模型组织回答。每个关键结论都带编号，点开就能回到原文。</p>
      <div class="path" aria-label="问答流程">
        <span>问题</span><i></i><span>检索</span><i></i><span>Prompt</span><i></i><span>回答</span>
      </div>
      <div class="suggestions">
        <h2>可以这样问</h2>
        ${suggestions}
      </div>
    </div>`;
}

function citedSources(sources, content) {
  if (!sources || !sources.length) return [];
  const cited = new Set();
  for (const match of String(content || "").matchAll(/\[(\d{1,2})\]/g)) {
    cited.add(Number(match[1]));
  }
  if (!cited.size) return sources;
  return sources.filter((source) => cited.has(Number(source.index)));
}

function answerFoot(message) {
  if (!message.sources || !message.sources.length) return "";
  const names = [];
  for (const source of message.sources) {
    if (!names.includes(source.document_name)) names.push(source.document_name);
  }
  const label = names.slice(0, 2).join("、") + (names.length > 2 ? " 等" : "");
  return `<div class="answer-foot"><span>${message.sources.length} 处引用</span><span class="sep">/</span><span>${escapeHtml(label)}</span></div>`;
}

function messageMarkup(message, index) {
  if (message.role === "user") {
    return `
      <article class="msg user" data-index="${index}">
        <div class="msg-role">你</div>
        <div class="msg-body">${escapeHtml(message.content)}</div>
      </article>`;
  }
  const pending = message.pending && !message.content;
  const body = pending
    ? `<span class="typing" aria-label="正在生成"><i></i><i></i><i></i></span>`
    : `${renderMarkdown(message.content)}${message.pending ? '<span class="caret"></span>' : ""}`;
  const mode = message.mode === "demo" ? `<span class="role-mode">演示模式</span>` : "";
  return `
    <article class="msg assistant" data-index="${index}">
      <div class="msg-role"><span>知源</span>${mode}</div>
      <div class="msg-body">
        <div class="answer">${body}</div>
        ${answerFoot(message)}
      </div>
    </article>`;
}

function renderThread(forceScroll = false) {
  if (!state.messages.length) {
    els.thread.innerHTML = `<div class="thread-inner">${emptyMarkup()}</div>`;
    return;
  }
  els.thread.innerHTML = `<div class="thread-inner">${state.messages
    .map((message, index) => messageMarkup(message, index))
    .join("")}</div>`;
  if (forceScroll) scrollToBottom();
}

function scrollToBottom() {
  els.thread.scrollTop = els.thread.scrollHeight;
}

function maybeScroll() {
  const distance = els.thread.scrollHeight - els.thread.scrollTop - els.thread.clientHeight;
  if (distance < 180) scrollToBottom();
}

function updateStreamingAssistant() {
  const index = state.messages.length - 1;
  const message = state.messages[index];
  if (!message || message.role !== "assistant") return;
  const answer = document.querySelector(`.msg[data-index="${index}"] .answer`);
  if (!answer) {
    renderThread();
    return;
  }
  answer.innerHTML = `${renderMarkdown(message.content)}${
    message.pending ? '<span class="caret"></span>' : ""
  }`;
  maybeScroll();
}

function sourceLocation(source) {
  const bits = [];
  if (source.page) bits.push(`第 ${source.page} 页`);
  if (source.section) bits.push(source.section);
  if (!bits.length) bits.push(`片段 ${(source.chunk_index || 0) + 1}`);
  return bits.join(" · ");
}

function renderEvidence() {
  const sources = state.sources || [];
  els.evidenceCount.textContent = String(sources.length);
  els.evidenceToggleCount.textContent = String(sources.length);
  if (!sources.length) {
    els.evidenceSub.textContent = "回答中的编号对应这里的片段";
    els.evidenceList.innerHTML = `
      <div class="evidence-empty">
        <strong>还没有引用</strong>
        提问后，回答里用到的文档片段会按编号列在这里。点击答案中的 [1] 可以直接定位。
      </div>`;
    return;
  }
  els.evidenceSub.textContent = `本次回答引用了 ${sources.length} 个片段`;
  els.evidenceList.innerHTML = sources
    .map((source) => {
      const active = state.activeSource === source.index ? " active" : "";
      return `
        <article class="evidence-item${active}" data-ev="${source.index}">
          <div class="ev-head">
            <span class="ev-index">${pad2(source.index)}</span>
            <span class="ev-doc">${escapeHtml(source.document_name)}</span>
          </div>
          <p class="ev-loc">${escapeHtml(sourceLocation(source))}</p>
          <p class="ev-snippet">${escapeHtml(source.snippet || "")}</p>
          <button class="ev-more" type="button" data-more="${source.index}">展开原文</button>
          <div class="ev-full" hidden>${escapeHtml(source.content || "")}</div>
        </article>`;
    })
    .join("");
}

function selectSource(index) {
  state.activeSource = index;
  renderEvidence();
  $$(".cite").forEach((cite) => {
    cite.classList.toggle("active", Number(cite.dataset.cite) === index);
  });
  if (window.matchMedia("(max-width: 1180px)").matches) {
    document.body.classList.add("evidence-open");
    updateScrim();
  }
  const item = document.querySelector(`.evidence-item[data-ev="${index}"]`);
  if (item) item.scrollIntoView({ block: "nearest", behavior: "smooth" });
}

/* ---------- data loading ---------- */

async function loadSettings() {
  const data = await api("/api/settings");
  state.settings = data.settings;
  state.embeddingLabel = data.embedding_label || "";
  state.reindexRequired = data.reindex_required || 0;
  state.mode = data.settings.mode || "demo";
  updateModeBadge();
}

async function refreshKbList() {
  const data = await api("/api/kbs");
  state.kbs = data.items || [];
  if (state.kbId && !state.kbs.some((kb) => kb.id === state.kbId)) {
    state.kbId = null;
  }
  renderKbs();
  renderStage();
  updateRailFoot();
}

async function loadDocs() {
  if (!state.kbId) {
    state.docs = [];
    renderDocs();
    return;
  }
  const data = await api(`/api/kbs/${encodeURIComponent(state.kbId)}/documents`);
  state.docs = data.items || [];
  renderDocs();
  renderStage();
}

async function loadConversations() {
  if (!state.kbId) {
    state.conversations = [];
    renderConversations();
    return;
  }
  const data = await api(`/api/conversations?kb_id=${encodeURIComponent(state.kbId)}`);
  state.conversations = data.items || [];
  renderConversations();
}

async function selectKb(kbId) {
  if (state.streaming) {
    showToast("正在生成回答，请稍候再切换知识库。", true);
    return;
  }
  const token = ++state.kbToken;
  state.kbId = kbId;
  state.conversationId = null;
  state.messages = [];
  state.sources = [];
  state.activeSource = null;
  state.docs = [];
  state.conversations = [];
  localStorage.setItem("zhiyuan.kb", kbId);
  renderKbs();
  renderStage();
  renderThread();
  renderEvidence();
  try {
    await Promise.all([loadDocs(), loadConversations()]);
    if (token !== state.kbToken) return;
    renderStage();
    renderThread();
    renderEvidence();
  } catch (error) {
    showToast(error.message, true);
  }
}

async function loadConversation(conversationId) {
  if (state.streaming) {
    showToast("正在生成回答，请稍候再切换对话。", true);
    return;
  }
  try {
    const data = await api(`/api/conversations/${encodeURIComponent(conversationId)}`);
    state.conversationId = data.id;
    state.messages = data.messages.map((message) => ({
      role: message.role,
      content: message.content,
      sources: citedSources(message.sources || [], message.content),
      mode: message.mode,
      pending: false,
    }));
    const lastAssistant = [...state.messages].reverse().find(
      (message) => message.role === "assistant" && message.sources.length
    );
    state.sources = lastAssistant ? lastAssistant.sources : [];
    state.activeSource = null;
    renderThread(true);
    renderEvidence();
    renderConversations();
    closeRail();
    els.question.focus();
  } catch (error) {
    showToast(error.message, true);
  }
}

/* ---------- chat ---------- */

function parseSseBlock(raw) {
  let event = "message";
  const data = [];
  for (const line of raw.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
  }
  if (!data.length) return null;
  try {
    return { event, data: JSON.parse(data.join("\n")) };
  } catch (_error) {
    return null;
  }
}

function handleStreamEvent(parsed, assistant) {
  const { event, data } = parsed;
  if (event === "meta") {
    state.conversationId = data.conversation_id;
    state.mode = data.mode || state.mode;
    assistant.mode = data.mode || assistant.mode;
    updateModeBadge();
  } else if (event === "sources") {
    assistant.sources = data.sources || [];
    state.sources = assistant.sources;
    state.activeSource = null;
    renderEvidence();
  } else if (event === "delta") {
    assistant.content += data.text || "";
    updateStreamingAssistant();
  } else if (event === "error") {
    assistant.error = data.message || "回答生成失败";
    showToast(assistant.error, true);
  } else if (event === "done") {
    assistant.pending = false;
    if (data.error) assistant.error = data.error;
    assistant.sources = citedSources(assistant.sources, assistant.content);
    state.sources = assistant.sources;
    state.streaming = false;
    els.sendBtn.disabled = false;
    renderEvidence();
  }
}

async function sendQuestion(question) {
  if (state.streaming) return;
  if (!state.kbId) {
    showToast("请先选择一个知识库。", true);
    return;
  }
  const text = question.trim();
  if (!text) return;

  state.messages.push({ role: "user", content: text, sources: [], pending: false });
  const assistant = {
    role: "assistant",
    content: "",
    sources: [],
    mode: state.mode,
    pending: true,
  };
  state.messages.push(assistant);
  state.sources = [];
  state.activeSource = null;
  state.streaming = true;
  els.sendBtn.disabled = true;
  els.question.value = "";
  resizeComposer();
  renderThread(true);
  renderEvidence();

  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kb_id: state.kbId, question: text }),
    });
    if (!response.ok) {
      let detail = `请求失败（${response.status}）`;
      try {
        const payload = await response.json();
        if (payload.detail) detail = payload.detail;
      } catch (_error) { /* ignore */ }
      throw new Error(detail);
    }
    if (!response.body) throw new Error("浏览器不支持流式响应");

    const reader = response.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buffer = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");
      let boundary = buffer.indexOf("\n\n");
      while (boundary >= 0) {
        const raw = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const parsed = parseSseBlock(raw);
        if (parsed) handleStreamEvent(parsed, assistant);
        boundary = buffer.indexOf("\n\n");
      }
    }
  } catch (error) {
    assistant.pending = false;
    assistant.error = error.message;
    if (!assistant.content) {
      assistant.content = `回答生成失败：${error.message}`;
    }
    showToast(error.message, true);
  } finally {
    state.streaming = false;
    els.sendBtn.disabled = false;
    assistant.pending = false;
    assistant.sources = citedSources(assistant.sources, assistant.content);
    state.sources = assistant.sources;
    renderThread(true);
    renderEvidence();
    updateModeBadge();
    loadConversations().catch(() => {});
    refreshKbList().catch(() => {});
    els.question.focus();
  }
}

/* ---------- uploads ---------- */

async function uploadFiles(fileList) {
  if (!state.kbId) {
    showToast("请先选择一个知识库。", true);
    return;
  }
  const files = Array.from(fileList || []);
  if (!files.length) return;
  let added = 0;
  for (const file of files) {
    if (file.size > 20 * 1024 * 1024) {
      showToast(`${file.name} 超过 20 MB，已跳过。`, true);
      continue;
    }
    const form = new FormData();
    form.append("file", file);
    showToast(`正在解析 ${file.name}…`);
    try {
      await api(`/api/kbs/${encodeURIComponent(state.kbId)}/documents`, {
        method: "POST",
        body: form,
      });
      added += 1;
    } catch (error) {
      showToast(`${file.name}：${error.message}`, true);
    }
  }
  if (added) {
    showToast(`已加入 ${added} 份文档，索引完成。`);
    await refreshKbList();
    await loadDocs();
    renderThread();
  }
}

async function deleteDoc(docId) {
  const doc = state.docs.find((item) => item.id === docId);
  if (!doc) return;
  if (!window.confirm(`删除《${doc.name}》及其索引片段？`)) return;
  try {
    await api(`/api/documents/${encodeURIComponent(docId)}`, { method: "DELETE" });
    await refreshKbList();
    await loadDocs();
    renderThread();
    showToast("文档已删除。");
  } catch (error) {
    showToast(error.message, true);
  }
}

async function deleteKb(kbId) {
  const kb = state.kbs.find((item) => item.id === kbId);
  if (!kb) return;
  if (!window.confirm(`删除知识库「${kb.name}」及其全部文档？此操作不可撤销。`)) return;
  try {
    await api(`/api/kbs/${encodeURIComponent(kbId)}`, { method: "DELETE" });
    const remaining = state.kbs.filter((item) => item.id !== kbId);
    await refreshKbList();
    if (state.kbId === kbId || !state.kbId) {
      if (remaining.length) await selectKb(remaining[0].id);
      else {
        state.kbId = null;
        state.messages = [];
        state.sources = [];
        state.docs = [];
        state.conversations = [];
        renderKbs();
        renderStage();
        renderThread();
        renderEvidence();
        renderDocs();
        renderConversations();
      }
    }
    showToast("知识库已删除。");
  } catch (error) {
    showToast(error.message, true);
  }
}

/* ---------- settings ---------- */

function fillSettingsForm() {
  const settings = state.settings || {};
  const form = els.settingsForm;
  const set = (name, value) => {
    const field = form.elements[name];
    if (field) field.value = value ?? "";
  };
  set("llm_base_url", settings.llm_base_url);
  set("llm_model", settings.llm_model);
  set("llm_temperature", settings.llm_temperature);
  set("llm_max_tokens", settings.llm_max_tokens);
  set("embedding_base_url", settings.embedding_base_url);
  set("embedding_model", settings.embedding_model);
  set("top_k", settings.top_k);
  set("chunk_size", settings.chunk_size);
  set("chunk_overlap", settings.chunk_overlap);
  set("hybrid_alpha", settings.hybrid_alpha);
  const alpha = form.elements.hybrid_alpha;
  if (alpha) $("#alphaOutput").textContent = Number(alpha.value || 0).toFixed(2);

  const llmKey = form.elements.llm_api_key;
  llmKey.value = "";
  llmKey.disabled = false;
  llmKey.placeholder = settings.llm_api_key_masked
    ? `已保存 ${settings.llm_api_key_masked}，留空则不修改`
    : "未配置，填写后启用模型回答";

  const embeddingKey = form.elements.embedding_api_key;
  embeddingKey.value = "";
  embeddingKey.disabled = false;
  embeddingKey.placeholder = settings.embedding_api_key_masked
    ? `已保存 ${settings.embedding_api_key_masked}，留空则不修改`
    : "sk-…";

  const provider = settings.embedding_provider || "local";
  const radio = form.querySelector(`input[name="embedding_provider"][value="${provider}"]`);
  if (radio) radio.checked = true;
  toggleEmbeddingFields();

  state.clearKeys = { llm: false, embedding: false };
  $$(".clear-key", form).forEach((button) => {
    button.classList.remove("armed");
    button.textContent = "清除";
  });
  $("#testLlmResult").hidden = true;
  $("#testEmbeddingResult").hidden = true;
  $("#settingsError").hidden = true;
  $("#settingsSaved").hidden = true;
}

function toggleEmbeddingFields() {
  const provider = els.settingsForm.querySelector(
    'input[name="embedding_provider"]:checked'
  );
  const isApi = provider && provider.value === "openai";
  $("#embeddingApiFields").hidden = !isApi;
}

function collectSettings() {
  const form = els.settingsForm;
  const value = (name) => String(form.elements[name].value || "").trim();
  const provider = form.querySelector('input[name="embedding_provider"]:checked');
  const body = {
    llm_base_url: value("llm_base_url"),
    llm_model: value("llm_model"),
    llm_temperature: Number(value("llm_temperature")),
    llm_max_tokens: Number(value("llm_max_tokens")),
    embedding_provider: provider ? provider.value : "local",
    top_k: Number(value("top_k")),
    chunk_size: Number(value("chunk_size")),
    chunk_overlap: Number(value("chunk_overlap")),
    hybrid_alpha: Number(value("hybrid_alpha")),
  };
  if (state.clearKeys.llm) body.clear_llm_key = true;
  else if (value("llm_api_key")) body.llm_api_key = value("llm_api_key");
  if (body.embedding_provider === "openai") {
    body.embedding_base_url = value("embedding_base_url");
    body.embedding_model = value("embedding_model");
    if (state.clearKeys.embedding) body.clear_embedding_key = true;
    else if (value("embedding_api_key")) body.embedding_api_key = value("embedding_api_key");
  }
  return body;
}

async function saveSettings(event) {
  event.preventDefault();
  const button = $("#saveSettingsBtn");
  const errorBox = $("#settingsError");
  errorBox.hidden = true;
  button.disabled = true;
  try {
    const data = await api("/api/settings", {
      method: "PUT",
      body: JSON.stringify(collectSettings()),
    });
    state.settings = data.settings;
    state.embeddingLabel = data.embedding_label || "";
    state.reindexRequired = data.reindex_required || 0;
    state.mode = data.settings.mode || "demo";
    updateModeBadge();
    renderStage();
    $("#settingsSaved").hidden = false;
    setTimeout(() => { $("#settingsSaved").hidden = true; }, 1800);
    showToast("设置已保存。");
  } catch (error) {
    errorBox.textContent = error.message;
    errorBox.hidden = false;
  } finally {
    button.disabled = false;
  }
}

async function testLlm() {
  const result = $("#testLlmResult");
  result.hidden = false;
  result.className = "test-result";
  result.textContent = "正在连接…";
  const form = els.settingsForm;
  const body = {
    llm_base_url: String(form.elements.llm_base_url.value || "").trim(),
    llm_model: String(form.elements.llm_model.value || "").trim(),
  };
  const key = String(form.elements.llm_api_key.value || "").trim();
  if (key && !state.clearKeys.llm) body.llm_api_key = key;
  try {
    const data = await api("/api/settings/test-llm", {
      method: "POST",
      body: JSON.stringify(body),
    });
    result.className = `test-result ${data.ok ? "" : "bad"}`;
    result.textContent = data.ok ? `连接成功：${data.message}` : data.message;
  } catch (error) {
    result.className = "test-result bad";
    result.textContent = error.message;
  }
}

async function testEmbedding() {
  const result = $("#testEmbeddingResult");
  result.hidden = false;
  result.className = "test-result";
  result.textContent = "正在测试…";
  const form = els.settingsForm;
  const provider = form.querySelector('input[name="embedding_provider"]:checked');
  const body = {
    embedding_provider: provider ? provider.value : "local",
    embedding_base_url: String(form.elements.embedding_base_url.value || "").trim(),
    embedding_model: String(form.elements.embedding_model.value || "").trim(),
  };
  const key = String(form.elements.embedding_api_key.value || "").trim();
  if (key && !state.clearKeys.embedding) body.embedding_api_key = key;
  try {
    const data = await api("/api/settings/test-embedding", {
      method: "POST",
      body: JSON.stringify(body),
    });
    result.className = `test-result ${data.ok ? "" : "bad"}`;
    result.textContent = data.ok ? `连接成功：${data.message}` : data.message;
  } catch (error) {
    result.className = "test-result bad";
    result.textContent = error.message;
  }
}

/* ---------- modals & drawers ---------- */

function openModal(id) {
  const modal = document.getElementById(id);
  if (!modal) return;
  modal.hidden = false;
  const focusable = modal.querySelector("input, textarea, button");
  if (focusable) setTimeout(() => focusable.focus(), 30);
}

function closeModal(id) {
  const modal = document.getElementById(id);
  if (modal) modal.hidden = true;
}

function updateScrim() {
  const railOpen = document.body.classList.contains("rail-open");
  const evidenceOpen = document.body.classList.contains("evidence-open");
  els.scrim.hidden = !(railOpen || evidenceOpen);
}

function closeRail() {
  document.body.classList.remove("rail-open");
  updateScrim();
}

function closeEvidence() {
  document.body.classList.remove("evidence-open");
  updateScrim();
}

/* ---------- composer ---------- */

function resizeComposer() {
  const field = els.question;
  field.style.height = "auto";
  field.style.height = `${Math.min(field.scrollHeight, 170)}px`;
}

/* ---------- events ---------- */

els.kbList.addEventListener("click", (event) => {
  const deleteButton = event.target.closest("[data-del-kb]");
  if (deleteButton) {
    event.stopPropagation();
    deleteKb(deleteButton.dataset.delKb);
    return;
  }
  const item = event.target.closest("[data-kb]");
  if (item) {
    selectKb(item.dataset.kb);
    closeRail();
  }
});
els.kbList.addEventListener("keydown", (event) => {
  if (event.key !== "Enter" && event.key !== " ") return;
  const item = event.target.closest("[data-kb]");
  if (item) {
    event.preventDefault();
    selectKb(item.dataset.kb);
    closeRail();
  }
});
els.kbList.addEventListener("click", (event) => {
  if (event.target.id === "emptyNewKbRail") openModal("kbModal");
});

els.docList.addEventListener("click", (event) => {
  const button = event.target.closest("[data-del-doc]");
  if (button) deleteDoc(button.dataset.delDoc);
});

els.convList.addEventListener("click", (event) => {
  const button = event.target.closest("[data-conv]");
  if (button) loadConversation(button.dataset.conv);
});

els.thread.addEventListener("click", (event) => {
  const cite = event.target.closest("[data-cite]");
  if (cite) {
    selectSource(Number(cite.dataset.cite));
    return;
  }
  const suggestion = event.target.closest("[data-question]");
  if (suggestion) {
    sendQuestion(suggestion.dataset.question);
    return;
  }
  if (event.target.id === "emptyNewKb") openModal("kbModal");
});

els.evidenceList.addEventListener("click", (event) => {
  const more = event.target.closest("[data-more]");
  if (more) {
    const item = more.closest(".evidence-item");
    const full = item.querySelector(".ev-full");
    full.hidden = !full.hidden;
    more.textContent = full.hidden ? "展开原文" : "收起原文";
    return;
  }
  const item = event.target.closest("[data-ev]");
  if (item) selectSource(Number(item.dataset.ev));
});

$("#newKbBtn").addEventListener("click", () => openModal("kbModal"));
$("#emptyNewKbRail")?.addEventListener("click", () => openModal("kbModal"));
$("#newChatBtn").addEventListener("click", () => {
  if (state.streaming) {
    showToast("正在生成回答，请稍候。", true);
    return;
  }
  state.conversationId = null;
  state.messages = [];
  state.sources = [];
  state.activeSource = null;
  renderThread();
  renderEvidence();
  renderConversations();
  closeRail();
  els.question.focus();
});

$("#settingsBtn").addEventListener("click", () => {
  fillSettingsForm();
  openModal("settingsModal");
});
$("#uploadBtn").addEventListener("click", () => els.fileInput.click());
els.fileInput.addEventListener("change", () => {
  uploadFiles(els.fileInput.files);
  els.fileInput.value = "";
});

$("#evidenceToggle").addEventListener("click", () => {
  document.body.classList.add("evidence-open");
  updateScrim();
});
$("#evidenceClose").addEventListener("click", closeEvidence);
$("#menuBtn").addEventListener("click", () => {
  document.body.classList.add("rail-open");
  updateScrim();
});
els.scrim.addEventListener("click", () => {
  closeRail();
  closeEvidence();
});

$$("[data-close]").forEach((button) => {
  button.addEventListener("click", () => closeModal(button.dataset.close));
});
$$(".modal-backdrop").forEach((backdrop) => {
  backdrop.addEventListener("click", (event) => {
    if (event.target === backdrop) closeModal(backdrop.id);
  });
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    const openModalEl = $$(".modal-backdrop").find((modal) => !modal.hidden);
    if (openModalEl) {
      closeModal(openModalEl.id);
      return;
    }
    if (document.body.classList.contains("rail-open")) closeRail();
    if (document.body.classList.contains("evidence-open")) closeEvidence();
  }
});

els.settingsForm.addEventListener("submit", saveSettings);
els.settingsForm.querySelectorAll('input[name="embedding_provider"]').forEach((radio) => {
  radio.addEventListener("change", toggleEmbeddingFields);
});
els.settingsForm.elements.hybrid_alpha.addEventListener("input", (event) => {
  $("#alphaOutput").textContent = Number(event.target.value || 0).toFixed(2);
});
$$(".clear-key").forEach((button) => {
  button.addEventListener("click", () => {
    const which = button.dataset.key;
    const input = els.settingsForm.elements[`${which}_api_key`];
    state.clearKeys[which] = !state.clearKeys[which];
    button.classList.toggle("armed", state.clearKeys[which]);
    button.textContent = state.clearKeys[which] ? "将清除" : "清除";
    input.value = "";
    input.disabled = state.clearKeys[which];
  });
});
$("#testLlmBtn").addEventListener("click", testLlm);
$("#testEmbeddingBtn").addEventListener("click", testEmbedding);

$("#kbForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const errorBox = $("#kbError");
  errorBox.hidden = true;
  const button = $("#createKbBtn");
  button.disabled = true;
  try {
    const data = await api("/api/kbs", {
      method: "POST",
      body: JSON.stringify({
        name: form.elements.name.value,
        description: form.elements.description.value,
      }),
    });
    closeModal("kbModal");
    form.reset();
    await refreshKbList();
    await selectKb(data.id);
    showToast("知识库已创建，可以上传文档了。");
  } catch (error) {
    errorBox.textContent = error.message;
    errorBox.hidden = false;
  } finally {
    button.disabled = false;
  }
});

$("#reindexBtn").addEventListener("click", async () => {
  if (!state.kbId) return;
  const button = $("#reindexBtn");
  button.disabled = true;
  try {
    await api(`/api/kbs/${encodeURIComponent(state.kbId)}/reindex`, { method: "POST" });
    await loadSettings();
    renderStage();
    showToast("索引已重建。");
  } catch (error) {
    showToast(error.message, true);
  } finally {
    button.disabled = false;
  }
});

els.question.addEventListener("input", resizeComposer);
els.question.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    $("#composer").requestSubmit();
  }
});
$("#composer").addEventListener("submit", (event) => {
  event.preventDefault();
  sendQuestion(els.question.value);
});

let dragDepth = 0;
const stage = $(".stage");
stage.addEventListener("dragenter", (event) => {
  if (!event.dataTransfer || !Array.from(event.dataTransfer.types).includes("Files")) return;
  event.preventDefault();
  dragDepth += 1;
  els.dropOverlay.hidden = false;
});
stage.addEventListener("dragover", (event) => {
  if (event.dataTransfer) event.preventDefault();
});
stage.addEventListener("dragleave", () => {
  dragDepth = Math.max(0, dragDepth - 1);
  if (!dragDepth) els.dropOverlay.hidden = true;
});
stage.addEventListener("drop", (event) => {
  event.preventDefault();
  dragDepth = 0;
  els.dropOverlay.hidden = true;
  uploadFiles(event.dataTransfer.files);
});

/* ---------- boot ---------- */

async function boot() {
  try {
    await loadSettings();
    await refreshKbList();
    const stored = localStorage.getItem("zhiyuan.kb");
    const target =
      state.kbs.find((kb) => kb.id === stored) || state.kbs[0] || null;
    if (target) await selectKb(target.id);
    else {
      renderDocs();
      renderConversations();
      renderThread();
      renderEvidence();
    }
  } catch (error) {
    showToast(`无法连接服务：${error.message}`, true);
    renderThread();
  }
}

boot();
