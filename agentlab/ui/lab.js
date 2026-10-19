"use strict";
/* agentlab platform UI: home + tutorial, agents, projects, jobs, schedules, chat (playground + rooms), models,
   training, knowledge, templates, terminal, usage, library. Loaded after app.js and reuses its helpers. */

/* ---------- helpers ---------- */
const UNLOCK = {}; // private agent id -> unlock token (memory only: a reload locks them again)
const lapi = async (path, body, raw) => {
  const headers = { "X-AgentLab-UI": TOKEN };
  const tokens = Object.values(UNLOCK);
  if (tokens.length) headers["X-Agent-Unlock"] = tokens.join(",");
  const init = { headers };
  if (raw !== undefined) Object.assign(init, { method: "POST", body: raw, headers: { ...headers, "Content-Type": "application/octet-stream" } });
  else if (body !== undefined) Object.assign(init, { method: "POST", body: JSON.stringify(body), headers: { ...headers, "Content-Type": "application/json" } });
  const res = await fetch(path, init);
  const data = await res.json().catch(() => ({ error: res.statusText }));
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
};

/* ---------- inline SVG icons (no emoji anywhere in the UI) ---------- */
const ICON_PATHS = {
  robot: '<rect x="5" y="8" width="14" height="11" rx="3"/><path d="M12 4v4M9 13h.01M15 13h.01M9.5 16.5h5"/>',
  compass: '<circle cx="12" cy="12" r="9"/><path d="M15.5 8.5l-2 5-5 2 2-5z"/>',
  code: '<path d="M8 8l-4 4 4 4M16 8l4 4-4 4M14 5l-4 14"/>',
  search: '<circle cx="11" cy="11" r="6"/><path d="M20 20l-4.3-4.3"/>',
  flask: '<path d="M9 3h6M10 3v6L5 18a2 2 0 0 0 1.8 3h10.4A2 2 0 0 0 19 18l-5-9V3"/><path d="M7.5 14h9"/>',
  palette: '<path d="M12 3a9 9 0 1 0 0 18c1.2 0 1.8-.9 1.5-1.8-.4-1.2.4-2.2 1.7-2.2H17a4 4 0 0 0 4-4c0-5-4-10-9-10z"/><circle cx="8" cy="12" r="1"/><circle cx="12" cy="8" r="1"/><circle cx="16" cy="12" r="1"/>',
  chart: '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
  globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.5 2.6 3.8 5.7 3.8 9S14.5 18.4 12 21c-2.5-2.6-3.8-5.7-3.8-9S9.5 5.6 12 3z"/>',
  shield: '<path d="M12 3l8 3v6c0 4.5-3.2 7.8-8 9-4.8-1.2-8-4.5-8-9V6z"/><path d="M9 12.5l2 2 4-4.5"/>',
  wrench: '<path d="M15 4a5 5 0 0 0-5.8 6.6L4 15.8V20h4.2l5.2-5.2A5 5 0 0 0 20 9l-3 3-2.5-2.5 3-3A5 5 0 0 0 15 4z"/>',
  pen: '<path d="M4 20h4L19 9a2.8 2.8 0 0 0-4-4L4 16z"/><path d="M14.5 6.5l3 3"/>',
  graduate: '<path d="M2 9l10-5 10 5-10 5z"/><path d="M6 11v5c0 1.7 2.7 3 6 3s6-1.3 6-3v-5"/>',
  map: '<path d="M9 4L3 7v13l6-3 6 3 6-3V4l-6 3z"/><path d="M9 4v13M15 7v13"/>',
  target: '<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="4"/><circle cx="12" cy="12" r="1"/>',
  chat: '<path d="M4 5h16v11H9l-5 4z"/>',
  folder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
  file: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>',
  clip: '<path d="M20 11l-8.5 8.5a4.5 4.5 0 0 1-6.4-6.4L13 4.8a3 3 0 1 1 4.3 4.3l-8 8a1.5 1.5 0 0 1-2.1-2.1l7.5-7.5"/>',
  moon: '<path d="M20 14.5A8.5 8.5 0 0 1 9.5 4a8.5 8.5 0 1 0 10.5 10.5z"/>',
  lock: '<rect x="5" y="11" width="14" height="10" rx="2.5"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
  warn: '<path d="M12 4l9 16H3z"/><path d="M12 10v4M12 17.5h.01"/>',
  check: '<path d="M5 12.5l4.5 4.5L19 7"/>',
  cross: '<path d="M6 6l12 12M18 6L6 18"/>',
  up: '<path d="M7 11l3-7a2 2 0 0 1 3 2l-.8 5H19a2 2 0 0 1 2 2.4l-1.3 5A2 2 0 0 1 17.7 20H7z"/><rect x="3" y="11" width="4" height="9" rx="1"/>',
  down: '<path d="M7 13l3 7a2 2 0 0 0 3-2l-.8-5H19a2 2 0 0 0 2-2.4l-1.3-5A2 2 0 0 0 17.7 4H7z"/><rect x="3" y="4" width="4" height="9" rx="1"/>',
  pause: '<rect x="7" y="5" width="4" height="14" rx="1"/><rect x="13" y="5" width="4" height="14" rx="1"/>',
  arrow: '<path d="M5 12h13M13 6l6 6-6 6"/>',
  dot: '<circle cx="12" cy="12" r="5"/>',
  puzzle: '<path d="M10 4h4a1 1 0 0 1 1 1v1.5a1.5 1.5 0 1 0 3 0V5h1a1 1 0 0 1 1 1v4h-1.5a1.5 1.5 0 1 0 0 3H20v4a1 1 0 0 1-1 1h-4v-1.5a1.5 1.5 0 1 0-3 0V18H7a1 1 0 0 1-1-1v-4H4.5a1.5 1.5 0 1 1 0-3H6V5a1 1 0 0 1 1-1z"/>',
  brain: '<path d="M9 4a3 3 0 0 0-3 3 3 3 0 0 0-1 5.8A3 3 0 0 0 8 18a3 3 0 0 0 4 2V4.8A3 3 0 0 0 9 4zM15 4a3 3 0 0 1 3 3 3 3 0 0 1 1 5.8A3 3 0 0 1 16 18a3 3 0 0 1-4 2"/>',
  build: '<path d="M3 21h18M5 21V10l7-5 7 5v11"/><path d="M10 21v-5h4v5"/>',
  game: '<rect x="2" y="7" width="20" height="11" rx="4"/><path d="M7 11v3M5.5 12.5h3M16 11.5h.01M18.5 14h.01"/>',
  sparkle: '<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/><path d="M18.5 15.5l.7 2 2 .7-2 .7-.7 2-.7-2-2-.7 2-.7z"/>',
  face_good: '<circle cx="12" cy="12" r="9"/><path d="M8.5 14.5a4.5 4.5 0 0 0 7 0M9 9.5h.01M15 9.5h.01"/>',
  face_flat: '<circle cx="12" cy="12" r="9"/><path d="M8.5 15h7M9 9.5h.01M15 9.5h.01"/>',
  face_sad: '<circle cx="12" cy="12" r="9"/><path d="M8.5 15.5a4.5 4.5 0 0 1 7 0M9 9.5h.01M15 9.5h.01"/>',
  face_think: '<circle cx="12" cy="12" r="9"/><path d="M9 15h4M9 9.5h.01M15 9.5h.01"/>',
  note: '<path d="M5 4h11l3 3v13a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1z"/><path d="M8 11h8M8 15h5"/>',
  box: '<path d="M3 8l9-4 9 4v8l-9 4-9-4z"/><path d="M3 8l9 4 9-4M12 12v8"/>',
  plug: '<path d="M9 7V3M15 7V3M7 7h10v4a5 5 0 0 1-10 0zM12 16v5"/>',
  rocket: '<path d="M12 3c3.5 2 5.5 5.5 5.5 9.5L14 16h-4l-3.5-3.5C6.5 8.5 8.5 5 12 3z"/><circle cx="12" cy="10" r="1.6"/><path d="M9 17l-2 4 4-2M15 17l2 4-4-2"/>',
  home: '<path d="M3 11l9-7 9 7v9a1 1 0 0 1-1 1h-5v-6h-6v6H4a1 1 0 0 1-1-1z"/>',
};
const sico = (name, size = 18, cls = "") => {
  const e = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  e.setAttribute("viewBox", "0 0 24 24"); e.setAttribute("width", size); e.setAttribute("height", size);
  e.setAttribute("fill", "none"); e.setAttribute("stroke", "currentColor"); e.setAttribute("stroke-width", "1.7");
  e.setAttribute("stroke-linecap", "round"); e.setAttribute("stroke-linejoin", "round"); e.setAttribute("aria-hidden", "true");
  if (cls) e.setAttribute("class", cls);
  e.innerHTML = ICON_PATHS[name] || ICON_PATHS.dot;
  return e;
};
const AVATARS = ["robot", "compass", "code", "search", "flask", "palette", "chart", "globe", "shield", "wrench", "pen", "graduate", "map", "target", "chat", "brain", "rocket", "build"];
const avatarIcon = (a, size = 18) => sico(AVATARS.includes(a) ? a : "robot", size);

const LT = {
  es: { "Agent lab": "Laboratorio", Start: "Inicio", Agents: "Agentes", Projects: "Proyectos", Jobs: "Trabajos", Schedules: "Programación", Talk: "Conversar", Playground: "Zona de pruebas", Rooms: "Salas", Models: "Modelos", Training: "Entrenamiento", Knowledge: "Conocimiento", Build: "Construir", Templates: "Plantillas", Terminal: "Terminal", Insights: "Análisis", Usage: "Uso", Library: "Biblioteca" },
  fr: { "Agent lab": "Labo d'agents", Start: "Accueil", Agents: "Agents", Projects: "Projets", Jobs: "Tâches", Schedules: "Planification", Talk: "Discuter", Playground: "Bac à sable", Rooms: "Salons", Models: "Modèles", Training: "Entraînement", Knowledge: "Connaissances", Build: "Créer", Templates: "Modèles de projet", Terminal: "Terminal", Insights: "Analyses", Usage: "Consommation", Library: "Bibliothèque" },
  de: { "Agent lab": "Agenten-Labor", Start: "Start", Agents: "Agenten", Projects: "Projekte", Jobs: "Aufträge", Schedules: "Zeitpläne", Talk: "Sprechen", Playground: "Spielwiese", Rooms: "Räume", Models: "Modelle", Training: "Training", Knowledge: "Wissen", Build: "Bauen", Templates: "Vorlagen", Terminal: "Terminal", Insights: "Einblicke", Usage: "Verbrauch", Library: "Bibliothek" },
  zh: { "Agent lab": "智能体实验室", Start: "开始", Agents: "智能体", Projects: "项目", Jobs: "任务", Schedules: "计划", Talk: "对话", Playground: "试验场", Rooms: "聊天室", Models: "模型", Training: "训练", Knowledge: "知识库", Build: "构建", Templates: "模板", Terminal: "终端", Insights: "洞察", Usage: "用量", Library: "资源库" },
  ja: { "Agent lab": "エージェントラボ", Start: "スタート", Agents: "エージェント", Projects: "プロジェクト", Jobs: "ジョブ", Schedules: "スケジュール", Talk: "会話", Playground: "プレイグラウンド", Rooms: "ルーム", Models: "モデル", Training: "トレーニング", Knowledge: "ナレッジ", Build: "ビルド", Templates: "テンプレート", Terminal: "ターミナル", Insights: "インサイト", Usage: "使用量", Library: "ライブラリ" },
};
const L = (s) => LT[S.lang]?.[s] ?? s;
const lhead = (title, sub) => [h("h1", {}, L(title)), h("p", { class: "lead" }, sub)];
const fmtN = (n) => (n == null ? "–" : n >= 1e6 ? (n / 1e6).toFixed(1) + "M" : n >= 1e4 ? (n / 1e3).toFixed(0) + "k" : n >= 1e3 ? (n / 1e3).toFixed(1) + "k" : String(Math.round(n * 100) / 100));
const fmtB = (b) => (b >= 1e9 ? (b / 1e9).toFixed(1) + " GB" : b >= 1e6 ? (b / 1e6).toFixed(1) + " MB" : b >= 1e3 ? (b / 1e3).toFixed(0) + " kB" : b + " B");
const money = (c) => (c ? "$" + (c < 0.01 ? c.toFixed(4) : c.toFixed(2)) : "$0");
const bar = (pct, label, tone = "") => h("div", { class: "pbar " + tone, role: "progressbar", "aria-valuenow": Math.round(pct || 0), "aria-valuemin": 0, "aria-valuemax": 100, title: label || `${Math.round(pct || 0)}%` },
  h("i", { style: `width:${Math.max(0, Math.min(100, pct || 0))}%` }));
const field = (label, control, hint) => h("div", { class: "field" }, h("label", {}, label), control, hint ? h("div", { class: "meta", style: "margin-top:4px" }, hint) : null);
const sel = (options, value, onchange, attrs = {}) => h("select", { ...attrs, onchange: (e) => onchange(e.target.value) },
  options.map((o) => (Array.isArray(o) ? h("option", { value: o[0], selected: String(o[0]) === String(value) }, o[1]) : h("option", { value: o, selected: String(o) === String(value) }, o))));
const inp = (value, oninput, attrs = {}) => h("input", { type: "text", spellcheck: "false", ...attrs, value: value ?? "", oninput: (e) => oninput(attrs.type === "number" ? +e.target.value : e.target.value) });
const tog = (checked, onchange, label) => h("label", { class: "switchrow", style: "margin:0" }, h("input", { type: "checkbox", class: "switch", checked: !!checked, onchange: (e) => onchange(e.target.checked) }), h("span", {}, label));
const errBox = () => h("div", { class: "err", role: "alert" });
const busy = async (btn, fn, err) => {
  if (err) err.textContent = "";
  const old = btn.textContent; btn.disabled = true; btn.textContent = "…";
  try { return await fn(); } catch (e) { if (err) err.textContent = e.message; else toast(e.message, "bad"); } finally { btn.disabled = false; btn.textContent = old; }
};
function toast(text, tone = "") {
  const el = h("div", { class: "toast " + tone, role: "status" }, text);
  document.body.append(el);
  setTimeout(() => el.classList.add("out"), 2600);
  setTimeout(() => el.remove(), 3100);
}
const tabs = (items, value, onpick) => h("div", { class: "seg", style: "margin-bottom:16px" }, items.map(([id, label]) => h("button", { "aria-pressed": id === value, onclick: () => onpick(id) }, label)));
const statusTone = { succeeded: "ok", failed: "bad", cancelled: "mute", stopped: "warn", queued: "mute", running: "", waiting_approval: "warn", paused: "warn", done: "ok", idle: "mute" };
const LIVE = ["queued", "running", "waiting_approval", "paused"];
function svgEl(tag, attrs) { const e = document.createElementNS("http://www.w3.org/2000/svg", tag); for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v); return e; }
function lineChart(series, labels, height = 160) {
  /* series: [{name, data:[num|null], cls}] */
  const W = 520, H = height, pad = 26, all = series.flatMap((s) => s.data).filter((v) => v != null);
  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, class: "linechart", role: "img", "aria-label": series.map((s) => s.name).join(", ") });
  if (!all.length) return svg;
  const max = Math.max(...all), min = Math.min(0, ...all), n = Math.max(...series.map((s) => s.data.length));
  const x = (i) => pad + (n <= 1 ? 0 : (i * (W - pad * 2)) / (n - 1)), y = (v) => H - pad - ((v - min) / (max - min || 1)) * (H - pad * 2);
  for (let k = 0; k <= 3; k++) { const v = min + ((max - min) * k) / 3; svg.append(svgEl("line", { x1: pad, x2: W - pad, y1: y(v), y2: y(v), class: "grid" })); const tx = svgEl("text", { x: 2, y: y(v) + 4, class: "tick" }); tx.textContent = fmtN(v); svg.append(tx); }
  series.forEach((s) => {
    const pts = s.data.map((v, i) => (v == null ? null : `${x(i)},${y(v)}`)).filter(Boolean);
    if (pts.length) svg.append(svgEl("polyline", { points: pts.join(" "), class: "ln " + (s.cls || "") }));
    s.data.forEach((v, i) => { if (v != null && s.mark?.(i)) svg.append(svgEl("circle", { cx: x(i), cy: y(v), r: 4.5, class: "mk " + (s.cls || "") })); });
  });
  if (labels) { const a = svgEl("text", { x: pad, y: H - 6, class: "tick" }); a.textContent = labels[0]; const b = svgEl("text", { x: W - pad, y: H - 6, class: "tick", "text-anchor": "end" }); b.textContent = labels[1]; svg.append(a, b); }
  return svg;
}
function barChart(values, labelFor) {
  const W = 520, H = 120, max = Math.max(1, ...values), bw = Math.min(W / Math.max(values.length, 1), 44);
  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, class: "bars", preserveAspectRatio: "none", role: "img" });
  values.forEach((v, i) => { const ht = v ? Math.max(3, (v / max) * (H - 6)) : 2; const r = svgEl("rect", { x: i * bw + 2, width: Math.max(1, bw - 4), y: H - ht, height: ht, rx: 3, class: v ? "" : "zero" }); const tt = svgEl("title", {}); tt.textContent = labelFor ? labelFor(i, v) : String(v); r.append(tt); svg.append(r); });
  return svg;
}

/* ---------- shared data ---------- */
const LAB = { agents: [], projects: [], presets: null, secrets: [] };
async function loadLab(which = ["agents", "projects"]) {
  const jobs_ = [];
  if (which.includes("agents")) jobs_.push(lapi("/api/lab/agents").then((r) => (LAB.agents = r.agents)).catch(() => {}));
  if (which.includes("projects")) jobs_.push(lapi("/api/lab/projects").then((r) => (LAB.projects = r.projects)).catch(() => {}));
  if (which.includes("presets") && !LAB.presets) jobs_.push(lapi("/api/lab/presets").then((r) => (LAB.presets = r)).catch(() => {}));
  if (which.includes("secrets")) jobs_.push(lapi("/api/lab/secrets").then((r) => (LAB.secrets = r.secrets)).catch(() => {}));
  await Promise.all(jobs_);
}
const agentOpts = (blank) => [...(blank ? [["", blank]] : []), ...LAB.agents.map((a) => [a.id, `${a.name}${a.locked ? " (private)" : ""}`])];
const projectOpts = (blank) => [...(blank ? [["", blank]] : []), ...LAB.projects.map((p) => [p.id, p.name])];
const go = (page, state) => { if (state) Object.assign(NAVSTATE, state); show(page); };
const NAVSTATE = {};

/* ---------- notifications bell ---------- */
const bell = { unread: 0, items: [] };
async function loadBell() {
  try { const r = await lapi("/api/lab/notifications"); bell.unread = r.unread; bell.items = r.items; } catch { return; }
  const b = $("bell");
  if (b) { b.querySelector(".badge").textContent = bell.unread > 9 ? "9+" : bell.unread || ""; b.querySelector(".badge").hidden = !bell.unread; }
}
function openBell() {
  const ov = $("overlay");
  ov.hidden = false; ov.className = ""; ov.dataset.kind = "settings";
  const tone = { error: "bad", warn: "warn", success: "ok", info: "mute" };
  fill(ov, h("div", { class: "sheet", role: "dialog", "aria-modal": "true", "aria-label": "Notifications" },
    h("div", { class: "sheet-head" }, h("h3", {}, "Notifications"), h("div", { class: "row" },
      h("button", { class: "btn plain", onclick: async () => { await lapi("/api/lab/notifications/read", {}); await loadBell(); openBell(); } }, "Mark all read"),
      h("button", { class: "btn plain", onclick: closeOverlay }, t("done")))),
    bell.items.length ? h("div", { class: "rows" }, bell.items.map((n) => h("button", { class: "clickrow" + (n.read ? " read" : ""), onclick: async () => {
      await lapi("/api/lab/notifications/read", { ids: [n.id] }).catch(() => {});
      closeOverlay(); loadBell();
      if (n.link?.page) go(n.link.page, { [n.link.page]: n.link.id });
    } }, h("span", { class: "t" }, n.read ? null : h("i", { class: "unread" }), n.title), h("span", { class: "meta" }, chip(n.kind, tone[n.level]), ago(n.ts)))))
      : h("div", { class: "empty" }, h("b", {}, "All quiet"), "Approvals, finished jobs, imports and training runs show up here.")));
}

