#!/usr/bin/env node
/* Moonraker WebSocket print status notifier -> OpenClaw.

This is NOT polling: it maintains a persistent websocket connection to Moonraker.

Requires:
- Node.js 18+ (global WebSocket + fetch)

Reads identity-scoped config/state:
- klipper-notifications/<identity>/config.json
- klipper-notifications/<identity>/state.json
- klipper-notifications/<identity>/secrets.env  (HOOK_TOKEN)

Config expectations (minimal):
- moonraker.base_url (http://host:7125)
- printer.name
- to (delivery target id; e.g. imessage:+1555...)
- default_channel (e.g. imessage)
- status_map (map of status -> { channel?, template? })

On each status transition, it triggers OpenClaw via POST /hooks/agent.
*/

import fs from 'node:fs';
import path from 'node:path';

function readJson(p, fallback) {
  try {
    return JSON.parse(fs.readFileSync(p, 'utf8'));
  } catch {
    return fallback;
  }
}

function writeJsonAtomic(p, obj) {
  const tmp = p + '.tmp';
  fs.writeFileSync(tmp, JSON.stringify(obj, null, 2) + '\n', 'utf8');
  fs.renameSync(tmp, p);
}

function readEnvFile(p) {
  const out = {};
  const txt = fs.readFileSync(p, 'utf8');
  for (const line of txt.split(/\r?\n/)) {
    const s = line.trim();
    if (!s || s.startsWith('#')) continue;
    const i = s.indexOf('=');
    if (i === -1) continue;
    out[s.slice(0, i).trim()] = s.slice(i + 1).trim();
  }
  return out;
}

function normalizeState(s) {
  return (s || '').toString().trim().toLowerCase();
}

function pct(progress) {
  try {
    if (progress === null || progress === undefined) return null;
    const v = Number(progress);
    if (!Number.isFinite(v)) return null;
    return Math.max(0, Math.min(100, Math.round(v * 100)));
  } catch {
    return null;
  }
}

function defaultMessage(printerName, state, filename, progressPct) {
  // Brief, chat-friendly.
  let label = state;
  if (state === 'complete') label = 'completed';

  const parts = [`${printerName} print ${label}.`];
  if (filename) parts.push(filename);
  if ((state === 'printing' || state === 'paused') && progressPct !== null) parts.push(`${progressPct}%`);
  return parts.join(' ').trim();
}

function applyTemplate(template, vars) {
  // Very small templater: replaces {{printer}}, {{status}}, {{filename}}, {{progress}}
  if (!template) return null;
  return template
    .replaceAll('{{printer}}', vars.printer)
    .replaceAll('{{status}}', vars.status)
    .replaceAll('{{filename}}', vars.filename)
    .replaceAll('{{progress}}', vars.progress);
}

async function postHook({ hookToken, hookUrl, message, channel, to }) {
  const payload = {
    message,
    name: 'Klipper',
    wakeMode: 'now',
    deliver: true,
    channel,
    to,
    timeoutSeconds: 60,
  };

  const res = await fetch(hookUrl, {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${hookToken}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(payload),
  });

  if (!res.ok) {
    const txt = await res.text().catch(() => '');
    throw new Error(`hooks/agent failed: ${res.status} ${txt}`);
  }
}

async function run() {
  const args = process.argv.slice(2);
  const identity = (args[0] || process.env.KLIPPER_IDENTITY || '').trim();
  if (!identity) {
    throw new Error('Usage: ws_listener.mjs <identity>  (or set KLIPPER_IDENTITY)');
  }

  const root = process.env.OPENCLAW_WORKSPACE || process.cwd();
  const baseDir = path.join(root, 'klipper-notifications', identity);

  const cfgPath = path.join(baseDir, 'config.json');
  const statePath = path.join(baseDir, 'state.json');
  const secretsPath = path.join(baseDir, 'secrets.env');

  const cfg = readJson(cfgPath, {});
  const secrets = readEnvFile(secretsPath);

  const hookToken = secrets.HOOK_TOKEN;
  const hookUrl = process.env.OPENCLAW_HOOK_URL || 'http://127.0.0.1:18789/hooks/agent';

  if (!hookToken) throw new Error('Missing HOOK_TOKEN in secrets.env');

  const moonBase = cfg?.moonraker?.base_url;
  const printerName = cfg?.printer?.name || 'Printer';
  const to = cfg?.to;
  const defaultChannel = cfg?.default_channel || 'imessage';
  const statusMap = cfg?.status_map || {};

  if (!moonBase) throw new Error('Missing moonraker.base_url in config.json');
  if (!to) throw new Error('Missing to in config.json (e.g., imessage:+1555...)');

  const wsUrl = moonBase.replace(/^http/i, 'ws').replace(/\/$/, '') + '/websocket';

  let backoffMs = 1000;

  while (true) {
    let ws;
    try {
      ws = new WebSocket(wsUrl);

      await new Promise((resolve, reject) => {
        ws.addEventListener('open', resolve, { once: true });
        ws.addEventListener('error', () => reject(new Error('websocket error')), { once: true });
      });

      backoffMs = 1000;

      ws.send(JSON.stringify({
        jsonrpc: '2.0',
        method: 'printer.objects.subscribe',
        params: { objects: { print_stats: null, virtual_sdcard: null } },
        id: 1,
      }));

      const stateObj = readJson(statePath, { identity, last_status: '', last_filename: '', last_notified_at: '' });
      let lastStatus = normalizeState(stateObj.last_status);
      let lastFile = (stateObj.last_filename || '').trim();

      const interesting = new Set(['paused', 'complete', 'error', 'cancelled', 'printing']);

      const handle = async (ev) => {
        let data;
        try {
          data = JSON.parse(ev.data);
        } catch {
          return;
        }

        if (data?.method !== 'notify_status_update') return;
        const params = data?.params;
        if (!Array.isArray(params) || params.length < 1) return;
        const status = params[0] || {};

        const ps = status.print_stats || {};
        const vsd = status.virtual_sdcard || {};

        const curStatus = normalizeState(ps.state);
        const filename = (vsd.filename || '').toString().trim();
        const progressPct = pct(vsd.progress);

        if (!curStatus || !interesting.has(curStatus)) return;

        if (curStatus !== lastStatus) {
          const mapping = statusMap[curStatus] || {};
          const channel = mapping.channel || defaultChannel;

          const vars = {
            printer: printerName,
            status: curStatus,
            filename: filename,
            progress: progressPct === null ? '' : String(progressPct),
          };

          const msgFromTemplate = applyTemplate(mapping.template, vars);
          const msg = (msgFromTemplate || defaultMessage(printerName, curStatus, filename && filename !== lastFile ? filename : '', progressPct)).trim();

          await postHook({ hookToken, hookUrl, message: msg, channel, to });

          lastStatus = curStatus;
          lastFile = filename;
          stateObj.last_status = curStatus;
          stateObj.last_filename = filename;
          stateObj.last_notified_at = new Date().toISOString();
          writeJsonAtomic(statePath, stateObj);
        }
      };

      ws.addEventListener('message', (ev) => {
        handle(ev).catch(() => {});
      });

      await new Promise((resolve) => {
        ws.addEventListener('close', resolve, { once: true });
        ws.addEventListener('error', resolve, { once: true });
      });

    } catch {
      // ignore
    } finally {
      try { ws?.close(); } catch {}
    }

    await new Promise(r => setTimeout(r, backoffMs));
    backoffMs = Math.min(backoffMs * 2, 30000);
  }
}

run().catch((err) => {
  console.error(String(err?.stack || err));
  process.exit(1);
});
