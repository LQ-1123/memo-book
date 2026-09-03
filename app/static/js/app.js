/* 知识库 v0.18 SPA — 桌面软件式工作台（零依赖）
   ribbon + 随视图切换侧栏 + 主区 + 右栏；问答为文档式直接书写（两次 ⏎ 发送，支持多轮追问）
   对话后端持久化（/threads），localStorage 为离线缓存；引用按文档聚合展示；
   小测验（/quiz，第四视图 #/quiz：单选/判断/简答 AI 判分，10/30/50 题）；
   视频捕获支持 B站（无字幕走 ASR 语音转写） */
"use strict";

/* ---------- 工具 ---------- */

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
/* 服务端返回的 id 拼入 API 路径前先做白名单清洗（请求另有同源强制校验） */
const safeId = (s) => String(s ?? "").replace(/[^A-Za-z0-9_-]/g, "");

function fmtRel(sec) {
  if (!sec) return "";
  const d = Date.now() / 1000 - sec;
  if (d < 60) return "刚刚";
  if (d < 3600) return Math.floor(d / 60) + "分";
  if (d < 86400) return Math.floor(d / 3600) + "时";
  if (d < 86400 * 7) return Math.floor(d / 86400) + "天";
  return Math.floor(d / 86400 / 7) + "周";
}
function fmtSize(n) { return n >= 1048576 ? (n / 1048576).toFixed(1) + " MB" : Math.round(n / 1024) + " KB"; }
const ST_LABEL = { indexed: "已索引", indexing: "解析中", pending: "排队中", failed: "失败" };

const state = {
  token: localStorage.getItem("lib_token") || "",
  theme: localStorage.getItem("theme") || "auto",
  health: null,
  busyMap: new Map(),        // threadId -> { abort, orb }：并发生成（每对话一条流）
  threadId: localStorage.getItem("lib_cur_thread") || null,
  lastEnter: 0,
  deferredInstall: null,
  docsCache: [],
  activeTasks: [],
  curDocId: null,
  docsTreeTimer: null,
  docsTaskTimer: null,
};

/* 桌面版首启交接：URL 带 #key=<首次生成的口令> → 存入并立刻从地址栏抹掉 */
(() => {
  const m = /^#key=([\w-]+)$/.exec(location.hash);
  if (m) {
    localStorage.setItem("lib_token", m[1]);
    state.token = m[1];
    history.replaceState(null, "", location.pathname + location.search);
  }
})();

/* ---------- API ---------- */

async function api(path, opts = {}) {
  /* 同源强制：所有 API 请求必须落在本服务的 /api/v1/ 下（协议 + 主机白名单） */
  const u = new URL("/api/v1" + path, location.origin);
  if (u.origin !== location.origin || !u.pathname.startsWith("/api/v1/") || (u.protocol !== "http:" && u.protocol !== "https:")) {
    throw new Error("blocked: 非本服务地址");
  }
  const headers = Object.assign(
    { "Content-Type": "application/json", "X-API-Key": state.token },
    opts.headers || {}
  );
  /* 同源白名单已在上方硬校验（origin 必须等于 location.origin，路径必须 /api/v1/），
     浏览器同源策略下前端 fetch 不构成服务端 SSRF */
  const resp = await fetch(u, Object.assign({}, opts, { headers })); // nosemgrep
  if (resp.status === 401) {
    if (!opts.quiet) {
      toast("需要访问口令，请到「设置」粘贴", "err");
      location.hash = "#/settings";
    }
    throw new Error("unauthorized");
  }
  if (resp.status === 503) throw new Error("服务端未配置 API_KEYS（见服务端 .env）");
  if (!resp.ok) {
    let detail = "HTTP " + resp.status;
    try { detail = (await resp.json()).detail || detail; } catch {}
    throw new Error(String(detail));
  }
  return resp;
}

/* ---------- toast / 主题 ---------- */

function toast(msg, type = "") {
  const el = document.createElement("div");
  el.className = "toast " + type;
  el.textContent = msg;
  $("#toasts").appendChild(el);
  setTimeout(() => {
    el.classList.add("bye");
    el.addEventListener("animationend", () => el.remove(), { once: true });
  }, 3000);
}

function applyTheme() {
  const dark =
    state.theme === "dark" ||
    (state.theme === "auto" && matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.dataset.theme = dark ? "dark" : "light";
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute("content", dark ? "#1e1e1e" : "#ffffff");
  const btn = document.querySelector("#btnTheme");
  if (btn) {
    const sun = btn.querySelector(".i-sun");
    const moon = btn.querySelector(".i-moon");
    if (sun) sun.hidden = dark;
    if (moon) moon.hidden = !dark;
  }
}
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => state.theme === "auto" && applyTheme());

function cycleTheme() {
  state.theme = state.theme === "auto" ? "light" : state.theme === "light" ? "dark" : "auto";
  localStorage.setItem("theme", state.theme);
  applyTheme();
  toast("主题：" + ({ auto: "跟随系统", light: "浅色", dark: "深色" }[state.theme]));
}

/* ---------- 健康 / 统计 ---------- */

async function pollHealth() {
  try {
    state.health = await (await api("/health", { quiet: true })).json();
  } catch {
    state.health = null;
  }
  renderHealth();
}
function renderHealth() {
  const h = state.health;
  const el = $("#health");
  if (!el) return;
  el.innerHTML = h
    ? [
        ["向量库", h.qdrant], ["嵌入", h.embed_configured],
        ["问答", h.llm_configured], ["OCR", h.ocr_available],
      ].map(([t, on]) => `<span class="dot ${on ? "on" : "off"}" title="${t} ${on ? "正常" : "不可用"}"></span>`).join("")
    : `<span class="dot off" title="服务不可达"></span>`;
  const stats = $("#sbStats");
  if (stats && h) stats.textContent = `${h.documents} 篇 · ${h.chunks} 块`;
}

/* ---------- 对话存储（localStorage，上限 50 条；支持多对话并发生成） ----------
   线程模型是唯一数据源：blocks 直接挂在线程对象上；编辑器只是当前线程的视图。
   切换对话不打断生成：流持续写回所属线程，切回来即见实时进度。 */

const TKEY = "lib_threads_v1";
function loadThreads() {
  try { return JSON.parse(localStorage.getItem(TKEY) || "[]"); } catch { return []; }
}
/* 内存缓存 = 唯一数据源（流式写它）；localStorage 只做持久化镜像 */
function threads() {
  if (!state.threads) state.threads = loadThreads();
  return state.threads;
}
function threadById(id, list) {
  return (list || threads()).find((x) => x.id === id) || null;
}
/* 节流保存：流式期间每 800ms 落一次盘；strip 运行时 streaming 标记 */
let _saveTimer = null;
function _strip(list) {
  return list.slice(0, 50).map((th) => ({
    ...th,
    blocks: th.blocks.map((bk) => {
      const { streaming, ...rest } = bk;
      return rest;
    }),
  }));
}
function _flush() {
  const clean = _strip(threads());
  try { localStorage.setItem(TKEY, JSON.stringify(clean)); }
  catch { try { localStorage.setItem(TKEY, JSON.stringify(clean.slice(25))); } catch {} }
  pushDirtyThreads(clean);
}
function queueSave() {
  if (_saveTimer) return;
  _saveTimer = setTimeout(() => { _saveTimer = null; _flush(); }, 800);
}
function saveThreadsNow() {
  if (_saveTimer) { clearTimeout(_saveTimer); _saveTimer = null; }
  _flush();
}
function curThread() {
  return threadById(state.threadId);
}

/* ---------- 对话后端同步：服务端为真源，localStorage 为离线缓存 ----------
   脏集跟踪待推送线程；启动时服务端列表与本地合并（按 id，服务端同 id 优先，
   本地独有的线程收养上传），任何一侧缺数据都不会丢对话。 */
state.threadsSync = false;
state._dirty = new Set();
function markThreadDirty(id) { if (id) state._dirty.add(id); }
function _stripOne(th) {
  return {
    id: th.id, title: th.title || "", ts: th.ts || 0,
    blocks: (th.blocks || []).map((bk) => { const { streaming, ...rest } = bk; return rest; }),
    draft: th.draft || "",
  };
}
async function pushDirtyThreads(clean) {
  if (!state.threadsSync || !state._dirty.size) return;
  const list = clean || _strip(threads());
  for (const id of [...state._dirty]) {
    const th = list.find((x) => x.id === id);
    if (!th) { state._dirty.delete(id); continue; }
    try {
      await api("/threads", { method: "POST", body: JSON.stringify(_stripOne(th)), quiet: true });
      state._dirty.delete(id);
    } catch { /* 离线：留在脏集，下次 flush 重试 */ }
  }
}
async function syncThreadsFromServer() {
  try {
    const items = (await (await api("/threads", { quiet: true })).json()).items || [];
    const local = threads();
    const byId = new Map();
    for (const it of items) {
      byId.set(it.id, {
        id: it.id, title: it.title || "", ts: it.ts || Math.round((it.updated_at || 0) * 1000),
        blocks: it.blocks || [], draft: it.draft || "",
      });
    }
    for (const th of local) {
      if (!byId.has(th.id)) { byId.set(th.id, th); markThreadDirty(th.id); }  // 本地独有 → 收养上传
    }
    state.threads = [...byId.values()].sort((a, b) => (b.ts || 0) - (a.ts || 0));
    state.threadsSync = true;
    if (state.threadId && !threadById(state.threadId)) {
      state.threadId = null;
      localStorage.removeItem("lib_cur_thread");
    }
    renderThreadList();
    const cur = curThread();
    if (cur && currentView() === "ask" && !state.busyMap.size) {
      renderBlocks(cur.blocks, cur.draft);
      refreshAskUi();
    }
    pushDirtyThreads();
  } catch { /* 离线/未授权：继续用 localStorage 缓存 */ }
}
async function deleteThreadRemote(id) {
  if (!state.threadsSync) return;
  try { await api("/threads?id=" + encodeURIComponent(id), { method: "DELETE", quiet: true }); } catch {}
}

function renderThreadList() {
  const wrap = $("#threadList");
  if (!wrap) return;
  const q = ($("#askSearch")?.value || "").toLowerCase();
  const list = threads().filter((x) => !q || (x.title || "").toLowerCase().includes(q));
  wrap.innerHTML = list.length
    ? list.map((x) => {
        const busy = state.busyMap.has(x.id);
        return `
      <div class="sb-conv ${x.id === state.threadId ? "on" : ""} ${busy ? "busy" : ""}" data-id="${esc(x.id)}">
        ${busy ? '<span class="sb-orb ai-orb"></span>' : ""}
        <span class="t">${esc(x.title)}</span><span class="tm ${busy ? "busy" : ""}">${busy ? "生成中" : fmtRel(x.ts / 1000)}</span>
        <button class="del" data-del="${esc(x.id)}" title="删除对话"><svg class="ic" style="width:11px;height:11px"><use href="#i-x"/></svg></button>
      </div>`;
      }).join("")
    : `<div class="sb-empty">暂无对话</div>`;
  /* busy 行挂小号线圈球（与问答回答同款动画） */
  wrap.querySelectorAll(".sb-conv").forEach((el) => {
    const host = el.querySelector(".sb-orb");
    if (!host) return;
    const ent = state.busyMap.get(el.dataset.id);
    if (ent && window.AIOrb) {
      if (ent.listOrb) ent.listOrb.stop();
      ent.listOrb = window.AIOrb.mount(host, 14);
    }
  });
  wrap.querySelectorAll(".sb-conv").forEach((el) =>
    el.addEventListener("click", (e) => {
      if (e.target.closest("[data-del]")) return;
      switchThread(el.dataset.id);
    })
  );
  wrap.querySelectorAll("[data-del]").forEach((b) =>
    b.addEventListener("click", () => {
      const id = b.dataset.del;
      const ent = state.busyMap.get(id);
      if (ent) { try { ent.abort.abort(); } catch {} state.busyMap.delete(id); }
      state.threads = threads().filter((x) => x.id !== id);
      saveThreadsNow();
      deleteThreadRemote(id);
      if (id === state.threadId) { state.threadId = null; localStorage.removeItem("lib_cur_thread"); clearEditor(); }
      renderThreadList();
      updatePill();
    })
  );
}

/* 草稿：切换对话时保存当前未发送文字，切回还原 */
function saveCurrentDraft() {
  const th = curThread();
  const draft = collectUserText();
  if (!th) { state._newDraft = draft; return; }
  if (draft || th.draft) {
    const hit = threadById(th.id);
    if (hit) { hit.draft = draft; markThreadDirty(th.id); saveThreadsNow(); }
  }
}

function stopOrbVisual() {
  /* 编辑器即将重绘：停掉当前展示的动画实例（数据流不受影响） */
  const ent = state.threadId && state.busyMap.get(state.threadId);
  if (ent && ent.orb) { ent.orb.stop(); ent.orb = null; }
}

function switchThread(id) {
  saveCurrentDraft();
  stopOrbVisual();
  state.threadId = id;
  localStorage.setItem("lib_cur_thread", id);
  const th = curThread();
  renderBlocks(th ? th.blocks : [], th ? th.draft : "");
  renderThreadList();
  updatePill();
  if (currentView() !== "ask") location.hash = "#/";
}

function newThread() {
  saveCurrentDraft();
  stopOrbVisual();
  state.threadId = null;
  localStorage.removeItem("lib_cur_thread");
  clearEditor();
  renderThreadList();
  updatePill();
  caretToEnd(ensureTrailingBlk());
}

function clearEditor() {
  const ed = $("#editor");
  ed.replaceChildren();
  ensureTrailingBlk();
  refreshAskUi();
  renderRightbar([]);
  updatePill();
}

/* 生成状态同步：移动端发送钮切换为停止态（桌面用 Esc 停止） */
function updatePill() {
  const mSend = $("#mSend");
  if (!mSend) return;
  const busy = state.threadId && state.busyMap.has(state.threadId);
  mSend.classList.toggle("stop", !!busy);
  $("#mSendIc").innerHTML = `<use href="#i-${busy ? "stop" : "send"}"/>`;
}

/* ---------- 问答：contenteditable 文档式书写 ---------- */

function mkBlk() {
  const d = document.createElement("div");
  d.className = "blk";
  return d;
}
/* 规范化：浏览器在 contenteditable 里可能把输入放进游离文本节点或无类名 div
   （不归属任何 .blk），导致发送时收集不到文字。此处统一归拢为 .blk。 */
