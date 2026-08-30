# WhatsApp Bridge - Home Assistant local add-on

Runs [lharries/whatsapp-mcp](https://github.com/lharries/whatsapp-mcp)'s Go bridge
on the Pi (always on, so it never misses messages) plus a small read-only HTTP API
that answers one question for n8n: **which chats am I still owing a reply?**

The MCP server itself is deliberately *not* included - it speaks stdio and is meant
for a chat client. n8n gets JSON over HTTP instead.

## What runs inside

| Process | Port | Purpose |
|---|---|---|
| `whatsapp-bridge` (Go) | 8080 | WhatsApp Web session, writes `/data/store/messages.db`, REST API for sending. **Unauthenticated - not exposed by default.** |
| `unanswered_api.py` | 8081 | Read-only view on that database. Bearer token. This is what n8n calls. |

## Install

1. Copy this folder to `/addons/whatsapp_bridge/` on the Pi - via the **Samba share**
   add-on (`addons` share) or the **Advanced SSH & Web Terminal** add-on.
2. Settings → Add-ons → Add-on Store → ⋮ → **Check for updates**. The add-on shows up
   under "Local add-ons".
3. Install. The first build compiles whatsmeow from source with CGO - expect
   **10-20 minutes on a Pi 4** and make sure the Pi has internet.
4. Configuration tab → set `api_token` to a long random string. Save.
5. Start, then open the **Log** tab.

## First start: pairing

The log prints a QR code. If the block characters render cleanly, scan it with
WhatsApp → Settings → Linked devices. If the log panel mangles them, look for the
line `QR_CODE_RAW: 2@abc...` and turn that string into a QR code yourself
(e.g. `qrencode -t ANSI "2@abc..."` on any machine).

The code rotates every ~20 seconds, so grab a fresh one if the first attempt fails.
After pairing, history sync takes several minutes on a busy account.

**WhatsApp expires the linked-device session after roughly 20 days** - you will have
to re-scan. The add-on log is where that shows up. If history ever gets out of sync,
stop the add-on, delete `/data/store/messages.db` and `whatsapp.db`, and start over.

## Options

| Option | Default | Meaning |
|---|---|---|
| `api_token` | *(empty)* | Bearer token for :8081. Empty = no auth (logged as a warning). |
| `grace_hours` | `4` | Ignore messages newer than this - you may still be typing. |
| `lookback_days` | `14` | Ignore anything older; long-dead chats are not todos. |
| `include_groups` | `false` | Groups almost always end on someone else's message. |

## API

```
GET /health
GET /api/unanswered?hours=4&days=14&context=15&groups=0
Authorization: Bearer <api_token>
```

Query parameters override the add-on options per call. `context` is how many recent
messages of each chat to include - the LLM stage needs the window, not just the last
line. Returns:

```json
[{
  "jid": "4915...@s.whatsapp.net",
  "chat_name": "Sarah",
  "last_message_id": "3EB0...",
  "waiting_since": "2026-08-29 14:42:43.351+02:00",
  "hours_waiting": 26.0,
  "last_message": "Und um wieviel Uhr?",
  "messages": [{"from": "them", "at": "...", "text": "Hey, passt Samstag?"}]
}]
```

`503` before the first pairing (no database yet) - that is expected, not a failure.

## Calling it from n8n

Both add-ons sit on the same Docker network, so n8n reaches this one by hostname -
nothing has to be published to the LAN, which is why `8081/tcp` maps to `null`
by default.

The hostname is the add-on's **full slug with underscores turned into dashes**,
and that slug carries a prefix identifying where the add-on came from:

| Installed from | Slug | Hostname |
|---|---|---|
| this repository | `<repo-hash>_whatsapp_bridge` | `<repo-hash>-whatsapp-bridge` |
| a folder in `/addons` | `local_whatsapp_bridge` | `local-whatsapp-bridge` |

The repo hash is stable per repository. Read it off any Supervisor log line about
this add-on, or off the URL of its page in the UI:

```
http://<repo-hash>-whatsapp-bridge:8081/api/unanswered?hours=4&context=15
```

Authenticate with an n8n **Header Auth** credential sending
`Authorization: Bearer <api_token>`.

To reach the API from a browser instead, set a free host port under
**Configuration -> Network** (8081 itself is often taken) and use
`http://<pi-ip>:<that port>/...`.

## Security notes

- Port 8080 is set to `null` in `config.yaml`, so the bridge's send/download REST API
  is not reachable from the LAN. It has no authentication whatsoever - only open it
  if you deliberately want n8n to *send* WhatsApp messages, and understand that
  anything on your network could then do the same.
- `/data/store/messages.db` is a plaintext copy of your entire WhatsApp history.
  It lives in the Supervisor's add-on volume and is included in Home Assistant
  backups. Consider excluding this add-on from cloud-uploaded backups.
- The upstream repo is pinned by commit in the `Dockerfile` (`WA_MCP_REF`), so a
  rebuild cannot silently pull new code. Bump it deliberately.
