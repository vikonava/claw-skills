---
name: bible-reading-plan
description: Create and manage a Bible reading plan with daily/weekly schedules, completion tracking, reminders, and monthly summaries. Use when a user wants a Bible reading plan, timeline to finish the Bible, reading day preferences, reminder time/channel, holiday handling, or to get next-day chapters ahead of schedule.
---

# Bible Reading Plan

## Defaults

- Default starting point: Genesis 1:1
- Default timezone: user locale if known, otherwise ask
- Enforce identity scoping: only the requesting identity can view or modify its own plans, schedules, and progress

## Required Inputs

- Starting point reference
- Timeline to finish the Bible
- Reading days
- Include US holidays as reading days or not
- Reminder time of day and delivery channel
- If using Apple Reminders: reminder creation time and reminder due time

## Plan Files

Store plan data **per identity** (per user). Never assume a single shared `plan.csv` or shared progress file.

Recommended layout:

- `bible-reading-plan/<identity>/reading-plan.json`
- `bible-reading-plan/<identity>/reading-progress.json`
- `bible-reading-plan/<identity>/plan.csv` (optional: date→reading schedule used by reminder/email automations)

Plan file schema (date-less, sequential):

```json
{
  "identity": "<identity>",
  "start_point": "Genesis 1:1",
  "timeline": {
    "type": "end_date|duration",
    "value": "YYYY-MM-DD|PxxD"
  },
  "reading_days": "daily|weekdays|weekends|custom",
  "custom_days": ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"],
  "include_us_holidays": true,
  "version": "<optional version string>",
  "entries": [
    {
      "day_index": 1,
      "start_ref": "Genesis 1:1",
      "end_ref": "Genesis 2:25",
      "books": ["Genesis"],
      "chapters": ["Genesis 1","Genesis 2"],
      "estimated_load": "<optional numeric or label>"
    }
  ]
}
```

Progress file schema (per-identity tracking):

```json
{
  "identity": "<identity>",
  "current_day_index": 1,
  "completed": [
    {"day_index": 1, "completed_at": "YYYY-MM-DDTHH:mm:ssZ"}
  ],
  "last_completed_at": "YYYY-MM-DDTHH:mm:ssZ",
  "last_reminder_sent_at": "YYYY-MM-DDTHH:mm:ssZ"
}
```

## Workflow

1. Collect required inputs. If a user provides partial preferences, ask only for missing fields.
2. Normalize the timeline.
3. Resolve the reading days into a set of allowed weekdays.
4. Build a sequential plan that reaches the end of the Bible within the timeline using allowed reading days.
5. Balance daily reading loads to avoid overly long days. Prefer to end at book boundaries and start a new book on the next day.
6. Write `bible-reading-plan/reading-plan.json` and initialize `bible-reading-plan/reading-progress.json` for the identity.
7. Create three schedules for the identity: daily reminders, weekly reminders, and monthly summaries.
8. Send a confirmation with the plan summary, reminder schedule, and the first day's reading.

All scheduled jobs must reference the identity-specific plan/progress paths (e.g., `bible-reading-plan/<identity>/...`).

## Timeline Normalization

Accept either:

- An end date
- A duration (days, weeks, or months)

Normalize to an end date in the user's timezone before plan generation.

## Reading Days

- `daily` means all days of the week.
- `weekdays` means Mon-Fri.
- `weekends` means Sat-Sun.
- `custom` means a user-specified list of weekdays.

If US holidays are excluded, skip those dates when mapping the plan to calendar reminders.

## Plan Generation Guidance

- Use a canonical Bible book order from Genesis through Revelation.
- Prefer using chapter verse counts to estimate reading load per chapter. If verse counts are unavailable, balance by chapter counts and adjust to avoid long streaks of heavy chapters.
- Do not split a single chapter across days.
- Avoid starting a new book on the same day that the prior book ends.
- If a boundary would cause a short day, keep the short day and start the next book the following day.
- If the timeline is too short for the requested reading days, ask the user to either shorten the reading load preference, expand days, or extend the timeline.

## Scheduling and Reminders

Create three schedules per identity. All schedules must respect identity scoping and must not read or modify other identities' data.

Daily reminder:

- Runs at the user-specified time on reading days.
- Delivery channel is the user-specified OpenClaw channel or Apple Reminders if configured on the host.
- For SMS delivery, format the message exactly as: `Today's Reading Plan: Book:Chapter-Chapter`.
- For Apple Reminders delivery, format the reminder title exactly as: `Read Bible: Book:Chapter-Chapter`.
- On trigger, look up the current `day_index` and check if it is already completed.
- If completed, advance to the next pending `day_index` and send that day's chapters.
- If not completed, re-send the current day's chapters and keep `day_index` unchanged.
- Accept completion confirmations via user reply such as "I read my Bible reading" or "I've done my Bible reading today", or via an Apple Reminder being marked complete.

Weekly reminder:

- Runs on Monday at the user-specified time.
- If the user has not completed any reading in the past 7 days, notify them of the pending book and chapters.

Monthly summary:

- Runs on the first day of each month at the user-specified time.
- Prefer a completion-based summary when completion data is available (e.g., Apple Reminders items marked complete).
- If completion data is unavailable, fall back to a planned (schedule-based) summary.
- For a completion-based monthly email template + generator script, use:
  - `scripts/monthly_summary.py`
    - Inputs: the **identity-specific** plan CSV (`date,reading`) + reminder completions (via `remindctl list <list> --json`)
    - Output: HTML email body + counts + expected finish date
    - Requires an explicit `--plan` path (or `BIBLE_PLAN_CSV`) so different users can have different plans
    - Can send email directly if `gog` is configured

## On-Demand Requests

If the user asks to get ahead, return the next day's chapters without marking them as completed. If the user confirms they read those chapters, mark the corresponding day as completed and advance `current_day_index`.

## Notes

- Never allow one identity to read or modify another identity's plan, schedules, or progress.
- Store only sequential entries in the plan file. Do not store dates in the plan file.
- Keep progress per-identity in the progress file.
