"""Widgets: small interactive things an AI can put in a chat (charts, tables, forms, calculators ...).

Two kinds:
  * declarative specs (chart, table, stats, buttons, form, progress, checklist, markdown, code): validated here and drawn
    by the app itself, so they cannot run code;
  * html widgets: a full HTML page that runs in a sandboxed frame with no network and no access to the app.
Any model can make them: the text protocol is a fenced ```widget (JSON) or ```widget-html block in its reply.
"""

import json
import re
import uuid
import time

from . import projects
from .hooks import data_dir

TYPES = ("chart", "table", "stats", "buttons", "form", "progress", "checklist", "markdown", "code", "html")
MAX_HTML = 200_000
FENCE = re.compile(r"```(widget|widget-html)[ \t]*\n(.*?)```", re.S)

PROTOCOL = """You can show interactive widgets in your reply when a visual helps (a chart, a table, a form, a calculator ...). Put each widget in a fenced block.
Declarative widget, as JSON:
```widget
{"type": "chart", "kind": "bar", "title": "Sales", "labels": ["Q1", "Q2"], "series": [{"name": "2026", "data": [10, 14]}]}
```
Types: chart (kind bar|line|pie), table (columns, rows), stats (items: label, value), buttons (options: label, send), form (fields: name, label, kind text|number|select|checkbox, options; submit: label, template using {name}), progress (items: label, percent), checklist (items: text, done), markdown (text), code (language, code).
Or a full interactive page, as one self-contained HTML document (no network, no external files):
```widget-html
<!doctype html><html><body>...</body></html>
```
Keep widgets small and useful, and say in plain text what the widget shows."""


class WidgetError(ValueError):
    def __init__(self, message, code=400):
        super().__init__(message)
        self.code = code


def _s(v, n=500):
    return str(v)[:n]


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise WidgetError(f"expected a number, got {v!r}")
    if f != f or f in (float("inf"), float("-inf")):
        raise WidgetError("numbers must be finite")
    return f


def _list(v, name, limit=200):
    if not isinstance(v, list) or len(v) > limit:
        raise WidgetError(f"{name} must be a list of at most {limit} items")
    return v


def validate(spec):
    """Normalise a widget spec or raise WidgetError. Unknown keys are dropped."""
    if not isinstance(spec, dict) or spec.get("type") not in TYPES:
        raise WidgetError(f"widget type must be one of {list(TYPES)}")
    t, title = spec["type"], _s(spec.get("title", ""), 120)
    if t == "chart":
        if spec.get("kind", "bar") not in ("bar", "line", "pie"):
            raise WidgetError("chart kind must be bar, line or pie")
        labels = [_s(x, 60) for x in _list(spec.get("labels"), "labels")]
        series = []
        for s in _list(spec.get("series"), "series", 8):
            data = [_num(x) for x in _list(s.get("data"), "data")]
            if len(data) != len(labels):
                raise WidgetError("every series needs one number per label")
            series.append({"name": _s(s.get("name", ""), 60), "data": data})
        if not series:
            raise WidgetError("a chart needs at least one series")
        return {"type": t, "kind": spec.get("kind", "bar"), "title": title, "labels": labels, "series": series}
    if t == "table":
        cols = [_s(c, 60) for c in _list(spec.get("columns"), "columns", 30)]
        rows = [[_s(c, 200) for c in _list(r, "row", 30)] for r in _list(spec.get("rows"), "rows", 500)]
        return {"type": t, "title": title, "columns": cols, "rows": rows}
    if t == "stats":
        return {"type": t, "title": title, "items": [{"label": _s(i.get("label", ""), 60), "value": _s(i.get("value", ""), 60), "hint": _s(i.get("hint", ""), 80)} for i in _list(spec.get("items"), "items", 24)]}
    if t == "buttons":
        opts = [{"label": _s(o.get("label", ""), 60), "send": _s(o.get("send") or o.get("label", ""), 500)} for o in _list(spec.get("options"), "options", 12)]
        if not opts:
            raise WidgetError("buttons needs at least one option")
        return {"type": t, "title": title, "options": opts}
    if t == "form":
        fields = []
        for f in _list(spec.get("fields"), "fields", 20):
            name = _s(f.get("name", ""), 30)
            if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
                raise WidgetError("form field names must be lowercase words like city")
            kind = f.get("kind", "text")
            if kind not in ("text", "number", "select", "checkbox"):
                raise WidgetError("field kind must be text, number, select or checkbox")
            fields.append({"name": name, "label": _s(f.get("label", name), 80), "kind": kind, "options": [_s(o, 60) for o in f.get("options", [])][:30], "default": _s(f.get("default", ""), 200)})
        sub = spec.get("submit") or {}
        template = _s(sub.get("template") or " ".join("{%s}" % f["name"] for f in fields), 1000)
        unknown = set(re.findall(r"\{([a-z][a-z0-9_]*)\}", template)) - {f["name"] for f in fields}
        if unknown:
            raise WidgetError(f"the submit template uses unknown fields: {sorted(unknown)}")
        return {"type": t, "title": title, "fields": fields, "submit": {"label": _s(sub.get("label", "Send"), 40), "template": template}}
    if t == "progress":
        return {"type": t, "title": title, "items": [{"label": _s(i.get("label", ""), 80), "percent": max(0, min(100, _num(i.get("percent", 0))))} for i in _list(spec.get("items"), "items", 30)]}
    if t == "checklist":
        return {"type": t, "title": title, "items": [{"text": _s(i.get("text", ""), 200), "done": bool(i.get("done"))} for i in _list(spec.get("items"), "items", 100)]}
    if t == "markdown":
        return {"type": t, "title": title, "text": _s(spec.get("text", ""), 20000)}
    if t == "code":
        return {"type": t, "title": title, "language": _s(spec.get("language", "text"), 20), "code": _s(spec.get("code", ""), 50000)}
    html = str(spec.get("html", ""))
    if not html.strip() or len(html) > MAX_HTML:
        raise WidgetError(f"html is required (max {MAX_HTML:,} characters)")
    return {"type": "html", "title": title, "html": html}


