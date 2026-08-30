#!/usr/bin/env python3
"""Read-only HTTP view on the whatsapp-bridge SQLite store.

    GET /health
    GET /api/unanswered[?hours=4&days=14&context=15&groups=0]
        -> [{jid, chat_name, last_message_id, waiting_since, hours_waiting,
             last_message, messages: [{from, at, text}, ...]}, ...]

The database is opened read-only; the Go bridge is writing to it concurrently.
"""

import json
import os
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

DB = os.environ.get("WA_DB", "/data/store/messages.db")
TOKEN = os.environ.get("WA_API_TOKEN", "").strip()
DEF_HOURS = int(os.environ.get("WA_GRACE_HOURS", "4"))
DEF_DAYS = int(os.environ.get("WA_LOOKBACK_DAYS", "14"))
DEF_GROUPS = os.environ.get("WA_INCLUDE_GROUPS", "false").lower() == "true"
PORT = int(os.environ.get("WA_API_PORT", "8081"))

_HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(_HERE, "unanswered.sql"), encoding="utf-8") as fh:
    CANDIDATES_SQL = fh.read()

CONTEXT_SQL = """
SELECT timestamp,
       is_from_me,
       CASE WHEN content    <> '' THEN content
            WHEN media_type <> '' THEN '[' || media_type || ']'
            ELSE '[leer]' END AS text
FROM messages
WHERE chat_jid = ?
ORDER BY timestamp DESC
LIMIT ?
"""


def fetch(hours, days, context, groups):
    con = sqlite3.connect("file:%s?mode=ro" % DB, uri=True, timeout=5.0)
    con.row_factory = sqlite3.Row
    try:
        params = {
            "grace": "-%d hours" % hours,
            "cutoff": "-%d days" % days,
            "include_groups": 1 if groups else 0,
        }
        out, seen = [], set()
        for row in con.execute(CANDIDATES_SQL, params):
            jid = row["jid"]
            if jid in seen:          # two messages can share the max timestamp
                continue
            seen.add(jid)
            chat = dict(row)
            if context:
                window = [dict(m) for m in con.execute(CONTEXT_SQL, (jid, context))]
                window.reverse()     # oldest first, the way a human reads it
                chat["messages"] = [
                    {
                        "from": "me" if m["is_from_me"] else "them",
                        "at": m["timestamp"],
                        "text": m["text"],
                    }
                    for m in window
                ]
            out.append(chat)
        return out
    finally:
        con.close()


class Handler(BaseHTTPRequestHandler):
    server_version = "whatsapp-unanswered/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        print("[api] " + (fmt % args), flush=True)

    def _send(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _int_param(self, query, name, default, low, high):
        try:
            value = int(query.get(name, [default])[0])
        except (TypeError, ValueError):
            value = default
        return max(low, min(high, value))

    def do_GET(self):
        url = urlparse(self.path)

        if url.path == "/health":
            self._send(200, {"ok": os.path.exists(DB), "db": DB})
            return

        if url.path != "/api/unanswered":
            self._send(404, {"error": "not found"})
            return

        if TOKEN and self.headers.get("Authorization") != "Bearer " + TOKEN:
            self._send(401, {"error": "unauthorized"})
            return

        query = parse_qs(url.query)
        hours = self._int_param(query, "hours", DEF_HOURS, 0, 720)
        days = self._int_param(query, "days", DEF_DAYS, 1, 3650)
        context = self._int_param(query, "context", 15, 0, 100)
        groups = query.get("groups", ["1" if DEF_GROUPS else "0"])[0].lower() in (
            "1", "true", "yes", "on",
        )

        try:
            self._send(200, fetch(hours, days, context, groups))
        except sqlite3.Error as err:
            # Normal before the first QR pairing: the database does not exist yet.
            self._send(503, {"error": "database unavailable: %s" % err})


if __name__ == "__main__":
    if not TOKEN:
        print("[api] WARNING: no api_token set - the endpoint is unauthenticated",
              flush=True)
    print("[api] serving %s on :%d" % (DB, PORT), flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
