#!/usr/bin/env python3
"""Bible Reading Plan - Monthly summary (completion-based).

Data sources:
- Plan: CSV with date,reading
- Completion: Apple Reminders list export (JSON) from remindctl

This script is designed to be run by an OpenClaw cron job and produce:
- a JSON blob (stdout) with subject/html/to, or
- optionally send the email itself (via gog) if --send is used.

It intentionally uses a simple, broadly-compatible HTML email layout.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

DEFAULT_TZ = "America/Chicago"
# NOTE: Plans are per-user. Do not assume a single global plan.csv.
# Require callers (cron/agent) to pass an explicit --plan path (or BIBLE_PLAN_CSV).
DEFAULT_PLAN_CSV = os.environ.get("BIBLE_PLAN_CSV") or ""
DEFAULT_LIST_NAME = os.environ.get("BIBLE_REMINDERS_LIST") or "Assistant"
DEFAULT_TO = os.environ.get("BIBLE_SUMMARY_TO") or ""

TITLE_PREFIXES = (
    "Bible Reading Plan:",
    "Bible Reading:",
)


def _iso_month(d: dt.date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def _month_bounds(month: str) -> Tuple[dt.date, dt.date]:
    """Given YYYY-MM, return (start_date, end_date) inclusive."""
    y, m = month.split("-")
    year = int(y)
    mon = int(m)
    start = dt.date(year, mon, 1)
    if mon == 12:
        end = dt.date(year + 1, 1, 1) - dt.timedelta(days=1)
    else:
        end = dt.date(year, mon + 1, 1) - dt.timedelta(days=1)
    return start, end


def _parse_rfc3339(s: str) -> dt.datetime:
    # remindctl returns like 2026-02-09T13:00:00Z
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return dt.datetime.fromisoformat(s)


def _local_date_from_iso(s: str, tz: str) -> dt.date:
    # Avoid external tz libs: interpret timestamps as UTC, then shift via system tz.
    # Use `date -d` fallback for correctness across DST.
    # Example input: 2026-02-09T13:00:00Z
    try:
        # GNU date is available on the gateway.
        out = subprocess.check_output(
            [
                "bash",
                "-lc",
                f"TZ={tz} date -d {shlex_quote(s)} +%Y-%m-%d",
            ],
            text=True,
        ).strip()
        return dt.date.fromisoformat(out)
    except Exception:
        # Best-effort fallback (UTC date)
        return _parse_rfc3339(s).date()


def shlex_quote(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"


@dataclass
class PlanEntry:
    date: dt.date
    reading: str


def load_plan_csv(path: str) -> List[PlanEntry]:
    entries: List[PlanEntry] = []
    with open(path, newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            if not row.get("date") or not row.get("reading"):
                continue
            entries.append(PlanEntry(dt.date.fromisoformat(row["date"]), row["reading"].strip()))
    entries.sort(key=lambda e: e.date)
    return entries


def extract_reading_from_title(title: str) -> Optional[str]:
    t = title.strip()
    for p in TITLE_PREFIXES:
        if t.startswith(p):
            return t[len(p) :].strip()
    return None


def fetch_reminders_json(remindctl_bin: str, list_name: str) -> List[dict]:
    cmd = [
        remindctl_bin,
        "list",
        list_name,
        "--json",
        "--no-input",
    ]
    raw = subprocess.check_output(cmd, text=True)
    return json.loads(raw)


def collect_completed_readings(
    reminders: List[dict],
    tz: str,
    start: dt.date,
    end: dt.date,
    *,
    planned_by_date: Optional[Dict[dt.date, str]] = None,
) -> List[Tuple[dt.date, str]]:
    """Return completed Bible readings within [start, end].

    If planned_by_date is provided, only include completions whose anchor date exists
    in the plan (prevents unrelated/test reminders from polluting stats).
    """
    out: List[Tuple[dt.date, str]] = []
    for r in reminders:
        if not r.get("isCompleted"):
            continue
        title = r.get("title") or ""
        reading = extract_reading_from_title(title)
        if not reading:
            continue

        # Prefer dueDate as the plan date anchor. If missing, fall back to completionDate.
        anchor_iso = r.get("dueDate") or r.get("completionDate")
        if not anchor_iso:
            continue
        anchor_date = _local_date_from_iso(anchor_iso, tz)

        if planned_by_date is not None and anchor_date not in planned_by_date:
            continue

        if start <= anchor_date <= end:
            out.append((anchor_date, reading))

    out.sort(key=lambda x: x[0])
    return out


def summarize_books(readings: List[str]) -> List[str]:
    """Best-effort extraction of book names from reading strings.

    Handles patterns like:
    - "2 Chronicles 24-27" (numeric ordinal is part of the book name)
    - "Esther 10; Job 1-2" (multiple books separated by semicolons)
    """
    parts: List[str] = []
    for r in readings:
        parts.extend([p.strip() for p in r.split(";") if p.strip()])

    books: List[str] = []
    for p in parts:
        # Book name is everything before the chapter number.
        # Allow leading ordinals (1/2/3) as part of the book name.
        m = re.match(r"^(?P<book>(?:[1-3]\s+)?[^\d]+?)\s+\d+\b", p)
        if m:
            book = m.group("book").strip()
        else:
            book = p.strip()
        if book and book not in books:
            books.append(book)
    return books


def html_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def _expected_finish_dates(plan_all: List[PlanEntry], ahead_behind_days: int) -> Tuple[Optional[dt.date], Optional[dt.date]]:
    """Return (planned_finish, expected_finish_adjusted).

    planned_finish is simply the last date in the plan.

    expected_finish_adjusted shifts by ahead/behind reading *days* using the plan's
    own reading dates as the calendar (so it respects weekends/holidays baked into
    the plan). Positive ahead shifts earlier; negative shifts later.
    """
    if not plan_all:
        return None, None

    planned_finish = plan_all[-1].date
    idx = (len(plan_all) - 1) - ahead_behind_days
    if idx < 0:
        idx = 0
    if idx >= len(plan_all):
        idx = len(plan_all) - 1
    expected_finish = plan_all[idx].date
    return planned_finish, expected_finish


def render_html(
    month: str,
    tz: str,
    planned: List[PlanEntry],
    completed: List[Tuple[dt.date, str]],
    ahead_behind_days: int,
    *,
    planned_finish: Optional[dt.date] = None,
    expected_finish: Optional[dt.date] = None,
) -> str:
    # Simple email-safe layout (tables, inline styles)
    planned_count = len(planned)
    completed_count = len(completed)

    completed_readings = [r for _, r in completed]
    books = summarize_books(completed_readings)

    def li(text: str) -> str:
        return f"<li style=\"margin:0 0 6px 0;\">{html_escape(text)}</li>"

    completed_items = "\n".join(li(f"{d.isoformat()}: {r}") for d, r in completed) or (
        "<li style=\"margin:0;\"><em>No completed readings recorded for this period.</em></li>"
    )

    planned_items = "\n".join(li(f"{e.date.isoformat()}: {e.reading}") for e in planned) or (
        "<li style=\"margin:0;\"><em>No planned readings found for this period.</em></li>"
    )

    books_line = ", ".join(html_escape(b) for b in books) if books else "(none)"

    pace_text = (
        f"Ahead by {ahead_behind_days} Reading Day(s)" if ahead_behind_days > 0 else
        (f"Behind by {abs(ahead_behind_days)} Reading Day(s)" if ahead_behind_days < 0 else "On Pace")
    )

    finish_line = ""
    if planned_finish and expected_finish:
        finish_line = f"{expected_finish.isoformat()}" + (
            f" <span style=\"color:#6b7280; font-weight:500;\">(plan: {planned_finish.isoformat()})</span>" if expected_finish != planned_finish else ""
        )
    elif planned_finish:
        finish_line = planned_finish.isoformat()

    return f"""<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>Bible Reading Plan — Monthly Summary</title>