def extract(text):
    """Pull widget blocks out of a model reply. Returns (clean_text, [valid widgets], [error strings])."""
    found, errors = [], []

    def take(m):
        kind, body = m.group(1), m.group(2).strip()
        try:
            found.append(validate({"type": "html", "html": body, "title": ""} if kind == "widget-html" else json.loads(body)))
        except (ValueError, WidgetError, AttributeError, TypeError) as e:
            errors.append(f"widget ignored: {e}")
        return ""
    clean = FENCE.sub(take, str(text or "")).strip()
    return clean, found, errors


# ---------------------------------------------------------------- offline templates (no model needed)
_BASE = ("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><style>"
         ":root{color-scheme:light dark;--bg:#fff;--fg:#1d1d1f;--mut:#6e6e73;--fill:#f2f2f7;--ac:#0071e3}"
         "@media(prefers-color-scheme:dark){:root{--bg:#1c1c1e;--fg:#f5f5f7;--mut:#98989d;--fill:#2c2c2e;--ac:#0a84ff}}"
         "*{box-sizing:border-box}body{margin:0;padding:14px;font:15px/1.4 -apple-system,system-ui,sans-serif;background:var(--bg);color:var(--fg)}"
         "button{font:inherit;border:0;border-radius:10px;padding:10px;background:var(--fill);color:var(--fg);cursor:pointer}button.ac{background:var(--ac);color:#fff}"
         "input,select{font:inherit;width:100%;padding:9px 11px;border:0;border-radius:10px;background:var(--fill);color:var(--fg)}h3{margin:0 0 10px;font-size:1rem}"
         ".row{display:flex;gap:8px;margin:8px 0}.big{font-size:2.2rem;font-weight:700;font-variant-numeric:tabular-nums;text-align:center}</style>")


def _page(body, script):
    return f"{_BASE}<body>{body}<script>{script}</script></body>"


