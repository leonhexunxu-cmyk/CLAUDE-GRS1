"""Scheduler: run an agent on a schedule (cron or every N minutes) with no one watching.

Guardrails for unattended work: pre-authorised categories only (anything else still asks), no overlapping runs of
the same schedule, no catch-up after downtime, optional run cap, and three failures in a row pause the schedule.
"""

import json
import re
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

from . import agents, projects
from .hooks import data_dir

FIELDS = (("minute", 0, 59), ("hour", 0, 23), ("dom", 1, 31), ("month", 1, 12), ("dow", 0, 6))
MAX_FAILURES = 3


class ScheduleError(ValueError):
    def __init__(self, message, code=400):
        super().__init__(message)
        self.code = code


def parse_cron(expr):
    """'*/15 9-17 * * 1-5' -> list of 5 sets of allowed values. Raises ScheduleError."""
    parts = str(expr).split()
    if len(parts) != 5:
        raise ScheduleError("cron needs 5 fields: minute hour day-of-month month day-of-week")
    sets = []
    for part, (name, lo, hi) in zip(parts, FIELDS):
        allowed = set()
        for piece in part.split(","):
            m = re.fullmatch(r"(\*|\d+(?:-\d+)?)(?:/(\d+))?", piece)
            if not m:
                raise ScheduleError(f"bad {name} field: {piece!r}")
            rng, step = m.group(1), int(m.group(2) or 1)
            if step < 1:
                raise ScheduleError(f"bad step in {name}")
            if rng == "*":
                a, b = lo, hi
            elif "-" in rng:
                a, b = map(int, rng.split("-"))
            else:
                a = b = int(rng)
            top = 7 if name == "dow" else hi  # day-of-week accepts 7 as Sunday
            if rng != "*" and "-" not in rng and m.group(2):
                b = top
            if not (lo <= a <= b <= top):
                raise ScheduleError(f"{name} must be between {lo} and {hi}")
            allowed.update(v % 7 if name == "dow" else v for v in range(a, b + 1, step))
        sets.append(allowed)
    return sets


def next_cron(expr, after, tz_minutes=0):
    """Next run (epoch seconds) strictly after `after`, evaluated in a fixed UTC offset."""
    minute, hour, dom, month, dow = parse_cron(expr)
    tz = timezone(timedelta(minutes=tz_minutes))
    t = datetime.fromtimestamp(after, tz).replace(second=0, microsecond=0) + timedelta(minutes=1)
    for _ in range(366 * 24 * 60):
        if t.month in month and t.day in dom and (t.isoweekday() % 7) in dow and t.hour in hour and t.minute in minute:
            return t.timestamp()
        t += timedelta(minutes=1)
    raise ScheduleError("this schedule never runs")


def next_run(rec, after):
    if rec.get("every_minutes"):
        return after + rec["every_minutes"] * 60
    return next_cron(rec["cron"], after, rec.get("tz_minutes", 0))