/* ---------- home: quick start, tutorial, suggestions ---------- */
const TUTORIAL = [
  ["agent", "Create an agent", "Pick a model (or the offline demo model), a role and how much freedom it gets.", "agents"],
  ["project", "Create a project", "A private workspace with a space quota. Start from a template to get something working right away.", "templates"],
  ["job", "Give an agent a job", "Describe a goal. Watch progress, approve risky steps, rate the result.", "jobs"],
  ["chat", "Chat and compare models", "Try prompts on several models side by side, attach files, ask for widgets.", "playground"],
  ["room", "Let agents cooperate", "Put a manager and a developer in a room with a goal.", "rooms"],
  ["schedule", "Schedule work", "Run an agent every morning, or every 30 minutes, without watching.", "schedules"],
  ["connect", "Connect your accounts", "GitHub, Hugging Face, OpenAI or Anthropic: import repos and models, list model names.", "models"],
];
function pageHome() {
  const tut = h("div", { class: "card" }), sug = h("div"), quick = h("div", { class: "cards3" }), recent = h("div", { class: "card" });
  const load = async () => {
    let d = {};
    try {
      const [ag_, pr, jb, sg, us, sc, rm, acc] = await Promise.all([lapi("/api/lab/agents"), lapi("/api/lab/projects"), lapi("/api/lab/jobs"), lapi("/api/lab/suggestions"),
        lapi("/api/lab/usage?days=30"), lapi("/api/lab/schedules"), lapi("/api/lab/rooms"), lapi("/api/lab/accounts")]);
      d = { agents: ag_.agents, projects: pr.projects, jobs: jb.jobs, sugg: sg.suggestions, usage: us, schedules: sc.schedules, rooms: rm.rooms, acc };
    } catch { return; }
    LAB.agents = d.agents; LAB.projects = d.projects;
    const kinds = Object.fromEntries((d.usage.by_kind || []).map((k) => [k.key, k.calls]));
    const done = { agent: d.agents.length > 0, project: d.projects.length > 0, job: d.jobs.length > 0, chat: !!kinds.playground, room: d.rooms.length > 0,
      schedule: d.schedules.length > 0, connect: Object.values(d.acc).some((a) => a.connected) };
    const n = Object.values(done).filter(Boolean).length;
    fill(tut, h("div", { class: "row", style: "justify-content:space-between" }, h("div", { class: "label", style: "margin:0" }, "Tutorial"), h("span", { class: "meta" }, `${n} of ${TUTORIAL.length} done`)),
      bar((100 * n) / TUTORIAL.length, "", "grad"),
      h("div", { class: "rows gap" }, TUTORIAL.map(([id, title, text, page], i) => h("div", { class: "step" + (done[id] ? " done" : "") },
        h("span", { class: "num" }, done[id] ? "\u2713" : i + 1), h("div", { class: "grow" }, h("b", {}, title), h("div", { class: "meta" }, text)),
        done[id] ? null : h("button", { class: "btn plain", onclick: () => go(page) }, "Open")))));
    fill(sug, ...(d.sugg.length ? [h("h2", {}, "Suggestions"), h("div", { class: "stack" }, d.sugg.slice(0, 6).map((s) => h("div", { class: "card sugg " + s.level },
      h("div", { class: "row", style: "justify-content:space-between;flex-wrap:nowrap" }, h("b", {}, s.title),
        h("div", { class: "row", style: "flex-wrap:nowrap" }, s.action?.page ? h("button", { class: "btn plain", onclick: () => go(s.action.page, { [s.action.page]: s.action.id }) }, s.action.label) : null,
          h("button", { class: "btn plain", title: "Hide for a week", onclick: async () => { await lapi("/api/lab/suggestions/dismiss", { key: s.key }); load(); } }, "\u00d7"))),
      h("div", { class: "meta", style: "margin-top:4px" }, s.text))))] : []));
    const live = d.jobs.filter((j) => LIVE.includes(j.status));
    fill(recent, h("div", { class: "label" }, live.length ? `Running now (${live.length})` : "Recent jobs"),
      ...(d.jobs.length ? (live.length ? live : d.jobs.slice(0, 5)).map((j) => h("button", { class: "clickrow", onclick: () => go("jobs", { jobs: j.id }) },
        h("div", { class: "grow" }, h("div", { class: "t" }, j.prompt.slice(0, 80)), bar(j.progress.percent)), h("span", { class: "meta" }, chip(j.status, statusTone[j.status]), j.agent_name)))
        : [h("div", { class: "meta" }, "No jobs yet.")]));
  };
  const demo = h("button", { class: "btn", onclick: () => busy(demo, async () => {
    const a = await lapi("/api/lab/agents", { name: "Demo builder", role_preset: "developer", mode: "auto", provider: { type: "mock" } });
    const b = await lapi("/api/lab/build", { template: "landing", name: "Demo site", agent: a.id, prompt: "Make the headline about a coffee shop called Bean There." });
    toast("Demo agent and project created");
    go("jobs", { jobs: b.job?.id });
  }) }, "Try the demo");
  fill(quick, 
    h("div", { class: "card quick" }, h("div", { class: "big" }, sico("robot", 34)), h("b", {}, "New agent"), h("div", { class: "meta" }, "Any model: API, local server or your own weights."), h("button", { class: "btn plain", onclick: () => go("agents", { agents: "new" }) }, "Create")),
    h("div", { class: "card quick" }, h("div", { class: "big" }, sico("puzzle", 34)), h("b", {}, "Build something"), h("div", { class: "meta" }, "Websites, games, models and infrastructure from templates."), h("button", { class: "btn plain", onclick: () => go("templates") }, "Browse")),
    h("div", { class: "card quick" }, h("div", { class: "big" }, sico("chat", 34)), h("b", {}, "Playground"), h("div", { class: "meta" }, "Chat, compare models on one prompt, generate widgets."), h("button", { class: "btn plain", onclick: () => go("playground") }, "Open")));
  load();
  poll = setInterval(load, 8000);
  return [h("div", { class: "hero" }, h("h1", {}, "Your agents, your models, one lab."), h("p", {}, "Build agents on any model, give them projects with their own space, let them work on a schedule, and see exactly what they do and cost."), h("div", { class: "row" }, demo, h("button", { class: "btn ghost", onclick: () => go("agents", { agents: "new" }) }, "Create an agent"))),
    quick, h("div", { class: "grid2 gap" }, tut, recent), sug];
}

/* ---------- provider editor (any model) ---------- */
function providerEditor(state, onchange) {
  /* state: {preset, model, key, base_url, tool_mode, tokenizer, device, threads} -> builds a provider via toProvider() */
  const P = LAB.presets?.providers || {};
  const box = h("div", { class: "stack" });
  const models = h("datalist", { id: "modelList" + Math.random().toString(36).slice(2) });
  const err = errBox();
  const paint = () => {
    const p = P[state.preset] || {};
    const local = /127\.0\.0\.1|localhost/.test(state.base_url ?? p.base_url ?? "");
    const listBtn = h("button", { class: "btn plain", onclick: () => busy(listBtn, async () => {
      const r = await lapi("/api/lab/models/list", { provider: toProvider(state) });
      fill(models, ...r.models.map((m) => h("option", { value: m.id }, m.name)));
      toast(`${r.models.length} models found`, "ok");
    }, err) }, "List models");
    fill(box, 
      h("div", { class: "optgrid" },
        field("Provider", sel(Object.entries(P).map(([k, v]) => [k, v.label || k]), state.preset, (v) => { state.preset = v; state.model = P[v]?.model || ""; state.key = P[v]?.key || ""; state.base_url = undefined; paint(); onchange?.(); })),
        field("Model name", h("div", { class: "row", style: "flex-wrap:nowrap" }, inp(state.model, (v) => { state.model = v; onchange?.(); }, { list: models.id, placeholder: p.model || "model id" }), ["anthropic", "openai"].includes(p.type) ? listBtn : null), "Pick from the list, or type any model id."),
        p.type === "mock" || p.type === "cli" ? null : field("API key name", h("div", {}, inp(state.key, (v) => { state.key = v; onchange?.(); }, { list: "secretNames", placeholder: local ? "(not needed for local servers)" : "openai" }),
          h("datalist", { id: "secretNames" }, LAB.secrets.map((s) => h("option", { value: s })))), "The name of a key saved under Models  Keys (never the key itself)."),
        p.type === "mock" || p.type === "cli" ? null : field("Base URL", inp(state.base_url ?? p.base_url ?? "", (v) => { state.base_url = v; onchange?.(); }, { placeholder: "https://…/v1" }))),
      h("details", {}, h("summary", {}, "Advanced: tools, tokenizer, CPU"),
        h("div", { class: "optgrid gap" },
          field("Tool calling", sel([["native", "Native (function calling)"], ["prompt", "Text protocol (any model)"]], state.tool_mode || "native", (v) => { state.tool_mode = v; onchange?.(); }), "Use the text protocol for models without function calling."),
          field("Tokenizer", sel(Object.entries(LAB.presets?.tokenizers || {}).map(([k, v]) => [k, v]), state.tokenizer || "chars4", (v) => { state.tokenizer = v; onchange?.(); })),
          field("Run on", sel([["auto", "Auto"], ["cpu", "CPU only"], ["gpu", "GPU"]], state.device || "auto", (v) => { state.device = v; onchange?.(); }), "For local weights."),
          field("CPU threads", inp(state.threads || "", (v) => { state.threads = v || null; onchange?.(); }, { type: "number", min: 1, max: 256, placeholder: "auto" })))),
      models, err);
  };
  paint();
  return box;
}
function toProvider(s) {
  const p = { ...(LAB.presets?.providers?.[s.preset] || { type: "mock" }) };
  delete p.label;
  if (s.model) p.model = s.model;
  if (s.key !== undefined && p.type !== "mock") p.key = s.key;
  if (s.base_url !== undefined && s.base_url !== "") p.base_url = s.base_url;
  p.tool_mode = s.tool_mode || p.tool_mode || "native";
  p.tokenizer = { kind: "preset", preset: s.tokenizer || "chars4" };
  p.compute = { device: s.device || "auto", threads: s.threads ? +s.threads : null };
  p.label = LAB.presets?.providers?.[s.preset]?.label || p.type;
  return p;
}
function fromProvider(p) {
  const P = LAB.presets?.providers || {};
  const preset = Object.keys(P).find((k) => P[k].type === p.type && (P[k].base_url || "") === (p.base_url || "") && (P[k].label === p.label || !p.label)) ||
    Object.keys(P).find((k) => P[k].type === p.type && (P[k].base_url || "") === (p.base_url || "")) || (p.type === "openai" ? "custom" : p.type in P ? p.type : "mock");
  return { preset, model: p.model, key: p.key, base_url: p.base_url, tool_mode: p.tool_mode, tokenizer: p.tokenizer?.preset, device: p.compute?.device, threads: p.compute?.threads };
}

/* ---------- agents ---------- */
const MODE_LABEL = { ask: "Ask before every action", ask_risky: "Ask only for risky actions", auto: "Full auto (no questions)" };
const ag2 = { tab: "overview", draft: null };
function agentForm(existing) {
  const a = existing ? JSON.parse(JSON.stringify(existing)) : { name: "", role: "", avatar: "robot", instructions: "", mode: "ask_risky", limits: { max_output_tokens: 1024, max_steps: 25, token_budget: 200000, temperature: null, daily_token_cap: null },
    preferences: { style: "balanced", language: "" }, memory_enabled: true, diary_enabled: true, evolve: "off", environment_control: false, skills: [], plugins: [], knowledge: [], protection: { dangerous_commands: "standard" },
    dream: { enabled: false, start: "02:00", end: "05:00", tz_minutes: -new Date().getTimezoneOffset(), token_budget: 20000, cycles: 2 }, provider: { type: "mock" } };
  const pv = fromProvider(a.provider);
  if (!existing) Object.assign(pv, { preset: "mock" });
  let role = "";
  const err = errBox(), kbs = { list: [] };
  const lib = LAB.presets?.library || { roles: [], skills: [], plugins: [] };
  const checks = (items, chosen) => h("div", { class: "checks" }, items.map((it) => h("label", { class: "check", title: it.description || it.role || "" },
    h("input", { type: "checkbox", checked: chosen.includes(it.id), onchange: (e) => { const i = chosen.indexOf(it.id); if (e.target.checked && i < 0) chosen.push(it.id); if (!e.target.checked && i >= 0) chosen.splice(i, 1); } }), it.name)));
  const kbBox = h("div");
  lapi("/api/lab/knowledge").then((r) => { kbs.list = r.knowledge; fill(kbBox, r.knowledge.length ? checks(r.knowledge, a.knowledge) : h("div", { class: "meta" }, "No knowledge bases yet (Models  Knowledge).")); }).catch(() => {});
  const save = h("button", { class: "btn", onclick: () => busy(save, async () => {
    const body = { ...a, provider: toProvider(pv) };
    if (role) body.role_preset = role;
    delete body.id; delete body.version; delete body.history; delete body.created; delete body.updated; delete body.private; delete body.locked;
    const r = existing ? await lapi(`/api/lab/agents/${existing.id}`, body) : await lapi("/api/lab/agents", body);
    toast(existing ? "Agent saved" : "Agent created", "ok");
    await loadLab(["agents"]);
    NAVSTATE.agents = r.id; ag2.tab = "overview"; renderPage();
  }, err) }, existing ? "Save changes" : "Create agent");
  const L_ = a.limits;
  return h("div", { class: "card stack" },
    h("h3", { class: "card-title" }, existing ? `Edit ${existing.name}` : "New agent"),
    existing ? null : field("Start from a role (optional)", sel([["", "No preset role"], ...lib.roles.map((r) => [r.id, `${r.name} · ${r.role}`])], role, (v) => { role = v; const r = lib.roles.find((x) => x.id === v); if (r && !a.name) { a.name = r.name; renderPage(); } }), "Presets fill in instructions, skills, plugins and safe permissions; your own fields win."),
    h("div", { class: "optgrid" }, field("Name", inp(a.name, (v) => (a.name = v), { placeholder: "e.g. Site builder" })), field("Role", inp(a.role, (v) => (a.role = v), { placeholder: "What it is for" }))),
    field("Model", providerEditor(pv)),
    field("Custom instructions", h("textarea", { style: "min-height:110px;font-family:var(--font);font-size:.92rem", value: a.instructions, placeholder: "How it should work, what to avoid, house style…", oninput: (e) => (a.instructions = e.target.value) })),
    h("div", { class: "optgrid" },
      field("Control mode", sel(Object.entries(MODE_LABEL), a.mode, (v) => (a.mode = v))),
      field("Answer style", sel([["concise", "Concise"], ["balanced", "Balanced"], ["detailed", "Detailed"]], a.preferences.style, (v) => (a.preferences.style = v))),
      field("Max tokens per answer", inp(L_.max_output_tokens, (v) => (L_.max_output_tokens = v), { type: "number", min: 16, max: 32768 })),
      field("Token budget per job", inp(L_.token_budget, (v) => (L_.token_budget = v), { type: "number", min: 1000 })),
      field("Max steps per job", inp(L_.max_steps, (v) => (L_.max_steps = v), { type: "number", min: 1, max: 200 })),
      field("Daily token cap", inp(L_.daily_token_cap || "", (v) => (L_.daily_token_cap = v || null), { type: "number", placeholder: "no cap" })),
      field("Reply language", inp(a.preferences.language, (v) => (a.preferences.language = v), { placeholder: "same as the user" })),
      field("Dangerous-command protection", sel([["standard", "Standard (block destructive, confirm risky)"], ["strict", "Strict (confirm more)"], ["off", "Off (hard blocks stay)"]], a.protection.dangerous_commands, (v) => (a.protection.dangerous_commands = v)))),
    h("details", {}, h("summary", {}, "Permissions per action"),
      h("div", { class: "optgrid gap" }, ["read", "write", "run", "network", "environment"].map((c) => field(c, sel([["inherit", `From mode`], ["allow", "Allow"], ["ask", "Ask"], ["deny", "Deny"]], (a.authorizations || {})[c] || "inherit", (v) => { a.authorizations = { ...(a.authorizations || {}), [c]: v }; }))))),
    h("div", { class: "row" }, tog(a.memory_enabled, (v) => (a.memory_enabled = v), "Memory"), tog(a.diary_enabled, (v) => (a.diary_enabled = v), "Diary"),
      tog(a.evolve === "suggest", (v) => (a.evolve = v ? "suggest" : "off"), "Suggest improvements"), tog(a.environment_control, (v) => (a.environment_control = v), "May change the project environment")),
    h("details", {}, h("summary", {}, "Dream mode (thinks at night)"),
      h("div", { class: "stack gap" }, tog(a.dream.enabled, (v) => (a.dream.enabled = v), "Dream during the sleep window"),
        h("div", { class: "optgrid" }, field("Sleep from", inp(a.dream.start, (v) => (a.dream.start = v), { type: "time" })), field("Until", inp(a.dream.end, (v) => (a.dream.end = v), { type: "time" })),
          field("Token budget per night", inp(a.dream.token_budget, (v) => (a.dream.token_budget = v), { type: "number", min: 1000 })), field("Dream cycles", inp(a.dream.cycles, (v) => (a.dream.cycles = v), { type: "number", min: 1, max: 5 }))),
        h("div", { class: "meta" }, "While dreaming the agent reviews its diary and memories, merges duplicates, writes insights and a plan. It never runs tools."))),
    h("details", {}, h("summary", {}, "Skills, plugins and knowledge"),
      h("div", { class: "stack gap" }, h("label", {}, "Skills"), checks(lib.skills, a.skills), h("label", {}, "Plugins (tools)"), checks([...lib.plugins, { id: "widgets", name: "Widgets" }], a.plugins), h("label", {}, "Knowledge bases"), kbBox,
        h("label", { class: "gap" }, "Teammates it can ask for help (agent swarm)"),
        h("div", { class: "checks" }, LAB.agents.filter((x) => !x.locked && x.id !== existing?.id).length
          ? LAB.agents.filter((x) => !x.locked && x.id !== existing?.id).map((x) => h("label", { class: "check" },
            h("input", { type: "checkbox", checked: (a.teammates || []).includes(x.id), onchange: (e) => { a.teammates = e.target.checked ? [...(a.teammates || []), x.id] : (a.teammates || []).filter((y) => y !== x.id); } }), x.name))
          : h("span", { class: "meta" }, "Create another agent first.")),
        h("div", { class: "meta" }, "A teammate answers one question as a chat, inside this agent's own token budget: no tools, no nesting, no runaway swarm."))),
    h("div", { class: "row" }, save, existing ? h("button", { class: "btn plain", onclick: () => { ag2.tab = "overview"; renderPage(); } }, t("cancel")) : null), err);
}
function unlockCard(a) {
  const pw = h("input", { type: "password", autocomplete: "off", placeholder: "Password" }), err = errBox();
  const b = h("button", { class: "btn", onclick: () => busy(b, async () => { UNLOCK[a.id] = (await lapi(`/api/lab/agents/${a.id}/unlock`, { password: pw.value })).token; await loadLab(["agents"]); renderPage(); }, err) }, "Unlock");
  pw.addEventListener("keydown", (e) => e.key === "Enter" && b.click());
  return h("div", { class: "card stack" }, h("div", { class: "empty", style: "padding:20px" }, h("b", { class: "row", style: "justify-content:center;gap:8px" }, sico("lock", 18), `${a.name} is private`), "Enter its password to use, view or change it. Unlocking lasts 30 minutes."), h("div", { class: "row" }, h("div", { class: "grow" }, pw), b), err);
}
function agentDetail(a) {
  if (a.locked) return unlockCard(a);
  const body = h("div", { class: "stack" });
  const top = h("div", { class: "card" }, h("div", { class: "row", style: "flex-wrap:nowrap" }, h("div", { class: "avatar" }, avatarIcon(a.avatar, 26)),
    h("div", { class: "grow" }, h("h3", { class: "card-title row", style: "margin:0;gap:8px" }, a.name, a.private ? sico("lock", 15) : null), h("div", { class: "meta" }, a.role || "No role", "·", `${a.provider.label || a.provider.type} ${a.provider.model || ""}`, "·", MODE_LABEL[a.mode], "·", `v${a.version}`)),
    h("button", { class: "btn", onclick: () => go("jobs", { jobs: "new", jobAgent: a.id }) }, "Give a job")));
  const T = [["overview", "Overview"], ["settings", "Settings"], ["memory", "Memory"], ["diary", "Diary"], ["improve", "Improve"], ["dreams", "Dreams"], ["privacy", "Privacy"]];
  const paint = async () => {
    const tab = ag2.tab;
    fill(body, h("div", { class: "empty" }, "…"));
    try {
      if (tab === "overview") {
        const s = await lapi(`/api/lab/agents/${a.id}/stats`);
        const tile = (num, lab, tone = "") => h("div", { class: "tile " + tone }, h("div", { class: "num" }, num), h("div", { class: "lab" }, lab));
        fill(body, h("div", { class: "tiles" }, tile(s.jobs.total, "jobs (30 days)"), tile(s.jobs.success_rate == null ? "–" : Math.round(s.jobs.success_rate * 100) + "%", "success", s.jobs.success_rate >= 0.8 ? "ok" : s.jobs.success_rate != null && s.jobs.success_rate < 0.5 ? "bad" : ""),
          tile(fmtN(s.totals.input_tokens + s.totals.output_tokens), "tokens"), tile(money(s.totals.cost), "cost"), tile(fmtN(s.efficiency.avg_steps), "avg steps"), tile(`${s.feedback.up}/${s.feedback.down}`, "good / poor ratings"), tile(s.knowledge.memories, "memories"), tile(s.totals.approvals_asked, "approvals asked")),
          h("div", { class: "card" }, h("div", { class: "row", style: "justify-content:space-between" }, h("div", { class: "label", style: "margin:0" }, "Health"), chip(s.health.level, { ok: "ok", warn: "warn", bad: "bad" }[s.health.level] || "mute")),
            h("div", { class: "meta", style: "margin-top:6px" }, s.health.reasons.map((r) => reasonText(r)).join(" · ")),
            s.by_day.length ? h("div", { class: "gap" }, h("div", { class: "label" }, "Tokens per day"), barChart(s.by_day.map((d) => d.tokens || 0), (i, v) => `${s.by_day[i].date}: ${fmtN(v)}`)) : null),
          s.tool_usage.length ? h("div", { class: "card" }, h("div", { class: "label" }, "Tools used"), h("div", { class: "row" }, s.tool_usage.map((x) => chip(`${x.tool} · ${x.count}`, "mute")))) : null,
          s.top_errors.length ? h("div", { class: "card" }, h("div", { class: "label" }, "Most common errors"), h("div", { class: "rows tight" }, s.top_errors.map((e) => h("div", { class: "kv" }, h("span", { class: "mono" }, e.error), h("span", { class: "meta" }, `×${e.count}`))))) : null);
      } else if (tab === "settings") {
        fill(body, agentForm(a), h("div", { class: "card" }, h("div", { class: "row", style: "justify-content:space-between" }, h("span", {}, "Delete this agent and its memory, diary and history."),
          h("button", { class: "btn danger", onclick: async () => { if (!confirm(t("confirm_delete"))) return; await lapi(`/api/lab/agents/${a.id}/delete`, {}); NAVSTATE.agents = null; await loadLab(["agents"]); renderPage(); } }, t("delete")))));
      } else if (tab === "memory") {
        const r = await lapi(`/api/lab/agents/${a.id}/memory`);
        const fact = inp("", () => {}, { placeholder: "Teach it something to remember, e.g. “deploys happen on Tuesdays”" });
        const add = h("button", { class: "btn", onclick: () => busy(add, async () => { await lapi(`/api/lab/agents/${a.id}/memory`, { fact: fact.value }); paint(); }) }, "Remember");
        fill(body, h("div", { class: "card" }, h("div", { class: "row" }, h("div", { class: "grow" }, fact), add)),
          h("div", { class: "card" }, r.facts.length ? h("div", { class: "rows" }, r.facts.map((f) => h("div", { class: "kv" }, h("div", {}, h("div", {}, f.fact), h("div", { class: "meta" }, chip(f.source, "mute"), ago(f.ts || f.created || 0))),
            h("button", { class: "btn danger", onclick: async () => { await lapi(`/api/lab/agents/${a.id}/memory/${f.id}/delete`, {}); paint(); } }, "Forget")))) : h("div", { class: "empty" }, "Nothing remembered yet.")));
      } else if (tab === "diary") {
        const r = await lapi(`/api/lab/agents/${a.id}/diary`);
        const note = h("textarea", { style: "min-height:70px;font-family:var(--font)", placeholder: "Write a note in the diary (the agent reads it)" });
        const add = h("button", { class: "btn", onclick: () => busy(add, async () => { await lapi(`/api/lab/agents/${a.id}/diary`, { text: note.value }); paint(); }) }, "Add note");
        const mood = { proud: "face_good", neutral: "face_flat", frustrated: "face_sad", curious: "face_think" };
        fill(body, h("div", { class: "card stack" }, note, h("div", {}, add)),
          ...(r.entries.length ? r.entries.map((e) => h("div", { class: "card diary" }, h("div", { class: "row", style: "justify-content:space-between" }, h("b", { class: "row", style: "gap:8px" }, sico(mood[e.mood] || "note", 16), e.title), h("span", { class: "meta" }, e.outcome ? chip(e.outcome, statusTone[e.outcome]) : null, ago(e.ts))),
            h("p", {}, e.body), e.lessons?.length ? h("div", {}, h("div", { class: "label" }, "Lessons"), h("ul", {}, e.lessons.map((l) => h("li", {}, l)))) : null)) : [h("div", { class: "card empty" }, "The diary is empty. Turn it on in Settings: the agent then reflects after every job.")]));
      } else if (tab === "improve") {
        const [p, ev] = await Promise.all([lapi(`/api/lab/agents/${a.id}/proposals`), lapi(`/api/lab/agents/${a.id}/evals`)]);
        const err = errBox();
        const propose = h("button", { class: "btn plain", onclick: () => busy(propose, async () => { await lapi(`/api/lab/agents/${a.id}/proposals`, {}); paint(); }, err) }, "Ask for a proposal");
        const auto = h("button", { class: "btn", disabled: !ev.cases.length, title: ev.cases.length ? "" : "Add evaluation cases first", onclick: () => busy(auto, async () => { const r = await lapi(`/api/lab/agents/${a.id}/improve`, {}); toast(`${r.verdict}: ${r.reason}`, r.verdict === "accepted" ? "ok" : "warn"); await loadLab(["agents"]); paint(); }, err) }, "Auto-improve (gated by tests)");
        const cp = inp("", () => {}, { placeholder: "Test prompt, e.g. Where is my order?" }), cv = inp("", () => {}, { placeholder: "The answer must contain…" });
        const ck = { kind: "contains" };
        const addCase = h("button", { class: "btn plain", onclick: () => busy(addCase, async () => { await lapi(`/api/lab/agents/${a.id}/evals`, { prompt: cp.value, checks: [{ kind: ck.kind, value: ck.kind === "json" ? null : cv.value }] }); paint(); }, err) }, "+ Add case");
        const runEv = h("button", { class: "btn plain", disabled: !ev.cases.length, onclick: () => busy(runEv, async () => { const r = await lapi(`/api/lab/agents/${a.id}/evals/run`, {}); toast(`Score ${Math.round(r.score * 100)}% (${r.passed}/${r.total})`, r.score === 1 ? "ok" : "warn"); }, err) }, "Run tests");
        fill(body, 
          h("div", { class: "card stack" }, h("div", { class: "label" }, "Evaluation cases"), h("div", { class: "meta" }, "Auto-improvement only accepts a change when the score goes up and nothing that passed now fails."),
            ev.cases.length ? h("div", { class: "rows tight" }, ev.cases.map((c) => h("div", { class: "kv" }, h("div", {}, h("div", {}, c.prompt), h("div", { class: "meta" }, c.checks.map((k) => chip(`${k.kind}${k.value != null ? " " + k.value : ""}`, "mute")))),
              h("button", { class: "btn danger", onclick: async () => { await lapi(`/api/lab/agents/${a.id}/evals/${c.id}/delete`, {}); paint(); } }, t("remove"))))) : null,
            h("div", { class: "optgrid" }, cp, h("div", { class: "row", style: "flex-wrap:nowrap" }, sel([["contains", "contains"], ["not_contains", "does not contain"], ["regex", "matches regex"], ["equals", "equals"], ["max_words", "max words"], ["json", "is valid JSON"]], ck.kind, (v) => (ck.kind = v), { style: "width:auto" }), cv)),
            h("div", { class: "row" }, addCase, runEv, auto), err),
          h("div", { class: "card" }, h("div", { class: "row", style: "justify-content:space-between" }, h("div", { class: "label", style: "margin:0" }, "Improvement proposals"), propose),
            p.proposals.length ? h("div", { class: "rows" }, [...p.proposals].reverse().map((x) => h("div", {}, h("div", { class: "row" }, chip(x.status, { accepted: "ok", rejected: "bad", pending: "warn" }[x.status]), h("span", { class: "meta" }, `from ${x.lessons_used} lessons · ${ago(x.ts)}`)),
              x.rationale ? h("p", { class: "meta" }, x.rationale) : null, x.changes?.length ? h("ul", {}, x.changes.map((c) => h("li", {}, c))) : null,
              h("details", {}, h("summary", {}, "New instructions"), h("pre", {}, x.instructions)),
              x.status === "pending" ? h("div", { class: "row" }, h("button", { class: "btn", onclick: async () => { await lapi(`/api/lab/agents/${a.id}/proposals/${x.id}/accept`, {}); await loadLab(["agents"]); paint(); } }, "Accept"),
                h("button", { class: "btn plain", onclick: async () => { await lapi(`/api/lab/agents/${a.id}/proposals/${x.id}/reject`, {}); paint(); } }, "Reject")) : null))) : h("div", { class: "meta gap" }, "No proposals yet. They come from diary lessons and your ratings.")),
          ev.history.length ? h("div", { class: "card" }, h("div", { class: "label" }, "Auto-improvement history"), h("div", { class: "rows tight" }, ev.history.map((r) => h("div", { class: "kv" }, h("span", {}, chip(r.verdict, r.verdict === "accepted" ? "ok" : "bad"), " ", r.reason), h("span", { class: "meta" }, ago(r.ts)))))) : null,
          a.history?.length ? h("div", { class: "card" }, h("div", { class: "label" }, "Versions"), h("div", { class: "rows tight" }, [...a.history].reverse().slice(0, 10).map((v) => h("div", { class: "kv" }, h("span", {}, `v${v.version} · ${v.reason || ""}`),
            h("button", { class: "btn plain", onclick: async () => { await lapi(`/api/lab/agents/${a.id}/rollback`, { version: v.version }); await loadLab(["agents"]); toast("Rolled back", "ok"); paint(); } }, "Restore"))))) : null);
      } else if (tab === "dreams") {
        const r = await lapi(`/api/lab/agents/${a.id}/dreams`);
        const now = h("button", { class: "btn plain", onclick: () => busy(now, async () => { await lapi(`/api/lab/agents/${a.id}/dream`, {}); paint(); }) }, "Dream now");
        fill(body, h("div", { class: "card" }, h("div", { class: "row", style: "justify-content:space-between" }, h("span", { class: "meta" }, a.dream.enabled ? `Sleeps ${a.dream.start}–${a.dream.end}` : "Dream mode is off (turn it on in Settings)."), now)),
          ...(r.dreams.length ? r.dreams.map((d) => h("div", { class: "card diary" }, h("div", { class: "row", style: "justify-content:space-between" }, h("b", { class: "row", style: "gap:8px" }, sico("moon", 16), `Night of ${d.night}`), h("span", { class: "meta" }, (d.themes || []).map((x) => chip(x, "mute")), `${fmtN(d.tokens)} tokens`, d.merged ? `${d.merged} memories merged` : null)),
            ...[["insights", "Insights"], ["ideas", "Ideas"], ["worries", "Worries"], ["plan_for_tomorrow", "Plan for tomorrow"]].map(([k, label]) => (d[k]?.length ? h("div", {}, h("div", { class: "label gap" }, label), h("ul", {}, d[k].map((x) => h("li", {}, x)))) : null)))) : [h("div", { class: "card empty" }, "No dreams yet.")]));
      } else if (tab === "privacy") {
        const pw = h("input", { type: "password", autocomplete: "new-password", placeholder: "New password (8+ characters)" }), cur = h("input", { type: "password", autocomplete: "current-password", placeholder: "Current password" }), err = errBox();
        const set = h("button", { class: "btn", onclick: () => busy(set, async () => { await lapi(`/api/lab/agents/${a.id}/private`, { password: pw.value, current: cur.value || undefined }); delete UNLOCK[a.id]; await loadLab(["agents"]); toast("The agent is private", "ok"); renderPage(); }, err) }, a.private ? "Change password" : "Make private");
        const rm = h("button", { class: "btn danger", onclick: () => busy(rm, async () => { await lapi(`/api/lab/agents/${a.id}/private/remove`, { password: cur.value }); await loadLab(["agents"]); renderPage(); }, err) }, "Remove password");
        fill(body, h("div", { class: "card stack" }, h("div", { class: "meta" }, "A private agent needs its password before anyone can use, view or change it in agentlab. The password is stored only as a scrypt hash. Honest limit: files on this computer are not encrypted."),
          a.private ? cur : null, pw, h("div", { class: "row" }, set, a.private ? rm : null, a.private ? h("button", { class: "btn plain", onclick: async () => { await lapi(`/api/lab/agents/${a.id}/lock`, {}); delete UNLOCK[a.id]; await loadLab(["agents"]); renderPage(); } }, "Lock now") : null), err));
      }
    } catch (e) { fill(body, h("div", { class: "card err" }, e.message)); }
  };
  paint();
  return h("div", { class: "stack" }, top, tabs(T, ag2.tab, (v) => { ag2.tab = v; renderPage(); }), body);
}
function pageAgents() {
  const list = h("div", { class: "list card" }), view = h("div");
  const paint = () => {
    const sel_ = NAVSTATE.agents;
    fill(list, ...(LAB.agents.length ? LAB.agents.map((a) => h("button", { "aria-current": a.id === sel_, onclick: () => { NAVSTATE.agents = a.id; ag2.tab = "overview"; renderPage(); } },
      h("div", { class: "t row", style: "gap:8px" }, avatarIcon(a.avatar, 16), a.name, a.private ? sico("lock", 13) : null), h("div", { class: "meta" }, a.locked ? "private" : `${a.provider.label || a.provider.type} · ${a.mode}`))) : [h("div", { class: "empty" }, "No agents yet.")]));
    const a = LAB.agents.find((x) => x.id === sel_);
    fill(view, sel_ === "new" || !a ? agentForm(null) : agentDetail(a));
  };
  loadLab(["agents", "presets", "secrets"]).then(async () => {
    const a = LAB.agents.find((x) => x.id === NAVSTATE.agents);
    if (a && !a.locked) Object.assign(a, await lapi(`/api/lab/agents/${a.id}`).catch(() => ({})));
    if (current === "agents") paint();
  });
  return [...lhead("Agents", "Any model becomes an agent: give it instructions, a role, limits and permissions. It remembers, keeps a diary, dreams at night and proposes improvements you approve."),
    h("div", { class: "split" }, h("div", {}, h("button", { class: "btn", style: "width:100%;margin-bottom:12px", onclick: () => { NAVSTATE.agents = "new"; paint(); } }, "+ New agent"), list), view)];
}

