# OpenClaw Remote macOS Connector Setup (iMessage + Reminders + Calendar)

This guide captures the exact pattern we used:

- **OpenClaw Gateway** runs on a Linux/Ubuntu server.
- **Messages / Reminders / Calendar** live on a remote **macOS** machine.
- Linux talks to macOS over **SSH**.
- iMessage uses `imsg rpc` over SSH.
- Calendar uses `osascript` over SSH.
- Reminders uses a **local-only proxy** on the Mac (because macOS TCC often blocks Reminders when launched via sshd).

You can reuse this for a new OpenClaw installation pointing at a different Mac by changing only the variables in the “Variables” section.

---

## Variables (fill these in)

On the **Linux gateway host**:

- `MAC_SSH` = `<mac_user>@<mac_ip_or_hostname>`
  - Example: `user@192.168.0.100`
- `MAC_USER` = `<mac_user>`
  - Example: `user`
- `IMSG_PATH` = path to `imsg` on the Mac
  - Apple Silicon Homebrew: `/opt/homebrew/bin/imsg`
  - Intel Homebrew: `/usr/local/bin/imsg`
- `REMINDCTL_PATH` = path to `remindctl` on the Mac
  - Apple Silicon Homebrew: `/opt/homebrew/bin/remindctl`
  - Intel Homebrew: `/usr/local/bin/remindctl`
- `CHAT_DB` = iMessage DB path on the Mac
  - `/Users/<mac_user>/Library/Messages/chat.db`

---

## Part A — macOS preparation (on the Mac)

### A1) Enable Remote Login (SSH)

System Settings → General → Sharing → **Remote Login** → ON.

### A2) Install required CLIs

Install Homebrew if needed, then:

```bash
brew install steipete/tap/imsg
brew install steipete/tap/remindctl
```

### A3) Grant permissions (TCC)

You must run these *from a GUI Terminal session on the Mac* at least once:

#### iMessage (imsg)

- Ensure Messages.app is signed in.
- Run:

```bash
imsg chats --limit 1
imsg send --to <your_number_or_email> --text "test"
```

Approve any Automation / Full Disk prompts.

#### Reminders (remindctl)

```bash
remindctl authorize
remindctl status
```

You should see **Full access**.

#### Calendar (osascript)

```bash
osascript -e 'tell application "Calendar" to get name of calendars'
```

Approve any prompts.

---

## Part B — Linux gateway preparation (on the OpenClaw host)

### B1) SSH key auth (required)

From Linux:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519 -N ""
ssh-copy-id <mac_user>@<mac_host>
ssh <mac_user>@<mac_host> true
```

### B2) Create runner scripts

Create these scripts on Linux (paths are suggestions; keep them consistent):

#### 1) iMessage runner: `imsg-ssh`

`~/.openclaw/scripts/imsg-ssh`

```bash
#!/usr/bin/env bash
set -euo pipefail
exec ssh -o BatchMode=yes -o ConnectTimeout=5 -T ${MAC_SSH} ${IMSG_PATH} "$@"
```

#### 2) Calendar runner: `osascript-ssh`

`~/.openclaw/scripts/osascript-ssh`

```bash
#!/usr/bin/env bash
set -euo pipefail
exec ssh -o BatchMode=yes -o ConnectTimeout=5 -T ${MAC_SSH} osascript "$@"
```

#### 3) Reminders runner: `remindctl-ssh` (PROXY-BASED)

**Do not** run `remindctl` directly over SSH; use the proxy described in Part C.

`~/.openclaw/scripts/remindctl-ssh`

```bash
#!/usr/bin/env bash
set -euo pipefail

req_json=$(python3 - "$@" <<'PY'
import json, sys
print(json.dumps({"args": sys.argv[1:]}))
PY
)

req_b64=$(printf %s "$req_json" | base64 | tr -d '\n')

ssh -o BatchMode=yes -o ConnectTimeout=5 -T ${MAC_SSH} \
  "OPENCLAW_REQ_B64='$req_b64' python3 -c 'import os,base64,json,socket,sys
