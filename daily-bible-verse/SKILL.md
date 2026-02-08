---
name: daily-bible-verse
description: Set up and manage daily Bible verse notifications, including schedule creation and updates, delivery channel selection, and Bible version preferences. Use when a user asks to receive daily Bible verses, set notification times/days/timezone, change delivery channels, or modify their existing daily verse settings.
---

# Daily Bible Verse

## Defaults

- Default Bible version: NASB1995
- Default schedule: once daily at 08:00 America/Chicago
- Allow multiple delivery times per day
- Allow any user-provided Bible version string
- Enforce identity scoping: only the requesting identity can view or modify its own schedules
- Do not reuse the same verse for the same identity within the last 30 days

## Workflow

1. Detect intent to create or modify daily Bible verse notifications.
2. Collect or confirm user preferences as needed:
   - Delivery channel type and destination (Email, Discord, Slack, SMS, iMessage)
   - Schedule: times, days of week, timezone (default America/Chicago)
   - Bible version string (default NASB1995)
3. If the user provides partial updates, modify only the specified fields and keep the rest unchanged.
4. Confirm the resolved settings (channel, schedule, timezone, version) before creating or updating the schedule.
5. Track delivered verse references per-identity and consult that history before selecting a verse.
6. Do not reuse the same verse for the same identity within the last 30 days. If the candidate verse is a repeat, select a different verse.
7. When delivering a verse, provide the verse text sourced by the agent handling the request.
8. Format the delivered message exactly as specified below.

## Notes

- Never allow one identity to read or modify another identity's schedules.
- If a user says "daily" with no time, use the default time and timezone.
- If a user provides multiple times, schedule all of them.
- Track delivered verse references per-identity to enforce the 30-day no-repeat rule.

## Usage Examples

- "I want to get bible verses daily."
- "I want to setup daily bible verse notifications."
- "I want to receive daily bible verses only monday through friday at 2PM."
- "Change my daily verse time to 7:30am."
- "Switch my version to ESV."
- "Send my daily verse by SMS instead."

## Delivery Format

Use the following message format:

Daily Bible Verse

“<verse text>” (<book> <chapter>:<verse>)
