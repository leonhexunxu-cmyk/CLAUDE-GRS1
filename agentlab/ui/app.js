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
  el.append(...kids.flat(Infinity).filter((k) => k != null && k !== false).map((k) => (k.nodeType ? k : document.createTextNode(String(k)))));
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
const DEFAULTS = { lang: null, theme: "auto", accent: "blue", size: "m", codeLang: "python", lineNumbers: true, wrap: false, reduceMotion: false };
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
const TABS = [["activity", "tab_activity"], ["sandbox", "tab_sandbox"], ["regress", "tab_regress"], ["tripwires", "tab_tripwires"], ["pad", "tab_pad"], ["api", "tab_api"]];
let current = "activity", poll = null, leave = null;

function renderChrome() {
  $("tabs").replaceChildren(...TABS.map(([id, key]) => h("button", { role: "tab", "aria-selected": id === current, onclick: () => show(id) }, t(key))));
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
}
function renderPage() {
  const page = $("page");
  const body = pages[current]();
  page.replaceChildren(h("div", { class: "reveal" }, body));
}
const head = (title, sub) => [h("h1", {}, t(title)), h("p", { class: "lead" }, t(sub))];

/* ---------- activity ---------- */
const act = { runs: [], selected: null, events: [], upto: 0 };
const ago = (ts) => {
  const s = Math.max(0, Date.now() / 1000 - ts);
  return s < 60 ? t("just_now") : s < 3600 ? t("m_ago", { n: Math.floor(s / 60) }) : s < 86400 ? t("h_ago", { n: Math.floor(s / 3600) }) : t("d_ago", { n: Math.floor(s / 86400) });
};
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
  if (!act.runs.length) { box.replaceChildren(h("div", { class: "empty" }, h("b", {}, t("no_runs")), t("no_runs_hint"), h("div", { class: "gap" }, h("code", {}, "agentlab install claude-code")))); return; }
  box.replaceChildren(...act.runs.map((r) => h("button", { "aria-current": r.id === act.selected, onclick: () => openRun(r.id) },
    h("div", { class: "t" }, r.title || r.id),
    h("div", { class: "meta" }, `${t("steps", { n: r.events })} · ${ago(r.last)}`, r.blocked ? chip(t("blocked_n", { n: r.blocked }), "bad") : null, r.errors ? chip(t("errors_n", { n: r.errors }), "bad") : null, r.forked_from ? chip(t("fork_tag"), "mute") : null))));
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
  timer = setTimeout(() => { onLine({ level: "error", text: "⏱ 5s" }); finish(); }, 5000);
  addEventListener("message", onMsg);
  document.body.append(frame);
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
    const area = h("textarea", { spellcheck: "false", "aria-label": p.name, value: p.content, wrap: "off" });
    const out = h("div", { class: "out card", hidden: true });
    const lines = () => { const n = area.value.split("\n").length; gutter.textContent = Array.from({ length: n }, (_, i) => i + 1).join("\n"); };
    const changed = () => { p.content = area.value; pd.dirty = true; status.replaceChildren("…"); lines(); queueSave(); };
    area.addEventListener("input", changed);
    area.addEventListener("scroll", () => (gutter.scrollTop = area.scrollTop));
    area.addEventListener("keydown", (e) => {
      if (e.key === "Tab") { e.preventDefault(); area.setRangeText("  ", area.selectionStart, area.selectionEnd, "end"); changed(); }
      else if ((e.metaKey || e.ctrlKey) && e.key === "s") { e.preventDefault(); queueSave.cancel(); savePad(); }
      else if ((e.metaKey || e.ctrlKey) && e.key === "Enter") { e.preventDefault(); run(); }
    });
    const runBtn = h("button", { class: "btn", title: p.language === "javascript" ? t("run_note") : t("only_js"), disabled: p.language !== "javascript", onclick: () => run() }, "▶ " + t("run"));
    const run = () => {
      if (p.language !== "javascript" || runBtn.disabled) return;
      runBtn.disabled = true;
      out.hidden = false;
      const lines_ = h("div");
      out.replaceChildren(h("div", { class: "label" }, t("output")), lines_);
      let any = false;
      runJs(area.value, (d) => { any = true; lines_.append(h("div", { class: "line " + d.level }, d.text)); }, () => { if (!any) lines_.append(h("div", { class: "meta" }, t("no_output"))); runBtn.disabled = false; });
    };
    const nameIn = h("input", { type: "text", class: "name", value: p.name, "aria-label": t("untitled"), oninput: (e) => { p.name = e.target.value; pd.dirty = true; queueSave(); } });
    const langSel = h("select", { "aria-label": t("language"), value: p.language, onchange: (e) => { p.language = e.target.value; pd.dirty = true; savePad().then(renderPage); } }, PAD_LANGS.map((l) => h("option", { value: l }, PAD_LABEL[l])));
    const copyBtn = h("button", { class: "btn plain", onclick: () => copyText(area.value, copyBtn) }, t("copy"));
    const del = h("button", { class: "btn danger", onclick: async () => {
      if (!confirm(t("confirm_delete"))) return;
      try { await api(`/api/pads/${p.id}/delete`, {}); } catch {}
      pd.pad = null; pd.dirty = false; await loadPads();
      if (pd.pads.length) pd.pad = await api("/api/pads/" + pd.pads[0].id);
      renderPage();
    } }, t("delete"));
    right = h("div", {}, h("div", { class: "pad-top" }, nameIn, langSel, status, copyBtn, del, runBtn),
      h("div", { class: "editor" + (S.wrap || !S.lineNumbers ? " wrap" : "") }, gutter, area), out);
    queueMicrotask(lines);
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
    h("div", { class: "card" }, h("div", { class: "row", style: "justify-content:flex-end;margin-bottom:4px" }, h("button", { class: "btn plain", onclick: () => ap.spec && download("agentlab-openapi.json", JSON.stringify(ap.spec, null, 2), "application/json") }, t("openapi") + " ↓")), endpoints)];
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
addEventListener("keydown", (e) => { if (e.key === "Escape" && $("overlay").dataset.kind === "settings") closeOverlay(); });
$("overlay").addEventListener("click", (e) => { if (e.target === $("overlay") && $("overlay").dataset.kind === "settings") closeOverlay(); });
$("openSettings").addEventListener("click", renderSettings);

/* ---------- pages table + boot ---------- */
const pages = {
  activity() {
    poll = setInterval(loadRuns, 3000);
    loadRuns().then(() => { if (act.selected) openRun(act.selected, true); });
    queueMicrotask(() => { paintRunList(); paintRun(); });
    return [...head("act_title", "act_sub"), h("div", { class: "split" }, h("div", { class: "list card", id: "runList" }), h("div", { class: "card", id: "runView" }, h("div", { class: "empty" }, t("select_run"))))];
  },
  sandbox: pageSandbox, regress: pageRegress, tripwires: pageTripwires, pad: pagePad, api: pageApi,
};

(async function boot() {
  try { I18N = await (await fetch("/static/i18n.json")).json(); } catch {}
  const first = !S.lang;
  if (first) {
    const guess = (navigator.languages || [navigator.language || "en"]).map((l) => l.slice(0, 2).toLowerCase()).find((l) => I18N[l]);
    S.lang = guess || "en";
  }
  applySettings();
  const start = TABS.some(([id]) => "#" + id === location.hash) ? location.hash.slice(1) : "activity";
  current = start;
  renderChrome();
  renderPage();
  if (first) renderWelcome(S.lang);
  api("/api/health").then(() => { $("status").classList.add("up"); renderChrome(); }).catch(() => renderChrome());
})();
