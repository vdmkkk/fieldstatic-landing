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
import smtplib
import sqlite3
import ssl
import threading
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formataddr
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STATIC_DIR = os.environ.get("STATIC_DIR", "/app/site")
DB_PATH = os.environ.get("DB_PATH", "/data/waitlist.db")
PORT = int(os.environ.get("PORT", "8080"))
ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASS = os.environ.get("ADMIN_PASS", "")  # required for /admin to work

# SMTP for the waitlist confirmation email. If SMTP_HOST/USER/PASS are unset the
# feature is silently disabled (e.g. local dev) and signups still work normally.
SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "465"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
SMTP_FROM = os.environ.get("SMTP_FROM", SMTP_USER)
SMTP_FROM_NAME = os.environ.get("SMTP_FROM_NAME", "Fieldstatic")
SITE_URL = os.environ.get("SITE_URL", "https://fieldstatic.shop").rstrip("/")

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
                   id            INTEGER PRIMARY KEY AUTOINCREMENT,
                   email         TEXT NOT NULL,
                   created_at    TEXT NOT NULL,   -- ISO 8601 UTC
                   ip            TEXT,
                   user_agent    TEXT,
                   name          TEXT,
                   quantity      INTEGER,
                   discount_code TEXT,
                   price_variant TEXT
               )"""
        )
        # migrate databases created before the cart/order fields existed
        existing = {row[1] for row in _conn.execute("PRAGMA table_info(subscribers)").fetchall()}
        for col, ddl in (("name", "name TEXT"),
                         ("quantity", "quantity INTEGER"),
                         ("discount_code", "discount_code TEXT"),
                         ("price_variant", "price_variant TEXT")):
            if col not in existing:
                _conn.execute("ALTER TABLE subscribers ADD COLUMN " + ddl)
        _conn.commit()
    return _conn


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Waitlist confirmation email (stdlib smtplib + email).
#
# Two variants: a warm welcome on the first signup, and a lighter "you're
# already on the list, sit tight" note when the same address signs up again.
# Markup is table-based with inline styles so it survives Gmail / Outlook /
# Apple Mail, and mirrors the site design tokens (see site/sold-out.html).
# ---------------------------------------------------------------------------

# brand palette — mirrors the :root tokens in site/sold-out.html
_PAPER = "#F3EFE5"
_WHITE = "#FFFFFF"
_INK = "#16150F"
_COBALT = "#1E22C4"
_LIME = "#C9F03B"


def _first_name(name):
    n = (name or "").strip()
    return n.split()[0] if n else ""


def _email_shell(preheader, body_html):
    """Wrap a card body in the shared frame (wordmark header + dark footer)."""
    return f"""\
<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="x-apple-disable-message-reformatting">
<title>Fieldstatic</title>
</head>
<body style="margin:0;padding:0;background:{_WHITE};-webkit-text-size-adjust:100%;">
<span style="display:none;max-height:0;overflow:hidden;opacity:0;color:{_WHITE};">{preheader}</span>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:{_WHITE};">
  <tr><td align="center" style="padding:30px 16px 44px;">
    <table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" style="width:600px;max-width:600px;">
      <tr><td style="padding:4px 6px 20px;">
        <span style="font-family:Archivo,'Helvetica Neue',Arial,sans-serif;font-weight:800;font-size:20px;letter-spacing:.16em;color:{_INK};">FIELDST<span style="color:{_COBALT};">A</span>TIC<sup style="font-size:9px;letter-spacing:0;">&#8482;</sup></span>
      </td></tr>
      <tr><td style="background:{_WHITE};border:1.5px solid {_INK};">{body_html}</td></tr>
      <tr><td style="background:{_INK};padding:22px 26px;">
        <div style="font-family:'Space Mono',ui-monospace,monospace;font-size:11px;letter-spacing:.06em;color:rgba(243,239,229,.6);line-height:1.7;">&#169; 2026 Fieldstatic &#8212; 6 fl oz of confidence.<br>Not DEET &#183; Not a repellent &#183; A different layer</div>
        <div style="margin-top:10px;"><a href="{SITE_URL}/privacy" style="font-family:'Space Mono',ui-monospace,monospace;font-size:11px;letter-spacing:.06em;color:{_LIME};text-decoration:none;">Privacy Policy</a></div>
      </td></tr>
    </table>
  </td></tr>