/* ---------- projects ---------- */
const pj = { tab: "files", path: "", file: null, envDraft: null };
function uploadFiles(pid, folder, files, onDone) {
  const box = h("div", { class: "stack" });
  (async () => {
    for (const f of files) {
      const row = h("div", {}, h("div", { class: "meta" }, `${f.name} · ${fmtB(f.size)}`), bar(0));
      box.append(row);
      const id = Math.random().toString(16).slice(2, 12);
      const path = (folder ? folder + "/" : "") + f.name;
      const tick = setInterval(async () => { try { const p = await lapi(`/api/lab/uploads/${id}`); row.lastChild.firstChild.style.width = p.percent + "%"; } catch {} }, 400);
      try { await lapi(`/api/lab/projects/${pid}/upload?path=${encodeURIComponent(path)}&id=${id}`, undefined, f); row.lastChild.firstChild.style.width = "100%"; row.firstChild.append(" ", chip("uploaded", "ok")); }
      catch (e) { row.firstChild.append(" ", chip(e.message, "bad")); }
      clearInterval(tick);
    }
    onDone?.();
  })();
  return box;
}
function projectDetail(p) {
  const body = h("div", { class: "stack" });
  const used = p.quota_bytes ? (100 * p.used_bytes) / p.quota_bytes : 0;
  const top = h("div", { class: "card" }, h("div", { class: "row", style: "justify-content:space-between" }, h("h3", { class: "card-title", style: "margin:0" }, p.name), h("div", { class: "row" },
    h("button", { class: "btn plain", onclick: () => go("terminal", { terminal: p.id }) }, "Open terminal"), h("button", { class: "btn", onclick: () => go("jobs", { jobs: "new", jobProject: p.id }) }, "Give a job"))),
    h("div", { class: "meta" }, chip(p.kind, "mute"), `${fmtB(p.used_bytes)} of ${fmtB(p.quota_bytes)} used · ${p.files} files`), bar(used, "", used > 90 ? "bad" : used > 75 ? "warn" : ""));
  const T = [["files", "Files"], ["environment", "Environment"], ["ai", "AI & training"], ["ship", "Docs & ship"], ["import", "Import"], ["settings", "Settings"]];
  const paint = async () => {
    fill(body, h("div", { class: "empty" }, "…"));
    try {
      if (pj.tab === "files") {
        const r = await lapi(`/api/lab/projects/${p.id}/files?path=${encodeURIComponent(pj.path)}`);
        const crumbs = ["", ...pj.path.split("/").filter(Boolean)];
        const fileIn = h("input", { type: "file", multiple: true, hidden: true, onchange: (e) => { body.prepend(h("div", { class: "card" }, h("div", { class: "label" }, "Uploading"), uploadFiles(p.id, pj.path, [...e.target.files], () => { setTimeout(paint, 600); loadLab(["projects"]); }))); } });
        const newName = inp("", () => {}, { placeholder: "new-file.txt" });
        const editor = pj.file ? (() => {
          const ta = h("textarea", { value: pj.file.content, style: "min-height:300px", spellcheck: "false" });
          const save = h("button", { class: "btn", onclick: () => busy(save, async () => { await lapi(`/api/lab/projects/${p.id}/file`, { path: pj.file.path, content: ta.value }); toast("Saved", "ok"); }) }, t("save"));
          return h("div", { class: "card stack" }, h("div", { class: "row", style: "justify-content:space-between" }, h("b", { class: "mono" }, pj.file.path), h("div", { class: "row" },
            /\.html?$/.test(pj.file.path) ? h("button", { class: "btn plain", onclick: async () => { try { const r = await lapi(`/api/lab/projects/${p.id}/serve`, {}); window.open(r.url + pj.file.path, "_blank", "noopener"); } catch (e) { toast(e.message, "bad"); } } }, "Preview on localhost") : null,
            h("button", { class: "btn plain", onclick: () => { pj.file = null; paint(); } }, "Close"))),
            pj.file.binary ? h("div", { class: "meta" }, "Binary file: not shown.") : ta, pj.file.binary ? null : h("div", {}, save));
        })() : null;
        fill(body, h("div", { class: "card" },
          h("div", { class: "row", style: "justify-content:space-between" }, h("div", { class: "crumbs" }, crumbs.map((c, i) => h("button", { class: "btn plain", onclick: () => { pj.path = crumbs.slice(1, i + 1).join("/"); pj.file = null; paint(); } }, i ? c : "workspace"))),
            h("div", { class: "row" }, h("button", { class: "btn plain", onclick: () => fileIn.click() }, "Upload files"), fileIn)),
          r.entries.length ? h("div", { class: "rows tight" }, r.entries.map((e) => h("div", { class: "kv" },
            h("button", { class: "linkish row", style: "gap:8px", onclick: async () => { const rel = (pj.path ? pj.path + "/" : "") + e.name; if (e.dir) { pj.path = rel; pj.file = null; paint(); } else { pj.file = await lapi(`/api/lab/projects/${p.id}/file?path=${encodeURIComponent(rel)}`); paint(); } } }, e.name),
            h("div", { class: "row", style: "flex-wrap:nowrap" }, e.dir ? null : h("span", { class: "meta" }, fmtB(e.size)),
              h("button", { class: "btn danger", title: t("delete"), onclick: async () => { if (!confirm(`${t("confirm_delete")} ${e.name}`)) return; await lapi(`/api/lab/projects/${p.id}/file/delete`, { path: (pj.path ? pj.path + "/" : "") + e.name }); paint(); } }, "\u00d7"))))) : h("div", { class: "meta", style: "padding:12px 0" }, "Empty folder. Upload files, import from GitHub or Hugging Face, or let an agent create them."),
          h("div", { class: "row gap" }, h("div", { class: "grow" }, newName), h("button", { class: "btn plain", onclick: async () => { if (!newName.value) return; const rel = (pj.path ? pj.path + "/" : "") + newName.value; await lapi(`/api/lab/projects/${p.id}/file`, { path: rel, content: "" }); pj.file = { path: rel, content: "" }; paint(); } }, "New file"))), editor);
      } else if (pj.tab === "environment") {
        const full = await lapi(`/api/lab/projects/${p.id}`);
        const env = (pj.envDraft = pj.envDraft?.id === p.id ? pj.envDraft : { id: p.id, ...full.environment });
        const st = await lapi(`/api/lab/projects/${p.id}/environment/setup`);
        const varsTa = h("textarea", { style: "min-height:90px", value: Object.entries(env.vars).map(([k, v]) => `${k}=${v}`).join("\n"), placeholder: "MODE=dev\nREGION=eu" });
        const pk = h("textarea", { style: "min-height:60px", value: env.python.packages.join("\n"), placeholder: "requests\npandas>=2" });
        const err = errBox();
        const save = h("button", { class: "btn", onclick: () => busy(save, async () => {
          const vars = Object.fromEntries(varsTa.value.split("\n").map((l) => l.trim()).filter(Boolean).map((l) => [l.split("=")[0].trim(), l.split("=").slice(1).join("=")]));
          await lapi(`/api/lab/projects/${p.id}/environment`, { vars, network: env.network, secrets: env.secrets, python: { enabled: env.python.enabled, packages: pk.value.split(/\s+/).filter(Boolean) }, timeout: env.timeout });
          pj.envDraft = null; toast("Environment saved", "ok"); paint();
        }, err) }, t("save"));
        const setup = h("button", { class: "btn plain", disabled: !env.python.enabled, onclick: () => busy(setup, async () => { await lapi(`/api/lab/projects/${p.id}/environment/setup`, {}); setTimeout(paint, 1500); }, err) }, "Set up Python now");
        fill(body, h("div", { class: "card stack" },
          field("Network for commands", sel([["allowed", "Allowed (when an action is approved)"], ["off", "Off: fully offline"]], env.network, (v) => (env.network = v))),
          field("Environment variables (not secrets)", varsTa, "Never put keys here. Share saved keys below instead."),
          field("Keys visible to commands in this project", h("div", { class: "checks" }, LAB.secrets.length ? LAB.secrets.map((s_) => h("label", { class: "check" }, h("input", { type: "checkbox", checked: env.secrets.includes(s_), onchange: (e) => { env.secrets = e.target.checked ? [...env.secrets, s_] : env.secrets.filter((x) => x !== s_); } }), s_)) : h("span", { class: "meta" }, "No saved keys."))),
          field("Command timeout (seconds)", inp(env.timeout, (v) => (env.timeout = v), { type: "number", min: 1, max: 600 })),
          tog(env.python.enabled, (v) => { env.python.enabled = v; setup.disabled = !v; }, "Python virtual environment"),
          field("Python packages", pk),
          h("div", { class: "row" }, save, setup, st.status !== "none" ? chip(`setup ${st.status}`, statusTone[st.status] || "") : null), err,
          st.log ? h("details", {}, h("summary", {}, "Setup log"), h("pre", {}, st.log)) : null));
      } else if (pj.tab === "ai") {
        const full = await lapi(`/api/lab/projects/${p.id}`);
        const ai = { trainer: "classifier", dataset: "", agent: "", options: {}, ...(full.ai || {}) };
        const runs = (await lapi(`/api/lab/training?project=${p.id}`)).runs;
        const err = errBox(), info = h("div");
        const dsIn = inp(ai.dataset, (v) => (ai.dataset = v), { placeholder: "data/train.csv" });
        const inspectBtn = h("button", { class: "btn plain", onclick: () => busy(inspectBtn, async () => {
          const r = await lapi(`/api/lab/projects/${p.id}/dataset?path=${encodeURIComponent(dsIn.value)}`);
          fill(info, h("div", { class: "meta gap" }, `${r.rows} rows · ${r.format} · columns: ${r.columns.join(", ")}`, r.suggested ? chip(`suggested: ${r.suggested}`) : null),
            r.labels ? h("div", { class: "row gap" }, Object.entries(r.labels).slice(0, 12).map(([k, v]) => chip(`${k} · ${v}`, "mute"))) : null);
          if (r.suggested) ai.trainer = r.suggested;
        }, err) }, "Inspect");
        const save = h("button", { class: "btn plain", onclick: () => busy(save, async () => { await lapi(`/api/lab/projects/${p.id}/ai`, { ...ai, dataset: dsIn.value }); toast("Project AI saved", "ok"); }, err) }, t("save"));
        const train = h("button", { class: "btn", onclick: () => busy(train, async () => { await lapi(`/api/lab/projects/${p.id}/ai`, { ...ai, dataset: dsIn.value }); const r = await lapi(`/api/lab/projects/${p.id}/train`, {}); go("training", { training: r.id }); }, err) }, "Train");
        const o = (ai.options = { epochs: 20, val_fraction: 0.2, patience: 3, overfit_protection: true, ...ai.options });
        fill(body, h("div", { class: "card stack" }, h("div", { class: "meta" }, "Choose the project's model and dataset, then press Train. The built-in trainer makes a text classifier on any CPU in seconds; LoRA fine-tunes an open model in this project's Python environment."),
          h("div", { class: "optgrid" }, field("Agent for this project", sel(agentOpts("None"), ai.agent, (v) => (ai.agent = v))), field("Trainer", sel([["classifier", "Built-in text classifier"], ["lora", "LoRA fine-tune (Hugging Face model)"]], ai.trainer, (v) => { ai.trainer = v; paint(); }))),
          field("Dataset (a file in this project)", h("div", { class: "row", style: "flex-wrap:nowrap" }, dsIn, inspectBtn)), info,
          ai.trainer === "lora" ? field("Base model", inp(o.base_model, (v) => (o.base_model = v), { placeholder: "Qwen/Qwen2.5-0.5B-Instruct" })) : null,
          h("div", { class: "optgrid" }, field("Epochs", inp(o.epochs, (v) => (o.epochs = v), { type: "number", min: 1, max: 500 })), field("Validation split", inp(o.val_fraction, (v) => (o.val_fraction = v), { type: "number", min: 0, max: 0.5, step: 0.05 })),
            field("Early-stopping patience", inp(o.patience, (v) => (o.patience = v), { type: "number", min: 1, max: 50 }))),
          tog(o.overfit_protection, (v) => (o.overfit_protection = v), "Overfit protection (validation split, early stopping, keep the best epoch)"),
          ai.trainer === "lora" ? tog(o.allow_network, (v) => (o.allow_network = v), "Allow network (to download the base model)") : null,
          h("div", { class: "row" }, train, save), err),
          runs.length ? h("div", { class: "card" }, h("div", { class: "label" }, "Training runs"), h("div", { class: "rows tight" }, runs.map((r) => h("button", { class: "clickrow", onclick: () => go("training", { training: r.id }) },
            h("span", { class: "t" }, r.name), h("span", { class: "meta" }, chip(r.status, statusTone[r.status]), r.best?.val_acc != null ? `val acc ${Math.round(r.best.val_acc * 100)}%` : r.best?.val_loss != null ? `val loss ${r.best.val_loss}` : "", ago(r.created)))))) : null);
      } else if (pj.tab === "ship") {
        const err = errBox(), out = h("div", { class: "stack" });
        const kinds = { readme: true, api: false, guide: false, changelog: false, contributing: false };
        const docs = h("button", { class: "btn", onclick: () => busy(docs, async () => { const r = await lapi(`/api/lab/projects/${p.id}/docs`, { kinds: Object.keys(kinds).filter((k) => kinds[k]) }); fill(out, h("div", { class: "card" }, h("div", { class: "label" }, "Written"), h("div", { class: "row" }, Object.keys(r.files).map((f) => chip(f, "ok")), chip("docs/html/*.html", "mute")), h("pre", { class: "gap" }, Object.values(r.files)[0]))); }, err) }, "Build docs");
        const check = h("button", { class: "btn plain", onclick: () => busy(check, async () => {
          const r = await lapi(`/api/lab/projects/${p.id}/check`);
          const tone = { error: "bad", warn: "warn", info: "mute" };
          fill(out, h("div", { class: "card" }, h("div", { class: "row", style: "justify-content:space-between" }, h("b", {}, `Score ${r.score}/100`), h("span", { class: "meta" }, r.kind, r.python.works_on.length ? `Python ${r.python.works_on[0]}+` : "")), bar(r.score, "", r.score < 60 ? "bad" : r.score < 85 ? "warn" : "ok"),
            ...[["Compatibility", r.compatibility], ["Optimisation", r.optimisation]].map(([title, items]) => h("div", { class: "gap" }, h("div", { class: "label" }, title), items.length ? h("div", { class: "rows tight" }, items.map((i) => h("div", {}, h("div", { class: "row" }, chip(i.level, tone[i.level]), h("span", {}, i.text)), i.path || i.fix ? h("div", { class: "meta" }, i.path ? h("code", {}, i.path) : null, i.fix ? " " + i.fix : null) : null))) : h("div", { class: "meta" }, "Nothing to report.")))));
        }, err) }, "Check compatibility & optimisation");
        const ver = inp("0.1.0", () => {}, { style: "width:110px" });
        const pack = h("button", { class: "btn plain", onclick: () => busy(pack, async () => { const r = await lapi(`/api/lab/projects/${p.id}/package`, { version: ver.value }); fill(out, h("div", { class: "card" }, h("b", { class: "row", style: "gap:8px" }, sico("box", 16), r.path), h("div", { class: "meta" }, `${fmtB(r.bytes)} · ${r.files} files · added ${r.added.join(", ")}`, r.skipped.length ? `· left out ${r.skipped.length} (secrets, caches, logs)` : ""))); }, err) }, "Build package");
        const live = (await lapi(`/api/lab/deploys?project=${p.id}`));
        const mine = live.live.find((x) => x.project === p.id);
        const serve = h("button", { class: "btn", onclick: () => busy(serve, async () => { const r = mine ? (await lapi(`/api/lab/projects/${p.id}/serve/stop`, {}), null) : await lapi(`/api/lab/projects/${p.id}/serve`, {}); if (r) window.open(r.url, "_blank", "noopener"); paint(); }, err) }, mine ? "Stop localhost" : "Run on localhost");
        const cf = h("button", { class: "btn plain", onclick: () => busy(cf, async () => { const r = await lapi(`/api/lab/projects/${p.id}/deploy`, {}); fill(out, h("div", { class: "card" }, h("b", {}, "Deployed to Cloudflare"), r.url ? h("div", {}, h("a", { href: r.url, target: "_blank", rel: "noopener" }, r.url)) : null, h("pre", { class: "gap" }, r.output))); paint(); }, err) }, "Deploy to Cloudflare");
        fill(body, h("div", { class: "card stack" }, h("div", { class: "label" }, "Documentation from templates"),
          h("div", { class: "checks" }, Object.keys(kinds).map((k) => h("label", { class: "check" }, h("input", { type: "checkbox", checked: kinds[k], onchange: (e) => (kinds[k] = e.target.checked) }), { readme: "README", api: "API reference", guide: "User guide", changelog: "Changelog", contributing: "Contributing" }[k]))), h("div", {}, docs)),
          h("div", { class: "card stack" }, h("div", { class: "label" }, "Package & install"), h("div", { class: "row" }, check, h("span", { class: "meta" }, "version"), ver, pack), h("div", { class: "meta" }, "Packages leave out secrets, caches and logs, and include install.sh and install.ps1.")),
          h("div", { class: "card stack" }, h("div", { class: "label" }, "Deploy in one click"), h("div", { class: "row" }, serve, cf, mine ? h("a", { href: mine.url, target: "_blank", rel: "noopener" }, mine.url) : null),
            h("div", { class: "meta" }, "Cloudflare needs keys named cloudflare_api_token and cloudflare_account (Models  Keys) and Node.js."),
            live.history.length ? h("div", { class: "rows tight" }, live.history.slice(0, 5).map((d) => h("div", { class: "kv" }, h("span", {}, chip(d.target, "mute"), " ", d.url ? h("a", { href: d.url, target: "_blank", rel: "noopener" }, d.url) : ""), h("span", { class: "meta" }, chip(d.status, d.status === "live" ? "ok" : "bad"), ago(d.started))))) : null),
          err, out);
      } else if (pj.tab === "import") {
        const imps = (await lapi(`/api/lab/imports?project=${p.id}`)).imports;
        const err = errBox();
        const repo = inp("", () => {}, { placeholder: "owner/repo or a GitHub URL" }), hf = inp("", () => {}, { placeholder: "e.g. Qwen/Qwen2.5-0.5B-Instruct-GGUF" }), pat = inp("", () => {}, { placeholder: "*.gguf, config.json (optional)" });
        const kind = { v: "model" };
        const gh = h("button", { class: "btn", onclick: () => busy(gh, async () => { await lapi("/api/lab/imports", { source: "github", project: p.id, repo: repo.value }); setTimeout(paint, 800); }, err) }, "Import repo");
        const hfb = h("button", { class: "btn", onclick: () => busy(hfb, async () => { await lapi("/api/lab/imports", { source: "huggingface", project: p.id, repo_id: hf.value, repo_type: kind.v, patterns: pat.value ? pat.value.split(/[,\s]+/).filter(Boolean) : undefined }); setTimeout(paint, 800); }, err) }, "Import");
        fill(body, h("div", { class: "card stack" }, h("div", { class: "label" }, "GitHub"), h("div", { class: "row" }, h("div", { class: "grow" }, repo), gh)),
          h("div", { class: "card stack" }, h("div", { class: "label" }, "Hugging Face"), h("div", { class: "row" }, sel([["model", "Model"], ["dataset", "Dataset"]], kind.v, (v) => (kind.v = v), { style: "width:auto" }), h("div", { class: "grow" }, hf)), pat, h("div", {}, hfb),
            h("div", { class: "meta" }, "Files are checked against their published checksums and counted against the project's space. Connect accounts under Models for private repos.")), err,
          imps.length ? h("div", { class: "card" }, h("div", { class: "label" }, "Imports"), h("div", { class: "rows" }, imps.map((i) => h("div", {}, h("div", { class: "kv" }, h("span", {}, `${i.kind} · ${i.source}`), h("span", { class: "meta" }, chip(i.status, statusTone[i.status] || ""), ago(i.created))),
            bar(i.status === "succeeded" ? 100 : i.progress?.total ? (100 * i.progress.done) / i.progress.total : 0, i.progress?.current), i.error ? h("div", { class: "err" }, i.error) : null)))) : null);
        if (imps.some((i) => ["queued", "running"].includes(i.status))) setTimeout(() => current === "projects" && pj.tab === "import" && paint(), 1500);
      } else if (pj.tab === "settings") {
        const q = { name: p.name, quota_mb: Math.round(p.quota_bytes / 1e6) }, err = errBox();
        const save = h("button", { class: "btn", onclick: () => busy(save, async () => { await lapi(`/api/lab/projects/${p.id}`, q); await loadLab(["projects"]); renderPage(); }, err) }, t("save"));
        const clone = h("button", { class: "btn plain", onclick: () => busy(clone, async () => { const r = await lapi(`/api/lab/projects/${p.id}/clone`, {}); await loadLab(["projects"]); NAVSTATE.projects = r.id; renderPage(); }, err) }, "Duplicate");
        fill(body, h("div", { class: "card stack" }, h("div", { class: "optgrid" }, field("Name", inp(q.name, (v) => (q.name = v))), field("Space quota (MB)", inp(q.quota_mb, (v) => (q.quota_mb = v), { type: "number", min: 1, max: 50000 }))), h("div", { class: "row" }, save, clone), err),
          h("div", { class: "card row", style: "justify-content:space-between" }, h("span", {}, "Delete the project and all its files."), h("button", { class: "btn danger", onclick: async () => { if (!confirm(t("confirm_delete"))) return; await lapi(`/api/lab/projects/${p.id}/delete`, {}); NAVSTATE.projects = null; await loadLab(["projects"]); renderPage(); } }, t("delete"))));
      }
    } catch (e) { fill(body, h("div", { class: "card err" }, e.message)); }
  };
  paint();
  return h("div", { class: "stack" }, top, tabs(T, pj.tab, (v) => { pj.tab = v; renderPage(); }), body);
}
function pageProjects() {
  const list = h("div", { class: "list card" }), view = h("div");
  const paint = () => {
    fill(list, ...(LAB.projects.length ? LAB.projects.map((p) => h("button", { "aria-current": p.id === NAVSTATE.projects, onclick: () => { NAVSTATE.projects = p.id; pj.path = ""; pj.file = null; renderPage(); } },
      h("div", { class: "t" }, p.name), h("div", { class: "meta" }, `${fmtB(p.used_bytes)} / ${fmtB(p.quota_bytes)}`), bar(p.quota_bytes ? (100 * p.used_bytes) / p.quota_bytes : 0))) : [h("div", { class: "empty" }, "No projects yet.")]));
    const p = LAB.projects.find((x) => x.id === NAVSTATE.projects);
    if (p) return fill(view, projectDetail(p));
    const f = { name: "", quota_mb: 500, kind: "blank" }, err = errBox();
    const create = h("button", { class: "btn", onclick: () => busy(create, async () => { const r = await lapi("/api/lab/projects", f); await loadLab(["projects"]); NAVSTATE.projects = r.id; renderPage(); }, err) }, "Create project");
    fill(view, h("div", { class: "card stack" }, h("h3", { class: "card-title" }, "New project"), h("div", { class: "optgrid" }, field("Name", inp(f.name, (v) => (f.name = v), { placeholder: "My website" })), field("Space quota (MB)", inp(f.quota_mb, (v) => (f.quota_mb = v), { type: "number", min: 1 })),
      field("Kind", sel([["blank", "Blank"], ["website", "Website"], ["game", "Game"], ["model", "Model"], ["infra", "Infrastructure"]], f.kind, (v) => (f.kind = v)))), h("div", { class: "row" }, create, h("button", { class: "btn plain", onclick: () => go("templates") }, "Start from a template instead")), err));
  };
  loadLab(["projects", "agents", "secrets"]).then(() => current === "projects" && paint());
  return [...lhead("Projects", "Each project is a private workspace with its own space quota, environment and AI. Agents can only reach what is inside it."),
    h("div", { class: "split" }, h("div", {}, h("button", { class: "btn", style: "width:100%;margin-bottom:12px", onclick: () => { NAVSTATE.projects = null; paint(); } }, "+ New project"), list), view)];
}