</head>
<body style=\"margin:0; padding:0; background:#f6f7f9; color:#111827;\">
  <div style=\"max-width:680px; margin:0 auto; padding:24px 14px;\">
    <div style=\"background:#ffffff; border:1px solid #e5e7eb; border-radius:12px; overflow:hidden;\">
      <div style=\"padding:18px 20px; background:#0f172a; color:#ffffff;\">
        <div style=\"font-size:14px; opacity:0.9;\">Bible Reading Plan</div>
        <div style=\"font-size:22px; font-weight:700; margin-top:4px;\">Monthly Summary — {html_escape(month)} ({html_escape(tz)})</div>
      </div>

      <div style=\"padding:18px 20px;\">
        <div style=\"display:flex; flex-wrap:wrap; gap:12px;\">
          <div style=\"flex:1 1 160px; padding:12px; border:1px solid #e5e7eb; border-radius:10px;\">
            <div style=\"font-size:12px; color:#6b7280;\">Completed</div>
            <div style=\"font-size:20px; font-weight:700;\">{completed_count}</div>
          </div>
          <div style=\"flex:1 1 160px; padding:12px; border:1px solid #e5e7eb; border-radius:10px;\">
            <div style=\"font-size:12px; color:#6b7280;\">Planned</div>
            <div style=\"font-size:20px; font-weight:700;\">{planned_count}</div>
          </div>
          <div style=\"flex:2 1 220px; padding:12px; border:1px solid #e5e7eb; border-radius:10px;\">
            <div style=\"font-size:12px; color:#6b7280;\">Pace</div>
            <div style=\"font-size:16px; font-weight:700;\">{html_escape(pace_text)}</div>
          </div>
          <div style=\"flex:2 1 220px; padding:12px; border:1px solid #e5e7eb; border-radius:10px;\">
            <div style=\"font-size:12px; color:#6b7280;\">Expected Finish Date</div>
            <div style=\"font-size:16px; font-weight:700;\">{finish_line or "—"}</div>
          </div>
        </div>

        <div style=\"margin-top:14px; font-size:14px; color:#374151;\">
          <strong>Books touched:</strong> {books_line}
        </div>

        <hr style=\"border:none; border-top:1px solid #e5e7eb; margin:16px 0;\" />

        <h2 style=\"margin:0 0 8px 0; font-size:16px;\">Completed readings</h2>
        <ul style=\"margin:0; padding-left:18px;\">{completed_items}</ul>

        <hr style=\"border:none; border-top:1px solid #e5e7eb; margin:16px 0;\" />

        <h2 style=\"margin:0 0 8px 0; font-size:16px;\">Plan for this month</h2>
        <ul style=\"margin:0; padding-left:18px;\">{planned_items}</ul>

        <div style=\"margin-top:18px; font-size:12px; color:#6b7280; line-height:1.4;\">
          This summary is based on Apple Reminders completion status for items titled “Bible Reading Plan: …” in the “{html_escape(DEFAULT_LIST_NAME)}” list.
        </div>
      </div>
    </div>

    <div style=\"text-align:center; margin-top:10px; font-size:11px; color:#9ca3af;\">
      Generated by OpenClaw
    </div>
  </div>