TEMPLATES = {
    "calculator": ("Calculator", ["calculator", "calc", "arithmetic"], _page(
        "<input id=d readonly value=0 style='text-align:right;font-size:1.6rem'><div id=k style='display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-top:8px'></div>",
        "const k=['7','8','9','/','4','5','6','*','1','2','3','-','0','.','=','+','C'];let e='';const d=document.getElementById('d');"
        "k.forEach(x=>{const b=document.createElement('button');b.textContent=x;if(x=='=')b.className='ac';b.onclick=()=>{if(x=='C'){e='';d.value=0;return}"
        "if(x=='='){try{if(!/^[0-9+\\-*/. ]+$/.test(e))throw 0;d.value=Function('return '+e)();e=String(d.value)}catch(_){d.value='Error';e=''}return}e+=x;d.value=e};document.getElementById('k').append(b)});")),
    "stopwatch": ("Stopwatch", ["stopwatch", "timer", "chrono"], _page(
        "<div class=big id=t>00:00.0</div><div class=row><button class=ac id=s>Start</button><button id=r>Reset</button></div>",
        "let a=0,run=false,st=0,iv;const t=document.getElementById('t'),f=ms=>{const s=ms/1000;return String(Math.floor(s/60)).padStart(2,'0')+':'+(s%60).toFixed(1).padStart(4,'0')};"
        "document.getElementById('s').onclick=e=>{if(run){clearInterval(iv);a+=Date.now()-st;e.target.textContent='Start'}else{st=Date.now();iv=setInterval(()=>t.textContent=f(a+Date.now()-st),100);e.target.textContent='Stop'}run=!run};"
        "document.getElementById('r').onclick=()=>{clearInterval(iv);run=false;a=0;t.textContent='00:00.0';document.getElementById('s').textContent='Start'};")),
    "todo": ("To-do list", ["todo", "to-do", "task list", "checklist"], _page(
        "<h3>To-do</h3><div class=row><input id=i placeholder='Add a task'><button class=ac id=a>Add</button></div><div id=l></div>",
        "const l=document.getElementById('l'),i=document.getElementById('i');function add(t){const d=document.createElement('label');d.style.cssText='display:flex;gap:8px;padding:6px 0';"
        "const c=document.createElement('input');c.type='checkbox';c.style.width='auto';const s=document.createElement('span');s.textContent=t;c.onchange=()=>s.style.textDecoration=c.checked?'line-through':'';d.append(c,s);l.append(d)}"
        "document.getElementById('a').onclick=()=>{if(i.value.trim()){add(i.value.trim());i.value=''}};i.onkeydown=e=>{if(e.key=='Enter')document.getElementById('a').click()};")),
    "counter": ("Counter", ["counter", "tally", "count"], _page(
        "<div class=big id=n>0</div><div class=row><button id=m>−</button><button class=ac id=p>+</button><button id=z>Reset</button></div>",
        "let n=0;const e=document.getElementById('n'),u=()=>e.textContent=n;document.getElementById('p').onclick=()=>{n++;u()};document.getElementById('m').onclick=()=>{n--;u()};document.getElementById('z').onclick=()=>{n=0;u()};")),
    "dice": ("Dice roller", ["dice", "roll", "random number"], _page(
        "<div class=big id=r>🎲</div><div class=row><button class=ac data-s=6>d6</button><button data-s=8>d8</button><button data-s=12>d12</button><button data-s=20>d20</button></div>",
        "document.querySelectorAll('[data-s]').forEach(b=>b.onclick=()=>{document.getElementById('r').textContent=1+Math.floor(Math.random()*b.dataset.s)});")),
    "color-picker": ("Colour picker", ["colour", "color", "palette"], _page(
        "<h3>Colour</h3><input type=color id=c value='#0071e3' style='height:60px;padding:4px'><div class=big id=h style='margin-top:10px;font-size:1.3rem'>#0071e3</div><div id=p style='height:50px;border-radius:12px;margin-top:8px;background:#0071e3'></div>",
        "const c=document.getElementById('c');c.oninput=()=>{document.getElementById('h').textContent=c.value;document.getElementById('p').style.background=c.value}")),
    "unit-converter": ("Unit converter", ["convert", "converter", "units", "temperature", "length"], _page(
        "<h3>Convert</h3><div class=row><input id=v type=number value=1><select id=f></select><select id=t></select></div><div class=big id=o></div>",
        "const U={km:1000,m:1,cm:.01,mi:1609.344,ft:.3048,in:.0254};const f=document.getElementById('f'),t=document.getElementById('t');Object.keys(U).forEach(k=>{f.add(new Option(k,k));t.add(new Option(k,k))});t.value='mi';"
        "const go=()=>document.getElementById('o').textContent=(document.getElementById('v').value*U[f.value]/U[t.value]).toPrecision(6)+' '+t.value;[f,t,document.getElementById('v')].forEach(x=>x.oninput=go);go();")),
    "pomodoro": ("Pomodoro timer", ["pomodoro", "focus", "countdown"], _page(
        "<h3>Focus</h3><div class=big id=t>25:00</div><div class=row><button class=ac id=s>Start</button><button id=r>Reset</button></div>",
        "let s=1500,iv=null;const t=document.getElementById('t'),u=()=>t.textContent=String(Math.floor(s/60)).padStart(2,'0')+':'+String(s%60).padStart(2,'0');"
        "document.getElementById('s').onclick=()=>{if(iv){clearInterval(iv);iv=null;return}iv=setInterval(()=>{if(--s<=0){clearInterval(iv);iv=null;s=0}u()},1000)};document.getElementById('r').onclick=()=>{clearInterval(iv);iv=null;s=1500;u()};")),
}