/* ---------- jobs: progress, metrics, approvals, debugger, batches ---------- */
const jb = { list: [], conc: null, detail: null, trace: null, showTrace: false, tab: "jobs", batches: [] };
function jobForm() {
  const f = { project: NAVSTATE.jobProject || LAB.projects[0]?.id || "", agent: NAVSTATE.jobAgent || LAB.agents.find((a) => !a.locked)?.id || "", prompt: "", step_mode: false, autonomous: false, deadline_minutes: "" };
  const err = errBox();
  const ta = h("textarea", { style: "min-height:110px;font-family:var(--font);font-size:.95rem", placeholder: "Describe the goal. You can also type a /command such as /review src/app.py", oninput: (e) => (f.prompt = e.target.value) });
  const start = h("button", { class: "btn", onclick: () => busy(start, async () => {
    let prompt = f.prompt.trim();
    if (prompt.startsWith("/")) {
      const x = await lapi("/api/lab/commands/expand", { line: prompt });
      if (x.kind === "shell") { const r = await lapi(`/api/lab/projects/${f.project}/command`, { line: prompt }); fill(err, h("pre", {}, `$ ${x.command}\n${r.output}\n(exit ${r.exit_code})`)); return; }
      prompt = x.text;
    }
    const j = await lapi("/api/lab/jobs", { project: f.project, agent: f.agent, prompt, step_mode: f.step_mode, autonomous: f.autonomous, deadline_minutes: f.deadline_minutes || undefined });
    NAVSTATE.jobs = j.id; renderPage();
  }, err) }, "Start");
  if (!LAB.projects.length || !LAB.agents.length) return h("div", { class: "card empty" }, h("b", {}, "You need an agent and a project first."), h("div", { class: "row gap", style: "justify-content:center" }, h("button", { class: "btn", onclick: () => go("agents", { agents: "new" }) }, "New agent"), h("button", { class: "btn plain", onclick: () => go("templates") }, "New project")));
  return h("div", { class: "card stack" }, h("h3", { class: "card-title" }, "New job"),
    h("div", { class: "optgrid" }, field("Agent", sel(agentOpts(), f.agent, (v) => (f.agent = v))), field("Project", sel(projectOpts(), f.project, (v) => (f.project = v)))),
    field("Goal", ta),
    field("Finish within (minutes, optional)", inp(f.deadline_minutes, (v) => (f.deadline_minutes = v), { type: "number", min: 1, placeholder: "no deadline" }),
      "The agent is told its time budget, plans backwards from it and finishes with what is done rather than being cut off."),
    h("div", { class: "row" }, tog(f.step_mode, (v) => (f.step_mode = v), "Debug step by step"), tog(f.autonomous, (v) => (f.autonomous = v), "Autonomous (no one is watching)")),
    h("div", { class: "row" }, start), err);
}
function approvalCard(j) {
  const a = j.pending;
  const decide = (allow, always) => lapi(`/api/lab/jobs/${j.id}/approve`, { approval: a.id, allow, always }).then(() => setTimeout(loadJob, 200)).catch((e) => toast(e.message, "bad"));
  return h("div", { class: "card approval" }, h("div", { class: "row", style: "justify-content:space-between" }, h("b", { class: "row", style: "gap:8px" }, sico("warn", 17), "The agent asks for permission"), chip(a.category, "warn")),
    h("div", { class: "gap" }, h("code", {}, a.tool), " ", h("pre", { class: "gap" }, JSON.stringify(a.arguments, null, 2))),
    a.risk?.length ? h("div", { class: "err" }, a.risk.join(" · ")) : null,
    h("div", { class: "row gap" }, h("button", { class: "btn", onclick: () => decide(true, false) }, "Allow once"), h("button", { class: "btn plain", onclick: () => decide(true, true) }, "Always allow in this job"), h("button", { class: "btn danger", onclick: () => decide(false, false) }, "Deny")));
}
function debugCard(j) {
  const a = j.pending, args = h("textarea", { style: "min-height:90px", value: JSON.stringify(a.arguments || {}, null, 2), spellcheck: "false" });
  const act_ = (action, edit) => {
    let body = { action };
    if (edit) { try { body.arguments = JSON.parse(args.value); } catch { return toast("The arguments are not valid JSON", "bad"); } }
    lapi(`/api/lab/jobs/${j.id}/debug`, body).then(() => setTimeout(loadJob, 250)).catch((e) => toast(e.message, "bad"));
  };
  return h("div", { class: "card debug" }, h("div", { class: "row", style: "justify-content:space-between" }, h("b", { class: "row", style: "gap:8px" }, sico("pause", 16), `Paused before ${a.at === "tool" ? "a tool call" : "the next model call"} · step ${a.step}`), chip("debugger", "warn")),
    a.at === "tool" ? h("div", { class: "stack gap" }, h("div", {}, h("code", {}, a.tool)), field("Arguments (edit them before it runs)", args)) : h("div", { class: "meta gap" }, `${a.messages} messages, about ${fmtN(a.context_tokens)} tokens of context.`),
    h("div", { class: "row gap" }, h("button", { class: "btn", onclick: () => act_("step", a.at === "tool") }, a.at === "tool" ? "Run this step" : "Step"),
      a.at === "tool" ? h("button", { class: "btn plain", onclick: () => act_("skip") }, "Skip this tool call") : null,
      h("button", { class: "btn plain", onclick: () => act_("continue", a.at === "tool") }, "Continue to the end")));
}
function traceView(trace) {
  if (!trace?.length) return h("div", { class: "meta" }, "No steps yet.");
  return h("div", { class: "trace" }, trace.map((e) => h("div", { class: "tr " + e.phase },
    h("div", { class: "n" }, e.step), h("div", {}, chip(e.phase === "model" ? "model" : e.tool, e.phase === "model" ? "" : e.skipped ? "mute" : e.edited ? "warn" : "ok"), h("div", { class: "t" }, `${e.ms} ms`, e.phase === "model" ? ` · ${fmtN(e.input_tokens)}${fmtN(e.output_tokens)} tok` : "")),
    h("div", { class: "c" }, e.phase === "model" ? [e.text || "", e.tool_calls?.length ? h("div", { class: "meta" }, "calls: " + e.tool_calls.map((c) => c.name).join(", ")) : null] : [h("div", { class: "mono" }, JSON.stringify(e.arguments)), h("div", { class: "res" }, e.result)]))));
}
async function loadJob() {
  const id = NAVSTATE.jobs;
  if (!id || id === "new") return;
  try { jb.detail = await lapi(`/api/lab/jobs/${id}?events=80`); if (jb.showTrace) jb.trace = (await lapi(`/api/lab/jobs/${id}/trace`)).trace; } catch { jb.detail = null; }
  if (current === "jobs") paintJob();
}
function paintJob() {
  const box = $("jobView");
  if (!box) return;
  const j = jb.detail;
  if (NAVSTATE.jobs === "new" || !j) return fill(box, jobForm());
  const m = j.metrics, live = LIVE.includes(j.status);
  const tile = (num, lab, tone = "") => h("div", { class: "tile " + tone }, h("div", { class: "num" }, num), h("div", { class: "lab" }, lab));
  const fb = (r) => lapi(`/api/lab/jobs/${j.id}/feedback`, { rating: r }).then(() => { toast("Thanks: the agent learns from this", "ok"); loadJob(); }).catch((e) => toast(e.message, "bad"));
  fill(box, 
    h("div", { class: "card" }, h("div", { class: "row", style: "justify-content:space-between;flex-wrap:nowrap" }, h("div", { style: "min-width:0" }, h("div", { class: "meta" }, `${j.agent_name} · ${j.project_name}${j.autonomous ? " · autonomous" : ""}${j.schedule ? " · scheduled" : ""}`), h("b", {}, j.prompt.slice(0, 200))),
      h("div", { class: "row", style: "flex-wrap:nowrap" }, chip(j.status, statusTone[j.status]), live ? h("button", { class: "btn danger", onclick: () => lapi(`/api/lab/jobs/${j.id}/cancel`, {}).then(loadJob) }, t("cancel")) : null)),
      h("div", { class: "gap" }, bar(j.progress.percent, j.progress.note, j.status === "failed" ? "bad" : "grad")), h("div", { class: "meta", style: "margin-top:6px" }, `${j.progress.percent}% · step ${j.progress.step} of ${j.progress.max_steps}`, j.progress.note ? `· ${j.progress.note}` : "",
        j.deadline ? chip(live ? `${Math.max(0, Math.round((j.deadline - Date.now() / 1000) / 60))} min left` : "had a deadline", live && j.deadline - Date.now() / 1000 < 120 ? "warn" : "mute") : null)),
    j.status === "waiting_approval" && j.pending ? approvalCard(j) : null,
    j.status === "paused" && j.pending ? debugCard(j) : null,
    h("div", { class: "tiles" }, tile(fmtN(m.input_tokens + m.output_tokens), "tokens"), tile(money(m.cost), "cost"), tile(m.llm_calls, "model calls"), tile(m.tool_calls, "tool calls"), tile(m.files_written, "files written"),
      tile(m.approvals_asked, "approvals"), tile(m.blocked, "blocked", m.blocked ? "warn" : ""), tile(m.tool_errors, "tool errors", m.tool_errors ? "bad" : ""), tile(`${m.duration_s || 0}s`, "time"), m.teammate_calls ? tile(m.teammate_calls, "teammate answers") : null),
    j.summary || j.error || j.stop_reason ? h("div", { class: "card" }, h("div", { class: "label" }, j.error ? "Error" : j.stop_reason ? "Stopped" : "Result"), h("div", { class: j.error ? "err" : "" }, j.summary || j.error || j.stop_reason),
      !live ? h("div", { class: "row gap" }, h("span", { class: "meta" }, "How did it do?"), h("button", { class: "btn plain", "aria-pressed": j.feedback?.rating === 1, onclick: () => fb(1), "aria-label": "Good" }, sico("up", 16)), h("button", { class: "btn plain", "aria-pressed": j.feedback?.rating === -1, onclick: () => fb(-1), "aria-label": "Poor" }, sico("down", 16))) : null) : null,
    h("div", { class: "card" }, h("div", { class: "row", style: "justify-content:space-between" }, h("div", { class: "label", style: "margin:0" }, jb.showTrace ? "Debugger trace" : "Events"),
      h("div", { class: "row" }, live ? h("button", { class: "btn plain", onclick: () => lapi(`/api/lab/jobs/${j.id}/step-mode`, { on: !j.debug?.step_mode }).then(loadJob) }, j.debug?.step_mode ? "Stop stepping" : "Pause & step") : null,
        h("button", { class: "btn plain", onclick: () => { jb.showTrace = !jb.showTrace; loadJob(); } }, jb.showTrace ? "Show events" : "Show debugger trace"))),
      jb.showTrace ? traceView(jb.trace) : h("div", {}, (j.events || []).slice(1).map((e) => h("div", { class: "ev" }, h("div", { class: "n" }, e.step), h("div", {}, chip(e.kind.replace("_", " "), kindTone[e.kind] ?? "")), h("div", { class: "c" }, String(e.content).slice(0, 3000)))))));
}
function batchesView() {
  const box = h("div", { class: "stack" });
  const f = { kind: "compare", project: LAB.projects[0]?.id || "", agents: [], agent: LAB.agents[0]?.id || "", prompt: "", prompts: "", isolate: true }, err = errBox();
  const paint = async () => {
    try { jb.batches = (await lapi("/api/lab/batches")).batches; } catch {}
    const start = h("button", { class: "btn", onclick: () => busy(start, async () => {
      await lapi("/api/lab/batches", { ...f, prompts: f.prompts.split("\n").map((x) => x.trim()).filter(Boolean) }); paint();
    }, err) }, "Start batch");
    fill(box, h("div", { class: "card stack" }, h("div", { class: "row" }, seg([["compare", "Compare agents on one task"], ["fanout", "Fan out many tasks"]], f.kind, (v) => { f.kind = v; paint(); }, true)),
      field("Project", sel(projectOpts(), f.project, (v) => (f.project = v))),
      f.kind === "compare" ? field("Agents (2 or more)", h("div", { class: "checks" }, LAB.agents.filter((a) => !a.locked).map((a) => h("label", { class: "check" }, h("input", { type: "checkbox", checked: f.agents.includes(a.id), onchange: (e) => { f.agents = e.target.checked ? [...f.agents, a.id] : f.agents.filter((x) => x !== a.id); } }), a.name))))
        : field("Agent", sel(agentOpts(), f.agent, (v) => (f.agent = v))),
      f.kind === "compare" ? field("Task", inp(f.prompt, (v) => (f.prompt = v), { placeholder: "The same task for every agent" })) : field("Tasks (one per line)", h("textarea", { style: "min-height:90px;font-family:var(--font)", value: f.prompts, oninput: (e) => (f.prompts = e.target.value) })),
      tog(f.isolate, (v) => (f.isolate = v), "Each job works on its own copy of the project"), h("div", {}, start), err),
      ...jb.batches.map((b) => h("div", { class: "card" }, h("div", { class: "row", style: "justify-content:space-between" }, h("b", {}, b.name), h("span", { class: "meta" }, chip(b.kind, "mute"), `${b.progress.done}/${b.progress.total} done`)), bar(b.progress.percent, "", "grad"),
        h("div", { class: "rows tight gap" }, b.items.map((r) => h("div", { class: "kv" }, h("button", { class: "linkish", onclick: () => r.job && go("jobs", { jobs: r.job }) }, `${r.agent_name || r.agent} · ${r.prompt.slice(0, 50)}`),
          h("span", { class: "meta" }, chip(r.status, statusTone[r.status]), r.tokens != null ? `${fmtN(r.tokens)} tok · ${r.duration_s}s` : "", r.job && r.job === b.fastest ? chip("fastest", "ok") : null, r.job && r.job === b.cheapest ? chip("cheapest", "ok") : null)))))));
    if (jb.batches.some((b) => !b.finished) && current === "jobs" && jb.tab === "batches") setTimeout(paint, 2000);
  };
  paint();
  return box;
}
async function loadJobs() {
  try { const r = await lapi("/api/lab/jobs"); jb.list = r.jobs; jb.conc = r.concurrency; } catch { return; }
  const list = $("jobList");
  if (!list) return;
  fill(list, ...(jb.list.length ? jb.list.slice(0, 60).map((j) => h("button", { "aria-current": j.id === NAVSTATE.jobs, onclick: () => { NAVSTATE.jobs = j.id; jb.trace = null; loadJob(); loadJobs(); } },
    h("div", { class: "t" }, j.prompt.slice(0, 60)), h("div", { class: "meta" }, chip(j.status, statusTone[j.status]), j.agent_name), LIVE.includes(j.status) ? bar(j.progress.percent, "", "grad") : null)) : [h("div", { class: "empty" }, "No jobs yet.")]));
  const c = $("concBox");
  if (c && jb.conc) fill(c, h("span", { class: "meta" }, `${jb.conc.running} running · ${jb.conc.queued} queued · parallel limit`),
    h("input", { type: "number", min: 1, max: 64, value: jb.conc.limit, style: "width:70px", onchange: (e) => lapi("/api/lab/concurrency", { limit: +e.target.value }).then(loadJobs).catch((x) => toast(x.message, "bad")) }));
}
function pageJobs() {
  const view = h("div", { id: "jobView", class: "stack" });
  loadLab(["agents", "projects"]).then(() => { if (current !== "jobs") return; if (jb.tab === "jobs") { if (!NAVSTATE.jobs) NAVSTATE.jobs = "new"; loadJob(); paintJob(); } });
  loadJobs();
  poll = setInterval(() => { loadJobs(); if (jb.tab === "jobs" && jb.detail && LIVE.includes(jb.detail.status)) loadJob(); }, 1500);
  return [...lhead("Jobs", "Agents work in the background: watch progress and metrics, approve risky steps, debug step by step, and run many jobs in parallel."),
    h("div", { class: "row", style: "justify-content:space-between;margin-bottom:12px" }, tabs([["jobs", "Jobs"], ["batches", "Parallel batches"]], jb.tab, (v) => { jb.tab = v; renderPage(); }), h("div", { class: "row", id: "concBox" })),
    jb.tab === "batches" ? batchesView() : h("div", { class: "split" }, h("div", {}, h("button", { class: "btn", style: "width:100%;margin-bottom:12px", onclick: () => { NAVSTATE.jobs = "new"; NAVSTATE.jobAgent = NAVSTATE.jobProject = null; paintJob(); } }, "+ New job"), h("div", { class: "list card", id: "jobList" })), view)];
}

