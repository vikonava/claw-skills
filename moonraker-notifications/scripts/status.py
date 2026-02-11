#!/usr/bin/env python3
"""On-demand Moonraker print status.

Usage:
  status.py <identity>

Reads:
- moonraker-notifications/<identity>/config.json

Outputs a compact, chat-friendly status line.
"""

import datetime as dt
import json
import os
import sys
import urllib.request
import urllib.error
from zoneinfo import ZoneInfo


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def normalize(s: str) -> str:
    return (s or "").strip().lower()


def pct(progress):
    try:
        if progress is None:
            return None
        v = float(progress)
        if v != v:
            return None
        return max(0, min(100, int(round(v * 100))))
    except Exception:
        return None


def main() -> int:
    if len(sys.argv) < 2 or not sys.argv[1].strip():
        print("Usage: status.py <identity>")
        return 2

    identity = sys.argv[1].strip()
    root = os.environ.get("OPENCLAW_WORKSPACE") or os.getcwd()
    cfg_path = os.path.join(root, "moonraker-notifications", identity, "config.json")

    cfg = load_json(cfg_path, {})
    base = (cfg.get("moonraker") or {}).get("base_url")
    printer = (cfg.get("printer") or {}).get("name") or "Printer"

    if not base:
        print(f"{printer} status: unavailable")
        return 0

    url = base.rstrip("/") + "/printer/objects/query?print_stats&virtual_sdcard&display_status"

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "openclaw-klipper-status"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            raw = resp.read().decode("utf-8", "replace")
        data = json.loads(raw)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        print(f"{printer} status: unavailable")
        return 0

    status = ((data.get("result") or {}).get("status") or {})
    ps = status.get("print_stats") or {}
    vsd = status.get("virtual_sdcard") or {}
    ds = status.get("display_status") or {}

    st = normalize(ps.get("state")) or "unknown"
    filename = (vsd.get("filename") or "").strip() or (ps.get("filename") or "").strip()
    prog_raw = vsd.get("progress")
    if prog_raw is None:
        prog_raw = ds.get("progress")
    p = pct(prog_raw)

    # Best-effort layer support (depends on macros/plugins)
    cur_layer = None
    total_layers = None
    info = ps.get("info") or {}
    for k in ("current_layer", "layer", "layer_num"):
        if k in info:
            cur_layer = info.get(k)
            break
    for k in ("total_layer", "total_layers", "layers"):
        if k in info:
            total_layers = info.get(k)
            break

    st_disp = {
        "complete": "Completed",
        "printing": "Printing",
        "paused": "Paused",
        "error": "Error",
        "cancelled": "Cancelled",
        "standby": "Standby",
    }.get(st, st.capitalize() if st else "Unknown")

    layer_txt = None
    try:
        if cur_layer is not None and total_layers is not None:
            layer_txt = f"{int(cur_layer)}/{int(total_layers)}"
        elif cur_layer is not None:
            layer_txt = f"{int(cur_layer)}"
    except Exception:
        layer_txt = None

    eta_txt = None
    finish_txt = None
    if p is not None and st in ("printing", "paused"):
        try:
            dur_s = float(ps.get("print_duration") or 0)
            if dur_s > 0 and p > 0:
                prog = p / 100.0
                total_est = dur_s / prog
                eta_s = max(0, int(round(total_est - dur_s)))
                hh = eta_s // 3600
                mm = (eta_s % 3600) // 60
                eta_txt = f"{hh}:{mm:02d}hrs"

                now_local = dt.datetime.now(dt.timezone.utc).astimezone(ZoneInfo("America/Chicago"))
                finish_local = now_local + dt.timedelta(seconds=eta_s)
                finish_txt = finish_local.strftime("%H:%M CT")
        except Exception:
            pass

    out = []
    pct_txt = f" ({p}%)" if p is not None and st in ("printing", "paused") else ""
    out.append(f"{printer} Status: {st_disp}{pct_txt}")

    if filename:
        out.append(f"File: {filename}")

    if eta_txt and finish_txt:
        out.append(f"ETA: {eta_txt} ({finish_txt})")

    if layer_txt:
        out.append(f"Layer: {layer_txt}")

    print("\n".join(out).strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