</table>
</body></html>"""


def _welcome_html(name):
    fn = _first_name(name)
    hi = ("Hi %s," % html.escape(fn)) if fn else "Hi there,"
    body = f"""
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
  <tr><td style="padding:34px 30px 0;">
    <span style="display:inline-block;font-family:'Space Mono',ui-monospace,monospace;font-size:11px;font-weight:700;letter-spacing:.16em;text-transform:uppercase;color:{_INK};background:{_LIME};border:1.5px solid {_INK};border-radius:999px;padding:7px 14px;">You're on the list</span>
  </td></tr>
  <tr><td style="padding:18px 30px 0;">
    <h1 style="margin:0;font-family:'Archivo Expanded',Archivo,'Helvetica Neue',Arial,sans-serif;font-weight:800;font-size:33px;line-height:1.05;letter-spacing:-.02em;color:{_INK};">Thanks for joining the <span style="background:{_LIME};padding:0 .08em;">waitlist.</span></h1>
  </td></tr>
  <tr><td style="padding:18px 30px 0;font-family:'Helvetica Neue',Arial,sans-serif;font-size:16px;line-height:1.62;color:{_INK};">
    {hi}<br><br>
    We're sorry we couldn't get a bottle to you this time &#8212; Batch&nbsp;No.&nbsp;001 sold out faster than we expected, and the next batch isn't ready quite yet. We know that's a letdown, and we're grateful you're willing to wait.<br><br>
    Here's where things stand &#8212; there's nothing else you need to do:
  </td></tr>
  <tr><td style="padding:22px 30px 0;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border:1.5px solid {_INK};">
      <tr>
        <td width="52" valign="top" style="padding:18px 0 16px 18px;"><div style="width:32px;height:32px;line-height:30px;text-align:center;border:1.5px solid {_INK};border-radius:50%;background:{_LIME};font-size:15px;color:{_INK};">&#10003;</div></td>
        <td valign="top" style="padding:18px 20px 16px 12px;font-family:'Helvetica Neue',Arial,sans-serif;">
          <div style="font-weight:700;font-size:15px;color:{_INK};margin-bottom:3px;">You're locked into the waitlist</div>
          <div style="font-size:14px;line-height:1.55;color:{_INK};">Your details are saved &#8212; no second sign-up, no re-typing your email.</div>
        </td>
      </tr>
      <tr><td colspan="2" style="padding:0 18px;"><div style="border-top:1.5px dashed rgba(22,21,15,.22);font-size:0;line-height:0;">&nbsp;</div></td></tr>
      <tr>
        <td width="52" valign="top" style="padding:16px 0 18px 18px;"><div style="width:32px;height:32px;line-height:30px;text-align:center;border:1.5px solid {_INK};border-radius:50%;background:{_LIME};font-size:15px;color:{_INK};">&#9993;</div></td>
        <td valign="top" style="padding:16px 20px 18px 12px;font-family:'Helvetica Neue',Arial,sans-serif;">
          <div style="font-weight:700;font-size:15px;color:{_INK};margin-bottom:3px;">We'll reach out the moment Batch&nbsp;No.&nbsp;002 is ready</div>
          <div style="font-size:14px;line-height:1.55;color:{_INK};">You'll be among the first to hear &#8212; one email, the day it ships. No spam in between.</div>
        </td>
      </tr>
    </table>
  </td></tr>
  <tr><td style="padding:20px 30px 0;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:{_INK};">
      <tr><td style="padding:20px 22px;">
        <div style="font-family:'Space Mono',ui-monospace,monospace;font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:{_LIME};margin-bottom:7px;">A thank-you for waiting</div>
        <div style="font-family:'Archivo Expanded',Archivo,Arial,sans-serif;font-weight:800;font-size:22px;color:{_PAPER};line-height:1.1;">10% off your first order</div>
        <div style="font-family:'Space Mono',ui-monospace,monospace;font-size:13px;color:rgba(243,239,229,.72);margin-top:10px;line-height:1.5;">Tied to your email and applied automatically when we reopen &#8212; there's nothing to enter.</div>
      </td></tr>
    </table>
  </td></tr>
  <tr><td style="padding:26px 30px 34px;">
    <a href="{SITE_URL}" style="display:inline-block;background:{_INK};color:{_PAPER};font-family:Archivo,'Helvetica Neue',Arial,sans-serif;font-weight:800;font-size:15px;text-decoration:none;border-radius:999px;padding:14px 28px;">Back to fieldstatic.shop &#8594;</a>
  </td></tr>