/* ---------- schedules ---------- */
function pageSchedules() {
  const box = h("div", { class: "stack" });
  const f = { project: "", agent: "", prompt: "", how: "every", every_minutes: 60, cron: "0 9 * * 1-5", max_runs: "", run: "inherit" }, err = errBox();
  const paint = async () => {
    await loadLab(["agents", "projects"]);
    f.project ||= LAB.projects[0]?.id || ""; f.agent ||= LAB.agents[0]?.id || "";
    const list = (await lapi("/api/lab/schedules")).schedules;
    const create = h("button", { class: "btn", onclick: () => busy(create, async () => {
      await lapi("/api/lab/schedules", { project: f.project, agent: f.agent, prompt: f.prompt, ...(f.how === "every" ? { every_minutes: f.every_minutes } : { cron: f.cron }), tz_minutes: -new Date().getTimezoneOffset(),
        max_runs: f.max_runs || undefined, overrides: f.run !== "inherit" ? { run: f.run } : undefined }); f.prompt = ""; paint();
    }, err) }, "Schedule");
    fill(box, h("div", { class: "card stack" }, h("h3", { class: "card-title" }, "New schedule"),
      h("div", { class: "optgrid" }, field("Agent", sel(agentOpts(), f.agent, (v) => (f.agent = v))), field("Project", sel(projectOpts(), f.project, (v) => (f.project = v)))),
      field("Task", inp(f.prompt, (v) => (f.prompt = v), { placeholder: "Check the site for broken links and fix them" })),
      h("div", { class: "row" }, seg([["every", "Every N minutes"], ["cron", "Cron"]], f.how, (v) => { f.how = v; paint(); }, true),
        f.how === "every" ? inp(f.every_minutes, (v) => (f.every_minutes = v), { type: "number", min: 1, style: "width:110px" }) : inp(f.cron, (v) => (f.cron = v), { style: "width:180px", placeholder: "0 9 * * 1-5" }),
        h("span", { class: "meta" }, f.how === "cron" ? "minute hour day month weekday (your local time)" : "minutes")),
      h("div", { class: "optgrid" }, field("Stop after N runs", inp(f.max_runs, (v) => (f.max_runs = v), { type: "number", min: 1, placeholder: "never" })),
        field("Commands while unattended", sel([["inherit", "As the agent's mode says"], ["allow", "Pre-approve commands"], ["deny", "Never run commands"]], f.run, (v) => (f.run = v)))),
      h("div", { class: "meta" }, "Unattended runs never catch up after downtime, never overlap, and pause after 3 failures in a row. Anything that still needs approval waits for you."),
      h("div", {}, create), err),
      list.length ? h("div", { class: "card" }, h("div", { class: "rows" }, list.map((s_) => h("div", {}, h("div", { class: "kv" }, h("div", {}, h("b", {}, s_.name), h("div", { class: "meta" }, s_.cron ? h("code", {}, s_.cron) : `every ${s_.every_minutes} min`, `· ${s_.runs} runs`, s_.next_run && s_.enabled ? `· next ${new Date(s_.next_run * 1000).toLocaleString(S.lang || undefined)}` : "", s_.note ? chip(s_.note, "warn") : null)),
        h("div", { class: "row", style: "flex-wrap:nowrap" }, h("input", { type: "checkbox", class: "switch", checked: s_.enabled, "aria-label": "Enabled", onchange: (e) => lapi(`/api/lab/schedules/${s_.id}`, { enabled: e.target.checked }).then(paint) }),
          s_.last_job ? h("button", { class: "btn plain", onclick: () => go("jobs", { jobs: s_.last_job }) }, "Last run") : null,
          h("button", { class: "btn danger", onclick: () => lapi(`/api/lab/schedules/${s_.id}/delete`, {}).then(paint) }, "\u00d7"))))))) : h("div", { class: "card empty" }, "No schedules yet."));
  };
  paint();
  return [...lhead("Schedules", "Let agents work while you are away: every N minutes or on a cron schedule, with guardrails for unattended runs."), box];
}

/* ---------- widgets ---------- */
function showWidgetFrame(frame, html) {
  const onMsg = (e) => { if (e.source === frame.contentWindow && e.data?.widgetReady) { removeEventListener("message", onMsg); frame.contentWindow.postMessage(html, "*"); } };
  addEventListener("message", onMsg);
  frame.src = "/static/widget.html";
  return frame;
}
function renderWidget(w, send) {
  const card = h("div", { class: "widget" }, w.title ? h("div", { class: "label" }, w.title) : null);
  const mx = (arr) => Math.max(1, ...arr);
  if (w.type === "chart") {
    if (w.kind === "pie") {
      const vals = w.series[0].data, total = vals.reduce((a, b) => a + b, 0) || 1;
      const svg = svgEl("svg", { viewBox: "-1.1 -1.1 2.2 2.2", class: "pie" });
      let a0 = -Math.PI / 2;
      vals.forEach((v, i) => { const a1 = a0 + (2 * Math.PI * v) / total; const large = a1 - a0 > Math.PI ? 1 : 0;
        const path = svgEl("path", { d: `M0 0 L${Math.cos(a0)} ${Math.sin(a0)} A1 1 0 ${large} 1 ${Math.cos(a1)} ${Math.sin(a1)} Z`, style: `fill:hsl(${(i * 57) % 360} 70% 55%)` });
        const tt = svgEl("title", {}); tt.textContent = `${w.labels[i]}: ${v}`; path.append(tt); svg.append(path); a0 = a1; });
      card.append(h("div", { class: "row" }, svg, h("div", { class: "legend" }, w.labels.map((l, i) => h("div", {}, h("i", { style: `background:hsl(${(i * 57) % 360} 70% 55%)` }), `${l} · ${vals[i]}`)))));
    } else if (w.kind === "line") {
      card.append(lineChart(w.series.map((s, i) => ({ name: s.name, data: s.data, cls: "s" + (i % 4) })), [w.labels[0], w.labels.at(-1)]), h("div", { class: "legend row" }, w.series.map((s, i) => h("div", {}, h("i", { class: "s" + (i % 4) }), s.name))));
    } else {
      const m = mx(w.series.flatMap((s) => s.data));
      card.append(h("div", { class: "hbars" }, w.labels.map((l, i) => h("div", { class: "hb" }, h("span", { class: "l" }, l), h("div", { class: "grow" }, w.series.map((s, k) => h("div", { class: "b s" + (k % 4), style: `width:${(100 * s.data[i]) / m}%`, title: `${s.name}: ${s.data[i]}` }))), h("span", { class: "v" }, w.series.map((s) => fmtN(s.data[i])).join(" / "))))));
    }
  } else if (w.type === "table") card.append(h("div", { class: "tablewrap" }, h("table", {}, h("tr", {}, w.columns.map((c) => h("th", {}, c))), w.rows.map((r) => h("tr", {}, r.map((c) => h("td", {}, c)))))));
  else if (w.type === "stats") card.append(h("div", { class: "tiles" }, w.items.map((i) => h("div", { class: "tile" }, h("div", { class: "num" }, i.value), h("div", { class: "lab" }, i.label, i.hint ? ` · ${i.hint}` : "")))));
  else if (w.type === "buttons") card.append(h("div", { class: "row" }, w.options.map((o) => h("button", { class: "btn plain", onclick: () => send?.(o.send) }, o.label))));
  else if (w.type === "form") {
    const vals = Object.fromEntries(w.fields.map((f) => [f.name, f.default || ""]));
    card.append(h("div", { class: "stack" }, w.fields.map((f) => field(f.label, f.kind === "select" ? sel(f.options, vals[f.name], (v) => (vals[f.name] = v)) : f.kind === "checkbox" ? h("input", { type: "checkbox", class: "switch", onchange: (e) => (vals[f.name] = e.target.checked ? "yes" : "no") }) : inp(vals[f.name], (v) => (vals[f.name] = v), { type: f.kind === "number" ? "number" : "text" }))),
      h("div", {}, h("button", { class: "btn", onclick: () => send?.(w.submit.template.replace(/\{(\w+)\}/g, (_, k) => vals[k] ?? "")) }, w.submit.label))));
  } else if (w.type === "progress") card.append(h("div", { class: "stack" }, w.items.map((i) => h("div", {}, h("div", { class: "kv meta" }, h("span", {}, i.label), h("span", {}, `${Math.round(i.percent)}%`)), bar(i.percent, "", "grad")))));
  else if (w.type === "checklist") card.append(h("div", {}, w.items.map((i) => h("label", { class: "check" }, h("input", { type: "checkbox", checked: i.done, onchange: (e) => (i.done = e.target.checked) }), i.text))));
  else if (w.type === "markdown") card.append(h("div", { class: "md" }, w.text));
  else if (w.type === "code") card.append(h("pre", {}, w.code));
  else if (w.type === "html") card.append(showWidgetFrame(h("iframe", { sandbox: "allow-scripts", class: "wframe", title: w.title || "Widget", loading: "lazy" }), w.html));
  const saveBtn = h("button", { class: "btn plain", onclick: () => busy(saveBtn, async () => { await lapi("/api/lab/widgets", { spec: w }); toast("Widget saved to your library", "ok"); }) }, "Save widget");
  card.append(h("div", { class: "row wact" }, saveBtn));
  return card;
}