function normalizeEditor() {
  const ed = $("#editor");
  if (!ed) return;
  [...ed.childNodes].forEach((n) => {
    if (n.nodeType === 3 && n.textContent.trim()) {
      const d = mkBlk();
      d.textContent = n.textContent;
      n.replaceWith(d);
    } else if (n.nodeType === 1 && !(n.classList.contains("blk") || n.classList.contains("q-block") || n.classList.contains("a-block"))) {
      const txt = n.textContent || "";
      if (txt.trim()) {
        const d = mkBlk();
        d.textContent = txt;
        n.replaceWith(d);
      } else {
        n.remove();
      }
    }
  });
  ensureTrailingBlk();
}
function ensureTrailingBlk() {
  const ed = $("#editor");
  if (!ed.lastElementChild || !ed.lastElementChild.classList.contains("blk")) ed.appendChild(mkBlk());
  return ed.lastElementChild;
}
function caretToEnd(el) {
  el.focus();
  const r = document.createRange();
  r.selectNodeContents(el);
  r.collapse(false);
  const s = getSelection();
  s.removeAllRanges();
  s.addRange(r);
}
function refreshAskUi() {
  const ed = $("#editor");
  if (!ed) return;
  const empty = !ed.querySelector(".q-block, .a-block") && ed.textContent.trim() === "";
  ed.classList.toggle("is-empty", empty);
  renderMobileHero();
}
function renderBlocks(blocks, draft) {
  const ed = $("#editor");
  ed.replaceChildren();
  for (const b of blocks) {
    if (b.r === "q") {
      const d = document.createElement("div");
      d.className = "q-block";
      d.contentEditable = "false";
      d.textContent = b.t;
      ed.appendChild(d);
    } else {
      const node = buildABlock(b.t, b.srcs || []);
      if (b.streaming) {
        node.setAttribute("data-stream", "1");
        const span = node.querySelector(".stream-text");
        if (span) span.classList.remove("done");   /* 流式中：光标闪烁由 CSS 承担 */
      }
      ed.appendChild(node);
    }
  }
  ensureTrailingBlk();
  if (draft) {
    const lines = String(draft).split("\n");
    const first = ed.querySelector(".blk");
    if (first && lines[0]) first.textContent = lines[0];
    for (let k = 1; k < lines.length; k++) {
      if (!lines[k]) continue;
      const d2 = mkBlk();
      d2.textContent = lines[k];
      ed.lastElementChild.before(d2);
    }
  }
  refreshAskUi();
  renderRightbar(lastSrcs(blocks));
}
function lastSrcs(blocks) {
  for (let i = blocks.length - 1; i >= 0; i--) if (blocks[i].r === "a" && blocks[i].srcs && blocks[i].srcs.length) return blocks[i].srcs;
  return [];
}

function buildABlock(text, srcs) {
  const d = document.createElement("div");
  d.className = "a-block";
  d.contentEditable = "false";   // 已生成的回答锁定不可编辑
  d._srcs = srcs || [];
  const span = document.createElement("span");
  span.className = "stream-text";
  span.innerHTML = mdRender(text, { cites: true });
  d.appendChild(span);
  if (srcs && srcs.length) d.insertAdjacentHTML("beforeend", srcsHtml(srcs));
  return d;
}