</table>"""
    pre = "Batch 001 sold out — but you're locked in, with 10% off when we reopen."
    return _email_shell(pre, body)


def _repeat_html(name):
    fn = _first_name(name)
    hi = ("Hi %s," % html.escape(fn)) if fn else "Hi there,"
    body = f"""
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
  <tr><td style="padding:34px 30px 0;">
    <span style="display:inline-block;font-family:'Space Mono',ui-monospace,monospace;font-size:11px;font-weight:700;letter-spacing:.16em;text-transform:uppercase;color:{_INK};background:{_LIME};border:1.5px solid {_INK};border-radius:999px;padding:7px 14px;">Already on the list</span>
  </td></tr>
  <tr><td style="padding:18px 30px 0;">
    <h1 style="margin:0;font-family:'Archivo Expanded',Archivo,'Helvetica Neue',Arial,sans-serif;font-weight:800;font-size:33px;line-height:1.05;letter-spacing:-.02em;color:{_INK};">We love the <span style="background:{_LIME};padding:0 .08em;">enthusiasm</span> ;)</h1>
  </td></tr>
  <tr><td style="padding:18px 30px 0;font-family:'Helvetica Neue',Arial,sans-serif;font-size:16px;line-height:1.62;color:{_INK};">
    {hi}<br><br>
    We appreciate the interest &#8212; really. But you're <b style="color:{_INK};">already on the Fieldstatic waitlist</b>, so there's no need to sign up again. Please be a little patient ;)<br><br>
    We'll email you the moment Batch&nbsp;No.&nbsp;002 is ready &#8212; you won't miss it.
  </td></tr>
  <tr><td style="padding:22px 30px 0;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:{_INK};">
      <tr><td style="padding:20px 22px;">
        <div style="font-family:'Space Mono',ui-monospace,monospace;font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:{_LIME};margin-bottom:7px;">Still locked in</div>
        <div style="font-family:'Archivo Expanded',Archivo,Arial,sans-serif;font-weight:800;font-size:20px;color:{_PAPER};line-height:1.15;">Your spot &amp; your 10% welcome discount</div>
        <div style="font-family:'Space Mono',ui-monospace,monospace;font-size:13px;color:rgba(243,239,229,.72);margin-top:10px;line-height:1.5;">Applied automatically when we reopen &#8212; nothing to do but wait.</div>
      </td></tr>
    </table>
  </td></tr>
  <tr><td style="padding:26px 30px 34px;">
    <a href="{SITE_URL}" style="display:inline-block;background:{_INK};color:{_PAPER};font-family:Archivo,'Helvetica Neue',Arial,sans-serif;font-weight:800;font-size:15px;text-decoration:none;border-radius:999px;padding:14px 28px;">Back to fieldstatic.shop &#8594;</a>
  </td></tr>