/* ---------- playground: chat, compare, attachments, widgets, insights ---------- */
const pg = { targets: [{ kind: "agent", agent_id: "", pv: { preset: "mock" } }], compare: false, messages: [], replies: [], attachments: [], system: "", max_tokens: 1024, temperature: "", widgets: true, sending: false, test: null };
function targetSpec(tg) { return tg.kind === "agent" && tg.agent_id ? { agent_id: tg.agent_id } : { provider: toProvider(tg.pv) }; }
function targetLabel(tg) { return tg.kind === "agent" && tg.agent_id ? LAB.agents.find((a) => a.id === tg.agent_id)?.name || "agent" : (tg.pv.model || LAB.presets?.providers?.[tg.pv.preset]?.label || tg.pv.preset); }
function pagePlayground() {
  const chatBox = h("div", { class: "chat" }), setup = h("div", { class: "stack" }), err = errBox();
  const input = h("textarea", { class: "composer", placeholder: "Message… (Enter to send, Shift+Enter for a new line). Try: “make me a pomodoro timer”", oninput: () => {} });
  const files = h("div", { class: "row" });
  const fileIn = h("input", { type: "file", multiple: true, hidden: true, onchange: async (e) => {
    for (const f of e.target.files) { if (f.size > 100000) { toast(`${f.name} is too large (100 kB max per file)`, "bad"); continue; } pg.attachments.push({ name: f.name, content: await f.text() }); }
    paintFiles(); fileIn.value = "";
  } });
  const paintFiles = () => fill(files, ...pg.attachments.map((a, i) => h("button", { class: "chip mute", title: "Remove", onclick: () => { pg.attachments.splice(i, 1); paintFiles(); } }, `${a.name}  \u00d7`)));
  const insightsRow = (ins) => h("div", { class: "insights" }, `${fmtN(ins.input_tokens)} in · ${fmtN(ins.output_tokens)} out`, `${ins.latency_ms} ms`, `${ins.tokens_per_second} tok/s`, money(ins.cost), ins.context.window ? `context ${ins.context.percent}%` : null);
  const paintChat = () => {
    fill(chatBox, ...(pg.messages.length ? pg.messages.map((m, i) => {
      if (m.role === "user") return h("div", { class: "msg user" }, h("div", { class: "bubble" }, m.content), m.files?.length ? h("div", { class: "meta" }, m.files.join(" · ")) : null);
      const r = pg.replies[i];
      if (!r) return null;
      const one = (x) => h("div", { class: "msg ai" }, h("div", { class: "who" }, x.label || x.model || ""), x.ok === false ? h("div", { class: "bubble err" }, x.error) : h("div", { class: "bubble" }, x.text || (x.widgets?.length ? "" : "…")),
        ...(x.widgets || []).map((w) => renderWidget(w, (text) => send(text))), x.widget_errors?.length ? h("div", { class: "meta" }, x.widget_errors.join(" · ")) : null, x.insights ? insightsRow(x.insights) : null);
      return r.results ? h("div", { class: "compare" }, r.results.map((x) => h("div", { class: x.label === r.fastest ? "best" : "" }, one(x), x.label === r.fastest ? chip("fastest", "ok") : null, x.label === r.cheapest ? chip("cheapest", "ok") : null))) : one(r);
    }) : [h("div", { class: "empty" }, h("b", {}, "Start a conversation"), "Pick any agent or model, attach text files, and ask for charts, tables, forms or small apps: they appear as live widgets.")]),
      pg.sending ? h("div", { class: "msg ai" }, h("div", { class: "bubble typing" }, h("i"), h("i"), h("i"))) : null);
    chatBox.scrollTop = chatBox.scrollHeight;
  };
  const send = async (text) => {
    text = (text ?? input.value).trim();
    if (!text || pg.sending) return;
    input.value = "";
    const history = [];
    pg.messages.forEach((m, i) => { if (m.role === "user") history.push({ role: "user", content: m.content }); else { const r = pg.replies[i]; const x = r?.results ? r.results.find((y) => y.ok) : r; if (x?.text != null) history.push({ role: "assistant", content: x.text || "(widget)" }); } });
    history.push({ role: "user", content: text });
    pg.messages.push({ role: "user", content: text, files: pg.attachments.map((a) => a.name) });
    const base = { messages: history, attachments: pg.attachments, widgets: pg.widgets, system: pg.system || undefined, max_tokens: pg.max_tokens, temperature: pg.temperature === "" ? undefined : +pg.temperature };
    pg.attachments = []; paintFiles();
    pg.sending = true; paintChat();
    try {
      const r = pg.compare && pg.targets.length > 1 ? await lapi("/api/lab/playground/compare", { ...base, targets: pg.targets.map((tg) => ({ ...targetSpec(tg), label: targetLabel(tg) })) })
        : { ...(await lapi("/api/lab/playground/chat", { ...base, ...targetSpec(pg.targets[0]) })), label: targetLabel(pg.targets[0]) };
      pg.messages.push({ role: "assistant" }); pg.replies[pg.messages.length - 1] = r;
    } catch (e) { pg.messages.push({ role: "assistant" }); pg.replies[pg.messages.length - 1] = { ok: false, error: e.message }; }
    pg.sending = false; paintChat();
  };
  input.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } });
  const paintSetup = () => {
    const testBtn = h("button", { class: "btn plain", onclick: () => busy(testBtn, async () => {
      const tg = pg.targets[0];
      const prov = tg.kind === "agent" && tg.agent_id ? (await lapi(`/api/lab/agents/${tg.agent_id}`)).provider : toProvider(tg.pv);
      pg.test = await lapi("/api/lab/models/test", { provider: prov }); paintSetup();
    }, err) }, "Test this model");
    fill(setup, h("div", { class: "card stack" },
      h("div", { class: "row", style: "justify-content:space-between" }, h("div", { class: "label", style: "margin:0" }, pg.compare ? "Models to compare" : "Talk to"), tog(pg.compare, (v) => { pg.compare = v; if (v && pg.targets.length < 2) pg.targets.push({ kind: "model", agent_id: "", pv: { preset: "mock" } }); paintSetup(); }, "Compare on the same prompt")),
      ...pg.targets.map((tg, i) => h("div", { class: "target" }, h("div", { class: "row" }, seg([["agent", "Agent"], ["model", "Model"]], tg.kind, (v) => { tg.kind = v; paintSetup(); }, true),
        tg.kind === "agent" ? h("div", { class: "grow" }, sel(agentOpts("Choose an agent"), tg.agent_id, (v) => (tg.agent_id = v))) : null,
        pg.compare && pg.targets.length > 2 ? h("button", { class: "btn danger", onclick: () => { pg.targets.splice(i, 1); paintSetup(); } }, "\u00d7") : null),
        tg.kind === "model" ? providerEditor(tg.pv) : null)),
      pg.compare && pg.targets.length < 8 ? h("button", { class: "btn plain", onclick: () => { pg.targets.push({ kind: "model", agent_id: "", pv: { preset: "mock" } }); paintSetup(); } }, "+ Add a model") : null,
      h("details", {}, h("summary", {}, "Options"), h("div", { class: "stack gap" },
        field("System prompt", h("textarea", { style: "min-height:60px;font-family:var(--font)", value: pg.system, oninput: (e) => (pg.system = e.target.value) })),
        h("div", { class: "optgrid" }, field("Max tokens per answer", inp(pg.max_tokens, (v) => (pg.max_tokens = v), { type: "number", min: 16, max: 32768 })), field("Temperature", inp(pg.temperature, (v) => (pg.temperature = v), { type: "number", min: 0, max: 2, step: 0.1, placeholder: "default" }))),
        tog(pg.widgets, (v) => (pg.widgets = v), "Allow widgets in answers"))),
      h("div", { class: "row" }, testBtn, h("button", { class: "btn plain", onclick: () => { pg.messages = []; pg.replies = []; paintChat(); } }, "New chat")), err,
      pg.test ? h("div", { class: "card", style: "box-shadow:none;background:var(--fill)" }, h("div", { class: "row", style: "justify-content:space-between" }, h("b", {}, `Test battery · ${pg.test.summary.passed}/${pg.test.summary.total} passed`), h("span", { class: "meta" }, pg.test.summary.avg_ms ? `${pg.test.summary.avg_ms} ms avg` : "")),
        h("div", { class: "rows tight" }, pg.test.tests.map((x) => h("div", { class: "kv" }, h("span", {}, x.name), h("span", { class: "meta" }, chip(x.status, { pass: "ok", fail: "bad", error: "bad", skip: "mute" }[x.status]), x.detail.slice(0, 60))))),
        h("div", { class: "meta gap" }, `Recommended tool mode: ${pg.test.recommendation.tool_mode}. `, pg.test.recommendation.notes.join(" "))) : null));
  };
  loadLab(["agents", "presets", "secrets"]).then(() => { if (current === "playground") { paintSetup(); } });
  paintSetup(); paintChat();
  return [...lhead("Playground", "Chat with any model or agent, compare several on the same prompt, attach files, and get live widgets. Every answer shows its tokens, speed and cost."),
    h("div", { class: "playground" }, setup, h("div", { class: "card chatcard" }, chatBox, files, h("div", { class: "composer-row" }, h("button", { class: "icon", title: "Attach text files", "aria-label": "Attach files", onclick: () => fileIn.click() }, sico("clip", 19)), fileIn, input, h("button", { class: "btn", onclick: () => send() }, "Send"))))];
}

/* ---------- rooms: agents talk and cooperate ---------- */
function pageRooms() {
  const list = h("div", { class: "list card" }), view = h("div", { class: "stack" });
  const paintList = async () => {
    const r = await lapi("/api/lab/rooms").catch(() => ({ rooms: [] }));
    fill(list, ...(r.rooms.length ? r.rooms.map((x) => h("button", { "aria-current": x.id === NAVSTATE.rooms, onclick: () => { NAVSTATE.rooms = x.id; paintList(); paintRoom(); } }, h("div", { class: "t" }, x.name), h("div", { class: "meta" }, chip(x.mode, "mute"), `${x.messages} messages`, chip(x.status, statusTone[x.status])))) : [h("div", { class: "empty" }, "No rooms yet.")]));
  };
  const newRoom = () => {
    const f = { name: "", agents: [], mode: "task", goal: "", manager: "", max_turns: 10 }, err = errBox();
    const create = h("button", { class: "btn", onclick: () => busy(create, async () => { const r = await lapi("/api/lab/rooms", { ...f, manager: f.manager || undefined }); NAVSTATE.rooms = r.id; await paintList(); paintRoom(); }, err) }, "Create room");
    fill(view, h("div", { class: "card stack" }, h("h3", { class: "card-title" }, "New room"), field("Name", inp(f.name, (v) => (f.name = v), { placeholder: "Launch team" })),
      field("Agents (2–8)", h("div", { class: "checks" }, LAB.agents.filter((a) => !a.locked).map((a) => h("label", { class: "check" }, h("input", { type: "checkbox", onchange: (e) => { f.agents = e.target.checked ? [...f.agents, a.id] : f.agents.filter((x) => x !== a.id); } }), a.name)))),
      h("div", { class: "optgrid" }, field("Mode", sel([["task", "Cooperate on a goal"], ["chat", "Free conversation"]], f.mode, (v) => (f.mode = v))), field("Manager (speaks first and last)", sel(agentOpts("No manager"), f.manager, (v) => (f.manager = v)))),
      field("Goal", h("textarea", { style: "min-height:70px;font-family:var(--font)", placeholder: "Plan the launch of our app: name, tagline and a 3-step plan.", oninput: (e) => (f.goal = e.target.value) })),
      field("Max turns per run", inp(f.max_turns, (v) => (f.max_turns = v), { type: "number", min: 1, max: 60 })), h("div", {}, create), err,
      h("div", { class: "meta" }, "Agents in a room only talk: no tools run. Turn the result into a job when you like it.")));
  };
  const paintRoom = async () => {
    if (!NAVSTATE.rooms) return newRoom();
    let r;
    try { r = await lapi(`/api/lab/rooms/${NAVSTATE.rooms}`); } catch { NAVSTATE.rooms = null; return newRoom(); }
    const msgIn = inp("", () => {}, { placeholder: "Say something to the room (use @Name to ask someone)" }), err = errBox();
    const post = async () => { if (!msgIn.value.trim()) return; await lapi(`/api/lab/rooms/${r.id}/post`, { text: msgIn.value }).catch((e) => toast(e.message, "bad")); paintRoom(); };
    msgIn.addEventListener("keydown", (e) => e.key === "Enter" && post());
    const run = h("button", { class: "btn", onclick: () => busy(run, async () => { await lapi(`/api/lab/rooms/${r.id}/run`, {}); paintRoom(); }, err) }, r.running ? "Talking…" : "Let them talk");
    const step = h("button", { class: "btn plain", onclick: () => busy(step, async () => { await lapi(`/api/lab/rooms/${r.id}/step`, {}); paintRoom(); }, err) }, "One turn");
    const colors = Object.fromEntries(r.agents.map((a, i) => [a, `hsl(${(i * 67 + 200) % 360} 65% 50%)`]));
    fill(view, h("div", { class: "card" }, h("div", { class: "row", style: "justify-content:space-between" }, h("div", {}, h("b", {}, r.name), h("div", { class: "meta" }, chip(r.mode, "mute"), chip(r.status, statusTone[r.status]), `${r.metrics.turns} turns · ${fmtN(r.metrics.input_tokens + r.metrics.output_tokens)} tokens · ${money(r.metrics.cost)}`)),
      h("div", { class: "row" }, step, run, r.running ? h("button", { class: "btn danger", onclick: () => lapi(`/api/lab/rooms/${r.id}/stop`, {}).then(paintRoom) }, "Stop") : null, h("button", { class: "btn danger", onclick: async () => { if (!confirm(t("confirm_delete"))) return; await lapi(`/api/lab/rooms/${r.id}/delete`, {}); NAVSTATE.rooms = null; paintList(); newRoom(); } }, "\u00d7"))),
      r.goal ? h("div", { class: "meta gap" }, "Goal: " + r.goal) : null, err),
      r.result ? h("div", { class: "card result" }, h("div", { class: "label" }, `Result · by ${r.result.by}`), h("div", { class: "md" }, r.result.text),
        h("div", { class: "row gap" }, h("button", { class: "btn plain", onclick: () => { NAVSTATE.jobs = "new"; go("jobs"); setTimeout(() => { const ta = document.querySelector("#jobView textarea"); if (ta) { ta.value = r.result.text; ta.dispatchEvent(new Event("input")); } }, 300); } }, "Turn into a job"))) : null,
      h("div", { class: "card room" }, r.messages.length ? r.messages.map((m) => h("div", { class: "rmsg" + (m.from === "you" ? " you" : m.from === "system" ? " sys" : "") }, h("div", { class: "who", style: colors[m.from] ? `color:${colors[m.from]}` : "" }, m.name), h("div", { class: "bubble" }, m.text)))
        : h("div", { class: "empty" }, "Say hello, or press “Let them talk”."), h("div", { class: "row gap" }, h("div", { class: "grow" }, msgIn), h("button", { class: "btn plain", onclick: post }, "Send"))));
    if (r.running && current === "rooms") setTimeout(() => current === "rooms" && NAVSTATE.rooms === r.id && paintRoom(), 1500);
  };
  loadLab(["agents"]).then(() => { if (current === "rooms") { paintList(); paintRoom(); } });
  return [...lhead("Rooms", "Put agents (on any models) in a room: they talk like a team, @mention each other and cooperate on a goal. A manager opens with a plan and closes with the answer."),
    h("div", { class: "split" }, h("div", {}, h("button", { class: "btn", style: "width:100%;margin-bottom:12px", onclick: () => { NAVSTATE.rooms = null; paintList(); newRoom(); } }, "+ New room"), list), view)];
}

/* ---------- models: dashboard, add a model (API or weights), accounts, keys ---------- */
const md = { tab: "dashboard" };
function pageModels() {
  const body = h("div", { class: "stack" });
  const paint = async () => {
    await loadLab(["presets", "secrets", "agents", "projects"]);
    fill(body, h("div", { class: "empty" }, "…"));
    try {
      if (md.tab === "dashboard") {
        const o = await lapi("/api/lab/models"), hw = o.hardware;
        const tile = (num, lab) => h("div", { class: "tile" }, h("div", { class: "num" }, num), h("div", { class: "lab" }, lab));
        fill(body, h("div", { class: "tiles" }, tile(o.totals.models, "models in use"), tile(fmtN(o.totals.calls), "calls (30 days)"), tile(money(o.totals.cost), "cost (30 days)"), tile(hw.cpu_count, "CPU cores"), tile(fmtB(hw.ram_bytes), "RAM"), tile(hw.gpus.length || "none", "GPUs")),
          h("div", { class: "card" }, h("div", { class: "label" }, "Models"), o.models.length ? h("div", { class: "tablewrap" }, h("table", {}, h("tr", {}, ["Model", "Used by", "Calls", "Tokens", "p50 / p95", "Cost", "Tests", "Tokenizer"].map((c) => h("th", {}, c))),
            o.models.map((m) => h("tr", {}, h("td", {}, h("b", {}, m.model || m.type), h("div", { class: "meta" }, m.label, m.host ? `· ${m.host}` : "")), h("td", {}, m.agents.map((a) => a.name).join(", ")), h("td", {}, fmtN(m.usage.calls)),
              h("td", {}, fmtN(m.usage.input_tokens + m.usage.output_tokens)), h("td", {}, m.usage.p50_ms ? `${m.usage.p50_ms} / ${m.usage.p95_ms} ms` : "–"), h("td", {}, money(m.usage.cost)),
              h("td", {}, m.test ? chip(`${m.test.passed}/${m.test.total}`, m.test.passed === m.test.total ? "ok" : "warn") : "–"), h("td", { class: "meta" }, m.tokenizer))))) : h("div", { class: "meta" }, "No models yet. Add one below.")),
          o.weights.length ? h("div", { class: "card" }, h("div", { class: "label" }, "Model files in your projects"), h("div", { class: "rows" }, o.weights.map((w) => h("div", { class: "kv" }, h("div", {}, h("div", { class: "mono" }, w.path), h("div", { class: "meta" }, `${w.project_name} · ${fmtB(w.size_bytes)} · ${w.format}`, w.fit ? chip(w.fit.verdict, { fits: "ok", tight: "warn", too_big: "bad" }[w.fit.verdict] || "mute") : null, w.fit?.advice || "")),
            w.format === "gguf" ? h("button", { class: "btn plain", onclick: () => { md.tab = "add"; md.weights = { project_id: w.project, path: w.path }; renderPage(); } }, "Make an agent") : null)))) : null,
          o.runners.length ? h("div", { class: "card" }, h("div", { class: "label" }, "Local model servers"), h("div", { class: "rows" }, o.runners.map((r) => h("div", { class: "kv" }, h("div", {}, h("b", {}, r.name), h("div", { class: "meta" }, `port ${r.port} · ${r.device || "auto"}${r.threads ? ` · ${r.threads} threads` : ""}`)),
            h("div", { class: "row" }, chip(r.status, r.status === "ready" ? "ok" : r.status === "stopped" ? "mute" : "warn"), r.status !== "stopped" ? h("button", { class: "btn danger", onclick: () => lapi(`/api/lab/runners/${r.id}/stop`, {}).then(paint) }, "Stop") : null))))) : null);
      } else if (md.tab === "add") {
        const api_ = { preset: "openai", name: "", model: "", api_key: "", role_preset: "", base_url: "" }, w = { project_id: LAB.projects[0]?.id || "", path: "", name: "", runtime: "llama-server", device: "auto", threads: "", role_preset: "", ...(md.weights || {}) };
        const err1 = errBox(), err2 = errBox();
        const P = LAB.presets.providers, roles = LAB.presets.library.roles;
        const modelsList = h("datalist", { id: "addModels" });
        const listBtn = h("button", { class: "btn plain", onclick: () => busy(listBtn, async () => {
          if (api_.api_key) await lapi("/api/lab/secrets", { name: P[api_.preset].key || api_.preset, value: api_.api_key });
          const r = await lapi("/api/lab/models/list", { provider: { ...P[api_.preset], ...(api_.base_url ? { base_url: api_.base_url } : {}) } });
          fill(modelsList, ...r.models.map((m) => h("option", { value: m.id }, m.name))); toast(`${r.models.length} models`, "ok");
        }, err1) }, "List models");
        const addApi = h("button", { class: "btn", onclick: () => busy(addApi, async () => { const a = await lapi("/api/lab/agentise/api", { ...api_, base_url: api_.base_url || undefined, role_preset: api_.role_preset || undefined }); toast(`${a.name} is ready`, "ok"); go("agents", { agents: a.id }); }, err1) }, "Test and create agent");
        const pick = h("input", { type: "file", hidden: true, accept: ".gguf,.safetensors,.bin", onchange: (e) => { const f = e.target.files[0]; if (!f || !w.project_id) return; fill(up, uploadFiles(w.project_id, "models", [f], () => { w.path = "models/" + f.name; md.weights = { ...w }; toast("Uploaded", "ok"); paint(); })); } });
        const up = h("div");
        const addW = h("button", { class: "btn", onclick: () => busy(addW, async () => { const a = await lapi("/api/lab/agentise/weights", { ...w, threads: w.threads ? +w.threads : undefined, role_preset: w.role_preset || undefined }); toast(`${a.name} is starting`, "ok"); md.weights = null; go("agents", { agents: a.id }); }, err2) }, "Create agent from weights");
        fill(body, 
          h("div", { class: "card stack" }, h("h3", { class: "card-title" }, "From an API"), h("div", { class: "meta" }, "OpenAI, Anthropic, OpenRouter, Groq, Mistral, Gemini, Hugging Face, Cloudflare Workers AI, Ollama, LM Studio, llama.cpp, vLLM, OpenClaw, or any OpenAI-compatible URL."),
            h("div", { class: "optgrid" }, field("Provider", sel(Object.entries(P).filter(([k]) => k !== "mock").map(([k, v]) => [k, v.label || k]), api_.preset, (v) => { api_.preset = v; paint(); })),
              field("Agent name", inp(api_.name, (v) => (api_.name = v), { placeholder: "My GPT agent" })),
              field("Model name", h("div", { class: "row", style: "flex-wrap:nowrap" }, inp(api_.model, (v) => (api_.model = v), { list: "addModels", placeholder: P[api_.preset]?.model || "model id" }), ["openai", "anthropic"].includes(P[api_.preset]?.type) ? listBtn : null), modelsList),
              P[api_.preset]?.type === "cli" ? null : field("API key", h("input", { type: "password", autocomplete: "off", placeholder: P[api_.preset]?.key ? `saved as “${P[api_.preset].key}”` : "not needed for local servers", oninput: (e) => (api_.api_key = e.target.value) })),
              field("Base URL (optional)", inp(api_.base_url, (v) => (api_.base_url = v), { placeholder: P[api_.preset]?.base_url || "" })),
              field("Preset role (optional)", sel([["", "No role"], ...roles.map((r) => [r.id, r.name])], api_.role_preset, (v) => (api_.role_preset = v)))),
            h("div", {}, addApi), err1),
          h("div", { class: "card stack" }, h("h3", { class: "card-title" }, "From your own weights"), h("div", { class: "meta" }, "A GGUF file in a project runs locally with llama-server or Ollama. Upload one, import it from Hugging Face (Projects  Import), or pick a file already there."),
            h("div", { class: "optgrid" }, field("Project", sel(projectOpts(), w.project_id, (v) => (w.project_id = v))), field("Weights file", h("div", { class: "row", style: "flex-wrap:nowrap" }, inp(w.path, (v) => (w.path = v), { placeholder: "models/model.gguf" }), h("button", { class: "btn plain", onclick: () => pick.click() }, "Upload"), pick)),
              field("Agent name", inp(w.name, (v) => (w.name = v))), field("Runtime", sel([["llama-server", "llama.cpp server"], ["ollama", "Ollama"]], w.runtime, (v) => (w.runtime = v))),
              field("Run on", sel([["auto", "Auto"], ["cpu", "CPU only"], ["gpu", "GPU"]], w.device, (v) => (w.device = v))), field("CPU threads", inp(w.threads, (v) => (w.threads = v), { type: "number", min: 1, placeholder: "auto" })),
              field("Preset role (optional)", sel([["", "No role"], ...roles.map((r) => [r.id, r.name])], w.role_preset, (v) => (w.role_preset = v)))), up, h("div", {}, addW), err2));
      } else if (md.tab === "accounts") {
        const st = await lapi("/api/lab/accounts");
        const names = { github: ["GitHub", "Import repositories; private ones too once connected."], huggingface: ["Hugging Face", "Download models and datasets, including gated ones."], openai: ["OpenAI (GPT)", "Use GPT models; lists the models your key can use."], anthropic: ["Anthropic (Claude)", "Use Claude models; lists the models your key can use."] };
        fill(body, h("div", { class: "cards3" }, Object.entries(st).map(([svc, s_]) => {
          const tok = h("input", { type: "password", autocomplete: "off", placeholder: "Paste a token or API key" }), err = errBox();
          const con = h("button", { class: "btn", onclick: () => busy(con, async () => { await lapi(`/api/lab/accounts/${svc}`, { token: tok.value }); toast("Connected", "ok"); paint(); }, err) }, "Connect");
          return h("div", { class: "card stack" }, h("div", { class: "row", style: "justify-content:space-between" }, h("b", {}, names[svc]?.[0] || svc), chip(s_.connected ? "connected" : "not connected", s_.connected ? "ok" : "mute")),
            h("div", { class: "meta" }, names[svc]?.[1] || ""), s_.connected ? h("div", { class: "meta" }, s_.identity ? `${s_.identity.name}${s_.identity.display ? " · " + s_.identity.display : ""}` : "", s_.hint) : null,
            s_.connected ? h("button", { class: "btn danger", onclick: () => lapi(`/api/lab/accounts/${svc}/disconnect`, {}).then(paint) }, "Disconnect") : h("div", { class: "stack" }, tok, h("div", {}, con)), err);
        })), h("div", { class: "meta" }, "Tokens are verified with the service, then stored in .agentlab/secrets.json (file mode 600), never in logs."));
      } else if (md.tab === "keys") {
        const n = inp("", () => {}, { placeholder: "name, e.g. openai" }), v = h("input", { type: "password", autocomplete: "off", placeholder: "value" }), err = errBox();
        const add = h("button", { class: "btn", onclick: () => busy(add, async () => { await lapi("/api/lab/secrets", { name: n.value, value: v.value }); paint(); }, err) }, "Save key");
        fill(body, h("div", { class: "card stack" }, h("div", { class: "meta" }, "Agents refer to keys by name. Values never leave this computer except to the provider they belong to, and are redacted from every log."),
          h("div", { class: "row" }, h("div", { class: "grow" }, n), h("div", { class: "grow" }, v), add), err,
          h("div", { class: "rows tight" }, LAB.secrets.map((s_) => h("div", { class: "kv" }, h("span", { class: "mono" }, s_), h("button", { class: "btn danger", onclick: () => lapi(`/api/lab/secrets/${encodeURIComponent(s_)}/delete`, {}).then(paint) }, t("remove")))))));
      }
    } catch (e) { fill(body, h("div", { class: "card err" }, e.message)); }
  };
  paint();
  return [...lhead("Models", "Every model in one place: what uses it, how fast it is, what it costs. Add models from any API or from your own weights, and connect your accounts."),
    tabs([["dashboard", "Dashboard"], ["add", "Add a model"], ["accounts", "Accounts"], ["keys", "Keys"]], md.tab, (v) => { md.tab = v; renderPage(); }), body];
}