SOCK=\"/tmp/openclaw-remindctl.sock\"
req=json.loads(base64.b64decode(os.environ[\"OPENCLAW_REQ_B64\"]).decode(\"utf-8\"))
with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
    s.connect(SOCK)
    s.sendall((json.dumps(req)+\"\\n\").encode(\"utf-8\"))
    data=b\"\"
    while True:
        chunk=s.recv(65536)
        if not chunk:
            break
        data+=chunk
resp=json.loads(data.decode(\"utf-8\") or \"{}\")
if resp.get(\"stdout\"): sys.stdout.write(resp[\"stdout\"])
if resp.get(\"stderr\"): sys.stderr.write(resp[\"stderr\"])
raise SystemExit(int(resp.get(\"code\",1)))'"
```

Make scripts executable:

```bash
chmod +x ~/.openclaw/scripts/imsg-ssh ~/.openclaw/scripts/osascript-ssh ~/.openclaw/scripts/remindctl-ssh
```

---

## Part C — Reminders proxy on macOS (local-only)

Because Reminders privacy is strict, run a small proxy as a **LaunchAgent** in the GUI user session.

### C1) Create proxy script (on the Mac)

Create:

`~/.openclaw/reminders_proxy.py`

```python
#!/usr/bin/env python3
import json, os, socket, subprocess, traceback

SOCK = os.environ.get("OPENCLAW_REMINDERS_SOCK", "/tmp/openclaw-remindctl.sock")
REMINDCTL = os.environ.get("OPENCLAW_REMINDCTL", "/opt/homebrew/bin/remindctl")

try:
    if os.path.exists(SOCK):
        os.remove(SOCK)
except Exception:
    pass

srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
srv.bind(SOCK)
os.chmod(SOCK, 0o600)
srv.listen(5)

while True:
    conn, _ = srv.accept()
    try:
        data = b""
        while not data.endswith(b"\n"):
            chunk = conn.recv(65536)
            if not chunk:
                break
            data += chunk
        req = json.loads(data.decode("utf-8").strip() or "{}")
        args = req.get("args")
        if not isinstance(args, list):
            raise ValueError("args must be a list")
        p = subprocess.run([REMINDCTL, *args], capture_output=True, text=True)
        resp = {"ok": p.returncode == 0, "code": p.returncode, "stdout": p.stdout, "stderr": p.stderr}
    except Exception as e:
        resp = {"ok": False, "code": 1, "stdout": "", "stderr": "proxy error: " + str(e) + "\n" + traceback.format_exc()}
    conn.sendall((json.dumps(resp) + "\n").encode("utf-8"))
    conn.close()
```

Make executable:

```bash
chmod +x ~/.openclaw/reminders_proxy.py
```

### C2) Create LaunchAgent plist (on the Mac)

`~/Library/LaunchAgents/com.openclaw.reminders-proxy.plist`

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.openclaw.reminders-proxy</string>

  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/python3</string>
    <string>/Users/<mac_user>/.openclaw/reminders_proxy.py</string>
  </array>

  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>

  <key>StandardOutPath</key>
  <string>/tmp/openclaw-reminders-proxy.out.log</string>
  <key>StandardErrorPath</key>
  <string>/tmp/openclaw-reminders-proxy.err.log</string>
</dict>
</plist>
```

Load it (GUI Terminal on the Mac):

```bash
launchctl unload ~/Library/LaunchAgents/com.openclaw.reminders-proxy.plist 2>/dev/null || true
launchctl load ~/Library/LaunchAgents/com.openclaw.reminders-proxy.plist
ls -la /tmp/openclaw-remindctl.sock
```

### C3) One-time Reminders permission for the proxy context

macOS may require you to allow **Python** (or similar) under:

System Settings → Privacy & Security → Reminders.

If you don’t see it, a reset can help:

```bash
tccutil reset Reminders
```

Then reload the agent and retry.

---

## Part D — OpenClaw configuration (on Linux)

### D1) iMessage channel config

In `~/.openclaw/openclaw.json`:

```json5
{
  "channels": {
    "imessage": {
      "enabled": true,
      "cliPath": "/home/<linux_user>/.openclaw/scripts/imsg-ssh",
      "remoteHost": "<mac_user>@<mac_host>",
      "dbPath": "/Users/<mac_user>/Library/Messages/chat.db",
      "dmPolicy": "pairing",
      "groupPolicy": "allowlist",
      "includeAttachments": false
    }
  }
}
```

### D2) Calendar + Reminders access control

Use an ACL file in the OpenClaw workspace (example path):

- `workspace/access/apple-pim-acl.json`

Recommended default:
- Boss: `rw` to `*`
- everyone else: deny until approved

---

## Quick verification checklist

From Linux:

```bash
# iMessage remote RPC reachable
~/.openclaw/scripts/imsg-ssh rpc --help

# Calendar list calendars
~/.openclaw/scripts/osascript-ssh -e 'tell application "Calendar" to get name of calendars'

# Reminders proxy path
~/.openclaw/scripts/remindctl-ssh status
~/.openclaw/scripts/remindctl-ssh list --plain
```

---

## Notes / gotchas

- iMessage + Reminders + Calendar all require macOS user to be signed in and prompts approved.
- Reminders is the most fragile because TCC often blocks sshd contexts.
- Prefer LAN+SSH keys or Tailscale for stability.
- Keep everything local-only on the Mac (UNIX socket + LaunchAgent) for Reminders.