</table>"""
    pre = "You're already on the waitlist — no need to sign up again. Sit tight!"
    return _email_shell(pre, body)


def build_waitlist_email(name, is_repeat):
    """Return (subject, text_body, html_body) for one confirmation email."""
    fn = _first_name(name)
    hi = ("Hi %s," % fn) if fn else "Hi there,"
    if is_repeat:
        subject = "You're already on the list — hang tight ;)"
        text = (
            f"{hi}\n\n"
            "We appreciate the interest -- really. But you're already on the "
            "Fieldstatic waitlist, so there's no need to sign up again. Please be "
            "a little patient ;)\n\n"
            "We'll email you the moment Batch No. 002 is ready -- you won't miss it.\n\n"
            "Your spot and your 10% welcome discount are still locked in -- tied to "
            "your email and applied automatically when we reopen.\n\n"
            f"{SITE_URL}\n\n-- Fieldstatic\n"
        )
        html_body = _repeat_html(name)
    else:
        subject = "You're on the Fieldstatic waitlist — and 10% is yours"
        text = (
            f"{hi}\n\n"
            "We're sorry we couldn't get a bottle to you this time -- Batch No. 001 "
            "sold out faster than we expected, and the next batch isn't ready quite "
            "yet. We're grateful you're willing to wait.\n\n"
            "Here's where things stand -- there's nothing else you need to do:\n\n"
            "  * You're locked into the waitlist. Your details are saved.\n"
            "  * We'll reach out the moment Batch No. 002 is ready -- one email, the "
            "day it ships. No spam in between.\n\n"
            "A thank-you for waiting: 10% off your first order, tied to your email and "
            "applied automatically when we reopen -- there's nothing to enter.\n\n"
            f"{SITE_URL}\n\n-- Fieldstatic\n"
        )
        html_body = _welcome_html(name)
    return subject, text, html_body


def send_waitlist_email(to_email, name, is_repeat):
    """Build and send one confirmation email. Safe to call from a worker thread."""
    if not (SMTP_HOST and SMTP_USER and SMTP_PASS):
        return
    try:
        subject, text_body, html_body = build_waitlist_email(name, is_repeat)
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = formataddr((SMTP_FROM_NAME, SMTP_FROM))
        msg["To"] = to_email
        msg.set_content(text_body)
        msg.add_alternative(html_body, subtype="html")
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ctx, timeout=20) as s:
            s.login(SMTP_USER, SMTP_PASS)
            s.send_message(msg)
        print("email sent to %s (repeat=%s)" % (to_email, is_repeat), flush=True)
    except Exception as e:
        print("EMAIL ERROR for %s: %r" % (to_email, e), flush=True)


def dispatch_waitlist_email(to_email, name, is_repeat):
    """Fire-and-forget the confirmation email so the API response isn't blocked."""
    if not (SMTP_HOST and SMTP_USER and SMTP_PASS):
        return
    threading.Thread(
        target=send_waitlist_email,
        args=(to_email, name, is_repeat),
        daemon=True,
    ).start()


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
        name = ""
        quantity = 1
        price_variant = ""
        ctype = (self.headers.get("Content-Type") or "").lower()
        try:
            if "application/json" in ctype:
                payload = json.loads(raw.decode("utf-8"))
                email = (payload.get("email") or "").strip()
                name = (payload.get("name") or "").strip()
                quantity = payload.get("quantity", 1)
                price_variant = (payload.get("price_variant") or "").strip()
            else:
                from urllib.parse import parse_qs
                form = parse_qs(raw.decode("utf-8"))
                email = (form.get("email", [""])[0]).strip()
                name = (form.get("name", [""])[0]).strip()
                quantity = form.get("quantity", ["1"])[0]
                price_variant = (form.get("price_variant", [""])[0]).strip()
        except Exception:
            return self._json(400, {"ok": False, "error": "Invalid request."})

        email = email.lower()
        if not email or len(email) > MAX_EMAIL_LEN or not EMAIL_RE.match(email):
            return self._json(400, {"ok": False, "error": "Please enter a valid email address."})

        name = name[:120]
        price_variant = price_variant[:40]
        try:
            quantity = int(quantity)
        except (TypeError, ValueError):
            quantity = 1
        quantity = max(1, min(99, quantity))

        ua = (self.headers.get("User-Agent") or "")[:500]
        ip = self._client_ip()
        try:
            with _db_lock:
                db = get_db()
                # a prior row with this address means they're already on the list
                prior = db.execute(
                    "SELECT 1 FROM subscribers WHERE email=? LIMIT 1", (email,)
                ).fetchone()
                is_repeat = prior is not None
                db.execute(
                    "INSERT INTO subscribers (email, created_at, ip, user_agent, name, quantity, discount_code, price_variant)"
                    " VALUES (?,?,?,?,?,?,?,?)",
                    (email, now_iso(), ip, ua, name, quantity, None, price_variant),
                )
                db.commit()
        except Exception:
            return self._json(500, {"ok": False, "error": "Could not save right now. Please try again."})

        # confirmation email — warm welcome on first signup, a lighter "sit tight"
        # note on repeats. Dispatched in the background so slow/failing SMTP never
        # blocks (or fails) the signup itself.
        dispatch_waitlist_email(email, name, is_repeat)
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
                "SELECT email, created_at, ip, user_agent, name, quantity, discount_code, price_variant"
                " FROM subscribers ORDER BY id DESC"
            ).fetchall()
            total = len(rows)
            unique = db.execute("SELECT COUNT(DISTINCT email) FROM subscribers").fetchone()[0]

        trs = []
        for i, (email, created, ip, ua, name, qty, code, pv) in enumerate(rows):
            trs.append(
                "<tr><td class='num'>%d</td><td class='nm'>%s</td><td class='em'>%s</td>"
                "<td class='qt'>%s</td><td class='pv'>%s</td><td class='cd'>%s</td><td class='ts'>%s</td>"
                "<td class='ip'>%s</td><td class='ua'>%s</td></tr>"
                % (
                    total - i,
                    html.escape(name or "—"),
                    html.escape(email),
                    html.escape(str(qty) if qty is not None else "—"),
                    html.escape(pv or "—"),
                    html.escape(code or "—"),
                    html.escape(created),
                    html.escape(ip or ""),
                    html.escape((ua or "")[:120]),
                )
            )
        body = trs and "".join(trs) or (
            "<tr><td colspan='9' class='empty'>No orders collected yet.</td></tr>"
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
                "SELECT id, name, email, quantity, price_variant, discount_code, created_at, ip, user_agent"
                " FROM subscribers ORDER BY id DESC"
            ).fetchall()
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["id", "name", "email", "quantity", "price_variant", "discount_code",
                    "collected_at_utc", "ip", "user_agent"])
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
        # allow pretty URLs: /cart -> cart.html, /sold-out -> sold-out.html
        if not os.path.isfile(full) and os.path.isfile(full + ".html"):
            full += ".html"
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
  td.ua{{color:#9a968b;font-size:12px;max-width:240px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
  td.nm{{font-weight:600}}
  td.qt{{text-align:center;font-variant-numeric:tabular-nums;color:#3a382f}}
  td.cd{{font-family:"Space Mono",ui-monospace,monospace;font-size:12px;color:var(--cobalt)}}
  td.pv{{font-family:"Space Mono",ui-monospace,monospace;font-size:12px;color:#6b675c;text-align:center}}
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
    <thead><tr><th>#</th><th>Name</th><th>Email</th><th>Qty</th><th>Price</th><th>Code</th><th>Collected (UTC)</th><th>IP</th><th>User agent</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
  <div class="foot">Generated {generated}</div>
</div></body></html>"""


def main():
    import sys
    argv = sys.argv[1:]
    # --preview [outdir]      write both email variants to HTML files to eyeball
    # --send-test TO [repeat] send a live test email through the configured SMTP
    if argv and argv[0] == "--preview":
        outdir = argv[1] if len(argv) > 1 else "."
        for tag, repeat in (("welcome", False), ("repeat", True)):
            _, _, h = build_waitlist_email("Alex Rivera", repeat)
            path = os.path.join(outdir, "email-preview-%s.html" % tag)
            with open(path, "w", encoding="utf-8") as f:
                f.write(h)
            print("wrote", path)
        return
    if argv and argv[0] == "--send-test":
        if len(argv) < 2:
            print("usage: app.py --send-test you@example.com [repeat]")
            return
        repeat = len(argv) > 2 and argv[2] == "repeat"
        send_waitlist_email(argv[1], "Alex Rivera", repeat)
        return

    if not ADMIN_PASS:
        print("WARNING: ADMIN_PASS not set — /admin is disabled.", flush=True)
    if SMTP_HOST and SMTP_USER and SMTP_PASS:
        print("email: enabled (%s:%d as %s)" % (SMTP_HOST, SMTP_PORT, SMTP_USER), flush=True)
    else:
        print("email: disabled (set SMTP_HOST/SMTP_USER/SMTP_PASS to enable)", flush=True)
    get_db()
    httpd = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print("fieldstatic listening on :%d  static=%s  db=%s" % (PORT, STATIC_DIR, DB_PATH), flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