/* ---------- training ---------- */
function pageTraining() {
  const list = h("div", { class: "list card" }), view = h("div", { class: "stack" });
  const paintList = async () => {
    const r = await lapi("/api/lab/training").catch(() => ({ runs: [] }));
    fill(list, ...(r.runs.length ? r.runs.map((x) => h("button", { "aria-current": x.id === NAVSTATE.training, onclick: () => { NAVSTATE.training = x.id; paintList(); paintRun(); } }, h("div", { class: "t" }, x.name), h("div", { class: "meta" }, chip(x.status, statusTone[x.status]), x.trainer), x.status === "running" ? bar(x.progress.percent, "", "grad") : null)) : [h("div", { class: "empty" }, "No training runs yet.")]));
  };
  const paintRun = async () => {
    if (!NAVSTATE.training) return fill(view, h("div", { class: "card empty" }, h("b", {}, "Train a specialised model"), "Open a project, choose a dataset under AI & training, and press Train. Or start from the Text classifier template.",
      h("div", { class: "row gap", style: "justify-content:center" }, h("button", { class: "btn", onclick: () => go("templates") }, "Model templates"), h("button", { class: "btn plain", onclick: () => go("projects") }, "Projects"))));
    let r;
    try { r = await lapi(`/api/lab/training/${NAVSTATE.training}`); } catch (e) { return fill(view, h("div", { class: "card err" }, e.message)); }
    const H_ = r.history.filter((x) => x.train_loss != null || x.val_loss != null);
    const bestI = H_.findIndex((x) => x.epoch === r.best?.epoch && x.val_loss != null);
    const out = h("div"), txt = inp("", () => {}, { placeholder: "Type a text to classify" }), lab_ = inp("", () => {}, { placeholder: "correct label" });
    const predict = h("button", { class: "btn plain", onclick: () => busy(predict, async () => { const p = await lapi(`/api/lab/training/${r.id}/predict`, { text: txt.value }); fill(out, h("div", { class: "row gap" }, p.predictions.map((x) => chip(`${x.label} · ${Math.round(x.p * 100)}%`, x === p.predictions[0] ? "ok" : "mute")))); }) }, "Predict");
    const teach = h("button", { class: "btn plain", onclick: () => busy(teach, async () => { await lapi(`/api/lab/training/${r.id}/edit`, { action: "teach", text: txt.value, label: lab_.value }); toast("Learned the correction", "ok"); predict.click(); }) }, "Teach this correction");
    const undo = h("button", { class: "btn plain", onclick: () => busy(undo, async () => { await lapi(`/api/lab/training/${r.id}/edit`, { action: "undo" }); toast("Undone", "ok"); }) }, "Undo last edit");
    fill(view, h("div", { class: "card" }, h("div", { class: "row", style: "justify-content:space-between" }, h("div", {}, h("b", {}, r.name), h("div", { class: "meta" }, chip(r.trainer, "mute"), `${r.examples} examples`, r.split ? `· ${r.split.train} train / ${r.split.val} validation` : "")),
      h("div", { class: "row" }, chip(r.status, statusTone[r.status]), r.status === "running" ? h("button", { class: "btn danger", onclick: () => lapi(`/api/lab/training/${r.id}/cancel`, {}).then(paintRun) }, t("cancel")) : null)),
      h("div", { class: "gap" }, bar(r.progress.percent, "", "grad")), h("div", { class: "meta", style: "margin-top:6px" }, `epoch ${r.progress.epoch} of ${r.progress.epochs}`, r.best?.val_loss != null ? `· best validation loss ${r.best.val_loss} at epoch ${r.best.epoch}` : "", r.best?.val_acc != null ? `· accuracy ${Math.round(r.best.val_acc * 100)}%` : "", r.stopped_early ? chip("stopped early", "ok") : null), r.error ? h("div", { class: "err" }, r.error) : null),
      h("div", { class: "card" }, h("div", { class: "label" }, "Loss"), lineChart([{ name: "train loss", data: H_.map((x) => x.train_loss ?? null), cls: "s0" }, { name: "validation loss", data: H_.map((x) => x.val_loss ?? null), cls: "s1", mark: (i) => i === bestI }], H_.length ? ["start", "now"] : null),
        h("div", { class: "legend row" }, h("div", {}, h("i", { class: "s0" }), "train"), h("div", {}, h("i", { class: "s1" }), "validation"), bestI >= 0 ? h("div", { class: "meta" }, "● best epoch (kept)") : null),
        H_.some((x) => x.val_acc != null) ? h("div", { class: "gap" }, h("div", { class: "label" }, "Accuracy"), lineChart([{ name: "train", data: H_.map((x) => x.train_acc ?? null), cls: "s0" }, { name: "validation", data: H_.map((x) => x.val_acc ?? null), cls: "s1" }], null, 120)) : null),
      r.warnings?.length ? h("div", { class: "card warnings" }, h("div", { class: "label" }, "Overfit protection"), h("ul", {}, r.warnings.map((w) => h("li", {}, w)))) : null,
      r.model?.kind === "classifier" && r.status === "succeeded" ? h("div", { class: "card stack" }, h("div", { class: "label" }, "Try and edit the model"), h("div", { class: "row" }, h("div", { class: "grow" }, txt), predict), out,
        h("div", { class: "row" }, h("div", { class: "grow" }, lab_), teach, undo), h("div", { class: "meta" }, `Labels: ${r.model.labels.join(", ")}. Teaching adds a correction on the spot (a new label too); undo restores the previous version.`)) : null,
      r.model?.kind === "lora" ? h("div", { class: "card" }, h("div", { class: "label" }, "Result"), h("div", {}, "Best checkpoint: ", h("code", {}, r.model.path)), h("div", { class: "meta" }, "Convert it to GGUF with llama.cpp to run it as a local agent.")) : null);
    if (r.status === "running" && current === "training") setTimeout(() => current === "training" && NAVSTATE.training === r.id && (paintRun(), paintList()), 1200);
  };
  paintList(); paintRun();
  return [...lhead("Training", "Train specialised models with live loss curves. Overfit protection holds out validation data, stops early and keeps the best epoch."), h("div", { class: "split" }, list, view)];
}

/* ---------- knowledge ---------- */
function pageKnowledge() {
  const list = h("div", { class: "list card" }), view = h("div", { class: "stack" });
  let presets = {};
  const paintList = async () => {
    const r = await lapi("/api/lab/knowledge").catch(() => ({ knowledge: [], embeddings: {} }));
    presets = r.embeddings;
    fill(list, ...(r.knowledge.length ? r.knowledge.map((k) => h("button", { "aria-current": k.id === NAVSTATE.knowledge, onclick: () => { NAVSTATE.knowledge = k.id; paintList(); paintKb(); } }, h("div", { class: "t" }, k.name), h("div", { class: "meta" }, `${k.sources.length} sources · ${k.chunks} chunks`))) : [h("div", { class: "empty" }, "No knowledge bases yet.")]));
  };
  const paintKb = async () => {
    if (!NAVSTATE.knowledge) {
      const f = { name: "", preset: "builtin" }, err = errBox();
      const create = h("button", { class: "btn", onclick: () => busy(create, async () => { const k = await lapi("/api/lab/knowledge", { name: f.name, embedding: { preset: f.preset } }); NAVSTATE.knowledge = k.id; paintList(); paintKb(); }, err) }, "Create");
      return fill(view, h("div", { class: "card stack" }, h("h3", { class: "card-title" }, "New knowledge base"), field("Name", inp(f.name, (v) => (f.name = v), { placeholder: "Product docs" })),
        field("Embedding model", sel(Object.entries(presets).map(([k, v]) => [k, v.label]), f.preset, (v) => (f.preset = v)), "Built-in works offline; an embedding server understands meaning better."), h("div", {}, create), err));
    }
    let k;
    try { k = await lapi(`/api/lab/knowledge/${NAVSTATE.knowledge}`); } catch { NAVSTATE.knowledge = null; return paintKb(); }
    const err = errBox(), url = inp("", () => {}, { placeholder: "https://docs.example.com/" }), pages = inp(1, () => {}, { type: "number", min: 1, max: 50, style: "width:90px" });
    const text = h("textarea", { style: "min-height:90px;font-family:var(--font)", placeholder: "Paste text" }), title = inp("", () => {}, { placeholder: "Title" });
    const q = inp("", () => {}, { placeholder: "Search…" }), res = h("div");
    const scrape = h("button", { class: "btn", onclick: () => busy(scrape, async () => { const r = await lapi(`/api/lab/knowledge/${k.id}/scrape`, { url: url.value, max_pages: +pages.value }); toast(`${r.added.length} pages added, ${r.skipped.length} skipped`, "ok"); paintKb(); paintList(); }, err) }, "Fetch");
    const addT = h("button", { class: "btn plain", onclick: () => busy(addT, async () => { await lapi(`/api/lab/knowledge/${k.id}/add`, { text: text.value, title: title.value }); paintKb(); paintList(); }, err) }, "Add text");
    const search = h("button", { class: "btn plain", onclick: () => busy(search, async () => { const r = await lapi(`/api/lab/knowledge/${k.id}/search`, { query: q.value }); fill(res, ...(r.results.length ? r.results.map((x) => h("div", { class: "card hit" }, h("div", { class: "meta" }, chip(x.score.toFixed(2)), x.source), h("div", {}, x.text))) : [h("div", { class: "meta" }, "No matches.")])); }) }, "Search");
    q.addEventListener("keydown", (e) => e.key === "Enter" && search.click());
    fill(view, h("div", { class: "card" }, h("div", { class: "row", style: "justify-content:space-between" }, h("div", {}, h("b", {}, k.name), h("div", { class: "meta" }, k.embedding.label || k.embedding.model, `· ${k.chunks} chunks`)),
      h("button", { class: "btn danger", onclick: async () => { if (!confirm(t("confirm_delete"))) return; await lapi(`/api/lab/knowledge/${k.id}/delete`, {}); NAVSTATE.knowledge = null; paintList(); paintKb(); } }, t("delete")))),
      h("div", { class: "card stack" }, h("div", { class: "label" }, "Web scrape"), h("div", { class: "row" }, h("div", { class: "grow" }, url), h("span", { class: "meta" }, "pages"), pages, scrape), h("div", { class: "meta" }, "Public pages only; robots.txt is respected and the crawl stays on the same site, one request per second.")),
      h("div", { class: "card stack" }, h("div", { class: "label" }, "Text"), title, text, h("div", {}, addT)), err,
      h("div", { class: "card stack" }, h("div", { class: "label" }, "Search"), h("div", { class: "row" }, h("div", { class: "grow" }, q), search), res),
      h("div", { class: "card" }, h("div", { class: "label" }, "Sources"), k.sources.length ? h("div", { class: "rows tight" }, k.sources.map((s_) => h("div", { class: "kv" }, h("div", {}, h("div", {}, s_.title), h("div", { class: "meta" }, chip(s_.kind, "mute"), `${s_.chunks} chunks`, s_.url ? h("a", { href: s_.url, target: "_blank", rel: "noopener" }, s_.url) : null)),
        h("button", { class: "btn danger", onclick: () => lapi(`/api/lab/knowledge/${k.id}/sources/${s_.id}/delete`, {}).then(() => { paintKb(); paintList(); }) }, t("remove"))))) : h("div", { class: "meta" }, "Nothing yet.")),
      h("div", { class: "meta" }, "Link this knowledge base to an agent under Agents  Settings  Skills, plugins and knowledge. The agent then gets matching passages and a search tool."));
  };
  paintList().then(paintKb);
  return [...lhead("Knowledge", "Give agents knowledge: paste text, add project files or scrape web pages. Passages are embedded and the best matches are handed to the agent."), h("div", { class: "split" }, h("div", {}, h("button", { class: "btn", style: "width:100%;margin-bottom:12px", onclick: () => { NAVSTATE.knowledge = null; paintKb(); } }, "+ New knowledge base"), list), view)];
}

/* ---------- templates gallery + builders ---------- */
const tp = { kind: "all", list: [] };
const KIND_ICON = { website: "globe", game: "game", model: "brain", infra: "build" };
function pageTemplates() {
  const grid = h("div", { class: "cards3" }), err = errBox();
  const paint = () => fill(grid, ...tp.list.filter((x) => tp.kind === "all" || x.kind === tp.kind).map((x) => {
    const make = (withAi) => {
      const f = { name: "", agent: LAB.agents.find((a) => !a.locked)?.id || "", prompt: "" };
      const ov = $("overlay"); ov.hidden = false; ov.className = ""; ov.dataset.kind = "settings";
      const e2 = errBox();
      const b = h("button", { class: "btn", onclick: () => busy(b, async () => {
        const r = await lapi("/api/lab/build", { template: x.id, name: f.name || undefined, ...(withAi ? { agent: f.agent, prompt: f.prompt } : {}) });
        closeOverlay(); await loadLab(["projects"]); toast("Project created", "ok");
        if (r.job) go("jobs", { jobs: r.job.id }); else go("projects", { projects: r.project.id });
      }, e2) }, withAi ? "Create and build with AI" : "Create project");
      fill(ov, h("div", { class: "sheet", role: "dialog", "aria-modal": "true" }, h("div", { class: "sheet-head" }, h("h3", { class: "row", style: "gap:10px" }, sico(KIND_ICON[x.kind], 22), x.name), h("button", { class: "btn plain", onclick: closeOverlay }, t("cancel"))),
        h("p", { class: "meta" }, x.description), h("div", { class: "stack" }, field("Project name", inp(f.name, (v) => (f.name = v), { placeholder: x.name })),
          withAi ? field("Agent", sel(agentOpts(), f.agent, (v) => (f.agent = v))) : null,
          withAi ? field("What should it build?", h("textarea", { style: "min-height:90px;font-family:var(--font)", placeholder: "A landing page for a bakery in Lyon, warm colours, opening hours and a map link", oninput: (e) => (f.prompt = e.target.value) })) : null,
          h("div", { class: "meta" }, "Files: " + x.files.join(", ")), h("div", {}, b), e2)));
    };
    return h("div", { class: "card tcard" }, h("div", { class: "thumb k-" + x.kind }, sico(KIND_ICON[x.kind], 46)), h("b", {}, x.name), h("div", { class: "meta" }, x.description), h("div", { class: "row" }, x.tags.map((t_) => chip(t_, "mute"))),
      h("div", { class: "row gap" }, h("button", { class: "btn plain", onclick: () => make(false) }, "Use"), LAB.agents.length ? h("button", { class: "btn", onclick: () => make(true) }, "Build with AI") : null));
  }));
  Promise.all([lapi("/api/lab/templates").then((r) => (tp.list = r.templates)), loadLab(["agents"])]).then(() => current === "templates" && paint()).catch((e) => (err.textContent = e.message));
  return [...lhead("Templates", "Start a website, game, model or infrastructure project from a working template, then let an agent customise it. Deploy to localhost or Cloudflare in one click."),
    h("div", { class: "row", style: "margin-bottom:16px" }, seg([["all", "All"], ["website", "Websites"], ["game", "Games"], ["model", "Models"], ["infra", "Infrastructure"]], tp.kind, (v) => { tp.kind = v; renderPage(); }, true)), grid, err];
}

