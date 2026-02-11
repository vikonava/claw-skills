---
name: klipper-notifications
description: Configure and manage Klipper print status notifications via Moonraker (HTTP API or webhooks) with per-status channel routing through OpenClaw. Use when a user wants to monitor Klipper/Moonraker print states, set status-triggered alerts, choose different channels per status, or update notification mappings.
---

# Klipper Notifications

## Defaults

- Enforce identity scoping: only the requesting identity can view or modify its own Klipper configuration, schedules, or notification mappings.
- Prefer Moonraker HTTP API when webhook support is unavailable.
- Avoid duplicate alerts by tracking last-seen status/event per identity.

## Required Inputs

- Moonraker connection method:
  - HTTP API base URL (e.g., local LAN URL), or
  - Webhook capability if Moonraker can POST to an OpenClaw webhook
- Auth details if needed (token, API key, or local network-only confirmation)
- Printer identifier (name or host) if multiple printers exist
- Statuses to track
- Channel mapping per status (can vary by status)
- Notification message preferences (brief vs. detailed)

## Identity-Scoped Storage

Store configuration and runtime state per identity. Suggested layout:

- `klipper-notifications/<identity>/config.json`
- `klipper-notifications/<identity>/state.json`

`config.json` schema (example):

```json
{
  "identity": "<identity>",
  "moonraker": {
    "mode": "http|webhook",
    "base_url": "http://printer.local",
    "auth": {
      "type": "none|token|api_key",
      "value": "<redacted>"
    }
  },
  "printer": {
    "name": "<printer-name>",
    "id": "<optional>"
  },
  "status_map": {
    "printing": {"channel": "<openclaw-channel>", "template": "<optional>"},
    "paused": {"channel": "<openclaw-channel>", "template": "<optional>"},
    "complete": {"channel": "<openclaw-channel>", "template": "<optional>"},
    "error": {"channel": "<openclaw-channel>", "template": "<optional>"}
  },
  "default_channel": "<openclaw-channel>",
  "message_style": "brief|detailed"
}
```

`state.json` schema (example):

```json
{
  "identity": "<identity>",
  "last_status": "<status>",
  "last_event_id": "<optional>",
  "last_notified_at": "YYYY-MM-DDTHH:mm:ssZ"
}
```

## Workflow

1. Collect required inputs. If the user provides partial preferences, ask only for missing fields.
2. Confirm identity-scoped storage path.
3. Determine status list:
   - Prefer fetching available statuses from Moonraker if possible.
   - If unavailable, ask the user to name desired statuses and treat them as literals.
4. Confirm per-status channel mapping. Allow a default channel for any status without an explicit mapping.
5. Choose integration mode:
   - **Webhook**: If Moonraker can POST to a webhook URL, configure it to send events to the user’s OpenClaw webhook.
   - **HTTP polling**: If no webhook support, poll Moonraker at a user-approved interval and compare to `state.json` to avoid duplicates.
6. Send a test notification per channel to confirm delivery.
7. Persist `config.json` and initialize `state.json`.

## Webhook Mode Guidance

- Ask for the OpenClaw webhook URL and any required auth headers.
- Provide OpenClaw webhook setup steps (hooks enablement, token, path, and which endpoint to use).
- Provide Moonraker setup steps so the user can configure its webhook target in their `moonraker.conf`.
- Configure Moonraker to POST status events to the webhook.
- Route the notification through the mapped OpenClaw channel for that status.

OpenClaw webhook setup (summary):

- Enable hooks in OpenClaw gateway config: `hooks.enabled=true`, set `hooks.token`, and optionally `hooks.path` (defaults to `/hooks`).
- Include the token on every webhook request using one of the supported methods:
  - `Authorization: Bearer <token>` (recommended)
  - `x-openclaw-token: <token>`
  - `?token=<token>` (deprecated)
- Choose the endpoint:
  - `POST /hooks/agent` to run a specific agent and optionally deliver to a specific channel/recipient.
  - `POST /hooks/<name>` if you want a mapped hook with transforms/templates.
- For per-status channel routing, use `POST /hooks/agent` and set `channel` and `to` based on the status mapping in `config.json`. Supported channels include: `last`, `whatsapp`, `telegram`, `discord`, `slack`, `mattermost` (plugin), `signal`, `imessage`, `msteams`.

Example `moonraker.conf` snippet (adapt to the user’s environment):

```ini
[webhooks]
target = https://openclaw.example/webhook/<token>
method = POST
content_type = application/json
```

## HTTP API Polling Guidance

- Poll Moonraker for current print status and job progress.
- If status changed since `state.json`, send a notification and update `state.json`.
- If status unchanged, do not notify.

## Notification Content

- **Brief**: `Printer <name>: <status> (<progress>%)`
- **Detailed**: include job name, elapsed, ETA if available, and a short summary of the last transition.

## On-Demand Requests

- If the user asks for current status, fetch live status and respond without modifying `state.json`.
- If the user asks to update mappings or channels, update `config.json` only for that identity.

## Safety Notes

- Never allow one identity to read or modify another identity’s config or mappings.
- Never send notifications to channels not explicitly approved by the requesting identity.