</body>
</html>"""


def compute_ahead_behind(
    plan: List[PlanEntry],
    reminders: List[dict],
    tz: str,
    through: dt.date,
) -> int:
    """Return completed_count - planned_count up to and including 'through'.

    Only counts completed reminders whose anchor date exists in the plan.
    """
    planned = [e for e in plan if e.date <= through]
    planned_by_date = {e.date: e.reading for e in plan}

    # Gather all completed (not restricted to a month) up to 'through'
    start = plan[0].date if plan else through
    completed = collect_completed_readings(
        reminders,
        tz,
        start,
        through,
        planned_by_date=planned_by_date if plan else None,
    )

    # De-dupe by anchor date (if user completes twice etc.) keeping first
    seen_dates = set()
    uniq: List[Tuple[dt.date, str]] = []
    for d, r in completed:
        if d in seen_dates:
            continue
        seen_dates.add(d)
        uniq.append((d, r))

    return len(uniq) - len(planned)


def _find_gog_bin() -> str:
    # Allow explicit override.
    if os.environ.get("GOG_BIN"):
        return os.environ["GOG_BIN"]

    # Prefer a local wrapper if present (common in OpenClaw setups).
    for p in (
        "./scripts/gog",
        "/home/vikonava/.openclaw/workspace-boss/scripts/gog",
        "/home/vikonava/.openclaw/workspace/scripts/gog",
    ):
        if os.path.exists(p) and os.access(p, os.X_OK):
            return p
    return "gog"


def send_email(to: str, subject: str, html: str, account: str) -> None:
    cmd = [
        _find_gog_bin(),
        "gmail",
        "send",
        "--to",
        to,
        "--subject",
        subject,
        "--body-html",
        html,
        "--account",
        account,
        "--no-input",
    ]
    subprocess.check_call(cmd)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", default=DEFAULT_PLAN_CSV, help="Path to plan CSV (date,reading)")
    ap.add_argument("--list", dest="list_name", default=DEFAULT_LIST_NAME, help="Apple Reminders list name")
    ap.add_argument(
        "--remindctl",
        default=os.environ.get("REMINDCTL_BIN") or "/home/vikonava/.openclaw/scripts/remindctl-ssh",
        help="Path to remindctl wrapper/binary that supports: remindctl list <list> --json",
    )
    ap.add_argument("--tz", default=DEFAULT_TZ)
    ap.add_argument("--to", default=DEFAULT_TO, help="Recipient email")
    ap.add_argument("--account", default=os.environ.get("GMAIL_ACCOUNT") or "", help="Gmail account to send from")
    ap.add_argument("--month", help="YYYY-MM (default: previous month)")
    ap.add_argument("--this-month-to-date", action="store_true", help="Use current month through today")
    ap.add_argument("--send", action="store_true", help="Actually send the email")
    args = ap.parse_args()

    if not args.plan:
        raise SystemExit("--plan (or BIBLE_PLAN_CSV env var) is required")
    if not os.path.exists(args.plan):
        raise SystemExit(f"Plan file not found: {args.plan}")

    if args.send:
        if not args.to:
            raise SystemExit("--to is required when --send is set")
        if not args.account:
            raise SystemExit("--account (or GMAIL_ACCOUNT env var) is required when --send is set")

    today = dt.date.today()

    if args.this_month_to_date:
        month = _iso_month(today)
        start, end = _month_bounds(month)
        end = min(end, today)
    else:
        if args.month:
            month = args.month
        else:
            # previous month
            first = dt.date(today.year, today.month, 1)
            prev_end = first - dt.timedelta(days=1)
            month = _iso_month(prev_end)
        start, end = _month_bounds(month)

    plan_all = load_plan_csv(args.plan)
    planned = [e for e in plan_all if start <= e.date <= end]

    reminders = fetch_reminders_json(args.remindctl, args.list_name)
    planned_by_date = {e.date: e.reading for e in plan_all}
    completed_raw = collect_completed_readings(
        reminders,
        args.tz,
        start,
        end,
        planned_by_date=planned_by_date,
    )

    # De-dupe by anchor date
    seen = set()
    completed: List[Tuple[dt.date, str]] = []
    for d, r in completed_raw:
        if d in seen:
            continue
        seen.add(d)
        completed.append((d, r))

    ahead_behind = compute_ahead_behind(plan_all, reminders, args.tz, end) if plan_all else 0

    subject = f"Bible Reading Plan — Monthly Summary ({month})"
    planned_finish, expected_finish = _expected_finish_dates(plan_all, ahead_behind)
    html = render_html(
        month,
        args.tz,
        planned,
        completed,
        ahead_behind,
        planned_finish=planned_finish,
        expected_finish=expected_finish,
    )

    payload = {
        "to": args.to,
        "subject": subject,
        "month": month,
        "tz": args.tz,
        "plannedCount": len(planned),
        "completedCount": len(completed),
        "aheadBehindDays": ahead_behind,
        "html": html,
    }

    if args.send:
        send_email(args.to, subject, html, args.account)

    print(json.dumps(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