/* ---------- terminal ---------- */
const tm = { id: null, offset: 0, project: "", alive: false, reading: false, text: "" };
const ANSI_RE = /\x1b\[[0-9;?]*[ -\/]*[@-~]|\x1b\][^\x07\x1b]*(\x07|\x1b\\)|\x1b[()][A-Za-z0-9]|\x1b[=>78]/g;
function termFeed(text) {
  /* a small line terminal: strips colours and handles carriage return, backspace and clear-screen */
  if (/\x1b\[2J|\x1bc/.test(text)) tm.text = "";
  for (const ch of text.replace(ANSI_RE, "")) {
    if (ch === "\r") continue;
    if (ch === "\b") tm.text = tm.text.slice(0, -1);
    else if (ch === "\x07") continue;
    else tm.text += ch;
  }
  if (tm.text.length > 200000) tm.text = tm.text.slice(-150000);
}
async function termLoop(screen) {
  if (tm.reading) return;
  tm.reading = true;
  while (tm.id && current === "terminal") {
    try {
      const r = await lapi(`/api/lab/terminals/${tm.id}?offset=${tm.offset}&wait=20`);
      tm.offset = r.offset; tm.alive = r.alive;
      if (r.data) { termFeed(r.data); screen.textContent = tm.text; screen.scrollTop = screen.scrollHeight; }
      if (!r.alive) { screen.textContent = tm.text + "\n[session ended]"; break; }
    } catch { break; }
  }
  tm.reading = false;
}
function pageTerminal() {
  const screen = h("pre", { class: "term", tabindex: "0", "aria-label": "Terminal: click and type" }, tm.text || "");
  const warn = h("div", { class: "err" });
  const KEYS = { Enter: "\r", Backspace: "\x7f", Tab: "\t", Escape: "\x1b", ArrowUp: "\x1b[A", ArrowDown: "\x1b[B", ArrowRight: "\x1b[C", ArrowLeft: "\x1b[D", Home: "\x1b[H", End: "\x1b[F", Delete: "\x1b[3~" };
  let queue = "", sending = false;
  const flush = async () => {  // one request at a time, so keystrokes arrive in order
    if (sending || !queue || !tm.id) return;
    sending = true;
    const data = queue; queue = "";
    try { const r = await lapi(`/api/lab/terminals/${tm.id}/input`, { data }); warn.textContent = r.warning ? "Warning: " + r.warning : ""; } catch {}
    sending = false; flush();
  };
  const sendKeys = (data) => { queue += data; flush(); };
  screen.addEventListener("keydown", (e) => {
    let d = null;
    if (e.ctrlKey && e.key.length === 1 && /[a-z]/i.test(e.key)) d = String.fromCharCode(e.key.toUpperCase().charCodeAt(0) - 64);
    else if (KEYS[e.key]) d = KEYS[e.key];
    else if (e.key.length === 1 && !e.metaKey) d = e.key;
    if (d) { e.preventDefault(); sendKeys(d); }
  });
  screen.addEventListener("paste", (e) => { e.preventDefault(); sendKeys(e.clipboardData.getData("text")); });
  const err = errBox();
  const open = h("button", { class: "btn", onclick: () => busy(open, async () => {
    if (tm.id) await lapi(`/api/lab/terminals/${tm.id}/close`, {}).catch(() => {});
    const cols = Math.max(40, Math.floor((screen.clientWidth - 24) / 8)), rows = Math.max(10, Math.floor(screen.clientHeight / 19));
    const r = await lapi("/api/lab/terminals", { project: tm.project, cols, rows });
    Object.assign(tm, { id: r.id, offset: 0, text: r.offline ? "(offline: this project's network policy is off)\n" : "" });
    screen.textContent = tm.text; screen.focus(); termLoop(screen);
  }, err) }, tm.id ? "Restart" : "Open terminal");
  loadLab(["projects"]).then(() => {
    if (current !== "terminal") return;
    tm.project = NAVSTATE.terminal || tm.project || LAB.projects[0]?.id || "";
    fill(pick, sel(projectOpts(), tm.project, (v) => (tm.project = v)));
    if (tm.id) termLoop(screen);
  });
  const pick = h("div", { class: "grow" });
  return [...lhead("Terminal", "A real shell inside a project: same folder, same environment and Python as the agents. Click the screen and type."),
    h("div", { class: "card stack" }, h("div", { class: "row" }, pick, open, tm.id ? h("button", { class: "btn danger", onclick: async () => { await lapi(`/api/lab/terminals/${tm.id}/close`, {}).catch(() => {}); tm.id = null; renderPage(); } }, "Close") : null), screen, warn, err,
      h("div", { class: "meta" }, "This is your own terminal: commands run as you, in the project folder. Destructive lines are flagged, not blocked. Agents never use this terminal.")), ];
}

/* ---------- usage ---------- */
function pageUsage() {
  const box = h("div", { class: "stack" });
  const st = { days: 14 };
  const paint = async () => {
    const u = await lapi(`/api/lab/usage?days=${st.days}`).catch(() => null);
    if (!u) return;
    const T = u.totals, tile = (num, lab) => h("div", { class: "tile" }, h("div", { class: "num" }, num), h("div", { class: "lab" }, lab));
    const table = (rows, label) => h("div", { class: "card" }, h("div", { class: "label" }, label), rows.length ? h("div", { class: "rows tight" }, rows.slice(0, 12).map((r) => {
      const total = T.input + T.output || 1, mine = r.input + r.output;
      return h("div", {}, h("div", { class: "kv" }, h("span", { class: "mono" }, r.key), h("span", { class: "meta" }, `${fmtN(mine)} tok · ${r.calls} calls · ${money(r.cost)}`)), bar((100 * mine) / total));
    })) : h("div", { class: "meta" }, "Nothing yet."));
    fill(box, h("div", { class: "row" }, seg([[7, "7 days"], [14, "14 days"], [30, "30 days"], [90, "90 days"]], st.days, (v) => { st.days = v; paint(); }, true)),
      h("div", { class: "tiles" }, tile(fmtN(T.input + T.output), "tokens"), tile(fmtN(T.input), "input"), tile(fmtN(T.output), "output"), tile(fmtN(T.calls), "model calls"), tile(money(T.cost), "cost")),
      h("div", { class: "card" }, h("div", { class: "label" }, "Tokens per day"), barChart(u.by_day.map((d) => d.input + d.output), (i, v) => `${u.by_day[i].date}: ${fmtN(v)} tokens, ${money(u.by_day[i].cost)}`), h("div", { class: "axis" }, h("span", {}, u.by_day[0]?.date), h("span", {}, u.by_day.at(-1)?.date))),
      h("div", { class: "grid2" }, table(u.by_agent, "By agent"), table(u.by_model, "By model")), h("div", { class: "grid2" }, table(u.by_kind, "By kind of work"), table(u.by_project, "By project")),
      h("div", { class: "meta" }, "Costs use the prices set on each provider (price per million input/output tokens). Local models cost nothing."));
  };
  paint();
  poll = setInterval(paint, 15000);
  return [...lhead("Usage", "Every model call is counted: tokens, cost and speed per agent, model, project and kind of work (jobs, chats, rooms, dreams, evaluations)."), box];
}

/* ---------- library: roles, skills, plugins, connectors, commands ---------- */
const lb = { tab: "roles" };
function pageLibrary() {
  const body = h("div", { class: "stack" });
  const paint = async () => {
    const [L_, C] = await Promise.all([lapi("/api/lab/library"), lapi("/api/lab/commands")]).catch(() => [null, null]);
    if (!L_) return;
    const err = errBox();
    if (lb.tab === "roles") fill(body, h("div", { class: "cards3" }, L_.roles.map((r) => h("div", { class: "card" }, h("div", { class: "big" }, avatarIcon(r.avatar, 30)), h("b", {}, r.name), h("div", { class: "meta" }, r.role), h("div", { class: "row gap" }, r.skills.map((s_) => chip(s_, "mute"))),
      h("button", { class: "btn plain", onclick: () => { NAVSTATE.agents = "new"; go("agents"); } }, "Use for a new agent")))));
    else if (lb.tab === "skills") {
      const f = { name: "", instructions: "", description: "" };
      const add = h("button", { class: "btn", onclick: () => busy(add, async () => { await lapi("/api/lab/library/skills", f); paint(); }, err) }, "Create skill");
      fill(body, h("div", { class: "cards3" }, [...L_.skills, ...L_.custom_skills].map((s_) => h("div", { class: "card" }, h("b", {}, s_.name, s_.id.startsWith("c-") ? " (custom)" : ""), h("div", { class: "meta" }, s_.description),
        s_.id.startsWith("c-") ? h("button", { class: "btn danger", onclick: () => lapi(`/api/lab/library/skills/${s_.id}/delete`, {}).then(paint) }, t("remove")) : null))),
        h("div", { class: "card stack" }, h("h3", { class: "card-title" }, "New skill"), field("Name", inp(f.name, (v) => (f.name = v))), field("Description", inp(f.description, (v) => (f.description = v))),
          field("Instructions", h("textarea", { style: "min-height:90px;font-family:var(--font)", oninput: (e) => (f.instructions = e.target.value) })), h("div", {}, add), err));
    } else if (lb.tab === "plugins") {
      const f = { tool: "", name: "", category: "read", command: "", params: "{}" };
      const add = h("button", { class: "btn", onclick: () => busy(add, async () => { await lapi("/api/lab/library/plugins", { ...f, params: JSON.parse(f.params || "{}") }); paint(); }, err) }, "Create plugin");
      fill(body, h("div", { class: "cards3" }, [...L_.plugins, ...L_.custom_plugins].map((p) => h("div", { class: "card" }, h("b", {}, p.name), h("div", { class: "meta" }, chip(p.category || "", "mute"), h("code", {}, p.tool)), h("div", { class: "meta" }, p.description),
        p.id.startsWith("c-") ? h("button", { class: "btn danger", onclick: () => lapi(`/api/lab/library/plugins/${p.id}/delete`, {}).then(paint) }, t("remove")) : null))),
        h("div", { class: "card stack" }, h("h3", { class: "card-title" }, "New plugin (a sandboxed command the agent can call)"),
          h("div", { class: "optgrid" }, field("Tool name", inp(f.tool, (v) => (f.tool = v), { placeholder: "count_lines" })), field("Category", sel([["read", "read"], ["run", "run"], ["network", "network"]], f.category, (v) => (f.category = v)))),
          field("Command template", inp(f.command, (v) => (f.command = v), { placeholder: "wc -l {path}" }), "Parameters are shell-quoted. Dangerous templates are refused."),
          field("Parameters (JSON)", inp(f.params, (v) => (f.params = v), { placeholder: '{"path": {"type": "string"}}' })), h("div", {}, add), err));
    } else if (lb.tab === "connectors") {
      fill(body, h("div", { class: "cards3" }, L_.connectors.map((c) => h("div", { class: "card" }, h("div", { class: "row", style: "justify-content:space-between" }, h("b", {}, c.name), chip(c.connected ? "connected" : c.detected ? "detected" : "not set up", c.connected ? "ok" : c.detected ? "" : "mute")),
        h("div", { class: "meta" }, chip(c.kind, "mute"), c.does), c.connected ? null : h("div", { class: "meta gap" }, c.setup)))));
    } else if (lb.tab === "commands") {
      const f = { name: "", kind: "prompt", template: "", description: "" };
      const add = h("button", { class: "btn", onclick: () => busy(add, async () => { await lapi("/api/lab/commands", f); paint(); }, err) }, "Create command");
      fill(body, h("div", { class: "card" }, h("div", { class: "rows tight" }, C.commands.map((c) => h("div", { class: "kv" }, h("div", {}, h("code", {}, "/" + c.name + (c.params?.length ? " " + c.params.map((p) => `<${p}>`).join(" ") : "")), " ", h("span", { class: "meta" }, c.description || c.template.slice(0, 80))),
        h("div", { class: "row", style: "flex-wrap:nowrap" }, chip(c.kind, "mute"), c.builtin ? null : h("button", { class: "btn danger", onclick: () => lapi(`/api/lab/commands/${c.name}/delete`, {}).then(paint) }, t("remove"))))))),
        h("div", { class: "card stack" }, h("h3", { class: "card-title" }, "New command"), h("div", { class: "optgrid" }, field("Name", inp(f.name, (v) => (f.name = v), { placeholder: "deploy-check" })), field("Kind", sel([["prompt", "Prompt for an agent"], ["shell", "Shell command in the project"]], f.kind, (v) => (f.kind = v)))),
          field("Template", inp(f.template, (v) => (f.template = v), { placeholder: "Review {1} for security issues and list fixes" }), "Use {1}, {2} … or {args} for what follows the command."), field("Description", inp(f.description, (v) => (f.description = v))), h("div", {}, add), err,
          h("div", { class: "meta" }, "Type /name in a job's goal to use it. Shell commands run sandboxed in the project; blocked commands are refused.")));
    }
  };
  paint();
  return [...lhead("Library", "Built-in roles, skills, plugins and connectors, plus your own skills, plugins and /commands."),
    tabs([["roles", "Roles"], ["skills", "Skills"], ["plugins", "Plugins"], ["connectors", "Connectors"], ["commands", "Commands"]], lb.tab, (v) => { lb.tab = v; renderPage(); }), body];
}

/* ---------- registration ---------- */
Object.assign(ICONS, {
  home: '<path d="M3 11l9-7 9 7v9a1 1 0 0 1-1 1h-5v-6h-6v6H4a1 1 0 0 1-1-1z"/>',
  agents: '<rect x="5" y="8" width="14" height="11" rx="3"/><path d="M12 4v4M9 13h.01M15 13h.01M9 16.5h6"/>',
  projects: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
  jobs: '<path d="M4 6h16M4 12h10M4 18h6"/><circle cx="18" cy="16" r="3"/>',
  schedules: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  playground: '<path d="M4 5h16v11H8l-4 4z"/>',
  rooms: '<circle cx="8" cy="9" r="3"/><circle cx="16" cy="9" r="3"/><path d="M2 19c1-3 3.5-4 6-4s5 1 6 4M14 15.5c.7-.3 1.3-.5 2-.5 2.5 0 5 1 6 4"/>',
  models: '<path d="M12 3l8 4.5v9L12 21l-8-4.5v-9z"/><path d="M12 12l8-4.5M12 12v9M12 12L4 7.5"/>',
  training: '<path d="M3 20h18M5 16l4-5 4 3 6-8"/>',
  knowledge: '<path d="M4 5a2 2 0 0 1 2-2h12v16H6a2 2 0 0 0-2 2zM8 7h6"/>',
  templates: '<rect x="3" y="3" width="8" height="8" rx="2"/><rect x="13" y="3" width="8" height="8" rx="2"/><rect x="3" y="13" width="8" height="8" rx="2"/><path d="M17 13v8M13 17h8"/>',
  terminal: '<rect x="3" y="4" width="18" height="16" rx="3"/><path d="M7 9l3 3-3 3M13 15h4"/>',
  usage: '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
  library: '<path d="M5 4h4v16H5zM10 4h4v16h-4zM15 5l3.5-1 3 15-3.5 1z"/>',
});
NAV.unshift(
  ["Agent lab", [["home", "Start"], ["agents", "Agents"], ["projects", "Projects"], ["jobs", "Jobs"], ["schedules", "Schedules"]]],
  ["Talk", [["playground", "Playground"], ["rooms", "Rooms"]]],
  ["Models", [["models", "Models"], ["training", "Training"], ["knowledge", "Knowledge"]]],
  ["Build", [["templates", "Templates"], ["terminal", "Terminal"], ["library", "Library"]]],
  ["Insights", [["usage", "Usage"]]],
);
TABS.unshift(...NAV.slice(0, 5).flatMap(([, items]) => items));
Object.assign(pages, { home: pageHome, agents: pageAgents, projects: pageProjects, jobs: pageJobs, schedules: pageSchedules, playground: pagePlayground, rooms: pageRooms,
  models: pageModels, training: pageTraining, knowledge: pageKnowledge, templates: pageTemplates, terminal: pageTerminal, usage: pageUsage, library: pageLibrary });
setInterval(loadBell, 10000);

/* ---------- model comparator: same prompt, two or more of your models, real numbers ---------- */
const cmp = { targets: [{ kind: "agent", agent_id: "", pv: { preset: "mock" } }, { kind: "agent", agent_id: "", pv: { preset: "mock" } }],
  prompt: "", system: "", repeats: 1, max_tokens: 600, temperature: "", result: null, running: false, judging: false, verdict: null, history: [] };
const METRICS = [
  ["total_tokens", "Tokens used", (v) => fmtN(v), "low"],
  ["input_tokens", "In", (v) => fmtN(v), null],
  ["output_tokens", "Out", (v) => fmtN(v), null],
  ["cost", "Cost", money, "low"],
  ["cost_per_1k_output", "Cost / 1k out", (v) => (v ? money(v) : "free"), "low"],
  ["latency_ms", "Latency", (v) => `${fmtN(v)} ms`, "low"],
  ["latency_spread_ms", "Spread (min–max)", (v, m) => `${fmtN(m.latency_min_ms)}–${fmtN(m.latency_max_ms)} ms`, "low"],
  ["tokens_per_second", "Tokens / second", (v) => fmtN(v), "high"],
  ["words", "Words in the answer", (v) => fmtN(v), null],
  ["agreement", "Agreement with the others", (v) => (v == null ? "–" : `${Math.round(v * 100)}%`), null],
];
function pageCompare() {
  const setup = h("div", { class: "card stack" }), out = h("div", { class: "stack" }), err = errBox();
  const paintSetup = () => {
    const row = (tg, i) => h("div", { class: "target" },
      h("div", { class: "row", style: "justify-content:space-between" },
        h("b", {}, `Model ${String.fromCharCode(65 + i)}`),
        h("div", { class: "row", style: "flex-wrap:nowrap" }, seg([["agent", "Agent"], ["model", "Model"]], tg.kind, (v) => { tg.kind = v; paintSetup(); }, true),
          cmp.targets.length > 2 ? h("button", { class: "btn danger", "aria-label": "Remove", onclick: () => { cmp.targets.splice(i, 1); paintSetup(); } }, "×") : null)),
      tg.kind === "agent" ? sel(agentOpts("Choose one of your agents"), tg.agent_id, (v) => (tg.agent_id = v)) : providerEditor(tg.pv));
    fill(setup,
      h("div", { class: "label", style: "margin:0" }, "The models to compare"),
      cmp.targets.map(row),
      cmp.targets.length < 8 ? h("button", { class: "btn plain", onclick: () => { cmp.targets.push({ kind: "model", agent_id: "", pv: { preset: "mock" } }); paintSetup(); } }, "+ Add another model") : null,
      field("The question they all get", h("textarea", { style: "min-height:90px;font-family:var(--font);font-size:.95rem", value: cmp.prompt, placeholder: "Explain what a vector database is, in three sentences.", oninput: (e) => (cmp.prompt = e.target.value) })),
      h("details", {}, h("summary", {}, "Options"), h("div", { class: "stack gap" },
        field("System prompt (same for all)", h("textarea", { style: "min-height:60px;font-family:var(--font)", value: cmp.system, oninput: (e) => (cmp.system = e.target.value) })),
        h("div", { class: "optgrid" },
          field("Ask each model this many times", inp(cmp.repeats, (v) => (cmp.repeats = v), { type: "number", min: 1, max: 5 }), "More runs give a fair latency: the median is used, and you see the spread."),
          field("Max tokens per answer", inp(cmp.max_tokens, (v) => (cmp.max_tokens = v), { type: "number", min: 16, max: 32768 })),
          field("Temperature", inp(cmp.temperature, (v) => (cmp.temperature = v), { type: "number", min: 0, max: 2, step: 0.1, placeholder: "default" }))))),
      h("div", { class: "row" }, run, h("span", { class: "meta" }, `${cmp.targets.length} models · ${cmp.repeats} run${cmp.repeats > 1 ? "s" : ""} each = ${cmp.targets.length * cmp.repeats} calls`)), err);
  };
  const run = h("button", { class: "btn", onclick: () => busy(run, async () => {
    cmp.verdict = null;
    cmp.result = await lapi("/api/lab/compare", {
      targets: cmp.targets.map((tg, i) => ({ ...targetSpec(tg), label: targetLabel(tg) || `Model ${String.fromCharCode(65 + i)}` })),
      prompt: cmp.prompt, system: cmp.system || undefined, repeats: +cmp.repeats || 1,
      max_tokens: +cmp.max_tokens || undefined, temperature: cmp.temperature === "" ? undefined : +cmp.temperature,
    });
    paintOut(); loadHistory();
  }, err) }, "Compare");
  const bestOf = (key, dir) => {
    const vals = (cmp.result?.results || []).filter((r) => r.ok && r.metrics[key] != null).map((r) => r.metrics[key]);
    if (vals.length < 2 || !dir || new Set(vals).size === 1) return null;  // a tie has no winner
    return (dir === "low" ? Math.min : Math.max)(...vals);
  };
  const paintOut = () => {
    const c = cmp.result;
    if (!c) return fill(out, h("div", { class: "card empty" }, h("b", {}, "Pick two of your models and ask them the same thing"),
      "You get both answers side by side, with the tokens, cost, speed and how much they agree."));
    const ok = c.results.filter((r) => r.ok);
    const W = [["fastest", "Fastest"], ["cheapest", "Cheapest"], ["fewest_tokens", "Fewest tokens"], ["most_concise", "Most concise"], ["most_detailed", "Most detailed"], ["steadiest", "Steadiest speed"]];
    const judgeBtn = h("button", { class: "btn plain", onclick: () => busy(judgeBtn, async () => {
      cmp.verdict = await lapi(`/api/lab/compare/${c.id}/judge`, { provider: toProvider(cmp.targets[0].kind === "model" ? cmp.targets[0].pv : { preset: "mock" }) });
      paintOut();
    }, err) }, "Ask a model to rank the answers");
    fill(out,
      h("div", { class: "card" }, h("div", { class: "row", style: "justify-content:space-between" }, h("div", { class: "label", style: "margin:0" }, "Measured winners"),
        h("span", { class: "meta" }, `${fmtN(c.totals.tokens)} tokens · ${money(c.totals.cost)} · ${c.totals.calls} calls`)),
        h("div", { class: "row gap" }, W.map(([k, lab]) => (c.winners[k] ? chip(`${lab}: ${c.winners[k]}`, "ok") : null))),
        h("div", { class: "meta gap" }, c.note)),
      h("div", { class: "card" }, h("div", { class: "label" }, "Analytics"),
        h("div", { class: "tablewrap" }, h("table", { class: "cmp" },
          h("tr", {}, h("th", {}, "Measure"), ok.map((r) => h("th", {}, r.label, h("div", { class: "meta" }, r.model)))),
          METRICS.map(([key, label, fmt, dir]) => {
            const best = bestOf(key, dir);
            return h("tr", {}, h("td", {}, label), ok.map((r) => {
              const v = r.metrics[key];
              return h("td", { class: dir && v === best && ok.length > 1 ? "best" : "" }, v == null ? "–" : fmt(v, r.metrics));
            }));
          }))),
        c.repeats > 1 ? h("div", { class: "meta gap" }, `Each model answered ${c.repeats} times; latency is the median of those runs.`) : null),
      ok.length > 1 ? h("div", { class: "card" }, h("div", { class: "label" }, "How much the answers overlap"),
        h("div", { class: "stack" }, c.pairs.map((p) => h("div", {}, h("div", { class: "kv meta" }, h("span", {}, `${p.a} vs ${p.b}`), h("span", {}, `${Math.round(p.overlap * 100)}% shared words`)), bar(p.overlap * 100, "", "grad")))),
        h("div", { class: "meta gap" }, "Shared vocabulary, not agreement on meaning: a low number simply means they said it differently.")) : null,
      h("div", { class: "cmpgrid" }, c.results.map((r) => h("div", { class: "card" },
        h("div", { class: "row", style: "justify-content:space-between" }, h("b", {}, r.label), r.ok ? h("span", { class: "meta" }, `${fmtN(r.metrics.output_tokens)} out · ${fmtN(r.metrics.latency_ms)} ms · ${money(r.metrics.cost)}`) : chip("failed", "bad")),
        r.ok ? h("div", { class: "answer" }, r.text || "(empty answer)") : h("div", { class: "err" }, r.error),
        r.ok && r.finish === "length" ? h("div", { class: "meta" }, chip("cut off by the token limit", "warn")) : null))),
      h("div", { class: "card" }, h("div", { class: "row", style: "justify-content:space-between" }, h("div", { class: "label", style: "margin:0" }, "Which answer is better?"), judgeBtn),
        cmp.verdict ? h("div", { class: "stack gap" },
          h("div", { class: "row" }, cmp.verdict.ranking.map((l, i) => chip(`${i + 1}. ${l}`, i ? "mute" : "ok"))),
          cmp.verdict.summary ? h("p", {}, cmp.verdict.summary) : null,
          h("div", { class: "rows tight" }, cmp.verdict.scores.map((s_) => h("div", { class: "kv" }, h("span", {}, h("b", {}, s_.label), " ", s_.why), h("span", { class: "meta" }, `${s_.score}/10`)))),
          h("div", { class: "meta" }, `${cmp.verdict.caveat} Judged by ${cmp.verdict.by}.`))
          : h("div", { class: "meta gap" }, "The numbers above are measurements. Quality is a judgement: a model can give its opinion, and it will be labelled as one.")));
  };
  const hist = h("div", { class: "list card" });
  const loadHistory = async () => {
    try { cmp.history = (await lapi("/api/lab/compare")).comparisons; } catch { return; }
    fill(hist, cmp.history.length ? cmp.history.map((x) => h("button", { onclick: async () => { cmp.result = await lapi(`/api/lab/compare/${x.id}`); cmp.verdict = null; paintOut(); } },
      h("div", { class: "t" }, x.prompt), h("div", { class: "meta" }, x.labels.join(" vs "), ago(x.ts)),
      h("div", { class: "meta" }, x.totals?.tokens ? `${fmtN(x.totals.tokens)} tokens` : null, x.winners?.fastest ? `fastest: ${x.winners.fastest}` : null)))
      : h("div", { class: "empty" }, "No comparisons yet."));
  };
  loadLab(["agents", "presets", "secrets"]).then(() => { if (current === "compare") paintSetup(); });
  paintSetup(); paintOut(); loadHistory();
  return [...lhead("Compare models", "Give two or more of your models the same question. You get both answers side by side and the numbers that matter: tokens, cost, speed and how much they overlap."),
    h("div", { class: "split" }, h("div", { class: "stack" }, setup, h("div", {}, h("div", { class: "label" }, "Earlier comparisons"), hist)), out)];
}
ICONS.compare = '<path d="M12 3v18M7 7H3l3.5 7L10 7H7zM21 7h-4l3.5 7L24 7"/><path d="M4 20h6M14 20h6"/>';
NAV[1][1].push(["compare", "Compare"]);
TABS.push(["compare", "Compare"]);
pages.compare = pageCompare;
