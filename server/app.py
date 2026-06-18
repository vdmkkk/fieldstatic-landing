#!/usr/bin/env python3
"""Fieldstatic landing page + waitlist email collector.

Single-file, stdlib-only service:
  - serves the static landing page from STATIC_DIR
  - POST /api/subscribe  -> stores {email, timestamp, ip, user agent} in SQLite
  - GET  /admin          -> HTTP Basic Auth dashboard of collected emails
  - GET  /admin/export.csv -> CSV download of all submissions
  - GET  /healthz        -> health probe

Storage is a dedicated SQLite file (DB_PATH); it has nothing to do with any
other database on the host.
"""

import base64
import csv
import hmac
import html
import io
import json
import mimetypes
import os
import re
import sqlite3
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STATIC_DIR = os.environ.get("STATIC_DIR", "/app/site")
DB_PATH = os.environ.get("DB_PATH", "/data/waitlist.db")
PORT = int(os.environ.get("PORT", "8080"))
ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASS = os.environ.get("ADMIN_PASS", "")  # required for /admin to work

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MAX_EMAIL_LEN = 254

_db_lock = threading.Lock()
_conn = None


def get_db():
    global _conn
    if _conn is None:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.execute("PRAGMA journal_mode=WAL;")
        _conn.execute(
            """CREATE TABLE IF NOT EXISTS subscribers (
                   id         INTEGER PRIMARY KEY AUTOINCREMENT,
                   email      TEXT NOT NULL,
                   created_at TEXT NOT NULL,   -- ISO 8601 UTC
                   ip         TEXT,
                   user_agent TEXT
               )"""
        )
        _conn.commit()
    return _conn


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Handler(BaseHTTPRequestHandler):
    server_version = "fieldstatic/1.0"

    # ---- helpers -------------------------------------------------------
    def _send(self, code, body=b"", ctype="text/plain; charset=utf-8", extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if extra:
            for k, v in extra.items():
                self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj), "application/json; charset=utf-8")

    def _client_ip(self):
        # behind the edge nginx, which sets X-Forwarded-For / X-Real-IP
        xff = self.headers.get("X-Forwarded-For")
        if xff:
            return xff.split(",")[0].strip()
        return self.headers.get("X-Real-IP") or self.client_address[0]

    # ---- routing -------------------------------------------------------
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/healthz":
            return self._send(200, "ok")
        if path == "/admin" or path == "/admin/":
            return self.admin_dashboard()
        if path == "/admin/export.csv":
            return self.admin_export()
        return self.serve_static(path)

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/subscribe":
            return self.subscribe()
        return self._json(404, {"ok": False, "error": "not found"})

    # ---- subscribe -----------------------------------------------------
    def subscribe(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > 10000:
            return self._json(400, {"ok": False, "error": "Invalid request."})
        raw = self.rfile.read(length)
        email = ""
        ctype = (self.headers.get("Content-Type") or "").lower()
        try:
            if "application/json" in ctype:
                email = (json.loads(raw.decode("utf-8")).get("email") or "").strip()
            else:
                from urllib.parse import parse_qs
                email = (parse_qs(raw.decode("utf-8")).get("email", [""])[0]).strip()
        except Exception:
            return self._json(400, {"ok": False, "error": "Invalid request."})

        email = email.lower()
        if not email or len(email) > MAX_EMAIL_LEN or not EMAIL_RE.match(email):
            return self._json(400, {"ok": False, "error": "Please enter a valid email address."})

        ua = (self.headers.get("User-Agent") or "")[:500]
        ip = self._client_ip()
        try:
            with _db_lock:
                db = get_db()
                db.execute(
                    "INSERT INTO subscribers (email, created_at, ip, user_agent) VALUES (?,?,?,?)",
                    (email, now_iso(), ip, ua),
                )
                db.commit()
        except Exception:
            return self._json(500, {"ok": False, "error": "Could not save right now. Please try again."})
        return self._json(200, {"ok": True})

    # ---- admin auth ----------------------------------------------------
    def _auth_ok(self):
        if not ADMIN_PASS:
            return False
        hdr = self.headers.get("Authorization", "")
        if not hdr.startswith("Basic "):
            return False
        try:
            decoded = base64.b64decode(hdr[6:]).decode("utf-8")
            user, _, pw = decoded.partition(":")
        except Exception:
            return False
        return hmac.compare_digest(user, ADMIN_USER) and hmac.compare_digest(pw, ADMIN_PASS)

    def _require_auth(self):
        self._send(
            401, "Authentication required.",
            extra={"WWW-Authenticate": 'Basic realm="Fieldstatic Admin"'},
        )

    def admin_dashboard(self):
        if not self._auth_ok():
            return self._require_auth()
        with _db_lock:
            db = get_db()
            rows = db.execute(
                "SELECT email, created_at, ip, user_agent FROM subscribers ORDER BY id DESC"
            ).fetchall()
            total = len(rows)
            unique = db.execute("SELECT COUNT(DISTINCT email) FROM subscribers").fetchone()[0]

        trs = []
        for i, (email, created, ip, ua) in enumerate(rows):
            trs.append(
                "<tr><td class='num'>%d</td><td class='em'>%s</td><td class='ts'>%s</td>"
                "<td class='ip'>%s</td><td class='ua'>%s</td></tr>"
                % (
                    total - i,
                    html.escape(email),
                    html.escape(created),
                    html.escape(ip or ""),
                    html.escape((ua or "")[:120]),
                )
            )
        body = trs and "".join(trs) or (
            "<tr><td colspan='5' class='empty'>No emails collected yet.</td></tr>"
        )
        page = ADMIN_HTML.format(
            total=total, unique=unique, rows=body, generated=now_iso()
        )
        self._send(200, page, "text/html; charset=utf-8")

    def admin_export(self):
        if not self._auth_ok():
            return self._require_auth()
        with _db_lock:
            db = get_db()
            rows = db.execute(
                "SELECT id, email, created_at, ip, user_agent FROM subscribers ORDER BY id DESC"
            ).fetchall()
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["id", "email", "collected_at_utc", "ip", "user_agent"])
        for r in rows:
            w.writerow(r)
        self._send(
            200, buf.getvalue(), "text/csv; charset=utf-8",
            extra={"Content-Disposition": 'attachment; filename="fieldstatic-waitlist.csv"'},
        )

    # ---- static files --------------------------------------------------
    def serve_static(self, path):
        if path == "/":
            path = "/index.html"
        # prevent path traversal
        rel = os.path.normpath(path).lstrip("/\\")
        full = os.path.join(STATIC_DIR, rel)
        if not os.path.abspath(full).startswith(os.path.abspath(STATIC_DIR)):
            return self._send(403, "Forbidden")
        if not os.path.isfile(full):
            return self._send(404, "Not found")
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        try:
            with open(full, "rb") as f:
                data = f.read()
        except OSError:
            return self._send(404, "Not found")
        cache = "public, max-age=3600"
        if full.endswith(".html"):
            cache = "no-cache"
        self._send(200, data, ctype, extra={"Cache-Control": cache})

    def log_message(self, fmt, *args):
        # concise access log to stdout (captured by docker logs)
        print("%s - %s" % (self._client_ip(), fmt % args), flush=True)


