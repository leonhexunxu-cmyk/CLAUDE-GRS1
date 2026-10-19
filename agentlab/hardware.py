"""What this machine can run: CPU threads, memory, GPUs, and whether a model file will fit."""

import os
import platform
import shutil
import subprocess
from pathlib import Path


def _ram_bytes():
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) * 1024
    except OSError:
        pass
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (ValueError, OSError, AttributeError):
        return None


def _available_ram():
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) * 1024
    except OSError:
        pass
    return None


def _gpus():
    if not shutil.which("nvidia-smi"):
        return []
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5).stdout
        return [{"name": n.strip(), "memory_bytes": int(float(m)) * 1024 * 1024} for n, m in (line.split(",") for line in out.strip().splitlines() if "," in line)]
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return []


def info():
    cpus = os.cpu_count() or 1
    return {"cpu_count": cpus, "recommended_threads": max(1, cpus - 1) if cpus > 2 else cpus, "ram_bytes": _ram_bytes(), "ram_available_bytes": _available_ram(),
            "gpus": _gpus(), "platform": f"{platform.system()} {platform.machine()}", "python": platform.python_version()}


def check_fit(model_bytes, context=4096, device="auto", hw=None):
    """Will a model of this file size run here? Rough rule: weights plus about 0.5 MB per 1,000 context tokens, plus 10%."""
    hw = hw or info()
    need = int(model_bytes * 1.1 + context * 500)
    use_gpu = device == "gpu" or (device == "auto" and hw["gpus"])
    budget = max((g["memory_bytes"] for g in hw["gpus"]), default=0) if use_gpu else (hw["ram_available_bytes"] or hw["ram_bytes"] or 0)
    where = "GPU memory" if use_gpu else "RAM"
    if not budget:
        return {"verdict": "unknown", "need_bytes": need, "have_bytes": 0, "where": where, "advice": "Could not read how much memory is available."}
    ratio = need / budget
    verdict = "fits" if ratio <= 0.75 else "tight" if ratio <= 1.0 else "too_big"
    advice = {"fits": f"Should run comfortably in {where}.", "tight": f"Close to the limit of your {where}: close other apps or use a smaller quantisation.",
              "too_big": f"Needs about {need / 1e9:.1f} GB but only {budget / 1e9:.1f} GB of {where} is available: use a smaller or more quantised model."}[verdict]
    return {"verdict": verdict, "need_bytes": need, "have_bytes": budget, "where": where, "advice": advice}