function mdInline(s, cites) {
  /* 还原 pymupdf4llm 常见内联 HTML 标签（已转义），再处理粗体/行内码/引用角标 */
  s = s.replace(/&lt;(\/?)(u|b|i|em|sub|sup|br)\s*&gt;/gi, "<$1$2>");
  s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  s = s.replace(/`([^`\n]+)`/g, "<code>$1</code>");
  if (cites) s = s.replace(/\[(\d{1,2})\]/g, '<span class="cite">[$1]</span>');
  return s;
}
function splitRow(line) {
  const s = line.trim().replace(/^\|/, "").replace(/\|$/, "");
  return s.split("|").map((c) => c.trim());
}

/* 轻量 Markdown 渲染：标题/表格/列表/引用/分隔线/段落 + mdInline 行内元素 */
function mdRender(src, { cites = false } = {}) {
  const lines = String(src ?? "").replace(/\r/g, "").split("\n");
  const out = [];
  const inline = (s) => mdInline(s, cites);
  let i = 0;
  while (i < lines.length) {
    const t = lines[i].trim();
    if (!t) { i++; continue; }
    const h = t.match(/^(#{1,6})\s+(.*)$/);
    if (h) {
      const lv = Math.min(h[1].length, 3);
      out.push(`<div class="md-h md-h${lv}">${inline(h[2])}</div>`);
      i++; continue;
    }
    if (/^(-{3,}|\*{3,}|_{3,})$/.test(t)) { out.push("<hr class='md-hr'>"); i++; continue; }
    if (t.startsWith("|")) {
      // 连续 | 行视为表格；有分隔行则首行作表头，否则全部作数据行（兼容被分块切断的表格）
      const rows = [];
      let j = i;
      while (j < lines.length && lines[j].trim().startsWith("|")) { rows.push(splitRow(lines[j])); j++; }
      let head = null, body = rows;
      if (rows.length >= 2 && rows[1].every((c) => /^\s*:?-{1,}:?\s*$/.test(c) || c === "")) {
        head = rows[0];
        body = rows.slice(2);
      }
      body = body.filter((r) => !(r.length === 1 && r[0] === ""));
      const th = head ? `<thead><tr>${head.map((c) => `<th>${inline(c)}</th>`).join("")}</tr></thead>` : "";
      out.push(
        `<div class="mdt-wrap"><table class="mdt">${th}<tbody>${body
          .map((r) => `<tr>${r.map((c) => `<td>${inline(c)}</td>`).join("")}</tr>`)
          .join("")}</tbody></table></div>`
      );
      i = j;
      continue;
    }
    if (/^[-*+]\s+/.test(t)) {
      const items = [];
      while (i < lines.length && /^\s*[-*+]\s+/.test(lines[i])) {
        items.push(`<li>${inline(lines[i].trim().replace(/^[-*+]\s+/, ""))}</li>`);
        i++;
      }
      out.push(`<ul class="md-ul">${items.join("")}</ul>`);
      continue;
    }
    if (/^\d+[.、)]\s+/.test(t)) {
      const items = [];
      while (i < lines.length && /^\s*\d+[.、)]\s+/.test(lines[i])) {
        items.push(`<li>${inline(lines[i].trim().replace(/^\d+[.、)]\s+/, ""))}</li>`);
        i++;
      }
      out.push(`<ol class="md-ol">${items.join("")}</ol>`);
      continue;
    }
    if (/^(&gt;)+/.test(t)) {
      out.push(`<blockquote class="md-q">${inline(t.replace(/^(&gt;)+\s*/, ""))}</blockquote>`);
      i++; continue;
    }
    const para = [inline(t)];
    i++;
    while (
      i < lines.length && lines[i].trim() &&
      !/^(#{1,6}\s|\||[-*+]\s|\d+[.、)]\s|(-{3,}|\*{3,}|_{3,})$)/.test(lines[i].trim())
    ) {
      para.push(inline(lines[i].trim()));
      i++;
    }
    out.push(`<p class="md-p">${para.join("<br>")}</p>`);
  }
  return out.join("");
}
function srcsHtml(srcs) {
  if (!srcs || !srcs.length) return "";
  return `<div class="a-srcs">${srcs
    .map((s) => {
      const loc = [s.title, s.heading, s.page ? "第 " + s.page + " 页" : ""].filter(Boolean).join(" · ");
      return `<div class="a-src"><b>[${s.ref}]</b><span title="${esc(s.path || s.url || "")}">${esc(loc)}</span></div>`;
    })
    .join("")}</div>`;
}

function splitBlockAtCaret() {
  const ed = $("#editor");
  const sel = getSelection();
  if (!sel.rangeCount) return;
  const r = sel.getRangeAt(0);
  let node = r.startContainer;
  if (node.nodeType === 3) node = node.parentElement;
  let blk = node.closest ? node.closest(".blk") : null;
  if (!blk || !ed.contains(blk)) blk = ensureTrailingBlk();
  const after = document.createRange();
  after.selectNodeContents(blk);
  after.setStart(r.endContainer, r.endOffset);
  const frag = after.extractContents();
  const nb = mkBlk();
  nb.appendChild(frag);
  blk.after(nb);
  caretToEnd(nb);
  refreshAskUi();
}

function collectUserText() {
  return $$(".blk", $("#editor"))
    .map((b) => b.textContent.trim())
    .filter(Boolean)
    .join("\n");
}

function sendFromEditor() {
  normalizeEditor();
  const text = collectUserText();
  if (!text) { toast("先写点什么再发送"); return; }
  if (!state.token) {
    toast("需要访问口令，请到「设置」粘贴", "err");
    location.hash = "#/settings";
    return;
  }
  /* 目标线程（数据源）：无则新建 */
  let th = curThread();
  if (!th) {
    th = { id: "t" + Date.now(), title: "", ts: Date.now(), blocks: [], draft: "" };
    threads().unshift(th);
    state.threadId = th.id;
    localStorage.setItem("lib_cur_thread", th.id);
  }
  if (state.busyMap.has(th.id)) { toast("本对话正在回答，等它完成或切换其他对话继续提问"); return; }
  /* 数据先行：q + 占位 a（streaming）入线程模型 */
  th.blocks.push({ r: "q", t: text });
  th.blocks.push({ r: "a", t: "", srcs: [], streaming: true });
  th.ts = Date.now();
  if (!th.title) th.title = text.slice(0, 24);
  markThreadDirty(th.id);
  saveThreadsNow();
  renderThreadList();
  state.busyMap.set(th.id, { abort: new AbortController(), orb: null });
  updatePill();
  /* 视图：当前线程则把 q/a 画进编辑器 */
  const ed = $("#editor");
  const userBlks = $$(".blk", ed).filter((x) => x.textContent.trim());
  const qd = document.createElement("div");
  qd.className = "q-block";
  qd.contentEditable = "false";
  qd.textContent = text;
  if (userBlks.length) userBlks[0].before(qd);
  else ed.appendChild(qd);
  userBlks.forEach((x) => x.remove());
  const ad = document.createElement("div");
  ad.className = "a-block";
  ad.setAttribute("data-stream", "1");
  const span = document.createElement("span");
  span.className = "stream-text";
  ad.appendChild(span);
  ed.appendChild(ad);
  ensureTrailingBlk();
  caretToEnd(ed.lastElementChild);
  refreshAskUi();
  document.body.classList.add("right-open");
  renderRightbar([]);
  ad.scrollIntoView({ block: "start", behavior: "smooth" });
  askStream(th.id, text, ad, span);
}

/* 每线程一条流：写回线程模型；仅当该线程正被查看时同步到编辑器 DOM */
async function askStream(threadId, q, ad, span) {
  let acc = "", srcs = [];
  const aIdx = () => {
    const th = threadById(threadId);
    return th ? th.blocks.length - 1 : -1;
  };
  const ent = state.busyMap.get(threadId);
  /* 多轮：携带当前问题之前最近 4 组问答（服务端再截 12 条），让追问可理解指代 */
  const hist = [];
  {
    const bl = (threadById(threadId) || { blocks: [] }).blocks;
    for (let i = bl.length - 4; i >= 0 && hist.length < 8; i -= 2) {
      const qb = bl[i], ab = bl[i + 1];
      if (qb && ab && qb.r === "q" && ab.r === "a" && qb.t && ab.t) {
        hist.push({ role: "user", content: String(qb.t).slice(0, 2000) });
        hist.push({ role: "assistant", content: String(ab.t).slice(0, 2000) });
      }
    }
    hist.reverse();
  }
  try {
    const resp = await api("/ask", {
      method: "POST",
      body: JSON.stringify(hist.length ? { question: q, stream: true, history: hist } : { question: q, stream: true }),
      signal: ent ? ent.abort.signal : undefined,
    });
    const reader = resp.body.getReader();
    const dec = new TextDecoder();
    let buf = "", got = false;
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf("\n\n")) >= 0) {
        const line = buf.slice(0, i).trim();
        buf = buf.slice(i + 2);
        if (!line.startsWith("data:")) continue;
        let ev;
        try { ev = JSON.parse(line.slice(5).trim()); } catch { continue; }
        if (ev.type === "sources") {
          srcs = ev.sources;
          const th = threadById(threadId);
          const idx = aIdx();
          if (th && idx >= 0 && th.blocks[idx]) th.blocks[idx].srcs = srcs;
          if (state.threadId === threadId) renderRightbar(srcs);
        } else if (ev.type === "delta") {
          acc += ev.text;
          got = true;
        } else if (ev.type === "error") {
          acc += "\n[生成失败：" + ev.message + "]";
        }
      }
      const th = threadById(threadId);
      const idx = aIdx();
      if (th && idx >= 0 && th.blocks[idx]) th.blocks[idx].t = acc;
      markThreadDirty(threadId);
      queueSave();
      if (state.threadId === threadId) {
        /* 实时查找当前视图里的流式节点：切换对话后节点会被重绘，闭包引用会失效 */
        const liveSpan = document.querySelector('#editor .a-block[data-stream] .stream-text');
        if (liveSpan) {
          liveSpan.innerHTML = mdRender(acc, { cites: true });
          liveSpan.closest(".a-block").scrollIntoView({ block: "end", behavior: "auto" });
        }
      }
    }
    if (!got && !acc) acc = "（无内容返回）";
  } catch (e) {
    if (e.name === "AbortError") acc += acc ? "\n（已停止）" : "（已停止，无返回内容）";
    else if (e.message !== "unauthorized") acc += (acc ? "\n" : "") + "[请求失败：" + e.message + "]";
  } finally {
    /* 收尾：数据回线程（无论是否正在查看） */
    const th = threadById(threadId);
    const idx = th ? th.blocks.length - 1 : -1;
    if (th && idx >= 0 && th.blocks[idx]) {
      th.blocks[idx].t = acc || "（无内容返回）";
      th.blocks[idx].srcs = srcs;
      delete th.blocks[idx].streaming;
    }
    const entF = state.busyMap.get(threadId);
    if (entF && entF.orb) entF.orb.stop();
    if (entF && entF.listOrb) { entF.listOrb.stop(); entF.listOrb = null; }
    state.busyMap.delete(threadId);
    markThreadDirty(threadId);
    saveThreadsNow();
    if (state.threadId === threadId) {
      const liveAd = document.querySelector('#editor .a-block[data-stream]');
      if (liveAd) {
        liveAd.removeAttribute("data-stream");
        liveAd._srcs = srcs || [];   // 移动端引用弹层取用
        const liveSpan = liveAd.querySelector(".stream-text");
        if (liveSpan) { liveSpan.innerHTML = mdRender(acc, { cites: true }); liveSpan.classList.add("done"); }
        if (srcs && srcs.length && !liveAd.querySelector(".a-srcs")) liveAd.insertAdjacentHTML("beforeend", srcsHtml(srcs));
      }
    }
    renderThreadList();
    updatePill();
  }
}

/* 停止“当前对话”的生成（后台对话不受影响） */
function stopAsk() {
  const ent = state.threadId && state.busyMap.get(state.threadId);
  if (ent) { try { ent.abort.abort(); } catch {} }
}

/* ---------- 右栏：本次引用 ---------- */

function locateInAnswer(ref) {
  /* 定位正文：点亮角标 + 高亮其所在句子（同引多处全部标出）并滚动 */
  const hits = $$(".a-block .cite", $("#editor")).filter((el) => el.textContent === "[" + ref + "]");
  if (!hits.length) { toast("正文中没有找到该引用角标"); return; }
  $$(".cite.on").forEach((x) => x.classList.remove("on"));
  const paras = new Set();
  hits.forEach((el) => {
    el.classList.add("on");
    const para = el.closest(".md-p, .md-h, .mdt td, .mdt th, li, blockquote") || el.parentElement;
    if (para) paras.add(para);
  });
  paras.forEach((p2) => {
    p2.classList.remove("cite-hl");
    void p2.offsetWidth;
    p2.classList.add("cite-hl");
    setTimeout(() => p2.classList.remove("cite-hl"), 1800);
  });
  hits[0].scrollIntoView({ block: "center", behavior: "smooth" });
}

function renderRightbar(srcs) {
  const box = $("#rbCards");
  if (!srcs || !srcs.length) {
    $("#rbCount").textContent = "0";
    box.innerHTML = `<p class="rb-empty">回答后这里显示引用来源</p>`;
    return;
  }
  /* 按文档聚合：同一文档的多段引用合并为一张卡，ref 角标保留原编号可定位正文 */
  const groups = [];
  const gmap = new Map();
  for (const s of srcs) {
    const key = s.doc_id || s.title || s.url || String(s.ref);
    let g = gmap.get(key);
    if (!g) {
      g = { key, title: s.title || s.url || "未知来源", url: s.url || "", items: [] };
      gmap.set(key, g);
      groups.push(g);
    }
    g.items.push(s);
  }
  $("#rbCount").textContent = groups.length + " 篇 · " + srcs.length + " 段";
  box.innerHTML = groups
    .map((g) => {
      const titleHtml = g.url
        ? `<a href="${esc(g.url)}" target="_blank" rel="noopener" title="打开原文">${esc(g.title)}</a>`
        : esc(g.title);
      return `<div class="cite-doc" data-key="${esc(g.key)}">
        <div class="cd-head">
          <div class="t">${titleHtml}</div>
          <div class="n">${g.items.length} 段</div>
        </div>
        <div class="cd-chips">${g.items
          .map((s) => `<button class="cd-chip" data-ref="${esc(String(s.ref))}" title="在正文中定位">[${esc(String(s.ref))}]</button>`)
          .join("")}</div>
        <div class="cd-body">${g.items
          .map((s) => {
            const loc = [s.heading, s.page ? "第 " + s.page + " 页" : ""].filter(Boolean).join(" · ");
            return `<div class="cd-chunk">
              ${loc ? `<div class="cd-loc">${esc(loc)}</div>` : ""}
              ${s.text ? `<div class="rb-quote">${mdRender(s.text)}</div>` : `<div class="cd-noquote">此条未存原句</div>`}
            </div>`;
          })
          .join("")}</div>
        <div class="rb-open">点击展开引用原句</div>
      </div>`;
    })
    .join("");
  box.querySelectorAll(".cite-doc").forEach((c) => {
    c.addEventListener("click", (e) => {
      const chip = e.target.closest(".cd-chip");
      if (chip) { e.stopPropagation(); locateInAnswer(chip.dataset.ref); return; }
      const link = e.target.closest("a");
      if (link) { e.stopPropagation(); return; }   // 原文链接走默认行为
      const wasOpen = c.classList.contains("open");
      box.querySelectorAll(".cite-doc.open").forEach((x) => x.classList.remove("open"));
      if (!wasOpen) c.classList.add("open");
    });
  });
}

/* ---------- 侧栏：问答 pane ---------- */

function insertQuestion(q) {
  const ed = $("#editor");
  $$(".blk", ed).forEach((b) => b.remove());   // 清掉全部草稿 blk（含顶部游离空行）
  const d = mkBlk();
  d.textContent = q;
  ed.appendChild(d);
  caretToEnd(d);
  sendFromEditor();
}

function bindAskSidebar() {
  $("#btnNewThread").addEventListener("click", newThread);
  $("#askSearch").addEventListener("input", renderThreadList);
  $("#askSearch").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.isComposing) {
      const q = e.target.value.trim();
      if (q) {
        e.target.value = "";
        renderThreadList();
        if (currentView() !== "ask") location.hash = "#/";
        insertQuestion(q);
      }
    }
  });
}

const TITLES = { ask: "问答", docs: "文档", quiz: "测验", settings: "设置" };
const ROUTES = { "#/": "ask", "#/docs": "docs", "#/quiz": "quiz", "#/settings": "settings" };

function currentView() { return ROUTES[location.hash || "#/"] || "ask"; }

function route() {
  const v = currentView();
  document.body.dataset.view = v;
  document.body.classList.remove("drawer-open");
  $("#scrim").hidden = true;
  document.documentElement.scrollLeft = 0;   // 防横向残留滚动把左侧工具顶出视口
  document.body.scrollLeft = 0;
  const routeKey = Object.keys(ROUTES).find((k) => ROUTES[k] === v);
  $$(".rib-btn[data-route], .tab[data-route]").forEach((a) =>
    a.classList.toggle("on", a.getAttribute("data-route") === routeKey)
  );
  $$(".sb-pane").forEach((p) => { p.hidden = p.dataset.for !== v; });
  $("#viewAsk").hidden = v !== "ask";
  $("#viewDocs").hidden = v !== "docs";
  $("#viewQuiz").hidden = v !== "quiz";
  $("#viewSettings").hidden = v !== "settings";
  $("#vName").textContent = TITLES[v];
  const mLink = $("#mBtnLink");
  if (mLink) mLink.hidden = v !== "docs";
  closeSheetM();
  if (v === "docs") enterDocs();
  if (v === "quiz") enterQuiz();
  if (v === "settings") enterSettings();
  if (v === "ask") refreshAskUi();
}

/* ---------- 小测验（v0.18）：出题配置（侧栏）+ 答题（主区） ---------- */

state.quizPlay = null;   // 进行中的答题会话 {quiz, idx, score, answers:[{ok, pick, given}]}

function enterQuiz() {
  if (!$("#qzGen").dataset.bound) bindQuizSide();
  if (!state.quizPlay) resetQzStage();
  loadQuizList();
}

function bindQuizSide() {
  $("#qzGen").dataset.bound = "1";
  $$("#qzCountSeg button").forEach((b) =>
    b.addEventListener("click", () => {
      $$("#qzCountSeg button").forEach((x) => x.classList.toggle("on", x === b));
    })
  );
  $("#qzFocus").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.isComposing) startQuizGen();
  });
  $("#qzGen").addEventListener("click", () => startQuizGen());
  /* 移动端：主区里的出题控件（与侧栏同一套逻辑） */
  $$("#qzCountSegM button").forEach((b) =>
    b.addEventListener("click", () => {
      $$("#qzCountSegM button").forEach((x) => x.classList.toggle("on", x === b));
    })
  );
  const fm = $("#qzFocusM");
  if (fm) fm.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.isComposing) startQuizGen();
  });
  const gm = $("#qzGenM");
  if (gm) gm.addEventListener("click", () => startQuizGen());
}

function startQuizGen(topic, count) {
  const mInput = $("#qzFocusM");
  const t = (topic !== undefined ? topic
    : (isMobile() && mInput && mInput.value.trim() ? mInput.value : $("#qzFocus").value)).trim();
  if (!t) {
    toast("先输入一个主题，如「Rust 所有权」", "err");
    (isMobile() && mInput ? mInput : $("#qzFocus")).focus();
    return;
  }
  const segSel = isMobile() && mInput ? "#qzCountSegM button.on" : "#qzCountSeg button.on";
  const n = count || Number(($(segSel) || { dataset: { n: 10 } }).dataset.n) || 10;
  if (mInput) mInput.value = "";
  api("/quiz", { method: "POST", body: JSON.stringify({ topic: t, count: n }) })
    .then(() => {
      toast(`正在围绕「${t.slice(0, 16)}」出题（${n} 题）`);
      loadQuizList();
    })
    .catch((e) => {
      if (e.message !== "unauthorized") toast(e.message, "err");
    });
}

let _qzPollTimer = null;
function ensureQuizPolling() {
  if (_qzPollTimer) return;
  _qzPollTimer = setInterval(() => loadQuizList(), 2500);
}
function stopQuizPolling() {
  if (_qzPollTimer) { clearInterval(_qzPollTimer); _qzPollTimer = null; }
}

async function loadQuizList() {
  const wrap = $("#qzList");
  if (!wrap) return;
  try {
    const [qr, tr] = await Promise.all([
      api("/quiz", { quiet: true }),
      api("/tasks?limit=10", { quiet: true }),
    ]);
    const quizzes = (await qr.json()).items || [];
    const tasks = ((await tr.json()).items || []).filter(
      (t) => t.kind === "quiz" && (t.status === "running" || t.status === "queued")
    );
    const taskRows = tasks.map((t) => `
      <div class="sb-conv busy qz-gen" title="正在出题">
        <span class="sb-orb ai-orb"></span>
        <span class="t">${esc(t.detail || "排队中")}</span>
        <span class="tm busy">生成中</span>
      </div>`).join("");
    const quizRows = quizzes.map((q) => `
      <div class="sb-conv qz-row" data-id="${esc(q.id)}">
        <span class="t">${esc((q.title || "测验").replace(/ · \d+ 题$/, ""))}</span>
        <span class="tm">${q.best_score != null ? `最佳 ${q.best_score}/${q.count}` : `${q.count} 题`}</span>
        <button class="del" data-del="${esc(q.id)}" title="删除测验"><svg class="ic" style="width:11px;height:11px"><use href="#i-x"/></svg></button>
      </div>`).join("");
    wrap.innerHTML = taskRows
      + quizRows
      + (!taskRows && !quizRows ? `<div class="sb-empty">暂无测验</div>` : "");
    /* 移动端：主区历史列表镜像（demo 式行：图标 + 标题 + 元信息 + 删除） */
    const mWrap = $("#qzMList");
    if (mWrap) {
      const mTaskRows = tasks.map((t) => `
        <div class="m-gen-row"><span class="sb-orb ai-orb"></span><span class="t">${esc(t.detail || "排队中")}</span><span class="tm">生成中</span></div>`).join("");
      const mRows = quizzes.map((q) => `
        <div class="m-doc-row qz-row" data-id="${esc(q.id)}">
          <span class="doc-ic"><svg class="ic"><use href="#i-quiz"/></svg></span>
          <span class="doc-mid">
            <div class="doc-t">${esc((q.title || "测验").replace(/ · \d+ 题$/, ""))}</div>
            <div class="doc-meta">${q.count} 题${q.best_score != null ? ` · 最佳 ${q.best_score}/${q.count}` : ""}</div>
          </span>
          <button class="qz-del" data-del="${esc(q.id)}" aria-label="删除测验"><svg class="ic"><use href="#i-x"/></svg></button>
        </div>`).join("");
      mWrap.innerHTML = mTaskRows + mRows + (!mTaskRows && !mRows ? `<div class="m-docs-empty">暂无测验<br>先在上方输入主题出一份</div>` : "");
      $$("#qzMList .qz-gen .sb-orb, #qzMList .m-gen-row .sb-orb").forEach((h) => {
        if (window.AIOrb) window.AIOrb.mount(h, 14);
      });
      $$("#qzMList .qz-row").forEach((row) =>
        row.addEventListener("click", (e) => {
          if (e.target.closest("[data-del]")) return;
          openQuizPlay(row.dataset.id);
        })
      );
      $$("#qzMList [data-del]").forEach((b) =>
        b.addEventListener("click", async () => {
          try {
            await api("/quiz?id=" + encodeURIComponent(b.dataset.del), { method: "DELETE" });
            loadQuizList();
          } catch (e) { if (e.message !== "unauthorized") toast(e.message, "err"); }
        })
      );
    }
    /* 生成中行挂小号线圈球（与问答回答同款动画） */
    $$("#qzList .qz-gen .sb-orb").forEach((h) => {
      if (window.AIOrb) window.AIOrb.mount(h, 14);
    });
    $$("#qzList .qz-row").forEach((row) =>
      row.addEventListener("click", (e) => {
        if (e.target.closest("[data-del]")) return;
        openQuizPlay(row.dataset.id);
      })
    );
    $$("#qzList [data-del]").forEach((b) =>
      b.addEventListener("click", async () => {
        try {
          await api("/quiz?id=" + encodeURIComponent(b.dataset.del), { method: "DELETE" });
          loadQuizList();
        } catch (e) { if (e.message !== "unauthorized") toast(e.message, "err"); }
      })
    );
    if (tasks.length) ensureQuizPolling();
    else stopQuizPolling();
  } catch { stopQuizPolling(); /* 静默 */ }
}

async function openQuizPlay(quizId) {
  try {
    const q = await (await api("/quiz?id=" + encodeURIComponent(quizId))).json();
    state.quizPlay = { quiz: q, idx: 0, score: 0, answers: [] };
    const view = $("#viewQuiz");
    if (view) view.classList.add("playing");   // 移动端：答题层推入
    renderQuizQuestion();
  } catch (e) {
    if (e.message !== "unauthorized") toast(e.message, "err");
  }
}

function quizScoreText(p) {
  return `${p.score % 1 === 0 ? p.score : p.score.toFixed(1)} / ${p.quiz.questions.length}`;
}

function renderQuizQuestion() {
  const stage = $("#qzStage");
  const p = state.quizPlay;
  if (!p || !stage) return;
  const qs = p.quiz.questions;
  if (p.idx >= qs.length) return renderQuizDone();
  const q = qs[p.idx];
  const prog = Math.round((p.idx / qs.length) * 100);
  let body = "";
  if (q.type === "short") {
    body = `
      <textarea class="qz-input" id="qzInput" rows="4" placeholder="输入你的答案…"></textarea>
      <div class="qz-actions"><button class="btn primary" id="qzSubmit">提交答案</button></div>
      <div class="qz-feedback" id="qzFeedback" hidden></div>`;
  } else {
    const opts = q.type === "bool"
      ? ["正确", "错误"]
      : q.options.map((o, i) => `${"ABCD"[i] || ""}. ${o}`);
    body = opts.map((o, i) => `
      <button class="qz-opt" data-i="${i}"><span class="qz-key">${q.type === "bool" ? ["✓", "✗"][i] : i + 1}</span><span class="qz-txt">${esc(o)}</span></button>`).join("")
      + `<div class="qz-feedback" id="qzFeedback" hidden></div>`;
  }
  stage.innerHTML = `
    <button class="m-stage-back only-mobile" id="qzExitM" aria-label="退出答题"><svg class="ic"><use href="#i-back"/></svg><span>${esc((p.quiz.title || "测验").replace(/ · \d+ 题$/, ""))}</span></button>
    <div class="set-wrap">
    <div class="qz-play">
      <div class="qz-top">
        <span class="qz-prog-info">${p.idx + 1} / ${qs.length} · 得分 ${quizScoreText(p)}</span>
        <span class="qz-src-tag">${esc((p.quiz.title || "").replace(/ · \d+ 题$/, ""))}</span>
      </div>
      <div class="qz-bar"><span style="width:${prog}%"></span></div>
      <div class="qz-card">
        <div class="qz-type">${q.type === "single" ? "单选" : q.type === "bool" ? "判断" : "简答"}${q.ref ? ` · <span class="qz-ref">${esc(q.ref)}</span>` : ""}</div>
        <div class="qz-stem">${esc(q.q)}</div>
        ${body}
      </div>
    </div>
  </div>`;
  const exitM = $("#qzExitM");
  if (exitM) exitM.addEventListener("click", exitQuizPlay);
  if (q.type === "short") {
    $("#qzInput").focus();
    $("#qzSubmit").addEventListener("click", () => submitShortAnswer(q));
  } else {
    const correct = q.type === "bool" ? (q.answer ? 0 : 1) : q.answer;
    $$(".qz-opt", stage).forEach((b) =>
      b.addEventListener("click", () => gradeObjective(q, Number(b.dataset.i), correct))
    );
  }
}

/* 退出答题：回列表（移动端收起推入层，桌面恢复空态） */
function exitQuizPlay() {
  state.quizPlay = null;
  const view = $("#viewQuiz");
  if (view) view.classList.remove("playing");
  resetQzStage();
  loadQuizList();
}

function resetQzStage() {
  const stage = $("#qzStage");
  if (!stage) return;
  stage.innerHTML = `
      <div class="qz-empty-state" id="qzEmpty">
        <svg class="ic"><use href="#i-quiz"/></svg>
        <div class="t">输入主题开始出题</div>
      </div>`;
}

function gradeObjective(q, pick, correct) {
  const stage = $("#qzStage");
  const p = state.quizPlay;
  if (!p || $("#qzNext")) return;   // 已判分，防重复点击
  const ok = pick === correct;
  if (ok) p.score += 1;
  p.answers.push({ q, ok, pick, correct });
  const info = $(".qz-prog-info");
  if (info) info.textContent = `${p.idx + 1} / ${p.quiz.questions.length} · 得分 ${quizScoreText(p)}`;
  const opts = $$(".qz-opt", stage);
  opts[correct].classList.add("ok");
  if (!ok) opts[pick].classList.add("bad");
  opts.forEach((o) => (o.disabled = true));
  const fb = $("#qzFeedback");
  fb.hidden = false;
  fb.innerHTML = `
    <div class="qz-verdict ${ok ? "ok" : "bad"}">${ok ? "✓ 回答正确" : "✗ 回答错误"}</div>
    ${q.explanation ? `<div class="qz-exp">${esc(q.explanation)}</div>` : ""}
    <div class="qz-actions"><button class="btn primary" id="qzNext">${p.idx + 1 >= p.quiz.questions.length ? "查看成绩" : "下一题"}</button></div>`;
  $("#qzNext").addEventListener("click", () => { p.idx += 1; renderQuizQuestion(); });
}

async function submitShortAnswer(q) {
  const p = state.quizPlay;
  const input = $("#qzInput");
  const answer = (input.value || "").trim();
  if (!answer) { toast("先写点答案再提交"); return; }
  const btn = $("#qzSubmit");
  btn.disabled = true;
  btn.textContent = "判分中…";
  input.disabled = true;
  try {
    const g = await (await api("/quiz/grade", {
      method: "POST",
      body: JSON.stringify({ id: p.quiz.id, index: p.idx, answer }),
    })).json();
    const gained = g.score / 2;   // 档位 2/1/0 → 1/0.5/0 分
    p.score += gained;
    p.answers.push({ q, ok: g.score === 2, partial: g.score === 1, given: answer, graded: g });
    const info = $(".qz-prog-info");
    if (info) info.textContent = `${p.idx + 1} / ${p.quiz.questions.length} · 得分 ${quizScoreText(p)}`;
    const fb = $("#qzFeedback");
    fb.hidden = false;
    fb.innerHTML = `
      <div class="qz-verdict ${g.score === 2 ? "ok" : g.score === 0 ? "bad" : "mid"}">${g.score === 2 ? "✓ 命中要点（+1 分）" : g.score === 1 ? "◐ 部分正确（+0.5 分）" : "✗ 未命中（0 分）"}</div>
      ${g.comment ? `<div class="qz-exp">评语：${esc(g.comment)}</div>` : ""}
      <div class="qz-exp">参考答案：${esc(g.reference)}</div>
      <div class="qz-actions"><button class="btn primary" id="qzNext">${p.idx + 1 >= p.quiz.questions.length ? "查看成绩" : "下一题"}</button></div>`;
    $("#qzNext").addEventListener("click", () => { p.idx += 1; renderQuizQuestion(); });
  } catch (e) {
    btn.disabled = false;
    btn.textContent = "提交答案";
    input.disabled = false;
    if (e.message !== "unauthorized") toast(e.message + "，可重试", "err");
  }
}

function renderQuizDone() {
  const stage = $("#qzStage");
  const p = state.quizPlay;
  const wrongs = p.answers.filter((a) => !a.ok);
  stage.innerHTML = `
    <button class="m-stage-back only-mobile" id="qzExitM" aria-label="退出答题"><svg class="ic"><use href="#i-back"/></svg><span>${esc((p.quiz.title || "测验").replace(/ · \d+ 题$/, ""))}</span></button>
    <div class="set-wrap">
    <div class="qz-done">
      <div class="qzd-score">${quizScoreText(p)}</div>
      <div class="qzd-sub">${wrongs.length ? `答错 ${wrongs.length} 题` : "全对，漂亮！"}</div>
      <div class="qz-actions" style="justify-content:center">
        <button class="btn ghost" id="qzRedo">重做</button>
        <button class="btn ghost" id="qzBack">返回列表</button>
      </div>
    </div>
    ${wrongs.length ? `<section class="set-card"><div class="sc-t">错题回顾（${wrongs.length}）</div>
      ${wrongs.map((a) => quizWrongHtml(a)).join("")}
    </section>` : ""}
  </div>`;
  api("/quiz/result", { method: "POST", body: JSON.stringify({ id: p.quiz.id, score: Math.round(p.score * 10) / 10 }), quiet: true })
    .then(() => loadQuizList()).catch(() => {});
  const exitM = $("#qzExitM");
  if (exitM) exitM.addEventListener("click", exitQuizPlay);
  $("#qzRedo").addEventListener("click", () => {
    state.quizPlay = { quiz: p.quiz, idx: 0, score: 0, answers: [] };
    renderQuizQuestion();
  });
  $("#qzBack") && $("#qzBack").addEventListener("click", exitQuizPlay);
}

function quizWrongHtml(a) {
  const q = a.q;
  let answerLine = "";
  if (q.type === "short") {
    answerLine = `<div class="qzw-line">参考答案：${esc((a.graded && a.graded.reference) || "")}</div>`;
  } else {
    const correctText = q.type === "bool" ? (q.answer ? "正确" : "错误") : (q.options || [])[a.correct] || "";
    answerLine = `<div class="qzw-line">正确答案：${esc(String(correctText))}</div>`;
  }
  return `<div class="qz-wrong">
    <div class="qzw-q">${esc(q.q)}</div>
    ${answerLine}
    ${q.explanation ? `<div class="qz-exp">${esc(q.explanation)}</div>` : ""}
  </div>`;
}

/* 测验视图键盘：数字键选选项 */
document.addEventListener("keydown", (e) => {
  if (currentView() !== "quiz" || e.metaKey || e.ctrlKey || e.isComposing) return;
  const p = state.quizPlay;
  if (!p || $("#qzNext")) return;
  const q = p.quiz.questions[p.idx];
  if (!q || q.type === "short") return;
  const n = Number(e.key);
  if (n >= 1 && n <= (q.type === "bool" ? 2 : 4)) {
    const b = $(`.qz-opt[data-i="${n - 1}"]`);
    if (b) b.click();
  }
});

/* ---------- 上传 / URL / B站（写入监听目录，watcher 自动索引） ---------- */

async function uploadFiles(files) {
  if (!files.length) return;
  for (const f of files) {
    try {
      const fd = new FormData();
      fd.append("file", f, f.name);
      const resp = await fetch("/api/v1/ingest/upload", {
        method: "POST",
        headers: { "X-API-Key": state.token },
        body: fd,
      });
      if (resp.status === 401) { toast("需要访问口令，请到「设置」粘贴", "err"); location.hash = "#/settings"; return; }
      const r = await resp.json();
      if (!resp.ok) throw new Error(r.detail || "HTTP " + resp.status);
      toast(`已接收「${f.name}」，自动索引中…`);
      loadDocTree(true);
    } catch (e) {
      toast(`「${f.name}」上传失败：${e.message}`, "err");
    }
  }
}

const isVideoUrl = (u) => /bilibili\.com|b23\.tv|kuaishou\.com/.test(u);

async function ingestUrl(url) {
  if (!url) return;
  try {
    await (await api("/ingest/url", { method: "POST", body: JSON.stringify({ url }) })).json();
    toast("已创建抓取任务");
    pollTasks();
    loadDocTree(true);
  } catch (e) {
    if (e.message !== "unauthorized") toast(e.message, "err");
  }
}

async function ingestVideo(url) {
  if (!url) return;
  try {
    await (await api("/ingest/video", { method: "POST", body: JSON.stringify({ url }) })).json();
    toast("已创建视频笔记任务");
    pollTasks();
  } catch (e) {
    if (e.message !== "unauthorized") toast(e.message, "err");
  }
}

/* 任务 → 文件树内"生成中"条目（线圈球动画与 AI 回答一致） */

function updateActiveTasks(items) {
  const active = items.filter((t) => t.status === "queued" || t.status === "running");
  const sig = JSON.stringify(active.map((t) => [t.id, t.status, t.detail]));
  const had = state.activeTasks.length > 0;
  if (sig === state._tasksSig) return;   // 无变化不重绘，避免打断线圈动画
  state._tasksSig = sig;
  state.activeTasks = active;
  renderDocTree();
  if (had && active.length === 0) loadDocTree(true);   // 任务完成：立即拉取新文档
}

async function pollTasks() {
  try {
    const r = await (await api("/tasks?limit=5")).json();
    updateActiveTasks(r.items);
  } catch { /* 网络抖动忽略 */ }
}

/* ---------- 视图：文档（分区目录树 + 快速预览 + 任务面板） ---------- */

const GROUPS = [
  ["root", "我的资料 · 监听目录"],
  ["uploads", "uploads · 上传"],
  ["clips", "clips · 网页摘录"],
  ["bilibili", "视频笔记（B站）"],
];
const groupOf = (d) => {
  const p = d.path || "";
  if (d.source === "video") return "bilibili";
  if (p.includes("/uploads/")) return "uploads";
  if (p.includes("/clips/")) return "clips";
  return "root";
};
const ICONS = { md: "#i-file-text", pdf: "#i-file", html: "#i-globe", code: "#i-code", text: "#i-file-text", docx: "#i-file-text", pptx: "#i-file-text", xlsx: "#i-file-text", image: "#i-image", video: "#i-play" };

function enterDocs() {
  loadDocTree();
  pollTasks();
  clearInterval(state.docsTreeTimer);
  clearInterval(state.docsTaskTimer);
  state.docsTreeTimer = setInterval(() => { if (currentView() === "docs") loadDocTree(true); }, 8000);
  state.docsTaskTimer = setInterval(() => { if (currentView() === "docs") pollTasks(); }, 3000);
}

async function loadDocTree(quiet = false) {
  try {
    const r = await (await api("/documents?limit=200")).json();
    const sig = JSON.stringify(r.items);
    const changed = sig !== state._docsSig;
    state._docsSig = sig;
    state.docsCache = r.items;
    if (changed || !quiet) renderDocTree();   // 静默刷新数据未变则不重绘，避免打断点击/折叠
    renderTaskBadge();
  } catch (e) {
    if (e.message !== "unauthorized" && !quiet) $("#docTree").innerHTML = `<div class="sb-empty">加载失败：${esc(e.message)}</div>`;
  }
}

function taskGroupOf(kind) { return kind === "video_ingest" ? "bilibili" : "clips"; }
function taskTitle(t) {
  if (t.kind === "video_ingest") return "视频笔记";
  try { const u = new URL(JSON.parse(t.payload || "{}").url || "about:blank"); return u.hostname || "网页"; } catch { return "网页"; }
}

function docSummary(d) {
  if (!d.summary) return null;
  try { const o = JSON.parse(d.summary); return o && o.summary ? o : null; } catch { return null; }
}

/* 监听目录分组：按相对监听目录的子路径建多级折叠树（状态存 localStorage，默认收起） */
const relDirOf = (d) => d.rel_dir || "";
function buildDirTree(items) {
  const root = { dirs: {}, files: [] };
  for (const d of items) {
    let node = root;
    const rel = relDirOf(d);
    if (rel) {
      for (const part of rel.split("/")) {
        node.dirs[part] = node.dirs[part] || { dirs: {}, files: [] };
        node = node.dirs[part];
      }
    }
    node.files.push(d);
  }
  return root;
}
const treeFold = () => {
  try { return JSON.parse(localStorage.getItem("lib_tree_fold") || "{}"); } catch { return {}; }
};
const countAll = (n) => n.files.length + Object.values(n.dirs).reduce((s, c) => s + countAll(c), 0);

function renderDocTree() {
  const tree = $("#docTree");
  const groups = Object.fromEntries(GROUPS.map(([k]) => [k, []]));
  state.docsCache.forEach((d) => groups[groupOf(d)].push(d));
  state.activeTasks.forEach((t) => groups[taskGroupOf(t.kind)].push({ _task: t }));
  const fold = treeFold();
  const fileRow = (d, depth) => {
    if (d._task) {
      return `<div class="sb-frow gen" style="padding-left:${25 + depth * 14}px">
        <span class="ai-orb"></span>
        <span class="t">${esc(d._task.detail || taskTitle(d._task))}</span>
        <span class="tm busy">生成中</span>
      </div>`;
    }
    const busy = d.status && d.status !== "indexed" && d.status !== "failed";
    return `<div class="sb-frow ${d.id === state.curDocId ? "on" : ""}" data-id="${esc(d.id)}" style="padding-left:${25 + depth * 14}px">
      <span class="t">${esc(d.title || d.url || "未命名")}</span>
      ${busy ? `<span class="tm busy">${ST_LABEL[d.status] || d.status}</span>` : ""}
    </div>`;
  };
  const dirNode = (name, node, key, depth) => {
    const open = !!fold[key];
    const kids = Object.keys(node.dirs).sort().map((dn) => dirNode(dn, node.dirs[dn], key ? key + "/" + dn : dn, depth + 1)).join("")
      + node.files.sort((a, b) => (a.title || "").localeCompare(b.title || "")).map((d) => fileRow(d, depth + 1)).join("");
    return `<div class="sb-dir">
      <div class="sb-dh${open ? " open" : ""}" data-key="${esc(key)}" style="padding-left:${8 + depth * 14}px">
        <svg class="ic chev"><use href="#i-chev-d"/></svg><span class="t">${esc(name)}</span><span class="n">${countAll(node)}</span>
      </div>
      <div class="sb-dc"${open ? "" : " hidden"}>${kids}</div>
    </div>`;
  };
  // 解析中的文档（watcher 自动入库/重试中）：线圈球 + 文件名 + 状态，置顶展示
  const parsing = state.docsCache.filter((d) => d.status === "indexing" || d.status === "pending");
  const parsingBlock = parsing.length
    ? `<div class="sb-grp parsing">
        <div class="sb-gh"><span class="t">解析中</span><span class="n">${parsing.length}</span></div>
        <div class="sb-gi">${parsing.map((d) => `
          <div class="sb-frow gen">
            <span class="ai-orb"></span>
            <span class="t">${esc(d.title || d.path || "未命名")}</span>
            <span class="tm busy">${ST_LABEL[d.status] || d.status}</span>
          </div>`).join("")}</div>
      </div>`
    : "";
  tree.innerHTML = parsingBlock + GROUPS.map(([k, label]) => {    const arr = groups[k];
    if (!arr.length) return "";
    let body;
    if (k === "root") {
      const tree_ = buildDirTree(arr.filter((d) => !d._task));
      body = Object.keys(tree_.dirs).sort().map((dn) => dirNode(dn, tree_.dirs[dn], dn, 0)).join("")
        + tree_.files.sort((a, b) => (a.title || "").localeCompare(b.title || "")).map((d) => fileRow(d, 0)).join("")
        + arr.filter((d) => d._task).map((d) => fileRow(d, 0)).join("");
    } else {
      body = arr
        .map((d) => {
          if (d._task) {
            return `<div class="sb-frow gen">
            <span class="ai-orb"></span>
            <span class="t">${esc(d._task.detail || taskTitle(d._task))}</span>
            <span class="tm busy">生成中</span>
          </div>`;
          }
          const busy = d.status && d.status !== "indexed" && d.status !== "failed";
          return `<div class="sb-frow ${d.id === state.curDocId ? "on" : ""}" data-id="${esc(d.id)}">
            <span class="t">${esc(d.title || d.url || "未命名")}</span>
            ${busy ? `<span class="tm busy">${ST_LABEL[d.status] || d.status}</span>` : ""}
          </div>`;
        })
        .join("");
    }
    return `<div class="sb-grp">
      <div class="sb-gh"><svg class="ic chev"><use href="#i-chev-d"/></svg><span class="t">${label}</span><span class="n">${arr.length}</span></div>
      <div class="sb-gi">${body}</div>
    </div>`;
  }).join("");
  tree.querySelectorAll(".sb-gh").forEach((gh) =>
    gh.addEventListener("click", () => gh.parentElement.classList.toggle("closed"))
  );
  tree.querySelectorAll(".sb-dh").forEach((dh) =>
    dh.addEventListener("click", () => {
      const key = dh.dataset.key;
      const f = treeFold();
      if (f[key]) delete f[key]; else f[key] = 1;
      localStorage.setItem("lib_tree_fold", JSON.stringify(f));
      renderDocTree();
    })
  );
  tree.querySelectorAll(".sb-frow:not(.gen)").forEach((row) => row.addEventListener("click", () => openPreview(safeId(row.dataset.id))));
  tree.querySelectorAll(".sb-frow.gen .ai-orb").forEach((h) => {
    if (window.AIOrb) window.AIOrb.mount(h, 14);
  });
  renderMobileDocs(groups);
}

/* 移动端：主区平铺分组列表（demo 式行，替代侧栏树） */
function renderMobileDocs(groups) {
  const box = $("#mDocs");
  if (!box) return;
  const src = groups || (() => {
    const g = Object.fromEntries(GROUPS.map(([k]) => [k, []]));
    state.docsCache.forEach((d) => g[groupOf(d)].push(d));
    state.activeTasks.forEach((t) => g[taskGroupOf(t.kind)].push({ _task: t }));
    return g;
  })();
  box.innerHTML = GROUPS.map(([k, label]) => {
    const arr = src[k];
    if (!arr || !arr.length) return "";
    const rows = arr.map((d) => {
      if (d._task) {
        return `<div class="m-gen-row"><span class="sb-orb ai-orb"></span><span class="t">${esc(d._task.detail || taskTitle(d._task))}</span><span class="tm">生成中</span></div>`;
      }
      const busy = d.status && d.status !== "indexed" && d.status !== "failed";
      const vid = d.doc_type === "video";
      const rd = k === "root" ? relDirOf(d) : "";
      return `<div class="m-doc-row" data-id="${esc(d.id)}">
        <span class="doc-ic ${vid ? "vid" : ""}"><svg class="ic"><use href="${ICONS[d.doc_type] || "#i-file"}"></use></svg></span>
        <span class="doc-mid">
          <div class="doc-t">${rd ? `<span class="doc-dir">${esc(rd)}/</span>` : ""}${esc(d.title || d.url || "未命名")}</div>
          ${busy ? `<div class="doc-meta"><span class="st-busy">${ST_LABEL[d.status] || d.status}</span></div>` : ""}
        </span>
        <svg class="ic chev"><use href="#i-chev-r"/></svg>
      </div>`;
    }).join("");
    return `<div class="m-grp-h"><span>${label}</span><span class="n">${arr.length}</span></div>${rows}`;
  }).join("") || `<div class="m-docs-empty">还没有文档<br>右上角「链接」入库，或等监听目录扫描</div>`;
  box.querySelectorAll(".m-doc-row").forEach((row) =>
    row.addEventListener("click", () => openPreview(safeId(row.dataset.id)))
  );
  box.querySelectorAll(".m-gen-row .sb-orb").forEach((h) => {
    if (window.AIOrb) window.AIOrb.mount(h, 14);
  });
}

function renderTaskBadge() {
  const n = state.docsCache.filter((d) => d.status === "indexing" || d.status === "pending").length;
  const b = $("#taskBadge");
  b.hidden = !n;
  if (n) b.textContent = "任务 " + n;
}

async function openPreview(rawId) {
  const id = safeId(rawId);
  state.curDocId = id;
  $$("#docTree .sb-frow").forEach((r) => r.classList.toggle("on", r.dataset.id === id));
  const box = $("#docPrev");
  box.hidden = false;
  box.innerHTML = `<div class="dp-meta">加载中…</div>`;
  let d;
  try { d = await (await api("/documents/" + id)).json(); }
  catch (e) { box.innerHTML = `<div class="dp-meta">加载失败：${esc(e.message)}</div>`; return; }
  const ic = ICONS[d.doc_type] || "#i-file";
  const stCls = d.status === "failed" ? "fail" : d.status === "indexed" ? "" : "busy";
  const frags = (d.chunks_preview || [])
    .map((c) => `<div class="frag">${c.heading ? `<span class="fh">${esc(c.heading)}${c.page ? " · 第 " + c.page + " 页" : ""}</span>` : ""}${mdRender(c.text)}</div>`)
    .join("");
  const from = (d.path || "").split("/").slice(-2).join("/");
  const canRaw = d.doc_type === "pdf" || d.doc_type === "html";
  const ds = docSummary(d);
  const sumBlock = `
    <div class="dp-sum">
      <div class="ds-head">
        <span class="ds-t">AI 摘要</span>
        <button class="mini-btn irow" id="pvDigest" title="用问答模型生成摘要与关键问题">${ds ? "重新生成" : "生成摘要"}</button>
      </div>
      ${ds ? `<div class="ds-body">${esc(ds.summary)}</div>
      ${(ds.questions || []).length ? `<ul class="ds-q">${ds.questions.map((q) => `<li>${esc(q)}</li>`).join("")}</ul>` : ""}`
      : `<div class="ds-none">还没有摘要——入库后会自动生成，也可以点上面的按钮立即生成。</div>`}
    </div>`;
  box.innerHTML = `
    <button class="m-stage-back only-mobile" id="dpBack" aria-label="返回文档列表"><svg class="ic"><use href="#i-back"/></svg><span>文档</span></button>
    <div class="dp-head">
      <svg class="ic"><use href="${ic}"/></svg>
      <span class="t">${esc(d.title || d.url || "未命名")}</span>
      <span class="dp-type">${esc(d.doc_type)}</span>
      <span class="dp-st ${stCls}">${ST_LABEL[d.status] || d.status || ""}</span>
      <span class="spacer"></span>
      <button class="mini-btn irow" id="pvRename" title="重命名"><svg class="ic"><use href="#i-rename"/></svg>重命名</button>
      <button class="mini-btn irow" id="pvQuiz"><svg class="ic"><use href="#i-quiz"/></svg>出题测验</button>
      <button class="mini-btn irow" id="pvRe"><svg class="ic"><use href="#i-refresh"/></svg>重新索引</button>
      <button class="mini-btn irow" id="pvDel" style="color:var(--err)"><svg class="ic"><use href="#i-trash"/></svg>删除</button>
    </div>
    ${sumBlock}
    <div class="dp-meta">
      ${esc(d.doc_type)} · ${d.chunk_count} 片段 · ${fmtSize(d.size || 0)} · ${new Date((d.indexed_at || d.created_at) * 1000).toLocaleString()} · 来自 ${esc(from)}
      ${d.url ? ` · <a href="${esc(d.url)}" target="_blank" rel="noopener">原文链接</a>` : ""}
    </div>
    ${canRaw ? `
    <div class="dp-tabs">
      <span class="dp-tab on" data-tab="raw">原文</span>
      <span class="dp-tab" data-tab="frags">片段</span>
    </div>
    <div class="dp-raw" id="dpRaw"><div class="dp-note">原文加载中…</div></div>
    <div class="dp-frags" id="dpFrags" hidden>
      <p class="dp-sec">片段预览 · 前 ${(d.chunks_preview || []).length} / ${d.chunk_count}</p>
      ${frags || '<div class="dp-note">暂无片段</div>'}
    </div>`
    : `
    <p class="dp-sec">片段预览 · 前 ${(d.chunks_preview || []).length} / ${d.chunk_count}</p>
    ${frags || '<div class="dp-note">暂无片段</div>'}`}
    <div class="dp-note">全文已入库可被检索</div>`;
  box.scrollTop = 0;
  if (canRaw) {
    if (d.doc_type === "pdf") loadRawPages(id, box);
    else loadRawHtml(id, box);
    $$(".dp-tab", box).forEach((tbtn) =>
      tbtn.addEventListener("click", () => {
        $$(".dp-tab", box).forEach((x) => x.classList.toggle("on", x === tbtn));
        $("#dpRaw", box).hidden = tbtn.dataset.tab !== "raw";
        $("#dpFrags", box).hidden = tbtn.dataset.tab !== "frags";
      })
    );
  }
  const dpBack = $("#dpBack", box);
  if (dpBack) dpBack.addEventListener("click", () => { box.hidden = true; state.curDocId = null; loadDocTree(true); });
  const pvRename = $("#pvRename", box);
  if (pvRename) pvRename.addEventListener("click", () => {
    const t = box.querySelector(".dp-head .t");
    if (!t || t.querySelector("input")) return;
    const old = t.textContent;
    t.innerHTML = `<input class="dp-rename-in" value="${esc(old)}" maxlength="120" aria-label="新标题">`;
    const inp = t.querySelector("input");
    inp.focus(); inp.select();
    const restore = () => { t.textContent = old; };
    const save = async () => {
      const v = inp.value.trim();
      if (!v || v === old) { restore(); return; }
      try {
        await api("/documents/" + id, { method: "PATCH", body: JSON.stringify({ title: v }) });
        toast("已重命名");
        const d = state.docsCache.find((x) => safeId(x.id) === id);
        if (d) d.title = v;
        t.textContent = v;
        loadDocTree(true);
      } catch (e) {
        restore();
        if (e.message !== "unauthorized") toast(e.message, "err");
      }
    };
    inp.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.isComposing) { e.preventDefault(); save(); }
      if (e.key === "Escape") restore();
    });
    inp.addEventListener("blur", () => { if (t.querySelector("input")) restore(); });
  });
  $("#pvQuiz", box).addEventListener("click", () => {
    location.hash = "#/quiz";
    setTimeout(() => {
      const d = state.docsCache.find((x) => safeId(x.id) === id);
      const topic = ((d && d.title) || "综合").replace(/^【视频】/, "").slice(0, 40);
      const input = $("#qzFocus");
      if (input) input.value = topic;
      const inputM = $("#qzFocusM");
      if (inputM) inputM.value = topic;
      startQuizGen(topic);
    }, 150);
  });
  $("#pvDigest", box).addEventListener("click", async () => {
    try {
      await api("/documents/digest", { method: "POST", body: JSON.stringify({ doc_id: id }) });
      toast("已提交摘要生成，稍后自动刷新");
      setTimeout(() => { loadDocTree(true); openPreview(id); }, 4000);
    } catch (e) { if (e.message !== "unauthorized") toast(e.message, "err"); }
  });
  $("#pvRe", box).addEventListener("click", async () => {
    try {
      await api("/documents/" + id + "/reindex", { method: "POST" });
      toast("重索引完成");
      loadDocTree(true);
      openPreview(id);
    } catch (e) { if (e.message !== "unauthorized") toast(e.message, "err"); }
  });
  $("#pvDel", box).addEventListener("click", async () => {
    if (!confirm("删除该文档及其索引？")) return;
    try {
      await api("/documents/" + id, { method: "DELETE" });
      toast("已删除");
      state.curDocId = null;
      box.hidden = true;
      box.replaceChildren();
      loadDocTree();
      pollHealth();
    } catch (e) { if (e.message !== "unauthorized") toast(e.message, "err"); }
  });
}

/* 原文加载：PDF=服务端按页出图（懒加载，随预览流自然滚动）；HTML=blob iframe 网页渲染 */

async function loadRawPages(id, box) {
  const host = $("#dpRaw", box);
  if (!host) return;
  try {
    const meta = await (await api("/documents/" + safeId(id) + "/pages")).json();
    const total = meta.pages || 0;
    if (!total) { host.innerHTML = `<div class="dp-note">无法读取页数</div>`; return; }
    const stack = document.createElement("div");
    stack.className = "pg-stack";
    host.replaceChildren(stack);
    // 视口作 root（移动端预览层是 fixed 独立滚动容器，元素 root 在部分 webview 不触发）；
    // 首屏可见页直接填，后续滚动交给 IO
    const io = new IntersectionObserver((entries) => {
      for (const en of entries) {
        if (!en.isIntersecting) continue;
        io.unobserve(en.target);
        fetchPageImg(id, Number(en.target.dataset.page), en.target);
      }
    }, { rootMargin: "400px" });
    for (let i = 1; i <= total; i++) {
      const d = document.createElement("div");
      d.className = "pg";
      d.dataset.page = i;
      d.innerHTML = `<span class="pg-no">${i} / ${total}</span>`;
      stack.appendChild(d);
      io.observe(d);
      if (d.getBoundingClientRect().top < window.innerHeight + 400) fetchPageImg(id, i, d);
    }
  } catch (e) {
    host.innerHTML = `<div class="dp-note">原文加载失败：${esc(e.message)}</div>`;
  }
}

async function fetchPageImg(id, n, slot) {
  if (slot.querySelector("img") || slot.dataset.ld) return;   // 幂等：IO 与首屏直填可能重复触发
  slot.dataset.ld = "1";
  try {
    const u = new URL(`/api/v1/documents/${safeId(id)}/pages/${n}`, location.origin);
    if (u.origin !== location.origin) throw new Error("blocked: 非本服务地址");
    const resp = await fetch(u, { headers: { "X-API-Key": state.token } });
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    const blob = await resp.blob();
    slot.querySelector(".pg-no")?.remove();
    const img = document.createElement("img");
    img.className = "pg-img";
    img.alt = `第 ${n} 页`;
    img.src = URL.createObjectURL(blob);
    slot.prepend(img);
  } catch {
    slot.insertAdjacentHTML("beforeend", `<span class="pg-no">第 ${n} 页加载失败</span>`);
  }
}

async function loadRawHtml(id, box) {
  const host = $("#dpRaw", box);
  if (!host) return;
  try {
    const u = new URL("/api/v1/documents/" + safeId(id) + "/file", location.origin);
    if (u.origin !== location.origin) throw new Error("blocked: 非本服务地址");
    const resp = await fetch(u, { headers: { "X-API-Key": state.token } });
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    const blob = await resp.blob();
    if (window._rawUrl) URL.revokeObjectURL(window._rawUrl);
    const url = URL.createObjectURL(blob);
    window._rawUrl = url;
    host.innerHTML = `<iframe class="dp-frame" src="${url}" title="原文预览"></iframe>`;
  } catch (e) {
    host.innerHTML = `<div class="dp-note">原文加载失败：${esc(e.message)}</div>`;
  }
}

function submitLink() {
  submitUrlValue($("#urlIn").value);
  $("#urlIn").value = "";
}

/* 链接入库共用：侧栏输入与移动端弹层都走这里 */
function submitUrlValue(raw) {
  const v = String(raw || "").trim();
  if (!v) return false;
  if (!/^https?:\/\//i.test(v)) { toast("请输入 http(s) 链接", "err"); return false; }
  if (isVideoUrl(v)) ingestVideo(v);
  else ingestUrl(v);
  return true;
}

function bindDocs() {
  const view = $("#viewDocs");
  $("#fileIn").addEventListener("change", (e) => { uploadFiles([...e.target.files]); e.target.value = ""; });
  $("#btnAddLink").addEventListener("click", submitLink);
  $("#urlIn").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.isComposing) submitLink();
  });
  let depth = 0;
  view.addEventListener("dragenter", (e) => { e.preventDefault(); depth++; $("#dropMask").hidden = false; });
  view.addEventListener("dragover", (e) => e.preventDefault());
  view.addEventListener("dragleave", () => { if (--depth <= 0) { depth = 0; $("#dropMask").hidden = true; } });
  view.addEventListener("drop", (e) => {
    e.preventDefault(); depth = 0; $("#dropMask").hidden = true;
    if (currentView() === "docs") uploadFiles([...e.dataTransfer.files]);
  });
}

/* Android PWA 分享目标：/?url=… 或 /?text=… 进来时自动入库 */
async function handleShareIntent() {
  const params = new URLSearchParams(location.search);
  const shared = params.get("url") || params.get("text") || params.get("title") || "";
  if (!shared) return;
  const url = (shared.match(/https?:\/\/\S+/) || [])[0];
  history.replaceState(null, "", location.pathname + location.hash);
  if (!url) return;
  toast("收到分享链接，正在入库…");
  try {
    if (isVideoUrl(url)) {
      const r = await (await api("/ingest/video", { method: "POST", body: JSON.stringify({ url }) })).json();
      pollTasks();
    } else {
      await (await api("/ingest/url", { method: "POST", body: JSON.stringify({ url }) })).json();
      toast("已提交抓取任务");
    }
    location.hash = "#/docs";
    route();
  } catch (e) {
    if (e.message !== "unauthorized") toast(e.message, "err");
  }
}

/* ---------- 视图：设置（分类锚点 + 表单） ---------- */

const CFG_FIELDS = [
  "embed_base_url", "embed_api_key", "embed_model", "embed_dim",
  "llm_base_url", "llm_api_key", "llm_model",
  "rerank_base_url", "rerank_api_key", "rerank_model",
  "bilibili_sessdata", "vision_model", "watch_dirs",
  "asr_model",
];

/* 本机回环访问判定：桌面版窗口 / 服务器宿主浏览器 */
function isLocalHost() { return /^(127\.|localhost$|\[::1\])/.test(location.hostname); }

/* 原生 <select> 在本环境（内嵌 webview）渲染异常：升级为应用内自绘下拉。
   原 select 保留在 DOM（display:none）供 loadCfgForm/saveCfg 按 id 读写 value。 */
function closeSelMenus(scope) {
  (scope || document).querySelectorAll(".sel-menu").forEach((m) => { m.hidden = true; m.parentElement?.removeAttribute("data-open"); });
}
function syncSelBtns(scope) {
  (scope || document).querySelectorAll(".sel").forEach((wrap) => {
    const sel = wrap.querySelector("select");
    const btn = wrap.querySelector(".sel-btn");
    if (!sel || !btn) return;
    btn.querySelector(".t").textContent = sel.options[sel.selectedIndex]?.textContent || "请选择";
    wrap.querySelectorAll(".sel-row").forEach((r) => r.classList.toggle("on", r.dataset.v === sel.value));
  });
}
function upgradeSelects(view) {
  view.querySelectorAll("select").forEach((sel) => {
    if (sel.dataset.upgraded) return;
    sel.dataset.upgraded = "1";
    const wrap = document.createElement("div");
    wrap.className = "sel";
    if (sel.style.marginBottom) wrap.style.marginBottom = sel.style.marginBottom;
    const btn = document.createElement("button");
    btn.type = "button"; btn.className = "sel-btn"; btn.setAttribute("aria-haspopup", "listbox");
    btn.innerHTML = `<span class="t"></span><svg class="ic"><use href="#i-chev-d"/></svg>`;
    const menu = document.createElement("div");
    menu.className = "sel-menu"; menu.hidden = true;
    [...sel.options].forEach((o) => {
      const row = document.createElement("button");
      row.type = "button"; row.className = "sel-row"; row.textContent = o.textContent; row.dataset.v = o.value;
      row.addEventListener("click", (e) => {
        e.stopPropagation();
        sel.value = o.value;
        syncSelBtns(view);
        closeSelMenus(view);
        sel.dispatchEvent(new Event("change", { bubbles: true }));
      });
      menu.appendChild(row);
    });
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const open = !menu.hidden;
      closeSelMenus(view);
      menu.hidden = open;
      wrap.classList.remove("up");
      if (open) {
        wrap.setAttribute("data-open", "");
        const r = menu.getBoundingClientRect();
        if (r.bottom > window.innerHeight - 8) wrap.classList.add("up");
      } else wrap.removeAttribute("data-open");
    });
    sel.style.display = "none";
    sel.replaceWith(wrap);
    wrap.append(sel, btn, menu);
  });
  if (!document.body.dataset.selUp) {
    document.body.dataset.selUp = "1";
    document.addEventListener("click", () => closeSelMenus(document));
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeSelMenus(document); });
  }
}

function enterSettings() {
  const view = $("#viewSettings");
  if (!view.dataset.built) { view.innerHTML = settingsHtml(); bindSettings(view); upgradeSelects(view); view.dataset.built = "1"; }
  renderSetCats();
  loadCfgForm(view);
  loadPair(view);
  renderStatCard(view);
  $("#saveBar", view).classList.remove("dirty");
}

function settingsHtml() {
  return `<div class="set-cats-mobile" id="setCatsM"></div>
  <div class="set-wrap">
    <div class="set-head">
      <h1>设置</h1>
      <div class="seg" id="themeSeg">
        <button data-t="light">浅色</button><button data-t="dark">深色</button><button data-t="auto">自动</button>
      </div>
    </div>

    <div class="stat-card" id="statCard">加载状态中…</div>

    <section class="set-card" id="sec-token" hidden>
      <div class="sc-t">访问口令</div>
      <div class="sc-d" id="tokHint">此设备经局域网访问服务，需要粘贴管理者提供的口令（仅存本机）。</div>
      <input type="password" id="setToken" placeholder="粘贴访问口令" value="${esc(state.token)}">
    </section>

    <section class="set-card" id="sec-model">
      <div class="sc-t">模型服务 <span class="sc-sub">嵌入 · 问答 · 重排</span></div>
      <div class="preset-row">
        <button class="sb-chip" data-preset="zhipu">智谱</button>
        <button class="sb-chip" data-preset="siliconflow">SiliconFlow</button>
      </div>
      <details class="acc" id="accEmbed" open>
        <summary>嵌入（语义检索）</summary>
        <div class="fld"><label>Base URL</label><input id="embed_base_url"></div>
        <div class="fld"><label>API Key</label><input type="password" id="embed_api_key"></div>
        <div class="fld"><label>模型</label><input id="embed_model" placeholder="embedding-3"></div>
        <div class="fld"><label>向量维度<small>换模型后需重建索引</small></label><input type="number" id="embed_dim" step="256"></div>
      </details>
      <details class="acc" open>
        <summary>问答（GLM）</summary>
        <div class="fld"><label>Base URL</label><input id="llm_base_url"></div>
        <div class="fld"><label>API Key</label><input type="password" id="llm_api_key"></div>
        <div class="fld"><label>模型</label><input id="llm_model" placeholder="glm-4.6"></div>
      </details>
      <details class="acc">
        <summary>重排（可选）</summary>
        <div class="fld"><label>Base URL</label><input id="rerank_base_url"></div>
        <div class="fld"><label>API Key</label><input type="password" id="rerank_api_key"></div>
        <div class="fld"><label>模型</label><input id="rerank_model" placeholder="BAAI/bge-reranker-v2-m3"></div>
      </details>
    </section>

    <section class="set-card" id="sec-source">
      <div class="sc-t">数据来源</div>
      <div class="fld"><label>监听目录<small>选择文件夹；保存后立即生效并自动扫描</small></label>
        <div class="wd-chips" id="wdChips"></div>
        <button class="btn" id="btnPickDir" style="margin-top:8px">选择目录</button>
        <textarea id="watch_dirs" hidden></textarea>
      </div>
      <div class="fld" style="margin-top:14px"><label>B 站登录<small>扫码获取 SESSDATA，用于视频 AI 字幕</small></label>
        <input type="password" id="bilibili_sessdata" placeholder="扫码登录后自动填入">
        <button class="btn" id="btnQrLogin" style="margin-top:8px">扫码登录 B 站</button>
      </div>
      <div class="qr-area" id="qrArea" hidden>
        <img id="qrImg" alt="B站登录二维码">
        <div class="qr-status" id="qrStatus">等待扫码…</div>
      </div>
      <div class="fld" style="margin-top:14px"><label>语音转写模型<small>无字幕视频自动转写；复用问答模型的智谱 key，按量计费；留空关闭</small></label>
        <input id="asr_model" placeholder="glm-asr-2512">
      </div>
      <div class="fld" style="margin-top:14px"><label>图像理解模型<small>理解图表含义；留空关闭</small></label>
        <input id="vision_model" placeholder="glm-4v-flash（免费）">
      </div>
    </section>

    ${isLocalHost() ? `
    <section class="set-card" id="sec-pair">
      <div class="sc-t">手机访问 <span class="sc-sub">同一 Wi-Fi · 扫码即用</span></div>
      <div class="sc-d">用手机相机扫码，即可在其他设备打开这个知识库，全程无需输入口令。</div>
      <div class="qr-area" id="pairArea" style="margin-top:10px">获取配对信息…</div>
    </section>` : ""}

    <section class="set-card" id="sec-about">
      <div class="sc-t">关于</div>
      <div class="about-grid">
        <span>版本</span><span>v0.24.1</span>
        <span>文档 / 分块</span><span id="abDocs">—</span>
        <span>目录监听</span><span id="abWatch">—</span>
        <span>监听路径</span><span id="abDir" style="word-break:break-all">—</span>
        <span>数据</span><span>全部保存在本机</span>
      </div>
    </section>

    <div class="save-bar" id="saveBar">
      <span id="dirtyHint">有未保存的修改</span>
      <button class="btn ghost" id="digestAll" title="为还没有摘要的文档批量生成 AI 摘要">补全摘要</button>
      <button class="btn ghost" id="reindex" title="重新解析并嵌入全部文档">重建索引</button>
      <button class="btn primary" id="saveAll">保存全部</button>
    </div>

    <div class="dir-modal" id="dirModal" hidden>
      <div class="dir-box">
        <div class="dir-head">
          <button class="mini-btn" id="dirUp">‹ 上级</button>
          <span class="dir-path" id="dirPath"></span>
          <button class="rib-btn" id="dirClose" aria-label="关闭"><svg class="ic" style="width:12px;height:12px"><use href="#i-x"/></svg></button>
        </div>
        <div class="dir-list" id="dirList"></div>
        <div class="dir-foot"><button class="btn primary" id="dirPick">选择此目录</button></div>
      </div>
    </div>
  </div>`;
}

/* 状态卡 + 关于：拉取健康数据渲染 */
async function renderStatCard(view) {
  await pollHealth();
  const h = state.health;
  const card = $("#statCard", view);
  if (!card) return;
  if (!h) { card.innerHTML = "服务不可达"; return; }
  const dots = [["向量库", h.qdrant], ["嵌入", h.embed_configured], ["问答", h.llm_configured], ["OCR", h.ocr_available]]
    .map(([n2, on]) => `<span class="st-item" title="${n2} ${on ? "正常" : "不可用"}"><span class="st-dot ${on ? "on" : ""}"></span>${n2}</span>`).join("");
  card.innerHTML = `<div class="st-row">${dots}</div>
    <div class="st-meta">${h.documents} 篇文档 · ${h.chunks} 块 · ${h.watching ? "监听中" : "监听未启用"}</div>`;
  const set = (id, v) => { const el = view.querySelector("#" + id); if (el) el.textContent = v; };
  set("abDocs", `${h.documents} 篇 / ${h.chunks} 块`);
  set("abWatch", h.watching ? "运行中" : "未启用");
  set("abDir", (h.watch_dirs && h.watch_dirs[0]) || "未配置");
}

/* 手机访问配对卡：拉取带 key 的局域网链接并渲染成二维码（key 只进二维码，不显示） */
async function loadPair(view) {
  const area = $("#pairArea", view);
  if (!area) return;  // 远程设备不渲染此卡
  try {
    const r = await (await api("/pair/url", { quiet: true })).json();
    if (!r.pairable) { area.textContent = r.reason || "当前不可配对"; return; }
    const qr = qrcode(0, "M");
    qr.addData(r.url);
    qr.make();
    area.innerHTML = qr.createSvgTag({ cellSize: 4, margin: 2 })
      + `<div class="sc-d" style="margin-top:8px">或手动访问：${esc(r.url.split("#")[0])}</div>`;
  } catch { area.textContent = "无法获取配对信息（服务未就绪？）"; }
}

function renderSetCats() {
  const cats = [
    ["sec-model", "模型服务"], ["sec-source", "数据来源"],
    ["sec-pair", "手机访问"],
    ["sec-about", "关于"],
  ].filter(([id]) => document.getElementById(id));  // 远程设备无配对卡，导航项随之隐藏
  $("#setCats").innerHTML = cats
    .map(([id, label]) => `<div class="sb-cat" data-sec="${id}">${label}</div>`)
    .join("");
  $$("#setCats .sb-cat").forEach((c) =>
    c.addEventListener("click", () => {
      const el = document.getElementById(c.dataset.sec);
      if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
    })
  );
  const m = $("#setCatsM");
  if (m) {
    m.innerHTML = cats.map(([id, label]) => `<span class="sb-chip" data-sec="${id}">${label}</span>`).join("");
    m.querySelectorAll(".sb-chip").forEach((c) =>
      c.addEventListener("click", () => {
        const el = document.getElementById(c.dataset.sec);
        if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
      })
    );
  }
  const obs = new IntersectionObserver(
    (entries) => {
      entries.forEach((en) => {
        if (!en.isIntersecting) return;
        $$("#setCats .sb-cat").forEach((c) => c.classList.toggle("on", c.dataset.sec === en.target.id));
      });
    },
    { root: $("#viewSettings"), threshold: 0.2 }
  );
  $$(".set-card", $("#viewSettings")).forEach((s) => obs.observe(s));
}

function markDirty() {
  const bar = $("#saveBar");
  if (!bar) return;
  bar.classList.add("dirty");
}

/* 监听目录：chips 展示 + 服务端目录浏览选择 */
function renderWatchChips() {
  const box = $("#wdChips");
  if (!box) return;
  const dirs = ($("#watch_dirs").value || "").split("\n").map((x) => x.trim()).filter(Boolean);
  box.innerHTML = dirs.length
    ? dirs.map((d) => `<span class="wd-chip">${esc(d)}<button data-rm="${esc(d)}" title="移除">✕</button></span>`).join("")
    : `<span class="wd-empty">未自定义（当前用 .env 默认目录）</span>`;
  box.querySelectorAll("[data-rm]").forEach((b) =>
    b.addEventListener("click", () => {
      const cur = ($("#watch_dirs").value || "").split("\n").map((x) => x.trim()).filter((x) => x && x !== b.dataset.rm);
      $("#watch_dirs").value = cur.join("\n");
      renderWatchChips();
      markDirty();
    })
  );
}

async function dirBrowse(p2) {
  const r = await (await api("/fs/dirs?path=" + encodeURIComponent(p2))).json();
  $("#dirPath").textContent = r.path;
  $("#dirPath").dataset.path = r.path;
  $("#dirUp").disabled = !r.parent;
  $("#dirUp").dataset.parent = r.parent || "";
  const list = $("#dirList");
  list.innerHTML = r.dirs.length
    ? r.dirs.map((d) => `<div class="dir-item" data-name="${esc(d)}">📁 ${esc(d)}</div>`).join("")
    : `<div class="dir-empty">（没有子目录）</div>`;
  list.querySelectorAll(".dir-item").forEach((el) =>
    el.addEventListener("click", () => dirBrowse($("#dirPath").dataset.path + "/" + el.dataset.name))
  );
}

function bindDirPicker(view) {
  $("#btnPickDir", view).addEventListener("click", async () => {
    $("#dirModal").hidden = false;
    try { await dirBrowse(String($("#dirPath").dataset.path || "~")); }
    catch (e) { try { await dirBrowse("~"); } catch {} }
  });
  $("#dirClose", view).addEventListener("click", () => { $("#dirModal").hidden = true; });
  $("#dirModal", view).addEventListener("click", (e) => { if (e.target.id === "dirModal") $("#dirModal").hidden = true; });
  $("#dirUp", view).addEventListener("click", () => { const p2 = $("#dirUp").dataset.parent; if (p2) dirBrowse(p2); });
  $("#dirPick", view).addEventListener("click", () => {
    const picked = $("#dirPath").dataset.path;
    if (!picked) return;
    const cur = ($("#watch_dirs").value || "").split("\n").map((x) => x.trim()).filter(Boolean);
    if (!cur.includes(picked)) cur.push(picked);
    $("#watch_dirs").value = cur.join("\n");
    renderWatchChips();
    markDirty();
    $("#dirModal").hidden = true;
    toast("已添加 " + picked + "，点「保存全部」生效");
  });
}

function bindSettings(view) {
  bindDirPicker(view);
  bindQrLogin(view);
  /* 服务商预设：一键填 Base URL 与模型名，key 手动粘贴 */
  const PRESETS = {
    zhipu: { embed_base_url: "https://open.bigmodel.cn/api/paas/v4", embed_model: "embedding-3", llm_base_url: "https://open.bigmodel.cn/api/paas/v4", llm_model: "glm-4.6" },
    siliconflow: { rerank_base_url: "https://api.siliconflow.cn/v1", rerank_model: "BAAI/bge-reranker-v2-m3" },
  };
  view.querySelectorAll("[data-preset]").forEach((b) => b.addEventListener("click", () => {
    for (const [k, v] of Object.entries(PRESETS[b.dataset.preset] || {})) {
      const el = $("#" + k, view);
      if (el) { el.value = v; el.dispatchEvent(new Event("input", { bubbles: true })); }
    }
    toast("已填充，粘贴 API Key 后保存即可");
  }));
  /* 任何输入 → 吸底栏提示未保存 */
  view.addEventListener("input", markDirty);
  /* 保存全部：口令 + 模型配置一次提交 */
  $("#saveAll", view).addEventListener("click", async () => {
    const tok = $("#setToken", view).value.trim();
    if (tok !== state.token) {
      state.token = tok;
      localStorage.setItem("lib_token", tok);
    }
    await saveCfg(view);
    $("#saveBar", view).classList.remove("dirty");
    pollHealth();
  });
  $("#reindex", view).addEventListener("click", () => reindexAll(view));
  $("#digestAll", view).addEventListener("click", () => digestAllMissing(view));
  /* 分段主题选择 */
  const seg = $("#themeSeg", view);
  const paint = () => {
    seg.querySelectorAll("button").forEach((b) => b.classList.toggle("on", b.dataset.t === state.theme));
  };
  seg.addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    state.theme = b.dataset.t;
    localStorage.setItem("theme", state.theme);
    applyTheme();
    paint();
  });
  paint();
}

/* B站扫码登录：生成二维码 → 2s 轮询 → 成功自动保存 SESSDATA */
let qrTimer = null;

function stopQr(view, msg) {
  if (qrTimer) { clearInterval(qrTimer); qrTimer = null; }
  const area = $("#qrArea", view);
  if (area) area.hidden = true;
  if (msg) toast(msg);
}

function bindQrLogin(view) {
  $("#btnQrLogin", view).addEventListener("click", async () => {
    try {
      const r = await (await api("/bilibili/qr/start", { method: "POST" })).json();
      $("#qrImg", view).src = r.image;
      $("#qrStatus", view).textContent = "等待扫码…";
      $("#qrArea", view).hidden = false;
      if (qrTimer) clearInterval(qrTimer);
      const started = Date.now();
      qrTimer = setInterval(async () => {
        if (Date.now() - started > 190000) { stopQr(view, "超时，请重新点击扫码登录"); return; }
        try {
          const p = await (await api("/bilibili/qr/poll?qrcode_key=" + encodeURIComponent(r.qrcode_key))).json();
          if (p.status === "ok") {
            stopQr(view, "B站登录成功，SESSDATA 已保存");
            loadCfgForm(view);
            toast("视频笔记现在可以处理需要登录态的 AI 字幕了");
          } else if (p.status === "expired") {
            stopQr(view, p.message);
          } else {
            $("#qrStatus", view).textContent = p.message || "等待扫码…";
          }
        } catch { /* 轮询失败下次再试 */ }
      }, 2000);
    } catch (e) {
      if (e.message !== "unauthorized") toast(e.message, "err");
    }
  });
}

async function loadCfgForm(view) {
  try {
    const cfg = await (await api("/config")).json();
    $("#sec-token", view).hidden = true;   // 认证通过 → 本机或已有有效口令，无需展示
    for (const f of CFG_FIELDS) {
      const el = view.querySelector("#" + f);
      if (!el) continue;
      const v = cfg[f];
      const secret = f.endsWith("_api_key") || f === "bilibili_sessdata";
      if (f === "embed_dim") { if (v) el.value = v; }
      else if (secret) el.placeholder = v ? `当前 ${v}（留空不修改）` : (f === "bilibili_sessdata" ? "扫码登录后自动填入" : "未配置");
      else if (v) el.value = v;
    }
    syncSelBtns(view);
    renderWatchChips();
  } catch (e) {
    /* 远程设备无有效口令 → 揭示口令卡；本机不会走到这里（回环免认证） */
    if (e.message === "unauthorized" && !isLocalHost()) {
      const card = $("#sec-token", view);
      if (card) card.hidden = false;
    }
  }
}

async function saveCfg(view) {
  const btn = $("#saveAll", view);
  if (btn) btn.disabled = true;
  try {
    const body = {};
    for (const f of CFG_FIELDS) {
      const el = view.querySelector("#" + f);
      const v = el.value.trim();
      if (v.includes("***")) { el.value = ""; continue; }   // 掩码回显值绝不回传
      if (v !== "") body[f] = f === "embed_dim" ? Number(v) : v;
      if (f.endsWith("_api_key")) el.value = "";
    }
    if (!Object.keys(body).length) { toast("没有需要保存的修改", "err"); return; }
    const r = await (await api("/config", { method: "PUT", body: JSON.stringify(body) })).json();
    toast("已保存并生效：" + r.updated.join(", "));
    if (r.reindex_recommended) toast("模型/维度已变，请「重建向量索引」", "err");
    loadCfgForm(view);
    pollHealth();
  } catch (e) {
    if (e.message === "unauthorized") {
      const card = $("#sec-token", view);
      if (card && !isLocalHost()) card.hidden = false;
      toast("口令无效或不完整，请填写后重试", "err");
    } else if (e.message !== "unauthorized") toast(e.message, "err");
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function reindexAll(view) {
  if (!confirm("重新解析并嵌入全部文档？可能耗时较长。")) return;
  const btn = $("#reindex", view);
  btn.disabled = true;
  try {
    const r = await (await api("/ingest/reconcile?force=true", { method: "POST" })).json();
    toast("重建完成：" + JSON.stringify(r.reconciled));
    pollHealth();
  } catch (e) {
    if (e.message !== "unauthorized") toast(e.message, "err");
  } finally {
    btn.disabled = false;
  }
}

async function digestAllMissing(view) {
  const btn = $("#digestAll", view);
  btn.disabled = true;
  try {
    const r = await (await api("/documents/digest-missing", { method: "POST" })).json();
    toast(r.queued ? `已排队 ${r.queued} 篇，生成中…` : "所有文档都已有摘要");
  } catch (e) {
    if (e.message !== "unauthorized") toast(e.message, "err");
  } finally {
    btn.disabled = false;
  }
}

/* ---------- 编辑器键盘：两次 ⏎ 发送 ---------- */

function bindEditor() {
  const ed = $("#editor");
  ed.addEventListener("keydown", (e) => {
    if (e.key !== "Enter" || e.isComposing || e.shiftKey) return;
    e.preventDefault();
    /* 发送规则（无计时依赖）：⌘⏎ 立即发送；光标所在行为空行时 ⏎ 发送；
       非空行 ⏎ 换行——"写完一段 → 回车 → 再回车"即发送，按快按慢都一样 */
    normalizeEditor();
    if (e.metaKey || e.ctrlKey) { sendFromEditor(); return; }
    const sel = getSelection();
    let node = sel.anchorNode;
    if (node && node.nodeType === 3) node = node.parentElement;
    const blk = node && node.closest ? node.closest("#editor .blk") : null;
    const onEmptyLine = !blk || blk.textContent.trim() === "";
    if (onEmptyLine) sendFromEditor();
    else splitBlockAtCaret();
  });
  ed.addEventListener("input", () => { normalizeEditor(); refreshAskUi(); });
  ed.addEventListener("paste", (e) => {
    e.preventDefault();
    document.execCommand("insertText", false, (e.clipboardData || window.clipboardData).getData("text/plain"));
  });
  /* 已生成的 q/a 块只读：任何把光标带进历史区的点击/聚焦都改道到末尾输入区 */
  ed.addEventListener("mousedown", (e) => {
    if (!e.target.closest(".q-block, .a-block")) return;
    e.preventDefault();
    caretToEnd(ensureTrailingBlk());
  });
  ed.addEventListener("selectstart", (e) => {
    if (e.target.closest(".q-block, .a-block")) e.preventDefault();
  });
  ed.addEventListener("focusin", () => {
    normalizeEditor();
    const sel = getSelection();
    let node = sel.anchorNode;
    if (node && node.nodeType === 3) node = node.parentElement;
    if (node && node.closest && node.closest(".q-block, .a-block")) {
      caretToEnd(ensureTrailingBlk());
    }
  });
}

/* ---------- 侧栏拖拽调宽（桌面端） ---------- */

const SB_W_MIN = 180, SB_W_MAX = 460;
const RB_W_MIN = 200, RB_W_MAX = 560;

function bindSbResize() {
  const rz = $("#sbResize");
  if (!rz) return;
  const saved = Number(localStorage.getItem("sb_w"));
  const applySaved = () => {
    if (!(saved >= SB_W_MIN && saved <= SB_W_MAX)) return;
    /* 窄窗口下钳制：至少给主区留 200px（44 ribbon + 200 main）；
       resize 兜底防止加载瞬间的瞬态视口把宽度钳死 */
    const w = Math.min(saved, Math.max(SB_W_MIN, window.innerWidth - 244));
    document.body.style.setProperty("--sb-w", Math.round(w) + "px");
  };
  applySaved();
  window.addEventListener("resize", applySaved);
  rz.addEventListener("mousedown", (e) => {
    e.preventDefault();
    const startX = e.clientX;
    const startW = $("#sidebar").getBoundingClientRect().width || 232;
    document.body.classList.add("sb-resizing");
    const onMove = (ev) => {
      const w = Math.max(SB_W_MIN, Math.min(SB_W_MAX, startW + ev.clientX - startX));
      document.body.style.setProperty("--sb-w", Math.round(w) + "px");
    };
    const onUp = () => {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      document.body.classList.remove("sb-resizing");
      const w = $("#sidebar").getBoundingClientRect().width;
      if (w) localStorage.setItem("sb_w", String(Math.round(w)));
    };
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  });
  rz.addEventListener("dblclick", () => {
    document.body.style.removeProperty("--sb-w");
    localStorage.removeItem("sb_w");
  });
}

/* 右栏（引用面板）宽度拖拽：向左拖变宽；--rb-w 只承载用户值，见 CSS --rb-final 注释 */
function bindRbResize() {
  const rz = $("#rbResize");
  if (!rz) return;
  const saved = Number(localStorage.getItem("rb_w"));
  const applySaved = () => {
    if (!(saved >= RB_W_MIN && saved <= RB_W_MAX)) return;
    /* 窄窗口钳制：给 ribbon+侧栏+主区至少留 560px */
    const w = Math.min(saved, Math.max(RB_W_MIN, window.innerWidth - 560));
    document.body.style.setProperty("--rb-w", Math.round(w) + "px");
  };
  applySaved();
  window.addEventListener("resize", applySaved);
  rz.addEventListener("mousedown", (e) => {
    e.preventDefault();
    const startX = e.clientX;
    const startW = $("#rightbar").getBoundingClientRect().width || 264;
    document.body.classList.add("rb-resizing");
    const onMove = (ev) => {
      const w = Math.max(RB_W_MIN, Math.min(RB_W_MAX, startW - (ev.clientX - startX)));
      document.body.style.setProperty("--rb-w", Math.round(w) + "px");
    };
    const onUp = () => {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      document.body.classList.remove("rb-resizing");
      const w = $("#rightbar").getBoundingClientRect().width;
      if (w) localStorage.setItem("rb_w", String(Math.round(w)));
    };
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  });
  rz.addEventListener("dblclick", () => {
    document.body.style.removeProperty("--rb-w");
    localStorage.removeItem("rb_w");
  });
}

/* ---------- 移动端（≤768px，demo 原型移植）：输入条 / 底部弹层 / 空态 ---------- */

const mqlMobile = window.matchMedia("(max-width: 768px)");
function isMobile() { return mqlMobile.matches; }

/* 手机上编辑器只作展示（历史问答），输入一律走底部输入条 */
function syncEditorEditable() {
  const ed = $("#editor");
  if (ed) ed.contentEditable = isMobile() ? "false" : "true";
}

function openSheetM(html) {
  const wrap = $("#mSheetWrap");
  if (!wrap) return;
  $("#mSheet").innerHTML = html;
  wrap.hidden = false;
  requestAnimationFrame(() => wrap.classList.add("on"));
}
function closeSheetM() {
  const wrap = $("#mSheetWrap");
  if (!wrap) return;
  wrap.classList.remove("on");
  setTimeout(() => { wrap.hidden = true; $("#mSheet").innerHTML = ""; }, 280);
}

/* 引用底部弹层：对应桌面右栏「本次引用」 */
function openCiteSheetM(srcs, focusRef) {
  if (!srcs || !srcs.length) { toast("该回答没有引用来源"); return; }
  openSheetM(`
    <div class="sh-grab"></div>
    <div class="sh-title">引用来源 <span class="sh-n">${srcs.length} 段</span></div>
    ${srcs.map((s) => {
      const loc = [s.title, s.heading, s.page ? "第 " + s.page + " 页" : ""].filter(Boolean).join(" · ");
      return `<div class="cite-card${focusRef === String(s.ref) ? " hl" : ""}" data-ref="${esc(String(s.ref))}">
        <div class="no">[${esc(String(s.ref))}]</div>
        <div class="t">${esc(s.title || s.url || "未知来源")}</div>
        ${s.text ? `<div class="s">${esc(String(s.text).slice(0, 160))}${String(s.text).length > 160 ? "…" : ""}</div>` : ""}
        <div class="loc"><svg class="ic"><use href="#i-eye"/></svg>${esc(loc)}</div>
      </div>`;
    }).join("")}
    <div class="sh-pad"></div>`);
  setTimeout(() => {
    $$("#mSheet .cite-card").forEach((c) =>
      c.addEventListener("click", () => {
        closeSheetM();
        if (!isMobile()) locateInAnswer(c.dataset.ref);
      })
    );
  }, 0);
}

/* 链接入库底部弹层 */
function openLinkSheetM() {
  openSheetM(`
    <div class="sh-grab"></div>
    <div class="sh-title">链接入库</div>
    <div class="sh-sub">粘贴网页 / B 站链接，自动抓取生成笔记</div>
    <div class="sh-input-row">
      <input id="mUrlIn" type="url" placeholder="https://…" autocomplete="off">
      <button class="btn primary" id="mUrlGo">入库</button>
    </div>
    <div class="sh-pad"></div>`);
  setTimeout(() => {
    const inp = $("#mUrlIn");
    if (!inp) return;
    inp.focus();
    const go = () => { if (submitUrlValue(inp.value)) { inp.value = ""; closeSheetM(); } };
    $("#mUrlGo").addEventListener("click", go);
    inp.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.isComposing) go(); });
  }, 50);
}

/* 问答空态 hero（移动端）：logo + 引导 */
function renderMobileHero() {
  const hero = $("#mHero");
  if (!hero) return;
  const empty = !$("#editor .q-block, #editor .a-block");
  hero.hidden = !empty;
  if (!empty) return;
  hero.innerHTML = `
    <div class="m-hero-logo"><img src="/icons/logo.png" alt="知识库"></div>
    <div class="m-hero-hi">问点什么</div>
    <div class="m-hero-sub">基于你的本地知识库，回答带引用来源</div>`;
}

function bindMobile() {
  syncEditorEditable();
  mqlMobile.addEventListener?.("change", syncEditorEditable);

  /* 底部输入条：自动增高 + 发送/停止 */
  const ta = $("#mAskIn");
  const grow = () => { ta.style.height = "auto"; ta.style.height = Math.min(116, ta.scrollHeight) + "px"; };
  ta.addEventListener("input", grow);
  ta.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      $("#mSend").click();
    }
  });
  $("#mSend").addEventListener("click", () => {
    if (state.threadId && state.busyMap.has(state.threadId)) { stopAsk(); return; }
    const text = ta.value.trim();
    if (!text) return;
    if (!state.token && !isLocalHost()) { toast("请先在「设置」粘贴访问口令", "err"); location.hash = "#/settings"; return; }
    ta.value = ""; grow();
    insertQuestion(text);
    ta.blur();   // 收起键盘，让流式回答占满屏
  });

  /* 顶栏：主题切换 + 文档页链接入库 */
  $("#btnThemeM").addEventListener("click", cycleTheme);
  $("#mBtnLink").addEventListener("click", openLinkSheetM);
  $("#mSheetWrap").addEventListener("click", (e) => { if (e.target === e.currentTarget) closeSheetM(); });

  /* 引用角标 / 引用行 → 底部弹层（桌面仍是正文定位高亮） */
  $("#editor").addEventListener("click", (e) => {
    if (!isMobile()) return;
    const cite = e.target.closest(".cite");
    const row = e.target.closest(".a-src");
    if (!cite && !row) return;
    const ab = (cite || row).closest(".a-block");
    const srcs = ab && ab._srcs;
    if (!srcs || !srcs.length) { toast("该回答没有引用来源"); return; }
    openCiteSheetM(srcs, cite ? cite.textContent.replace(/[[\]]/g, "") : row.querySelector("b")?.textContent.replace(/[[\]]/g, ""));
  });
}

/* ---------- 全局绑定 / 启动 ---------- */

function bindChrome() {
  $("#btnTheme").addEventListener("click", cycleTheme);
  $("#rbClose").addEventListener("click", () => document.body.classList.remove("right-open"));
  $("#btnSb").addEventListener("click", () => {
    const off = document.body.classList.toggle("sb-collapsed");
    localStorage.setItem("sb_collapsed", off ? "1" : "");
  });
  if (localStorage.getItem("sb_collapsed")) document.body.classList.add("sb-collapsed");
  bindSbResize();
  bindRbResize();
  $("#btnDrawer").addEventListener("click", () => {
    document.body.classList.add("drawer-open");
    $("#scrim").hidden = false;
  });
  $("#scrim").addEventListener("click", () => {
    document.body.classList.remove("drawer-open");
    $("#scrim").hidden = true;
  });
  document.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
      e.preventDefault();
      if (currentView() !== "ask") location.hash = "#/";
      caretToEnd(ensureTrailingBlk());
    }
    if (e.key === "Escape") {
      if (state.threadId && state.busyMap.has(state.threadId)) stopAsk();
      document.body.classList.remove("drawer-open");
      $("#scrim").hidden = true;
    }
  });
  window.addEventListener("hashchange", route);
}

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => navigator.serviceWorker.register("/sw.js"));
  let swReloaded = false;
  navigator.serviceWorker.addEventListener("controllerchange", () => {
    if (swReloaded) return;
    swReloaded = true;
    location.reload();   // 新 SW 接管时自动刷新一次，根治"要刷两次"
  });
}
window.addEventListener("beforeinstallprompt", (e) => {
  e.preventDefault();
  state.deferredInstall = e;
});

applyTheme();
bindChrome();
bindEditor();
bindAskSidebar();
bindDocs();
bindMobile();
/* ---------- 首次启动配置向导 ---------- */

/* 预设服务商：任意 OpenAI 兼容服务选「自定义」手填；key 同用（llm+embed），异构服务商去设置页细配 */
const OB_PROVIDERS = {
  zhipu: {
    keyPh: "粘贴智谱 API Key", keyLink: "https://open.bigmodel.cn/usercenter/apikeys",
    llm_base_url: "https://open.bigmodel.cn/api/paas/v4", llm_model: "glm-4.6",
    embed_base_url: "https://open.bigmodel.cn/api/paas/v4", embed_model: "embedding-3",
  },
  siliconflow: {
    keyPh: "粘贴硅基流动 API Key", keyLink: "https://cloud.siliconflow.cn/account/ak",
    llm_base_url: "https://api.siliconflow.cn/v1", llm_model: "deepseek-ai/DeepSeek-V3.1",
    embed_base_url: "https://api.siliconflow.cn/v1", embed_model: "BAAI/bge-m3",
    embed_dim: 1024,  // bge-m3 维度；不写会与集合默认 2048 冲突触发重建
  },
};

function maybeOnboard() {
  if (localStorage.getItem("lib_onboarded")) return;
  const h = state.health;
  if (!h || h.llm_configured) return;  // 已配置过 key 的环境永不打扰
  $("#onboard").hidden = false;
}

function closeOnboard() {
  localStorage.setItem("lib_onboarded", "1");
  const el = $("#onboard");
  if (el) el.hidden = true;
}

function bindOnboard() {
  const wrap = $("#onboard");
  if (!wrap) return;
  let prov = "zhipu";
  const chips = $$("#obProv .ob-chip");
  const syncProv = () => {
    const p = OB_PROVIDERS[prov];
    $("#obKey").placeholder = p ? p.keyPh : "粘贴 API Key";
    if (p) $("#obKeyLink").href = p.keyLink;
    $("#obKeyLink").style.display = p ? "" : "none";
    $("#obCustom").hidden = prov !== "custom";
  };
  chips.forEach((c) => c.addEventListener("click", () => {
    prov = c.dataset.p;
    chips.forEach((x) => x.classList.toggle("on", x === c));
    syncProv();
  }));
  syncProv();

  $("#obGo").addEventListener("click", async () => {
    const key = $("#obKey").value.trim();
    if (!key) { toast("先粘贴 API Key，或点下方跳过", "err"); return; }
    const body = { llm_api_key: key, embed_api_key: key };  // 同 key 双用；异构需求去设置页
    if (prov === "custom") {
      const base = $("#obBaseUrl").value.trim().replace(/\/+$/, "");
      const lm = $("#obLlmModel").value.trim();
      const em = $("#obEmbedModel").value.trim();
      if (!base || !lm) { toast("自定义服务需要填 Base URL 和对话模型", "err"); return; }
      Object.assign(body, { llm_base_url: base, llm_model: lm });
      if (em) Object.assign(body, { embed_base_url: base, embed_model: em });
    } else {
      const p = OB_PROVIDERS[prov];
      Object.assign(body, {
        llm_base_url: p.llm_base_url, llm_model: p.llm_model,
        embed_base_url: p.embed_base_url, embed_model: p.embed_model,
      });
      if (p.embed_dim) body.embed_dim = p.embed_dim;
    }
    try {
      await api("/config", { method: "PUT", body: JSON.stringify(body) });
      closeOnboard();
      toast("模型已配置，开始提问吧");
      pollHealth();
    } catch (e) {
      toast(e.message === "unauthorized" ? "口令无效，请在设置里检查" : "保存失败：" + e.message, "err");
    }
  });
  $("#obKey").addEventListener("keydown", (e) => {
    if (e.key === "Enter") $("#obGo").click();
  });
}

window.addEventListener("DOMContentLoaded", async () => {
  const th = curThread();
  if (th) renderBlocks(th.blocks, th.draft);
  else {
    clearEditor();
    if (state._newDraft) {
      const first = document.querySelector("#editor .blk");
      if (first) first.textContent = state._newDraft;
      delete state._newDraft;
      refreshAskUi();
    }
  }
  renderThreadList();
  updatePill();
  route();
  await pollHealth();
  bindOnboard();
  maybeOnboard();
  syncThreadsFromServer();   // 服务端为准合并对话（离线自动回落 localStorage）
  handleShareIntent();
  setInterval(pollHealth, 15000);
});