def generate_offline(prompt):
    """Pick the best matching built-in template for a request (no model needed). Returns a widget spec or None."""
    p = str(prompt or "").lower()
    best = max(TEMPLATES.items(), key=lambda kv: sum(2 if re.search(r"\b" + re.escape(w) + r"\b", p) else 0 for w in kv[1][1]))
    if not any(re.search(r"\b" + re.escape(w) + r"\b", p) for w in best[1][1]):
        return None
    return validate({"type": "html", "title": best[1][0], "html": best[1][2]})


# ---------------------------------------------------------------- store
def _dir(root):
    return data_dir(root) / "widgets"


def save(root, spec, agent=None, source="user"):
    w = validate(spec)
    wid = uuid.uuid4().hex[:8]
    rec = {"id": wid, "title": w["title"] or w["type"], "spec": w, "agent": agent, "source": source, "created": time.time()}
    _dir(root).mkdir(parents=True, exist_ok=True)
    (_dir(root) / f"{wid}.json").write_text(json.dumps(rec), encoding="utf-8")
    return rec


def get(root, wid):
    p = _dir(root) / f"{wid}.json"
    if not re.fullmatch(r"[a-f0-9]{8}", wid or "") or not p.exists():
        raise WidgetError("widget not found", 404)
    return json.loads(p.read_text(encoding="utf-8"))


def list_widgets(root):
    out = []
    for p in _dir(root).glob("*.json") if _dir(root).exists() else []:
        try:
            r = json.loads(p.read_text(encoding="utf-8"))
            out.append({k: r[k] for k in ("id", "title", "agent", "source", "created")} | {"type": r["spec"]["type"]})
        except ValueError:
            pass
    return sorted(out, key=lambda r: -r["created"])


def delete(root, wid):
    get(root, wid)
    (_dir(root) / f"{wid}.json").unlink()


def to_html(spec):
    """A standalone HTML page for a widget (html widgets as they are; declarative ones as a readable static page)."""
    if spec["type"] == "html":
        return spec["html"]
    esc = lambda s: str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    body = f"<h3>{esc(spec.get('title', ''))}</h3>"
    if spec["type"] == "table":
        body += "<table border=0 cellpadding=6><tr>" + "".join(f"<th align=left>{esc(c)}</th>" for c in spec["columns"]) + "</tr>" + "".join("<tr>" + "".join(f"<td>{esc(c)}</td>" for c in r) + "</tr>" for r in spec["rows"]) + "</table>"
    elif spec["type"] == "stats":
        body += "".join(f"<div><b class=big>{esc(i['value'])}</b> {esc(i['label'])}</div>" for i in spec["items"])
    elif spec["type"] == "chart":
        mx = max([v for s in spec["series"] for v in s["data"]] + [1])
        body += "".join(f"<div class=row><span style='width:90px'>{esc(l)}</span><div style='background:var(--ac);height:18px;width:{100 * s['data'][i] / mx:.0f}%;border-radius:6px'></div> {s['data'][i]:g}</div>" for s in spec["series"] for i, l in enumerate(spec["labels"]))
    elif spec["type"] in ("markdown", "code"):
        body += f"<pre style='white-space:pre-wrap'>{esc(spec.get('text') or spec.get('code'))}</pre>"
    else:
        body += f"<pre>{esc(json.dumps(spec, indent=2))}</pre>"
    return _page(body, "")


def export_to_project(root, wid, project_id, rel_path=None):
    rec = get(root, wid)
    name = rel_path or "widgets/" + (re.sub(r"[^a-z0-9]+", "-", rec["title"].lower()).strip("-") or "widget") + ".html"
    return projects.write_file(root, project_id, name, to_html(rec["spec"]))
