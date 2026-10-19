"use strict";

/* ---------- helpers ---------- */
const $ = (id) => document.getElementById(id);
const h = (tag, props = {}, ...kids) => {
  const el = document.createElement(tag);
  const later = [];
  for (const [k, v] of Object.entries(props)) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "value" || k === "checked") later.push([k, v]);
    else el.setAttribute(k, v === true ? (k.startsWith("aria-") ? "true" : "") : v);
  }
  const spaced = /\b(meta|insights)\b/.test(props.class || ""); // flex rows: give each text its own box so the gap applies
  el.append(...kids.flat(Infinity).filter((k) => k != null && k !== false).map((k) => (k.nodeType ? k : spaced ? Object.assign(document.createElement("span"), { textContent: String(k) }) : document.createTextNode(String(k)))));
  for (const [k, v] of later) el[k] = v;
  return el;
};
const TOKEN = document.querySelector('meta[name="agentlab-token"]').content;
const api = async (path, body) => {
  const init = { headers: { "X-AgentLab-UI": TOKEN } };
  if (body !== undefined) Object.assign(init, { method: "POST", headers: { ...init.headers, "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const res = await fetch(path, init);
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
};
const debounce = (fn, ms = 300) => { let id; const d = (...a) => { clearTimeout(id); id = setTimeout(() => fn(...a), ms); }; d.cancel = () => clearTimeout(id); return d; };
const fill = (el, ...kids) => el.replaceChildren(...kids.flat(Infinity).filter((k) => k != null && k !== false && k !== ""));
const chip = (text, tone = "") => h("span", { class: "chip " + tone }, text);
const download = (name, text, type = "text/plain") => {
  const a = h("a", { href: URL.createObjectURL(new Blob([text], { type })), download: name });
  document.body.append(a); a.click(); a.remove();
};
const copyText = async (text, btn) => {
  try { await navigator.clipboard.writeText(text); } catch {
    const ta = h("textarea", { value: text }); document.body.append(ta); ta.select(); document.execCommand("copy"); ta.remove();
  }
  if (btn) { const old = btn.textContent; btn.textContent = t("copied"); setTimeout(() => (btn.textContent = old), 1500); }
};

/* ---------- settings + i18n ---------- */
const DEFAULTS = { lang: null, theme: "auto", accent: "blue", size: "m", codeLang: "python", lineNumbers: true, wrap: false, reduceMotion: false,
  gradient: "aurora", gradA: "#7f5af0", gradB: "#2cb67d", gradC: "", gradAngle: 135, gradButtons: false, gradBg: false, gradSide: false };
const GRADIENTS = { aurora: ["#7f5af0", "#2cb67d"], sunset: ["#ff7e5f", "#feb47b"], ocean: ["#2193b0", "#6dd5ed"], candy: ["#ff6cab", "#7366ff"], forest: ["#134e5e", "#71b280"],
  fire: ["#f12711", "#f5af19"], dusk: ["#2c3e50", "#fd746c"], mint: ["#00b09b", "#96c93d"], royal: ["#141e30", "#243b55"], peach: ["#ffd3a5", "#fd6585"], mono: ["#434343", "#8e9eab"] };
const gradientCss = () => {
  const c = S.gradient === "custom" ? [S.gradA, S.gradB, S.gradC].filter(Boolean) : GRADIENTS[S.gradient] || GRADIENTS.aurora;
  return `linear-gradient(${+S.gradAngle || 135}deg, ${c.join(", ")})`;
};
const ACCENTS = { blue: ["#0071e3", "#0a84ff"], purple: ["#8944ab", "#bf5af2"], pink: ["#d30f5b", "#ff375f"], orange: ["#c93400", "#ff9f0a"], green: ["#248a3d", "#30d158"], graphite: ["#636366", "#98989d"] };
const SIZES = { s: "14px", m: "15px", l: "17px" };
const CODE_LANGS = ["python", "javascript", "typescript", "shell"];
let S = { ...DEFAULTS };
try { Object.assign(S, JSON.parse(localStorage.getItem("agentlab.settings") || "{}")); } catch {}
let I18N = { en: {} };
const t = (key, vars) => String(I18N[S.lang]?.[key] ?? I18N.en[key] ?? key).replace(/\{(\w+)\}/g, (_, n) => vars?.[n] ?? "");
const dark = matchMedia("(prefers-color-scheme: dark)");

function applySettings() {
  const root = document.documentElement;
  const theme = S.theme === "auto" ? (dark.matches ? "dark" : "light") : S.theme;
  root.dataset.theme = theme;
  root.dataset.motion = S.reduceMotion ? "reduce" : "";
  root.lang = S.lang || "en";
  root.style.setProperty("--accent", (ACCENTS[S.accent] || ACCENTS.blue)[theme === "dark" ? 1 : 0]);
  root.style.setProperty("--fs", SIZES[S.size] || SIZES.m);
  const none = S.gradient === "none";
  root.style.setProperty("--grad", none ? "var(--accent)" : gradientCss());
  root.dataset.gbtn = !none && S.gradButtons ? "1" : "";
  root.dataset.gbg = !none && S.gradBg ? "1" : "";
  root.dataset.gside = !none && S.gradSide ? "1" : "";
}
function setSetting(patch) {
  Object.assign(S, patch);
  try { localStorage.setItem("agentlab.settings", JSON.stringify(S)); } catch {}
  applySettings();
  renderChrome();
  if (!$("overlay").hidden && $("overlay").dataset.kind === "settings") renderSettings();
  renderPage();
}
dark.addEventListener("change", applySettings);

/* ---------- navigation ---------- */
const NAV = [
  ["nav_monitor", [["dashboard", "tab_dashboard"], ["activity", "tab_activity"]]],
  ["nav_run", [["tasks", "tab_tasks"], ["pad", "tab_pad"]]],
  ["nav_protect", [["sandbox", "tab_sandbox"], ["checks", "tab_checks"]]],
  ["nav_connect", [["connectors", "tab_connectors"], ["api", "tab_api"]]],
];
const TABS = NAV.flatMap(([, items]) => items);
const ICONS = {
  dashboard: '<rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/>',
  activity: '<path d="M3 12h4l3-8 4 16 3-8h4"/>',
  tasks: '<rect x="3" y="4" width="18" height="16" rx="3"/><path d="M10 9l5 3-5 3z"/>',
  pad: '<path d="M8 8l-4 4 4 4M16 8l4 4-4 4M14 5l-4 14"/>',
  sandbox: '<path d="M12 3l8 3v6c0 4.5-3.2 7.8-8 9-4.8-1.2-8-4.5-8-9V6z"/>',
  checks: '<circle cx="12" cy="12" r="9"/><path d="M8 12.5l3 3 5-6"/>',
  connectors: '<path d="M9 7V3M15 7V3M7 7h10v4a5 5 0 0 1-10 0zM12 16v5"/>',
  api: '<path d="M8 4c-2 0-3 1-3 3v2c0 1.5-1 3-2 3 1 0 2 1.5 2 3v2c0 2 1 3 3 3M16 4c2 0 3 1 3 3v2c0 1.5 1 3 2 3-1 0-2 1.5-2 3v2c0 2-1 3-3 3"/>',
};
const icon = (id) => { const s_ = document.createElementNS("http://www.w3.org/2000/svg", "svg"); s_.setAttribute("width", "18"); s_.setAttribute("height", "18"); s_.setAttribute("viewBox", "0 0 24 24"); s_.setAttribute("fill", "none"); s_.setAttribute("stroke", "currentColor"); s_.setAttribute("stroke-width", "1.7"); s_.setAttribute("stroke-linecap", "round"); s_.setAttribute("stroke-linejoin", "round"); s_.innerHTML = ICONS[id]; return s_; };
let current = "dashboard", poll = null, leave = null;

const navLabel = (k) => (k in I18N.en ? t(k) : typeof L === "function" ? L(k) : k); // platform pages (lab.js) use plain English keys
function renderChrome() {
  $("nav").replaceChildren(...NAV.map(([group, items]) => h("div", { class: "nav-group" }, h("div", { class: "nav-label" }, navLabel(group)),
    items.map(([id, key]) => h("button", { class: "nav-item", "aria-current": id === current, onclick: () => show(id) }, icon(id), h("span", {}, navLabel(key)))))));
  $("openSettings").setAttribute("aria-label", t("settings"));
  $("status").title = $("status").classList.contains("up") ? t("connected") : t("offline");
}
async function show(name) {
  if (leave) await leave();
  leave = null; clearInterval(poll); poll = null;
  current = name;
  history.replaceState(null, "", "#" + name);
  renderChrome();
  renderPage();
  scrollTo(0, 0);
}
function renderPage() {
  clearInterval(poll); poll = null;
  $("page").replaceChildren(h("div", { class: "reveal" }, pages[current]()));
}
const head = (title, sub) => [h("h1", {}, t(title)), h("p", { class: "lead" }, t(sub))];

/* ---------- activity ---------- */
const act = { runs: [], selected: null, events: [], upto: 0 };
const ago = (ts) => {
  const s = Math.max(0, Date.now() / 1000 - ts);
  return s < 60 ? t("just_now") : s < 3600 ? t("m_ago", { n: Math.floor(s / 60) }) : s < 86400 ? t("h_ago", { n: Math.floor(s / 3600) }) : t("d_ago", { n: Math.floor(s / 86400) });
};
const SOURCES = { kimi_code_cli: "Kimi", openclaw: "OpenClaw", task: "Task" };
const ag = { list: [] };
async function loadAgents() { try { ag.list = (await api("/api/agents")).agents; } catch {} }
const kindTone = { decision: "bad", error: "bad", output: "ok", file_edit: "warn", prompt: "", tool_call: "mute", tool_result: "mute" };

async function loadRuns() {
  try { act.runs = (await api("/api/runs")).runs; } catch { return; }
  if (current !== "activity") return;
  paintRunList();
  const r = act.runs.find((x) => x.id === act.selected);
  if (r && r.events + 1 !== act.events.length) openRun(act.selected, true);
}
function paintRunList() {
  const box = $("runList");
  if (!box) return;
  if (!act.runs.length) { box.replaceChildren(h("div", { class: "empty" }, h("b", {}, t("no_runs")), t("no_runs_hint"), h("button", { class: "btn gap", onclick: () => show("connectors") }, t("connect")))); return; }
  box.replaceChildren(...act.runs.map((r) => h("button", { "aria-current": r.id === act.selected, onclick: () => openRun(r.id) },
    h("div", { class: "t" }, r.title || r.id),
    h("div", { class: "meta" }, `${t("steps", { n: r.events })} · ${ago(r.last)}`, r.blocked ? chip(t("blocked_n", { n: r.blocked }), "bad") : null, r.errors ? chip(t("errors_n", { n: r.errors }), "bad") : null, r.forked_from ? chip(t("fork_tag"), "mute") : null, SOURCES[r.source] ? chip(SOURCES[r.source], "mute") : null))));
}
async function openRun(id, keep = false) {
  act.selected = id;
  try { act.events = (await api("/api/runs/" + encodeURIComponent(id))).events; } catch { return; }
  const max = act.events.length - 1;
  if (!keep || act.upto > max || act.upto === act.prevMax) act.upto = max;
  act.prevMax = max;
  paintRunList();
  paintRun();
}
function paintRun() {
  const box = $("runView");
  if (!box) return;
  if (!act.events.length) { box.replaceChildren(h("div", { class: "empty" }, t("select_run"))); return; }
  const max = act.events.length - 1, t0 = act.events[0].ts;
  const label = h("span", { class: "meta" }, t("step_of", { a: act.upto, b: max }));
  const list = h("div");
  const paintEvents = () => list.replaceChildren(...act.events.slice(1).map((e) => h("div", { class: "ev" + (e.step > act.upto ? " future" : "") },
    h("div", { class: "n" }, e.step),
    h("div", {}, chip(t("k_" + e.kind), kindTone[e.kind] ?? ""), h("div", { class: "t" }, `+${(e.ts - t0).toFixed(1)}s${e.tool ? " · " + e.tool : ""}`)),
    h("div", { class: "c" }, String(e.content)))));
  const slider = h("input", { type: "range", min: 0, max, value: act.upto, "aria-label": t("step_of", { a: act.upto, b: max }), oninput: (e) => { act.upto = +e.target.value; label.textContent = t("step_of", { a: act.upto, b: max }); paintEvents(); } });
  const fork = h("button", { class: "btn plain", onclick: async () => {
    try { const f = await api(`/api/runs/${encodeURIComponent(act.selected)}/fork`, { step: act.upto }); await loadRuns(); openRun(f.id); } catch (e) { alert(e.message); }
  } }, t("fork_btn"));
  box.replaceChildren(h("div", { class: "scrub" }, slider, label, fork), list);
  paintEvents();
}

/* ---------- sandbox ---------- */
const sb = { rules: "", loaded: false };
function pageSandbox() {
  const err = h("div", { class: "err" });
  const sim = h("div"), perms = h("pre"), tryOut = h("span");
  const saved = h("span");
  const ta = h("textarea", { spellcheck: "false", placeholder: "allow read src/**\nallow write src/\nallow run pytest\nnever read .env", value: sb.rules, "aria-label": t("rules"), oninput: (e) => { sb.rules = e.target.value; refresh(); } });
  const act_ = h("select", { onchange: () => tryNow() }, ["read", "write", "run", "network"].map((a) => h("option", {}, a)));
  const target = h("input", { type: "text", placeholder: "src/app.py", spellcheck: "false", oninput: () => tryNow() });
  const tryNow = debounce(async () => {
    if (!target.value.trim() || !sb.rules.trim()) return tryOut.replaceChildren();
    try {
      const r = await api("/api/policy/check", { rules: sb.rules, action: act_.value, target: target.value.trim() });
      tryOut.replaceChildren(chip(r.allowed ? t("allowed") : t("blocked"), r.allowed ? "ok" : "bad"), h("span", { class: "meta", style: "margin-left:8px" }, r.reason));
    } catch {}
  }, 150);
  const refresh = debounce(async () => {
    err.textContent = "";
    if (!sb.rules.trim()) { sim.replaceChildren(h("div", { class: "empty" }, t("add_rule"))); perms.textContent = ""; return; }
    try {
      const [s, p] = await Promise.all([api("/api/policy/simulate", { rules: sb.rules }), api("/api/policy/settings", { rules: sb.rules })]);
      const open = s.results.filter((r) => r.allowed).length;
      sim.replaceChildren(
        h("div", { class: "row", style: "margin-bottom:6px" }, chip(t("n_blocked", { n: s.results.length - open }), "ok"), open ? chip(t("n_allowed", { n: open }), "bad") : null),
        h("div", { class: "rows tight" }, s.results.map((r) => h("div", { class: "kv" }, h("span", { class: "mono" }, `${r.action} ${r.target}`), chip(r.allowed ? t("allowed") : t("blocked"), r.allowed ? "bad" : "ok")))));
      perms.textContent = JSON.stringify(p, null, 2);
      tryNow();
    } catch (e) { err.textContent = e.message; }
  }, 250);
  if (!sb.loaded) api("/api/policy").then((p) => { sb.loaded = true; if (!sb.rules) { sb.rules = p.rules; ta.value = p.rules; } refresh(); }).catch(() => {});
  else refresh();
  const save = h("button", { class: "btn", onclick: async () => {
    err.textContent = "";
    try { await api("/api/policy", { rules: sb.rules }); saved.replaceChildren(chip(t("saved"), "ok")); setTimeout(() => saved.replaceChildren(), 2200); } catch (e) { err.textContent = e.message; }
  } }, t("save_policy"));
  return [...head("sb_title", "sb_sub"), h("div", { class: "grid2" },
    h("div", { class: "card" }, h("label", {}, t("rules")), ta, h("div", { class: "row gap" }, save, saved), err,
      h("div", { class: "label gap" }, t("try_action")), h("div", { class: "row" }, h("div", { style: "flex:0 0 110px" }, act_), h("div", { class: "grow" }, target)), h("div", { class: "gap" }, tryOut)),
    h("div", { class: "stack" }, h("div", { class: "card" }, h("div", { class: "label" }, t("simulation")), sim), h("div", { class: "card" }, h("div", { class: "label" }, t("permissions")), perms)))];
}

/* ---------- regression ---------- */
const rg = {
  base: JSON.stringify({ greeting: { output: "Hello! How can I help you today?", must_contain: ["hello"] } }, null, 2),
  cand: JSON.stringify({ greeting: { output: "Go away." } }, null, 2), thr: 0.8,
};
function pageRegress() {
  const err = h("div", { class: "err" }), out = h("div", { class: "card gap", hidden: true });
  const base = h("textarea", { spellcheck: "false", value: rg.base, oninput: (e) => (rg.base = e.target.value), "aria-label": t("baseline") });
  const cand = h("textarea", { spellcheck: "false", value: rg.cand, oninput: (e) => (rg.cand = e.target.value), "aria-label": t("candidate") });
  const thr = h("input", { type: "number", min: 0, max: 1, step: 0.05, value: rg.thr, oninput: (e) => (rg.thr = +e.target.value) });
  const tone = { ok: "ok", new: "mute" };
  const run = h("button", { class: "btn", onclick: async () => {
    err.textContent = "";
    let baseline, candidate;
    try { baseline = JSON.parse(rg.base); candidate = JSON.parse(rg.cand); } catch { err.textContent = t("invalid_json"); return; }
    try {
      const r = await api("/api/regress", { baseline, candidate, threshold: rg.thr });
      out.hidden = false;
      out.replaceChildren(h("div", { class: "row" }, chip(r.regressed ? t("regressions_found") : t("no_regressions"), r.regressed ? "bad" : "ok")),
        h("div", { class: "rows" }, r.findings.map((f) => h("div", {},
          h("div", { class: "row" }, chip(t("st_" + f.status), tone[f.status] || "bad"), h("b", {}, f.case), f.similarity != null ? h("span", { class: "meta" }, t("similar", { n: Math.round(f.similarity * 100) })) : null),
          f.detail && f.status !== "ok" ? h("pre", { class: "gap" }, f.detail) : null))));
    } catch (e) { err.textContent = e.message; }
  } }, t("compare"));
  return [...head("rg_title", "rg_sub"),
    h("div", { class: "grid2" }, h("div", { class: "card" }, h("label", {}, t("baseline")), base), h("div", { class: "card" }, h("label", {}, t("candidate")), cand)),
    h("div", { class: "row end gap" }, h("div", { style: "width:170px" }, h("label", {}, t("threshold")), thr), run), err, out];
}

/* ---------- tripwires ---------- */
const tw = { seed: "my-secret", n: 3, doc: "", answer: "" };
function pageTripwires() {
  const plantOut = h("div", { class: "stack gap" }), auditOut = h("div", { class: "gap" });
  const perr = h("div", { class: "err" }), aerr = h("div", { class: "err" });
  const bind = (key, num) => (e) => (tw[key] = num ? +e.target.value : e.target.value);
  const all = h("input", { type: "checkbox", class: "switch", "aria-label": t("require_all") });
  const common = () => ({ seed: tw.seed, n: tw.n });
  const statusTone = { correct: "ok", contradicted: "bad", unsupported: "bad", unused: "mute" };
  return [...head("tw_title", "tw_sub"), h("div", { class: "grid2" },
    h("div", { class: "card" },
      h("div", { class: "row end" }, h("div", { class: "grow" }, h("label", {}, t("seed")), h("input", { type: "text", value: tw.seed, spellcheck: "false", oninput: bind("seed") })), h("div", { style: "width:90px" }, h("label", {}, t("canaries")), h("input", { type: "number", min: 1, max: 10, value: tw.n, oninput: bind("n", true) }))),
      h("div", { class: "gap" }, h("label", {}, t("document")), h("textarea", { placeholder: t("doc_ph"), value: tw.doc, oninput: bind("doc") })),
      h("div", { class: "row gap" }, h("button", { class: "btn", onclick: async () => {
        perr.textContent = "";
        try {
          const r = await api("/api/tripwires/plant", { ...common(), document: tw.doc });
          plantOut.replaceChildren(h("div", {}, h("div", { class: "label" }, t("doc_with")), h("pre", {}, r.document)),
            h("div", {}, h("div", { class: "label" }, t("ask_expect")), h("div", { class: "rows tight" }, r.questions.map((q) => h("div", { class: "kv" }, h("span", {}, q.question), h("span", { class: "mono" }, q.expected))))));
        } catch (e) { perr.textContent = e.message; }
      } }, t("plant"))), perr, plantOut),
    h("div", { class: "card" },
      h("label", {}, t("answer_label")), h("textarea", { placeholder: t("answer_ph"), value: tw.answer, oninput: bind("answer") }),
      h("div", { class: "row gap" }, h("button", { class: "btn", onclick: async () => {
        aerr.textContent = "";
        try {
          const r = await api("/api/tripwires/audit", { ...common(), answer: tw.answer, expect_all: all.checked });
          auditOut.replaceChildren(chip(r.healthy ? t("looks_healthy") : t("possible_hall"), r.healthy ? "ok" : "bad"),
            h("div", { class: "rows tight gap" }, r.report.map((x) => h("div", { class: "kv" }, h("span", {}, x.subject), chip(t("s_" + x.status), statusTone[x.status])))));
        } catch (e) { aerr.textContent = e.message; }
      } }, t("audit")), h("label", { class: "row", style: "margin:0;gap:8px" }, all, t("require_all"))), aerr, auditOut))];
}

/* ---------- pad ---------- */
const PAD_LANGS = ["javascript", "typescript", "python", "shell", "json", "text"];
const PAD_LABEL = { javascript: "JavaScript", typescript: "TypeScript", python: "Python", shell: "Shell", json: "JSON", text: "Text" };
const TEMPLATES = { javascript: 'console.log("Hello from the pad");\n', typescript: 'const greet = (name: string) => `Hello, ${name}`;\nconsole.log(greet("pad"));\n', python: 'print("Hello from the pad")\n', shell: 'echo "Hello from the pad"\n', json: "{\n  \n}\n", text: "" };
const pd = { pads: [], pad: null, dirty: false, status: null, timer: null };
const GEN_TO_PAD = { python: "python", javascript: "javascript", typescript: "typescript", curl: "shell" };

async function loadPads() { try { pd.pads = (await api("/api/pads")).pads; } catch {} }
async function savePad() {
  if (!pd.pad || !pd.dirty) return;
  pd.dirty = false;
  try { pd.pad = { ...pd.pad, ...(await api("/api/pads", pd.pad)) }; await loadPads(); } catch (e) { pd.dirty = true; }
  if (pd.status && current === "pad") { pd.status.replaceChildren(pd.dirty ? "" : chip(t("saved"), "ok")); paintPadList(); }
}
const queueSave = debounce(savePad, 500);
async function newPad(language = S.codeLang === "shell" ? "shell" : S.codeLang, content, name) {
  await savePad();
  pd.pad = await api("/api/pads", { name: name || t("untitled"), language, content: content ?? TEMPLATES[language] ?? "" });
  pd.dirty = false;
  await loadPads();
}
async function openPad(id) {
  await savePad();
  pd.pad = await api("/api/pads/" + id);
  pd.dirty = false;
  renderPage();
}
function paintPadList() {
  const box = $("padList");
  if (!box) return;
  if (!pd.pads.length) { box.replaceChildren(h("div", { class: "empty" }, t("no_pads"))); return; }
  box.replaceChildren(...pd.pads.map((p) => h("button", { "aria-current": p.id === pd.pad?.id, onclick: () => openPad(p.id) }, h("div", { class: "t" }, p.name), h("div", { class: "meta" }, PAD_LABEL[p.language]))));
}
function runJs(code, onLine, onDone) {
  const frame = h("iframe", { sandbox: "allow-scripts", src: "/static/runner.html", hidden: true, "aria-hidden": "true", tabindex: "-1" });
  let timer;
  const finish = () => { clearTimeout(timer); removeEventListener("message", onMsg); frame.remove(); onDone(); };
  const onMsg = (e) => {
    if (e.source !== frame.contentWindow || !e.data) return;
    if (e.data.ready) frame.contentWindow.postMessage(code, "*");
    else if (e.data.done) finish();
    else if (e.data.level) onLine(e.data);
  };
  timer = setTimeout(() => { onLine({ level: "error", text: "stopped after 5s" }); finish(); }, 5000);
  addEventListener("message", onMsg);
  document.body.append(frame);
}
/* ---------- code pad: highlighting, smart editing, find/replace, run, review ---------- */
const KW = {
  javascript: "await async break case catch class const continue debugger default delete do else export extends finally for from function if import in instanceof let new of return static super switch this throw try typeof var void while with yield",
  typescript: "abstract any as asserts await async boolean break case catch class const constructor continue declare default delete do else enum export extends finally for from function if implements import in instanceof interface let namespace new number of private protected public readonly return static string super switch this throw try type typeof var void while yield",
  python: "and as assert async await break class continue def del elif else except False finally for from global if import in is lambda None nonlocal not or pass raise return True try while with yield match case",
  shell: "if then else elif fi for while do done case esac function return export local readonly set unset shift source exit trap echo cd",
  json: "true false null",
  text: "",
};
const LINE_COMMENT = { javascript: "//", typescript: "//", python: "#", shell: "#", json: null, text: null };
const PAIRS = { "(": ")", "[": "]", "{": "}", '"': '"', "'": "'", "`": "`" };
const escHtml = (x) => x.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
const TRIPLE = ['"""', "'''"];

function highlight(code, lang) {
  /* One pass over the source: strings, comments, numbers, keywords, calls. A reader's aid, not a parser. */
  const words = new Set((KW[lang] || "").split(" ").filter(Boolean));
  const lineC = LINE_COMMENT[lang];
  let out = "", i = 0;
  const push = (cls, text) => (out += cls ? `<span class="t-${cls}">${escHtml(text)}</span>` : escHtml(text));
  while (i < code.length) {
    const c = code[i], rest = code.slice(i);
    if (lineC && rest.startsWith(lineC)) { const n = code.indexOf("\n", i); const stop = n < 0 ? code.length : n; push("com", code.slice(i, stop)); i = stop; continue; }
    if ((lang === "javascript" || lang === "typescript") && rest.startsWith("/*")) { const stop = code.indexOf("*/", i + 2); const end = stop < 0 ? code.length : stop + 2; push("com", code.slice(i, end)); i = end; continue; }
    const tri = lang === "python" ? TRIPLE.find((q) => rest.startsWith(q)) : null;
    if (tri) { const stop = code.indexOf(tri, i + 3); const end = stop < 0 ? code.length : stop + 3; push("str", code.slice(i, end)); i = end; continue; }
    if (c === '"' || c === "'" || c === "`") {
      let j = i + 1;
      while (j < code.length && code[j] !== c) { if (code[j] === "\\") j++; if (code[j] === "\n" && c !== "`") break; j++; }
      push("str", code.slice(i, Math.min(j + 1, code.length))); i = j + 1; continue;
    }
    if (/[0-9]/.test(c) && !/[\w.]/.test(code[i - 1] || "")) { const m = rest.match(/^[0-9][\w.]*/)[0]; push("num", m); i += m.length; continue; }
    if (/[A-Za-z_$@]/.test(c)) {
      const m = rest.match(/^[\w$@]+/)[0];
      const call = code.slice(i + m.length).match(/^\s*\(/);
      push(words.has(m) ? "kw" : call ? "fn" : null, m); i += m.length; continue;
    }
    push(/[{}()[\].,;:]/.test(c) ? "pun" : null, c); i++;
  }
  return out + "\n";
}

/* ---------- live suggestions: a small offline checker that runs as you type ---------- */
const OPENERS = { ")": "(", "]": "[", "}": "{" };
function lintCode(text, lang) {
  const out = [], lines = text.split("\n");
  const add = (line, level, message, fix) => out.push({ line, level, message, fix });
  // brackets and quotes, skipping strings and comments
  const stack = [];
  let i = 0, line = 1, inStr = null, strLine = 1, lineC = LINE_COMMENT[lang];
  while (i < text.length) {
    const c = text[i], two = text.slice(i, i + 2), three = text.slice(i, i + 3);
    if (c === "\n") { line++; i++; continue; }
    if (inStr) {
      if (c === "\\") { i += 2; continue; }
      if (inStr.length === 3 ? three === inStr : c === inStr) { inStr = null; i += inStr === null && three === two + text[i + 2] ? 1 : 1; }
      else if (c === "\n") line++;
      i++;
      continue;
    }
    if (lineC && text.startsWith(lineC, i)) { const n = text.indexOf("\n", i); i = n < 0 ? text.length : n; continue; }
    if ((lang === "javascript" || lang === "typescript") && two === "/*") { const n = text.indexOf("*/", i + 2); const stop = n < 0 ? text.length : n + 2; line += text.slice(i, stop).split("\n").length - 1; i = stop; continue; }
    if (lang === "python" && TRIPLE.includes(three)) { inStr = three; strLine = line; i += 3; continue; }
    if (c === '"' || c === "'" || c === "`") { inStr = c; strLine = line; i++; continue; }
    if ("([{".includes(c)) stack.push({ c, line });
    else if (OPENERS[c]) {
      const top = stack.pop();
      if (!top) add(line, "error", `Closing ${c} with nothing open.`);
      else if (top.c !== OPENERS[c]) add(line, "error", `${c} closes a ${top.c} opened on line ${top.line}.`);
    }
    i++;
  }
  if (inStr) add(strLine, "error", "This string is never closed.");
  stack.forEach((s) => add(s.line, "error", `${s.c} opened here is never closed.`));

  lines.forEach((raw, idx) => {
    const n = idx + 1, l = raw.trim();
    if (raw.length > 200) add(n, "info", `Very long line (${raw.length} characters). Consider breaking it up.`);
    if (/\s+$/.test(raw) && l) add(n, "info", "Trailing whitespace.", { find: raw, replace: raw.replace(/\s+$/, "") });
    if (/\t/.test(raw) && lang === "python") add(n, "warn", "Tab indentation: Python mixes badly with spaces.", { find: raw, replace: raw.replace(/\t/g, "    ") });
    if (/\b(TODO|FIXME|XXX)\b/.test(l)) add(n, "info", "Unfinished note left in the code.");
    if (lang === "python") {
      if (/^(if|elif|else|for|while|def|class|try|except|finally|with|match|case)\b.*[^:\s\\]$/.test(l) && !l.startsWith("#") && !/[([{,]$/.test(l) && !/^(else|try|finally)$/.test(l))
        add(n, "error", "Missing colon at the end of this line.", { find: raw, replace: raw.replace(/\s*$/, ":") });
      if (/^if\s+[\w.\[\]"']+\s*=\s*[^=]/.test(l)) add(n, "error", "Assignment inside if: did you mean ==?");
      if (/^except\s*:/.test(l)) add(n, "warn", "Bare except hides real errors. Catch a specific exception.");
      if (/^print\s+[^(=]/.test(l)) add(n, "error", "Python 3 needs print(...) with brackets.");
      if (/\bl\s*=\s*\[\]/.test(l) && false) add(n, "info", "");
    }
    if (lang === "javascript" || lang === "typescript") {
      if (/[^=!<>]==[^=]/.test(l) && !l.startsWith("//")) add(n, "warn", "Loose == compares across types. === is usually what you want.", { find: raw, replace: raw.replace(/([^=!<>])==([^=])/g, "$1===$2") });
      if (/^\s*var\s/.test(raw)) add(n, "info", "var is function-scoped; let or const is safer.", { find: raw, replace: raw.replace(/^(\s*)var\s/, "$1let ") });
      if (/^\s*(if|while|for)\s*\([^)]*[^=!<>]=[^=][^)]*\)/.test(raw)) add(n, "error", "Assignment inside a condition: did you mean === ?");
      if (/\bconsole\.log\(/.test(l)) add(n, "info", "console.log left in the code.");
      if (/\bawait\b/.test(l) && !/async/.test(text.slice(0, text.split("\n").slice(0, idx + 1).join("\n").length)) && lang !== "typescript")
        add(n, "warn", "await outside an async function.");
    }
    if (lang === "shell") {
      if (/\brm\s+-[rRf]*f/.test(l) && /\$\w+/.test(l) && !/"\$/.test(l)) add(n, "error", "Unquoted variable in an rm command: quote it, or an empty value deletes the wrong thing.");
      if (/^\s*\w+\s*=\s/.test(raw)) add(n, "error", "No spaces around = in a shell assignment.", { find: raw, replace: raw.replace(/^(\s*\w+)\s*=\s+/, "$1=") });
      if (/\[\s[^\]]*\s\]/.test(l) && /==/.test(l)) add(n, "info", "Inside [ ], = compares strings; == is a bashism.");
    }
  });
  if (lang === "shell") {
    const open = (text.match(/^\s*(if)\b/gm) || []).length, close = (text.match(/^\s*fi\b/gm) || []).length;
    if (open > close) add(lines.length, "error", `${open - close} if without a matching fi.`);
    const dos = (text.match(/^\s*(for|while|until)\b/gm) || []).length, dones = (text.match(/^\s*done\b/gm) || []).length;
    if (dos > dones) add(lines.length, "error", `${dos - dones} loop without a matching done.`);
  }
  if (lang === "json" && text.trim()) {
    try { JSON.parse(text); } catch (e) {
      const m = /position (\d+)/.exec(e.message);
      const at = m ? text.slice(0, +m[1]).split("\n").length : 1;
      add(at, "error", e.message.replace(/ in JSON at position \d+.*/, "") + ".");
    }
  }
  const seen = new Set();
  return out.filter((s) => { const k = s.line + s.message; if (seen.has(k)) return false; seen.add(k); return true; })
    .sort((a, b) => a.line - b.line || ({ error: 0, warn: 1, info: 2 }[a.level] - { error: 0, warn: 1, info: 2 }[b.level])).slice(0, 40);
}

function padStats(text, sel) {
  const before = text.slice(0, sel);
  return { line: before.split("\n").length, col: sel - (before.lastIndexOf("\n") + 1) + 1,
    lines: text.split("\n").length, chars: text.length, tokens: Math.max(1, Math.round(text.length / 3.5)) };
}

function pagePad() {
  if (!pd.loaded) { pd.loaded = true; loadPads().then(async () => { if (!pd.pad && pd.pads.length) pd.pad = await api("/api/pads/" + pd.pads[0].id); if (current === "pad") renderPage(); }); }
  leave = savePad;
  const list = h("div", { class: "list card", id: "padList" });
  const left = h("div", {}, h("button", { class: "btn", style: "width:100%;margin-bottom:12px", onclick: async () => { await newPad(); renderPage(); } }, "+ " + t("new_pad")), list);
  const p = pd.pad;
  let right;
  if (!p) right = h("div", { class: "card" }, h("div", { class: "empty" }, h("b", {}, t("no_pads")), t("pad_sub")));
  else {
    const status = (pd.status = h("span", { class: "meta" }));
    const gutter = h("div", { class: "gutter", "aria-hidden": "true" });
    const code = h("code");
    const pre = h("pre", { class: "hl", "aria-hidden": "true" }, code);
    const area = h("textarea", { spellcheck: "false", "aria-label": p.name, value: p.content, wrap: "off", autocapitalize: "off", autocomplete: "off", autocorrect: "off" });
    const out = h("div", { class: "out card", hidden: true });
    const statsBar = h("div", { class: "statusbar" });
    const tips = h("div", { class: "tips" });
    const jumpTo = (ln) => {
      const pos = area.value.split("\n").slice(0, ln - 1).join("\n").length + (ln > 1 ? 1 : 0);
      area.focus(); area.setSelectionRange(pos, pos); paint();
    };
    const applyFix = (s_) => {
      const rows = area.value.split("\n");
      if (rows[s_.line - 1] !== s_.fix.find) return toast("The line changed since that suggestion", "warn");
      rows[s_.line - 1] = s_.fix.replace;
      setValue(rows.join("\n"), area.value.split("\n").slice(0, s_.line).join("\n").length);
    };
    const lintNow = debounce(() => {
      let found = [];
      try { found = lintCode(area.value, p.language); } catch { found = []; }
      const n = { error: 0, warn: 0, info: 0 };
      found.forEach((x) => n[x.level]++);
      fill(tips,
        h("div", { class: "row", style: "justify-content:space-between" },
          h("div", { class: "label", style: "margin:0" }, "Suggestions"),
          h("div", { class: "row" }, n.error ? chip(`${n.error} to fix`, "bad") : null, n.warn ? chip(`${n.warn} to check`, "warn") : null,
            n.info ? chip(`${n.info} tidy-up`, "mute") : null, found.length ? null : chip("nothing to flag", "ok"))),
        found.length ? h("div", { class: "rows tight" }, found.map((s_) => h("div", { class: "kv tip " + s_.level },
          h("button", { class: "linkish", onclick: () => jumpTo(s_.line) }, h("b", {}, `Line ${s_.line}`), " ", s_.message),
          s_.fix ? h("button", { class: "btn plain", onclick: () => applyFix(s_) }, "Fix") : null)))
          : h("div", { class: "meta", style: "padding:6px 0" }, "No problems found as you type. This is a quick offline check, not a full compiler."));
    }, 350);
    const paint = () => {
      const n = area.value.split("\n").length;
      gutter.textContent = Array.from({ length: n }, (_, i) => i + 1).join("\n");
      code.innerHTML = highlight(area.value, p.language);
      pre.scrollTop = area.scrollTop; pre.scrollLeft = area.scrollLeft;
      const st = padStats(area.value, area.selectionStart), selLen = area.selectionEnd - area.selectionStart;
      lintNow();
      fill(statsBar, h("span", {}, `Ln ${st.line}, Col ${st.col}`), selLen ? h("span", {}, `${selLen} selected`) : null,
        h("span", {}, `${st.lines} lines`), h("span", {}, `${st.chars.toLocaleString()} chars`), h("span", {}, `~${st.tokens.toLocaleString()} tokens`), h("span", { class: "grow" }), status);
    };
    const changed = () => { p.content = area.value; pd.dirty = true; status.textContent = "saving…"; paint(); queueSave(); };
    const setValue = (text, a, b) => { area.value = text; area.setSelectionRange(a, b == null ? a : b); changed(); };
    area.addEventListener("input", changed);
    area.addEventListener("scroll", () => { gutter.scrollTop = area.scrollTop; pre.scrollTop = area.scrollTop; pre.scrollLeft = area.scrollLeft; });
    ["click", "keyup", "select"].forEach((ev) => area.addEventListener(ev, paint));

    const lineRange = () => {
      const v = area.value, a = v.lastIndexOf("\n", area.selectionStart - 1) + 1;
      let b = v.indexOf("\n", area.selectionEnd);
      if (b < 0) b = v.length;
      return [a, b];
    };
    const indentBlock = (dedent) => {
      const [a, b] = lineRange(), v = area.value;
      const block = v.slice(a, b).split("\n").map((l) => (dedent ? l.replace(/^ {1,2}/, "") : "  " + l)).join("\n");
      setValue(v.slice(0, a) + block + v.slice(b), a, a + block.length);
    };
    const toggleComment = () => {
      const mark = LINE_COMMENT[p.language];
      if (!mark) return;
      const [a, b] = lineRange(), v = area.value, rows = v.slice(a, b).split("\n");
      const on = rows.every((l) => !l.trim() || l.trimStart().startsWith(mark));
      const block = rows.map((l) => (!l.trim() ? l : on ? l.replace(new RegExp("^(\\s*)" + mark.replace(/\//g, "\\/") + " ?"), "$1") : l.replace(/^(\s*)/, "$1" + mark + " "))).join("\n");
      setValue(v.slice(0, a) + block + v.slice(b), a, a + block.length);
    };
    const moveLine = (dir) => {
      const v = area.value, rows = v.split("\n"), [a] = lineRange();
      const i = v.slice(0, a).split("\n").length - 1, j = i + dir;
      if (j < 0 || j >= rows.length) return;
      const keep = area.selectionStart - a;
      [rows[i], rows[j]] = [rows[j], rows[i]];
      const text = rows.join("\n");
      const pos = rows.slice(0, j).join("\n").length + (j ? 1 : 0) + keep;
      setValue(text, pos);
    };
    const duplicate = () => {
      const [a, b] = lineRange(), v = area.value, block = v.slice(a, b);
      setValue(v.slice(0, b) + "\n" + block + v.slice(b), b + 1 + (area.selectionStart - a));
    };
    area.addEventListener("keydown", (e) => {
      const meta = e.metaKey || e.ctrlKey, v = area.value, s0 = area.selectionStart, s1 = area.selectionEnd;
      if (e.key === "Tab") { e.preventDefault(); if (s0 !== s1 || e.shiftKey) indentBlock(e.shiftKey); else setValue(v.slice(0, s0) + "  " + v.slice(s1), s0 + 2); }
      else if (meta && e.key.toLowerCase() === "s") { e.preventDefault(); queueSave.cancel(); savePad(); }
      else if (meta && e.key === "Enter") { e.preventDefault(); run(); }
      else if (meta && e.key === "/") { e.preventDefault(); toggleComment(); }
      else if (meta && e.key.toLowerCase() === "d") { e.preventDefault(); duplicate(); }
      else if (meta && e.key.toLowerCase() === "f") { e.preventDefault(); findBar.hidden = false; findIn.focus(); findIn.select(); }
      else if (e.altKey && (e.key === "ArrowUp" || e.key === "ArrowDown")) { e.preventDefault(); moveLine(e.key === "ArrowUp" ? -1 : 1); }
      else if (e.key === "Enter") {
        e.preventDefault();
        const lineStart = v.lastIndexOf("\n", s0 - 1) + 1;
        const indent = (v.slice(lineStart, s0).match(/^\s*/) || [""])[0];
        const prev = v.slice(lineStart, s0).trimEnd(), next = v[s0];
        const deeper = /[{([:]$/.test(prev) ? "  " : "";
        if (deeper && PAIRS[prev.slice(-1)] === next) setValue(v.slice(0, s0) + "\n" + indent + deeper + "\n" + indent + v.slice(s1), s0 + 1 + indent.length + 2);
        else setValue(v.slice(0, s0) + "\n" + indent + deeper + v.slice(s1), s0 + 1 + indent.length + deeper.length);
      } else if (PAIRS[e.key] && !meta) {
        e.preventDefault();
        if (s0 !== s1) setValue(v.slice(0, s0) + e.key + v.slice(s0, s1) + PAIRS[e.key] + v.slice(s1), s0 + 1, s1 + 1);
        else setValue(v.slice(0, s0) + e.key + PAIRS[e.key] + v.slice(s1), s0 + 1);
      } else if (e.key === "Backspace" && s0 === s1 && PAIRS[v[s0 - 1]] === v[s0]) { e.preventDefault(); setValue(v.slice(0, s0 - 1) + v.slice(s0 + 1), s0 - 1); }
      queueMicrotask(paint);
    });

    const findIn = h("input", { type: "text", placeholder: "Find", spellcheck: "false" });
    const replIn = h("input", { type: "text", placeholder: "Replace with", spellcheck: "false" });
    const hits = h("span", { class: "meta" });
    const count = () => {
      const q = findIn.value, n = q ? area.value.split(q).length - 1 : 0;
      hits.textContent = q ? `${n} match${n === 1 ? "" : "es"}` : "";
      return n;
    };
    const findNext = () => {
      const q = findIn.value;
      if (!q) return;
      let i = area.value.indexOf(q, area.selectionEnd);
      if (i < 0) i = area.value.indexOf(q);
      if (i < 0) return;
      area.focus(); area.setSelectionRange(i, i + q.length); paint();
    };
    findIn.addEventListener("input", count);
    findIn.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); findNext(); } if (e.key === "Escape") findBar.hidden = true; });
    const findBar = h("div", { class: "findbar", hidden: true }, findIn, replIn, hits,
      h("button", { class: "btn plain", onclick: findNext }, "Next"),
      h("button", { class: "btn plain", onclick: () => { const q = findIn.value; if (!q) return; if (area.value.slice(area.selectionStart, area.selectionEnd) === q) setValue(area.value.slice(0, area.selectionStart) + replIn.value + area.value.slice(area.selectionEnd), area.selectionStart + replIn.value.length); findNext(); count(); } }, "Replace"),
      h("button", { class: "btn plain", onclick: () => { const q = findIn.value; if (!q) return; const n = count(); setValue(area.value.split(q).join(replIn.value), 0); count(); toast(`Replaced ${n}`, "ok"); } }, "All"),
      h("button", { class: "btn plain", onclick: () => (findBar.hidden = true) }, "Close"));

    const runBtn = h("button", { class: "btn", title: p.language === "javascript" ? t("run_note") : t("only_js"), disabled: p.language !== "javascript", onclick: () => run() }, t("run"));
    const run = () => {
      if (p.language !== "javascript" || runBtn.disabled) return;
      runBtn.disabled = true;
      out.hidden = false;
      const lines_ = h("div"), t0 = performance.now();
      fill(out, h("div", { class: "row", style: "justify-content:space-between" }, h("div", { class: "label", style: "margin:0" }, t("output")),
        h("div", { class: "row" }, h("button", { class: "btn plain", onclick: () => copyText([...lines_.children].map((x) => x.textContent).join("\n")) }, t("copy")),
          h("button", { class: "btn plain", onclick: () => (out.hidden = true) }, "Hide"))), lines_);
      let any = false;
      runJs(area.value, (d) => { any = true; lines_.append(h("div", { class: "line " + d.level }, d.text)); },
        () => { if (!any) lines_.append(h("div", { class: "meta" }, t("no_output"))); lines_.append(h("div", { class: "meta" }, `finished in ${Math.round(performance.now() - t0)} ms`)); runBtn.disabled = false; });
    };
    const envBtn = h("button", { class: "btn plain", disabled: !["python", "shell"].includes(p.language), title: t("env_note"), onclick: async () => {
      await savePad();
      if (!iso.network_isolation && !confirm(t("iso_no"))) return;
      const py = p.language === "python";
      try { const rec = await api("/api/tasks", { name: p.name, command: py ? "python3 pad.py" : "sh pad.sh", files: { [py ? "pad.py" : "pad.sh"]: area.value }, network: !iso.network_isolation }); tk.selected = rec.id; tk.changes = null; await show("tasks"); } catch (e) { alert(e.message); }
    } }, t("run_env"));
    const review = h("button", { class: "btn plain", onclick: async () => {
      if (!area.value.trim()) return;
      review.disabled = true;
      out.hidden = false;
      fill(out, h("div", { class: "label" }, "Review"), h("div", { class: "meta" }, "asking a model…"));
      try {
        const mine = (await lapi("/api/lab/agents")).agents.filter((a) => !a.locked);
        const target = mine[0] ? { agent_id: mine[0].id } : { provider: { type: "mock" } };
        const r = await lapi("/api/lab/playground/chat", { ...target, widgets: false, max_tokens: 900,
          messages: [{ role: "user", content: `Review this ${PAD_LABEL[p.language] || p.language} code. List concrete problems with line numbers, then the smallest fix for each. Be brief.\n\n\`\`\`\n${area.value.slice(0, 20000)}\n\`\`\`` }] });
        fill(out, h("div", { class: "row", style: "justify-content:space-between" }, h("div", { class: "label", style: "margin:0" }, `Review by ${r.provider || "model"}`),
            h("button", { class: "btn plain", onclick: () => (out.hidden = true) }, "Hide")),
          h("div", { class: "answer" }, r.text || "(no answer)"),
          h("div", { class: "meta" }, `${r.insights.input_tokens + r.insights.output_tokens} tokens · ${r.insights.latency_ms} ms`));
      } catch (e) { fill(out, h("div", { class: "err" }, e.message)); }
      review.disabled = false;
    } }, "Review with an agent");

    const nameIn = h("input", { type: "text", class: "name", value: p.name, "aria-label": t("untitled"), oninput: (e) => { p.name = e.target.value; pd.dirty = true; queueSave(); } });
    const langSel = h("select", { "aria-label": t("language"), value: p.language, onchange: (e) => { p.language = e.target.value; pd.dirty = true; savePad().then(renderPage); } }, PAD_LANGS.map((l) => h("option", { value: l }, PAD_LABEL[l])));
    const copyBtn = h("button", { class: "btn plain", onclick: () => copyText(area.value, copyBtn) }, t("copy"));
    const EXT = { javascript: "js", typescript: "ts", python: "py", shell: "sh", json: "json", text: "txt" };
    const dlBtn = h("button", { class: "btn plain", onclick: () => download(`${(p.name || "pad").replace(/[^\w.-]+/g, "-")}.${EXT[p.language]}`, area.value) }, t("download"));
    const del = h("button", { class: "btn danger", onclick: async () => {
      if (!confirm(t("confirm_delete"))) return;
      try { await api(`/api/pads/${p.id}/delete`, {}); } catch {}
      pd.pad = null; pd.dirty = false; await loadPads();
      if (pd.pads.length) pd.pad = await api("/api/pads/" + pd.pads[0].id);
      renderPage();
    } }, t("delete"));
    right = h("div", {}, h("div", { class: "pad-top" }, nameIn, langSel, copyBtn, dlBtn, del, review, envBtn, runBtn),
      findBar,
      h("div", { class: "editor" + (S.wrap || !S.lineNumbers ? " wrap" : "") }, gutter, h("div", { class: "codewrap" }, pre, area)),
      statsBar, tips, out,
      h("div", { class: "meta", style: "margin-top:8px" }, "Tab indents, Shift+Tab outdents, Cmd/Ctrl+/ comments, Cmd/Ctrl+D duplicates, Alt+Up/Down moves a line, Cmd/Ctrl+F finds, Cmd/Ctrl+S saves, Cmd/Ctrl+Enter runs."));
    queueMicrotask(paint);
  }
  queueMicrotask(paintPadList);
  return [...head("pad_title", "pad_sub"), h("div", { class: "split" }, left, right)];
}

/* ---------- api ---------- */
const GEN = [["python", "Python"], ["javascript", "JavaScript"], ["typescript", "TypeScript"], ["curl", "curl"]];
const ap = { lang: null, base: location.origin, code: "", file: "", keys: [], fresh: null, spec: null };
function pageApi() {
  if (!ap.lang) ap.lang = S.codeLang === "shell" ? "curl" : S.codeLang;
  const codeBox = h("pre"), err = h("div", { class: "err" });
  const gen = debounce(async () => {
    err.textContent = "";
    try {
      const r = await api("/api/generate", { language: ap.lang, base_url: ap.base });
      ap.code = r.code; ap.file = r.filename; codeBox.textContent = r.code;
    } catch (e) { err.textContent = e.message; }
  }, 150);
  gen();
  const seg = h("div", { class: "seg small" });
  const paintSeg = () => seg.replaceChildren(...GEN.map(([id, label]) => h("button", { "aria-pressed": ap.lang === id, onclick: () => { ap.lang = id; paintSeg(); gen(); } }, label)));
  paintSeg();
  const copyBtn = h("button", { class: "btn plain", onclick: () => copyText(ap.code, copyBtn) }, t("copy"));

  const keyList = h("div", { class: "rows" }), fresh = h("div"), keyErr = h("div", { class: "err" });
  const label = h("input", { type: "text", placeholder: t("key_label"), spellcheck: "false" });
  const paintKeys = () => keyList.replaceChildren(...(ap.keys.length ? ap.keys.map((k) => h("div", { class: "kv" },
    h("div", {}, h("div", {}, k.label), h("div", { class: "meta" }, `${k.prefix}… · ${t("created")} ${new Date(k.created * 1000).toLocaleDateString(S.lang || undefined)}`)),
    h("button", { class: "btn danger", onclick: async () => { try { await api(`/api/keys/${k.id}/revoke`, {}); await loadKeys(); } catch (e) { keyErr.textContent = e.message; } } }, t("revoke")))) : [h("div", { class: "meta", style: "padding:8px 0" }, t("no_keys"))]));
  const loadKeys = async () => { try { ap.keys = (await api("/api/keys")).keys; paintKeys(); } catch {} };
  loadKeys();
  const paintFresh = () => fresh.replaceChildren(...(ap.fresh ? [h("div", { class: "card gap", style: "background:var(--fill);box-shadow:none" }, h("div", { class: "label" }, t("copy_now")), h("div", { class: "row" }, h("code", { class: "grow mono" }, ap.fresh), (() => { const b = h("button", { class: "btn plain", onclick: () => copyText(ap.fresh, b) }, t("copy")); return b; })()))] : []));
  paintFresh();

  const endpoints = h("div", { class: "rows tight" });
  const loadSpec = async () => {
    try {
      ap.spec = ap.spec || (await api("/api/openapi.json"));
      endpoints.replaceChildren(...Object.entries(ap.spec.paths).flatMap(([path, ops]) => Object.entries(ops).map(([m, op]) => h("div", { class: "kv" },
        h("div", { class: "row", style: "flex-wrap:nowrap" }, chip(m.toUpperCase(), m === "get" ? "" : "warn"), h("span", { class: "mono" }, path)), h("span", { class: "meta" }, op.summary)))));
    } catch {}
  };
  loadSpec();

  return [...head("api_title", "api_sub"),
    h("div", { class: "card" },
      h("div", { class: "row" }, h("div", { class: "label", style: "margin:0" }, t("client")), seg),
      h("div", { class: "row end gap" }, h("div", { class: "grow" }, h("label", {}, t("base_url")), h("input", { type: "text", value: ap.base, spellcheck: "false", oninput: (e) => { ap.base = e.target.value; gen(); } })),
        copyBtn,
        h("button", { class: "btn plain", onclick: () => download(ap.file, ap.code) }, t("download")),
        h("button", { class: "btn", onclick: async () => { await newPad(GEN_TO_PAD[ap.lang], ap.code, ap.file); await show("pad"); } }, t("open_in_pad"))),
      err, h("div", { class: "gap" }, codeBox)),
    h("h2", {}, t("keys")),
    h("div", { class: "card" }, h("p", { class: "meta", style: "margin:0 0 12px" }, t("keys_hint")),
      h("div", { class: "row" }, h("div", { class: "grow" }, label), h("button", { class: "btn", onclick: async () => {
        keyErr.textContent = "";
        try { const r = await api("/api/keys", { label: label.value }); ap.fresh = r.key; label.value = ""; paintFresh(); await loadKeys(); } catch (e) { keyErr.textContent = e.message; }
      } }, t("generate_key"))), keyErr, fresh, h("div", { class: "gap" }, keyList)),
    h("h2", {}, t("endpoints")),
    h("div", { class: "card" }, h("div", { class: "row", style: "justify-content:flex-end;margin-bottom:4px" }, h("button", { class: "btn plain", onclick: () => ap.spec && download("agentlab-openapi.json", JSON.stringify(ap.spec, null, 2), "application/json") }, t("openapi"))), endpoints)];
}

/* ---------- dashboard ---------- */
const db = { data: null };
const levelKey = { ok: "h_ok", warn: "h_warn", bad: "h_bad", idle: "h_idle", off: "h_off" };
const reasonText = (r) => t("r_" + r.code, { n: r.n, pct: r.pct });
function bars(hourly) {
  const NS = "http://www.w3.org/2000/svg", w = 480, hgt = 100, max = Math.max(1, ...hourly), bw = w / hourly.length;
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", `0 0 ${w} ${hgt}`); svg.setAttribute("class", "bars"); svg.setAttribute("preserveAspectRatio", "none"); svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", t("chart_h"));
  hourly.forEach((n, i) => {
    const r = document.createElementNS(NS, "rect"), ht = n ? Math.max(4, (n / max) * (hgt - 6)) : 3;
    r.setAttribute("x", i * bw + 2); r.setAttribute("width", bw - 4); r.setAttribute("y", hgt - ht); r.setAttribute("height", ht); r.setAttribute("rx", 3);
    r.setAttribute("class", (i === hourly.length - 1 ? "now " : "") + (n ? "" : "zero"));
    const title = document.createElementNS(NS, "title"); title.textContent = `${n}`; r.append(title); svg.append(r);
  });
  return svg;
}
function pageDashboard() {
  const box = h("div");
  const paint = () => {
    const d = db.data;
    if (!d) return box.replaceChildren(h("div", { class: "empty" }, "…"));
    const T = d.totals, tile = (num, label, tone = "") => h("div", { class: "tile " + tone }, h("div", { class: "num" }, num), h("div", { class: "lab" }, label));
    const agents = d.agents.length ? d.agents.map((a) => h("div", { class: "agent" }, h("span", { class: "hdot " + a.level }),
      h("div", { class: "body" }, h("div", { class: "name" }, a.name), h("div", { class: "why" }, a.reasons.map(reasonText).join(" · ")),
        a.stats.last ? h("div", { class: "meta", style: "margin-top:2px" }, t("last_active", { when: ago(a.stats.last) }) + (a.stats.steps ? ` · ${t("steps", { n: a.stats.steps })}` : "")) : null),
      chip(t(levelKey[a.level]), { ok: "ok", warn: "warn", bad: "bad" }[a.level] || "mute")))
      : [h("div", { class: "empty" }, h("b", {}, t("no_runs")), t("no_runs_hint"), h("button", { class: "btn gap", onclick: () => show("connectors") }, t("connect")))];
    box.replaceChildren(
      h("div", { class: "tiles" }, tile(T.active, t("t_active"), T.active ? "ok" : ""), tile(T.sessions, t("t_sessions")), tile(T.tool_calls, t("t_calls")),
        tile(T.blocked, t("t_blocked"), T.blocked ? "warn" : ""), tile(T.errors, t("t_errors"), T.errors ? "bad" : ""), tile(d.tasks.pending_review, t("t_review"), d.tasks.pending_review ? "warn" : "")),
      h("div", { class: "grid2" },
        h("div", { class: "card" }, h("div", { class: "label" }, t("agents_h")), h("div", {}, agents)),
        h("div", { class: "stack" },
          h("div", { class: "card" }, h("div", { class: "label" }, t("chart_h")), bars(d.hourly), h("div", { class: "axis" }, h("span", {}, "−24 h"), h("span", {}, t("just_now"))),
            d.tools.length ? h("div", { class: "row gap" }, d.tools.map((x) => chip(`${x.tool} · ${x.count}`, "mute"))) : null),
          h("div", { class: "card" }, h("div", { class: "label" }, t("recent_h")),
            d.recent.length ? d.recent.map((r) => h("button", { class: "clickrow", onclick: async () => { act.selected = r.id; await show("activity"); openRun(r.id); } },
              h("span", { class: "t" }, r.title), h("span", { class: "meta" }, r.blocked ? chip(t("blocked_n", { n: r.blocked }), "bad") : null, SOURCES[r.source] ? chip(SOURCES[r.source], "mute") : null, ago(r.last))))
              : h("div", { class: "meta" }, t("no_runs"))))),
      h("div", { class: "row gap" }, h("button", { class: "btn", onclick: () => { tk.selected = null; show("tasks"); } }, t("run_task")), h("button", { class: "btn plain", onclick: () => show("connectors") }, t("connect"))));
  };
  const load = async () => { try { db.data = await api("/api/insights"); } catch {} if (current === "dashboard" && box.isConnected) paint(); };
  poll = setInterval(load, 5000);
  load(); paint();
  return [...head("dash_title", "dash_sub"), box];
}

/* ---------- tasks ---------- */
let iso = { network_isolation: true, docker: false };
const tk = { tasks: [], selected: null, detail: null, changes: null, changesFor: null, result: null, open: new Set(), picked: new Set(),
  form: { tmpl: "custom", name: "", command: "", network: false, timeout: 600, backend: "local", image: "python:3.12-slim", pass_env: "" } };
const stTone = { succeeded: "ok", failed: "bad", timeout: "bad", interrupted: "bad", running: "", queued: "mute", cancelled: "mute" };
const isLive = (r) => r && ["queued", "running"].includes(r.status);
async function loadTasks() {
  try { tk.tasks = (await api("/api/tasks")).tasks; } catch { return; }
  if (tk.selected) {
    try { tk.detail = await api("/api/tasks/" + tk.selected); } catch { tk.detail = null; }
    const d = tk.detail;
    if (d && !isLive(d) && !d.discarded && d.changes && Object.values(d.changes).some(Boolean) && tk.changesFor !== d.id) {
      tk.changesFor = d.id; tk.picked = new Set();
      try { tk.changes = (await api(`/api/tasks/${d.id}/changes`)).changes; tk.changes.forEach((c) => { if (!c.protected) tk.picked.add(c.path); }); } catch { tk.changes = []; }
    }
  }
  if (current === "tasks") paintTasks();
}
function paintTasks() {
  const list = $("taskList"), view = $("taskView");
  if (!list || !view) return;
  list.replaceChildren(...(tk.tasks.length ? tk.tasks.map((r) => h("button", { "aria-current": r.id === tk.selected, onclick: () => { tk.selected = r.id; tk.detail = r; tk.changes = null; tk.changesFor = null; tk.result = null; loadTasks(); paintTasks(); } },
    h("div", { class: "t" }, r.name), h("div", { class: "meta" }, chip(t("st_" + r.status), stTone[r.status] || ""), ago(r.created)))) : [h("div", { class: "empty" }, t("no_tasks"))]));
  view.replaceChildren(...(tk.selected && tk.detail ? taskDetail(tk.detail) : [taskForm()]));
  const log = view.querySelector(".log"); if (log) log.scrollTop = log.scrollHeight;
}
function taskForm() {
  const f = tk.form, noIso = !iso.network_isolation && f.backend === "local";
  const err = h("div", { class: "err" });
  const bind = (k, num) => (e) => (f[k] = num ? +e.target.value : e.target.value);
  const tmpl = h("div", { class: "seg small" }, [["custom", "tmpl_custom"], ["claude", "tmpl_claude"]].map(([id, key]) => h("button", { "aria-pressed": f.tmpl === id, onclick: () => {
    f.tmpl = id;
    if (id === "claude") Object.assign(f, { name: "Claude Code", command: 'claude -p "Describe what Claude Code should do"', network: true, pass_env: "ANTHROPIC_API_KEY" });
    paintTasks();
  } }, t(key))));
  const seg_ = h("div", { class: "seg small" }, [["local", t("b_local")], ["docker", t("b_docker")]].map(([id, label]) => h("button", { "aria-pressed": f.backend === id, disabled: id === "docker" && !iso.docker, onclick: () => { f.backend = id; paintTasks(); } }, label)));
  const net = h("input", { type: "checkbox", class: "switch", checked: noIso ? true : f.network, disabled: noIso, "aria-label": t("network_on"), onchange: (e) => (f.network = e.target.checked) });
  const go = h("button", { class: "btn", onclick: async () => {
    err.textContent = ""; go.disabled = true;
    try {
      const rec = await api("/api/tasks", { name: f.name || undefined, command: f.command, network: noIso ? true : f.network, timeout: f.timeout, backend: f.backend, image: f.image,
        pass_env: f.pass_env.split(/[,\s]+/).filter(Boolean) });
      tk.selected = rec.id; tk.detail = rec; tk.changes = null; tk.changesFor = null; tk.result = null; await loadTasks();
    } catch (e) { err.textContent = e.message; go.disabled = false; }
  } }, t("start"));
  return h("div", { class: "card stack" }, h("div", { class: "row" }, h("h3", { style: "margin:0;flex:1;font-size:1.2rem" }, t("new_task")), tmpl),
    h("div", {}, h("label", {}, t("task_name")), h("input", { type: "text", value: f.name, oninput: bind("name") })),
    h("div", {}, h("label", {}, t("task_cmd")), h("textarea", { style: "min-height:90px", spellcheck: "false", placeholder: t("cmd_ph"), value: f.command, oninput: bind("command") })),
    h("div", { class: "optgrid" }, h("div", {}, h("label", {}, t("backend")), seg_),
      h("div", {}, h("label", {}, t("timeout_s")), h("input", { type: "number", min: 1, max: 3600, value: f.timeout, oninput: bind("timeout", true) })),
      f.backend === "docker" ? h("div", {}, h("label", {}, t("image")), h("input", { type: "text", value: f.image, oninput: bind("image"), spellcheck: "false" })) : null,
      h("div", {}, h("label", {}, t("pass_env")), h("input", { type: "text", placeholder: t("pass_env_ph"), value: f.pass_env, oninput: bind("pass_env"), spellcheck: "false" }))),
    h("div", { class: "switchrow" }, net, h("span", {}, t("network_on"))),
    h("div", { class: "meta" }, noIso ? t("iso_no") : iso.network_isolation || f.backend === "docker" ? t("iso_ok") : "", " ", f.backend === "local" && !iso.docker ? t("docker_no") : ""),
    h("div", { class: "meta" }, t("env_note") + " " + t("review_hint")), h("div", { class: "row" }, go), err);
}
function diffView(text) {
  const pre = h("pre", { class: "diff" });
  for (const line of text.split("\n")) pre.append(h("span", { class: line.startsWith("+") && !line.startsWith("+++") ? "add" : line.startsWith("-") && !line.startsWith("---") ? "del" : line.startsWith("@@") ? "hunk" : "" }, line + "\n"));
  return pre;
}
function taskDetail(d) {
  const live = isLive(d), out = [];
  out.push(h("div", { class: "card" }, h("div", { class: "row" }, h("h3", { style: "margin:0;flex:1;font-size:1.2rem" }, d.name), chip(t("st_" + d.status), stTone[d.status] || ""),
    d.exit_code != null ? h("span", { class: "meta" }, t("exit_code", { n: d.exit_code })) : null,
    live ? h("button", { class: "btn danger", onclick: async () => { try { await api(`/api/tasks/${d.id}/cancel`, {}); } catch (e) { alert(e.message); } } }, t("cancel")) : null),
    h("div", { class: "meta", style: "margin:6px 0 10px" }, h("code", {}, d.command)), d.error ? h("div", { class: "err" }, d.error) : null,
    h("div", { class: "label" }, t("output")), h("pre", { class: "log" }, d.log || (live ? "…" : t("no_output")))));
  if (!live && !d.discarded) {
    const ch = tk.changes;
    const card = h("div", { class: "card" }, h("div", { class: "row" }, h("div", { class: "label", style: "margin:0;flex:1" }, t("changes_h")), h("span", { class: "meta" }, t("review_hint"))));
    if (!ch || !ch.length) card.append(h("div", { class: "meta", style: "padding:10px 0" }, ch ? t("no_changes") : "…"));
    else {
      card.append(...ch.map((c) => {
        const cb = h("input", { type: "checkbox", checked: tk.picked.has(c.path), disabled: c.protected, "aria-label": c.path, onclick: (e) => e.stopPropagation(), onchange: (e) => (e.target.checked ? tk.picked.add(c.path) : tk.picked.delete(c.path)) });
        const row = h("div", { class: "file", onclick: () => { tk.open.has(c.path) ? tk.open.delete(c.path) : tk.open.add(c.path); paintTasks(); } }, cb,
          chip(t("ch_" + c.status), c.status === "deleted" ? "bad" : c.status === "added" ? "ok" : "warn"), h("span", { class: "mono grow" }, c.path), c.protected ? chip(t("protected_l"), "mute") : null);
        return h("div", {}, row, tk.open.has(c.path) && c.diff ? diffView(c.diff) : null);
      }));
      card.append(h("div", { class: "row gap" }, h("button", { class: "btn", onclick: async () => {
        try { tk.result = await api(`/api/tasks/${d.id}/apply`, { paths: [...tk.picked] }); tk.changesFor = null; tk.changes = null; await loadTasks(); } catch (e) { alert(e.message); }
      } }, t("apply_sel"))));
    }
    if (tk.result) card.append(h("div", { class: "row gap" }, chip(`${t("applied")}: ${tk.result.applied.length}`, "ok"), ...tk.result.skipped.map((s_) => chip(`${t("skipped")}: ${s_.path} (${s_.reason})`, "mute"))));
    card.append(h("div", { class: "row gap" }, h("button", { class: "btn danger", onclick: async () => { try { await api(`/api/tasks/${d.id}/discard`, {}); tk.changes = null; await loadTasks(); } catch (e) { alert(e.message); } } }, t("discard_env"))));
    out.push(card);
  }
  return out;
}
function pageTasks() {
  loadIso().then(() => { if (current === "tasks") paintTasks(); });
  loadTasks();
  poll = setInterval(() => { if (tk.tasks.some(isLive) || isLive(tk.detail)) loadTasks(); }, 1500);
  setTimeout(paintTasks, 0);
  return [...head("tasks_title", "tasks_sub"), h("div", { class: "split" },
    h("div", {}, h("button", { class: "btn", style: "width:100%;margin-bottom:12px", onclick: () => { tk.selected = null; tk.detail = null; paintTasks(); } }, "+ " + t("new_task")), h("div", { class: "list card", id: "taskList" })),
    h("div", { id: "taskView", class: "stack" }))];
}
async function loadIso() { try { iso = await api("/api/isolation"); } catch {} }

/* ---------- connectors ---------- */
const cn = { webhooks: [], edit: null, tested: {} };
function pageConnectors() {
  const agentBox = h("div", { class: "cards3" }), hookBox = h("div");
  const paintAgents = () => agentBox.replaceChildren(...ag.list.map((a) => {
    const b = h("button", { class: "btn plain", onclick: () => copyText(a.command, b) }, t("copy"));
    return h("div", { class: "card" }, h("div", { class: "row", style: "justify-content:space-between" }, h("b", {}, a.name), chip(a.connected ? t("a_connected") : a.detected ? t("a_detected") : t("a_missing"), a.connected ? "ok" : a.detected ? "" : "mute")),
      a.connected ? h("div", { class: "meta gap" }, h("code", {}, `agentlab disconnect ${a.id}`)) : h("div", { class: "row gap" }, h("code", { class: "grow mono" }, a.command), b));
  }));
  const saveAll = async (list) => { try { cn.webhooks = (await api("/api/connectors", { webhooks: list })).webhooks; cn.edit = null; } catch (e) { alert(e.message); } paintHooks(); };
  const paintHooks = () => {
    const rows = cn.webhooks.map((w) => {
      const res = cn.tested[w.id];
      return h("div", { class: "hook" }, h("div", { class: "info" }, h("div", {}, h("b", {}, w.name)), h("div", { class: "u" }, w.url), h("div", { class: "row", style: "margin-top:6px" }, w.events.map((e) => chip(t("ev_" + e), "mute")))),
        h("div", { class: "row", style: "flex-wrap:nowrap" }, res ? chip(res.ok ? t("sent_ok") : t("send_fail"), res.ok ? "ok" : "bad") : null,
          h("button", { class: "btn plain", onclick: async () => { try { cn.tested[w.id] = (await api("/api/connectors/test", { id: w.id })).results[0]; } catch (e) { cn.tested[w.id] = { ok: false }; } paintHooks(); } }, t("test_send")),
          h("button", { class: "btn plain", onclick: () => { cn.edit = { ...w, secret: "" }; paintHooks(); } }, t("edit")),
          h("button", { class: "btn danger", onclick: () => saveAll(cn.webhooks.filter((x) => x.id !== w.id)) }, t("remove"))));
    });
    const e = cn.edit;
    const form = e && h("div", { class: "card stack", style: "margin-top:14px;background:var(--bg)" },
      h("div", { class: "optgrid" }, h("div", {}, h("label", {}, t("task_name")), h("input", { type: "text", value: e.name || "", oninput: (ev) => (e.name = ev.target.value) })),
        h("div", {}, h("label", {}, t("wh_url")), h("input", { type: "text", value: e.url || "", spellcheck: "false", placeholder: "https://hooks.slack.com/…", oninput: (ev) => (e.url = ev.target.value) }))),
      h("div", {}, h("label", {}, t("wh_events")), h("div", { class: "row" }, ["blocked", "task_finished", "error"].map((ev) => h("label", { class: "switchrow", style: "margin:0" },
        h("input", { type: "checkbox", class: "switch", checked: e.events.includes(ev), onchange: (x) => (e.events = x.target.checked ? [...new Set([...e.events, ev])] : e.events.filter((y) => y !== ev)) }), h("span", {}, t("ev_" + ev)))))),
      h("div", {}, h("label", {}, t("wh_secret")), h("input", { type: "password", autocomplete: "off", placeholder: e.has_secret ? t("wh_secret_set") : "", oninput: (ev) => (e.secret = ev.target.value) })),
      h("div", { class: "row" }, h("button", { class: "btn", onclick: () => {
        const body = { id: e.id, name: e.name, url: e.url, events: e.events, ...(e.secret ? { secret: e.secret } : {}) };
        saveAll([...cn.webhooks.filter((x) => x.id !== e.id).map(({ id, name, url, events }) => ({ id, name, url, events })), body]);
      } }, t("save")), h("button", { class: "btn plain", onclick: () => { cn.edit = null; paintHooks(); } }, t("cancel"))));
    hookBox.replaceChildren(h("div", { class: "card" }, h("div", { class: "row", style: "justify-content:space-between;margin-bottom:6px" }, h("span", { class: "meta" }, t("webhooks_hint")),
      h("button", { class: "btn plain", onclick: () => { cn.edit = { name: "", url: "", events: ["blocked", "task_finished"], secret: "" }; paintHooks(); } }, "+ " + t("add_webhook"))),
      rows.length ? h("div", { class: "rows" }, rows) : h("div", { class: "meta", style: "padding:8px 0" }, t("no_webhooks")), form));
  };
  loadAgents().then(paintAgents);
  api("/api/connectors").then((r) => { cn.webhooks = r.webhooks; paintHooks(); }).catch(() => {});
  paintAgents(); paintHooks();
  return [...head("conn_title", "conn_sub"), h("h2", {}, t("agents_h")), agentBox, h("h2", {}, t("webhooks_h")), hookBox];
}

/* ---------- checks (regression + tripwires) ---------- */
const ck = { tab: "regress" };
function pageChecks() {
  const tabs = h("div", { class: "seg", style: "margin-bottom:20px" }, [["regress", "tab_regress"], ["tripwires", "tab_tripwires"]].map(([id, key]) => h("button", { "aria-pressed": ck.tab === id, onclick: () => { ck.tab = id; renderPage(); } }, t(key))));
  return [h("h1", {}, t("checks_title")), h("p", { class: "lead" }, t("checks_sub")), tabs, ...(ck.tab === "regress" ? pageRegress() : pageTripwires()).slice(2)];
}

/* ---------- settings + welcome ---------- */
function seg(options, value, onpick, small) {
  return h("div", { class: "seg" + (small ? " small" : "") }, options.map(([id, label]) => h("button", { "aria-pressed": id === value, onclick: () => onpick(id) }, label)));
}
function renderSettings() {
  const ov = $("overlay");
  ov.hidden = false; ov.className = ""; ov.dataset.kind = "settings";
  const row = (label, control) => h("div", { class: "setrow" }, h("span", {}, label), control);
  const toggle = (key) => h("input", { type: "checkbox", class: "switch", checked: S[key], onchange: (e) => setSetting({ [key]: e.target.checked }) });
  const langs = Object.keys(I18N).map((k) => h("option", { value: k }, I18N[k]._name));
  ov.replaceChildren(h("div", { class: "sheet", role: "dialog", "aria-modal": "true", "aria-label": t("set_title") },
    h("div", { class: "sheet-head" }, h("h3", {}, t("set_title")), h("button", { class: "btn plain", onclick: closeOverlay }, t("done"))),
    h("h2", {}, t("g_general")),
    h("div", { class: "group" }, row(t("set_language"), h("select", { value: S.lang || "en", onchange: (e) => setSetting({ lang: e.target.value }) }, langs))),
    h("h2", {}, t("g_personalise")),
    h("div", { class: "group" },
      row(t("set_appearance"), seg([["auto", t("ap_auto")], ["light", t("ap_light")], ["dark", t("ap_dark")]], S.theme, (v) => setSetting({ theme: v }), true)),
      row(t("accent"), h("div", { class: "swatches" }, Object.entries(ACCENTS).map(([k, c]) => h("button", { style: `--c:${c[0]}`, "aria-pressed": S.accent === k, "aria-label": k, onclick: () => setSetting({ accent: k }) })))),
      row(t("text_size"), seg([["s", t("ts_s")], ["m", t("ts_m")], ["l", t("ts_l")]], S.size, (v) => setSetting({ size: v }), true)),
      row(t("reduce_motion"), toggle("reduceMotion"))),
    h("h2", {}, "Gradients"),
    h("div", { class: "group" },
      h("div", { class: "grads" }, [...Object.keys(GRADIENTS), "custom", "none"].map((k) => h("button", { "aria-pressed": S.gradient === k, "aria-label": k, title: k, class: k === "none" ? "none" : "",
        style: k === "none" ? "" : `background:${k === "custom" ? `linear-gradient(${S.gradAngle}deg, ${[S.gradA, S.gradB, S.gradC].filter(Boolean).join(", ")})` : `linear-gradient(135deg, ${GRADIENTS[k].join(", ")})`}`,
        onclick: () => setSetting({ gradient: k }) }, k === "custom" ? "C" : k === "none" ? "×" : ""))),
      S.gradient === "custom" ? row("Colours", h("div", { class: "row", style: "flex-wrap:nowrap" }, ["gradA", "gradB", "gradC"].map((k) => h("input", { type: "color", class: "color", value: S[k] || "#ffffff", "aria-label": k, onchange: (e) => setSetting({ [k]: e.target.value }) })),
        S.gradC ? h("button", { class: "btn plain", onclick: () => setSetting({ gradC: "" }) }, "−") : null)) : null,
      S.gradient === "custom" ? row("Angle", h("input", { type: "range", min: 0, max: 360, value: S.gradAngle, style: "max-width:180px", onchange: (e) => setSetting({ gradAngle: +e.target.value }) })) : null,
      row("Gradient buttons", toggle("gradButtons")), row("Tinted background", toggle("gradBg")), row("Gradient sidebar", toggle("gradSide")),
      h("div", { class: "grad-preview", style: "background:var(--grad)" }, "Preview")),
    h("h2", {}, t("g_code")),
    h("div", { class: "group" },
      row(t("code_lang"), h("select", { value: S.codeLang, onchange: (e) => setSetting({ codeLang: e.target.value }) }, CODE_LANGS.map((l) => h("option", { value: l }, PAD_LABEL[l])))),
      row(t("line_numbers"), toggle("lineNumbers")),
      row(t("wrap"), toggle("wrap"))),
    h("div", { class: "row gap", style: "justify-content:center" }, h("button", { class: "btn danger", onclick: () => setSetting({ ...DEFAULTS, lang: S.lang }) }, t("reset")))));
}
function closeOverlay() { $("overlay").hidden = true; $("overlay").dataset.kind = ""; }
function renderWelcome(choice) {
  const ov = $("overlay");
  ov.hidden = false; ov.className = "solid"; ov.dataset.kind = "welcome";
  const draw = (lang) => {
    S.lang = lang; document.documentElement.lang = lang;
    ov.replaceChildren(h("div", { class: "welcome" },
      h("h1", { class: "reveal" }, t("welcome_title")), h("p", { class: "lead" }, t("welcome_sub")),
      h("div", { class: "langs" }, Object.keys(I18N).map((k) => h("button", { "aria-pressed": k === lang, lang: k, onclick: () => draw(k) }, I18N[k]._name))),
      h("button", { class: "btn", onclick: () => { closeOverlay(); setSetting({ lang }); } }, t("continue"))));
  };
  draw(choice);
}
const dismissible = () => $("overlay").dataset.kind === "settings";
addEventListener("keydown", (e) => { if (e.key === "Escape" && dismissible()) closeOverlay(); });
$("overlay").addEventListener("click", (e) => { if (e.target === $("overlay") && dismissible()) closeOverlay(); });
addEventListener("focus", () => { if (current === "connectors") renderPage(); });
$("openSettings").addEventListener("click", renderSettings);

addEventListener("hashchange", () => { const id = location.hash.slice(1); if (id !== current && TABS.some(([tab]) => tab === id)) show(id); });

/* ---------- pages table + boot ---------- */
const pages = {
  activity() {
    poll = setInterval(loadRuns, 3000);
    loadRuns().then(() => { if (act.selected) openRun(act.selected, true); });
    queueMicrotask(() => { paintRunList(); paintRun(); });
    return [...head("act_title", "act_sub"), h("div", { class: "split" }, h("div", { class: "list card", id: "runList" }), h("div", { class: "card", id: "runView" }, h("div", { class: "empty" }, t("select_run"))))];
  },
  dashboard: pageDashboard, tasks: pageTasks, connectors: pageConnectors, checks: pageChecks,
  sandbox: pageSandbox, pad: pagePad, api: pageApi,
};

addEventListener("DOMContentLoaded", async function boot() {  // after lab.js has added its pages
  try { I18N = await (await fetch("/static/i18n.json")).json(); } catch {}
  const first = !S.lang;
  if (first) {
    const guess = (navigator.languages || [navigator.language || "en"]).map((l) => l.slice(0, 2).toLowerCase()).find((l) => I18N[l]);
    S.lang = guess || "en";
  }
  applySettings();
  const start = TABS.some(([id]) => "#" + id === location.hash) ? location.hash.slice(1) : pages.home ? "home" : "dashboard";
  current = start;
  renderChrome();
  renderPage();
  if (first) renderWelcome(S.lang);
  loadIso();
  api("/api/health").then(() => { $("status").classList.add("up"); renderChrome(); }).catch(() => renderChrome());
  if (typeof loadBell === "function") { $("bell").addEventListener("click", openBell); loadBell(); }
});