ADMIN_HTML = """<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Fieldstatic · Waitlist</title>
<style>
  :root{{--paper:#F3EFE5;--ink:#16150F;--cobalt:#1E22C4;--lime:#C9F03B;}}
  *{{box-sizing:border-box}}
  body{{margin:0;background:var(--paper);color:var(--ink);
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif}}
  .wrap{{max-width:1100px;margin:0 auto;padding:32px 24px 64px}}
  h1{{font-size:22px;letter-spacing:.04em;margin:0 0 4px}}
  .sub{{color:#6b675c;font-size:13px;margin-bottom:24px}}
  .cards{{display:flex;gap:16px;margin-bottom:24px;flex-wrap:wrap}}
  .card{{background:#fff;border:1.5px solid var(--ink);padding:16px 20px;min-width:150px}}
  .card .k{{font-size:11px;letter-spacing:.12em;text-transform:uppercase;color:#6b675c}}
  .card .v{{font-size:30px;font-weight:800;margin-top:4px}}
  .bar{{display:flex;align-items:center;gap:12px;margin-bottom:14px}}
  .btn{{display:inline-block;background:var(--ink);color:var(--paper);text-decoration:none;
    font-size:13px;font-weight:700;padding:9px 16px;border-radius:999px}}
  .btn:hover{{background:var(--cobalt)}}
  table{{width:100%;border-collapse:collapse;background:#fff;border:1.5px solid var(--ink);font-size:14px}}
  th,td{{text-align:left;padding:10px 14px;border-bottom:1px solid rgba(22,21,15,.12)}}
  th{{background:var(--lime);font-size:11px;letter-spacing:.1em;text-transform:uppercase}}
  tr:last-child td{{border-bottom:none}}
  td.num{{color:#9a968b;font-variant-numeric:tabular-nums;width:42px}}
  td.em{{font-weight:600}}
  td.ts{{font-family:"Space Mono",ui-monospace,monospace;white-space:nowrap;color:#3a382f}}
  td.ip{{color:#6b675c;font-family:ui-monospace,monospace}}
  td.ua{{color:#9a968b;font-size:12px;max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
  td.empty{{text-align:center;color:#9a968b;padding:36px}}
  .foot{{color:#9a968b;font-size:12px;margin-top:16px}}
</style></head><body>
<div class="wrap">
  <h1>FIELDSTATIC — WAITLIST</h1>
  <div class="sub">Collected emails, newest first. Timestamps are UTC.</div>
  <div class="cards">
    <div class="card"><div class="k">Total submissions</div><div class="v">{total}</div></div>
    <div class="card"><div class="k">Unique emails</div><div class="v">{unique}</div></div>
  </div>
  <div class="bar"><a class="btn" href="/admin/export.csv">Download CSV</a></div>
  <table>
    <thead><tr><th>#</th><th>Email</th><th>Collected (UTC)</th><th>IP</th><th>User agent</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
  <div class="foot">Generated {generated}</div>
</div></body></html>"""


def main():
    if not ADMIN_PASS:
        print("WARNING: ADMIN_PASS not set — /admin is disabled.", flush=True)
    get_db()
    httpd = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print("fieldstatic listening on :%d  static=%s  db=%s" % (PORT, STATIC_DIR, DB_PATH), flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