class Scheduler:
    def __init__(self, root, runtime, on_event=None, clock=time.time):
        self.root, self.runtime, self.on_event, self.clock = root, runtime, on_event, clock
        self.path = data_dir(root) / "schedules.json"
        self._lock = threading.Lock()
        self._thread = None
        self._stop = threading.Event()

    def _load(self):
        return json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else []

    def _save(self, items):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(items), encoding="utf-8")

    def list(self):
        return sorted(self._load(), key=lambda s: s["created"])

    def get(self, sid):
        for s in self._load():
            if s["id"] == sid:
                return s
        raise ScheduleError("schedule not found", 404)

    def create(self, spec):
        name = str(spec.get("name") or "").strip()[:80]
        prompt = str(spec.get("prompt") or "").strip()
        if not prompt or len(prompt) > 20000:
            raise ScheduleError("a prompt is required")
        projects.get(self.root, spec.get("project"))
        agents.get(self.root, spec.get("agent"))
        every, cron = spec.get("every_minutes"), spec.get("cron")
        if bool(every) == bool(cron):
            raise ScheduleError("give either cron or every_minutes")
        if every:
            try:
                every = int(every)
            except (TypeError, ValueError):
                raise ScheduleError("every_minutes must be a number")
            if not 1 <= every <= 60 * 24 * 30:
                raise ScheduleError("every_minutes must be between 1 and 43200")
        else:
            parse_cron(cron)
        tz = int(spec.get("tz_minutes") or 0)
        if not -840 <= tz <= 840:
            raise ScheduleError("tz_minutes is out of range")
        max_runs = spec.get("max_runs")
        rec = {"id": uuid.uuid4().hex[:8], "name": name or prompt[:40], "project": spec["project"], "agent": spec["agent"], "prompt": prompt, "cron": cron or None,
               "every_minutes": every or None, "tz_minutes": tz, "enabled": True, "overrides": {k: v for k, v in (spec.get("overrides") or {}).items() if k in agents.CATEGORIES and v in ("allow", "ask", "deny")},
               "max_runs": int(max_runs) if max_runs else None, "created": self.clock(), "last_run": None, "last_job": None, "runs": 0, "failures": 0, "note": ""}
        rec["next_run"] = next_run(rec, self.clock())
        with self._lock:
            self._save(self._load() + [rec])
        return rec

    def update(self, sid, **fields):
        with self._lock:
            items = self._load()
            rec = next((s for s in items if s["id"] == sid), None)
            if not rec:
                raise ScheduleError("schedule not found", 404)
            if "enabled" in fields:
                rec["enabled"] = bool(fields["enabled"])
                if rec["enabled"]:
                    rec.update(failures=0, note="", next_run=next_run(rec, self.clock()))
            self._save(items)
            return rec

    def delete(self, sid):
        with self._lock:
            items = self._load()
            kept = [s for s in items if s["id"] != sid]
            if len(kept) == len(items):
                raise ScheduleError("schedule not found", 404)
            self._save(kept)

    def _notify(self, name, text, data):
        if self.on_event:
            try:
                self.on_event(name, text, data)
            except Exception:
                pass

    def tick(self, now=None):
        """Start every due schedule. Returns the ids of jobs started."""
        now = now or self.clock()
        started = []
        with self._lock:
            items = self._load()
            for s in items:
                if not s["enabled"] or s["next_run"] > now:
                    continue
                s["next_run"] = next_run(s, now)  # no catch-up: the next run is always in the future
                last = None
                if s["last_job"]:
                    try:
                        last = self.runtime.get(s["last_job"])
                    except Exception:
                        pass
                if last and last["status"] not in ("succeeded", "failed", "cancelled", "stopped"):
                    s["note"] = "skipped: the previous run is still going"
                    continue
                if last:  # judge the previous run now that it has finished
                    s["failures"] = s["failures"] + 1 if last["status"] in ("failed",) else 0
                    if s["failures"] >= MAX_FAILURES:
                        s.update(enabled=False, note=f"paused after {MAX_FAILURES} failed runs in a row")
                        self._notify("schedule_paused", f"schedule “{s['name']}” was paused after {MAX_FAILURES} failures in a row", {"schedule": s["id"]})
                        continue
                try:
                    job = self.runtime.start(s["project"], s["agent"], s["prompt"], autonomous=True, overrides=s["overrides"], schedule=s["id"])
                except Exception as e:
                    s["failures"] += 1
                    s["note"] = f"could not start: {e}"[:200]
                    continue
                s.update(last_run=now, last_job=job["id"], runs=s["runs"] + 1, note="")
                started.append(job["id"])
                if s["max_runs"] and s["runs"] >= s["max_runs"]:
                    s.update(enabled=False, note="finished: reached its run limit")
            self._save(items)
        return started

    def start(self, interval=15):
        if self._thread:
            return
        def loop():
            while not self._stop.wait(interval):
                try:
                    self.tick()
                except Exception:
                    pass
        self._thread = threading.Thread(target=loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
