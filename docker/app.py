from flask import Flask, jsonify, request, Response, session, redirect
import json
import os
import time
import random
import secrets
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import timedelta

STATUS_FILE = "/data/status.json"
OVERRIDE_FILE = "/data/override"
OVERRIDE_EXPIRY_FILE = "/data/override_expiry"
VALID_IFACES = ["eno4", "eno1", "eno2", "eno3"]

# ---- Auth / OTP config (set these via docker-compose environment) ------
SMTP_HOST = os.environ.get("SMTP_HOST", "mail.example.in")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "noreply@example.in")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
MAIL_FROM = os.environ.get("MAIL_FROM", SMTP_USER)
OTP_RECIPIENT = os.environ.get("OTP_RECIPIENT", "karanbir@virtualoplossing.com")

OTP_TTL_SECONDS = 300        # code valid for 5 minutes
OTP_RESEND_COOLDOWN = 30      # seconds between resend requests
OTP_MAX_ATTEMPTS = 5          # wrong tries allowed before requiring a fresh code
SESSION_HOURS = 12            # how long a successful login stays valid

# In-memory OTP state — fine for a single-admin dashboard with one
# recipient. Resets if the container restarts (user just requests a new code).
otp_state = {"code": None, "expires_at": 0, "attempts": 0, "last_sent": 0}

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32)
app.permanent_session_lifetime = timedelta(hours=SESSION_HOURS)


LOGIN_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sign in — WAN Failover</title>
<style>
  :root {
    --bg: #0f1115; --panel: #171a21; --border: #262b36;
    --text: #e7e9ee; --muted: #8b93a3; --accent: #4f8cff;
    --red: #f2495c; --green: #33d17a;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; min-height: 100vh; display: flex; align-items: center; justify-content: center;
    background: var(--bg); color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    padding: 16px;
  }
  .card {
    background: var(--panel); border: 1px solid var(--border); border-radius: 14px;
    padding: 28px 26px; width: 100%; max-width: 360px;
  }
  h1 { font-size: 18px; font-weight: 600; margin: 0 0 4px; }
  .sub { color: var(--muted); font-size: 13px; margin-bottom: 22px; }
  input {
    width: 100%; background: var(--bg); border: 1px solid var(--border); color: var(--text);
    padding: 10px 12px; border-radius: 8px; font-size: 16px; letter-spacing: 2px;
    text-align: center; margin-bottom: 12px;
  }
  button {
    width: 100%; background: var(--accent); border: none; color: #0f1115;
    padding: 10px 12px; border-radius: 8px; font-size: 14px; font-weight: 600; cursor: pointer;
  }
  button:disabled { opacity: 0.4; cursor: default; }
  button.secondary {
    background: transparent; border: 1px solid var(--border); color: var(--text); margin-top: 8px;
  }
  .msg { font-size: 12px; margin-top: 10px; min-height: 16px; }
  .msg.error { color: var(--red); }
  .msg.ok { color: var(--green); }
  .hint { color: var(--muted); font-size: 12px; margin-top: 16px; text-align: center; }
</style>
</head>
<body>
<div class="card">
  <h1>WAN Failover</h1>
  <div class="sub">Sign in with a one-time code</div>

  <div id="step1">
    <p class="hint" style="margin-top:0;">A 6-digit code will be emailed to<br><b id="recipient-mask"></b></p>
    <button id="send-btn" onclick="requestOtp()">Send code</button>
    <div class="msg" id="req-msg"></div>
  </div>

  <div id="step2" style="display:none;">
    <input id="otp-input" inputmode="numeric" maxlength="6" placeholder="000000"
           onkeydown="if(event.key==='Enter') verifyOtp()">
    <button onclick="verifyOtp()">Verify &amp; sign in</button>
    <button class="secondary" id="resend-btn" onclick="requestOtp()">Resend code</button>
    <div class="msg" id="verify-msg"></div>
  </div>
</div>

<script>
const RECIPIENT = "__OTP_RECIPIENT__";
document.getElementById('recipient-mask').textContent = RECIPIENT;

let cooldownTimer = null;

async function requestOtp() {
  const sendBtn = document.getElementById('send-btn');
  const resendBtn = document.getElementById('resend-btn');
  if (sendBtn) sendBtn.disabled = true;
  if (resendBtn) resendBtn.disabled = true;

  const reqMsg = document.getElementById('req-msg');
  const verifyMsg = document.getElementById('verify-msg');

  try {
    const res = await fetch('/api/request-otp', { method: 'POST' });
    const data = await res.json();
    if (data.ok) {
      document.getElementById('step1').style.display = 'none';
      document.getElementById('step2').style.display = 'block';
      verifyMsg.textContent = 'Code sent. Check the inbox.';
      verifyMsg.className = 'msg ok';
      startCooldown();
    } else {
      const msg = data.error || 'Could not send code';
      if (reqMsg) { reqMsg.textContent = msg; reqMsg.className = 'msg error'; }
      if (verifyMsg) { verifyMsg.textContent = msg; verifyMsg.className = 'msg error'; }
      if (sendBtn) sendBtn.disabled = false;
      if (resendBtn) resendBtn.disabled = false;
    }
  } catch (e) {
    if (reqMsg) { reqMsg.textContent = 'Network error sending code'; reqMsg.className = 'msg error'; }
    if (sendBtn) sendBtn.disabled = false;
    if (resendBtn) resendBtn.disabled = false;
  }
}

function startCooldown() {
  const resendBtn = document.getElementById('resend-btn');
  let remaining = 30;
  resendBtn.disabled = true;
  resendBtn.textContent = `Resend code (${remaining}s)`;
  clearInterval(cooldownTimer);
  cooldownTimer = setInterval(() => {
    remaining -= 1;
    if (remaining <= 0) {
      clearInterval(cooldownTimer);
      resendBtn.disabled = false;
      resendBtn.textContent = 'Resend code';
    } else {
      resendBtn.textContent = `Resend code (${remaining}s)`;
    }
  }, 1000);
}

async function verifyOtp() {
  const code = document.getElementById('otp-input').value.trim();
  const verifyMsg = document.getElementById('verify-msg');
  if (!/^\\d{6}$/.test(code)) {
    verifyMsg.textContent = 'Enter the 6-digit code';
    verifyMsg.className = 'msg error';
    return;
  }
  try {
    const res = await fetch('/api/verify-otp', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code }),
    });
    const data = await res.json();
    if (data.ok) {
      window.location.href = '/';
    } else {
      verifyMsg.textContent = data.error || 'Incorrect or expired code';
      verifyMsg.className = 'msg error';
    }
  } catch (e) {
    verifyMsg.textContent = 'Network error verifying code';
    verifyMsg.className = 'msg error';
  }
}
</script>
</body>
</html>
"""

INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>WAN Failover Status</title>
<style>
  :root {
    --bg: #0f1115;
    --panel: #171a21;
    --border: #262b36;
    --text: #e7e9ee;
    --muted: #8b93a3;
    --green: #33d17a;
    --red: #f2495c;
    --gray: #4a5062;
    --accent: #4f8cff;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    background: var(--bg);
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    padding: 32px 16px;
  }
  .wrap { max-width: 720px; margin: 0 auto; }
  h1 { font-size: 20px; font-weight: 600; margin: 0 0 4px; }
  .sub { color: var(--muted); font-size: 13px; margin-bottom: 24px; }
  .card {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 16px 18px;
    margin-bottom: 12px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
  }
  .left { display: flex; align-items: center; gap: 14px; }
  .dot {
    width: 12px; height: 12px; border-radius: 50%;
    background: var(--gray);
    box-shadow: 0 0 0 3px rgba(255,255,255,0.03);
    flex-shrink: 0;
  }
  .dot.green { background: var(--green); box-shadow: 0 0 8px var(--green); }
  .dot.red { background: var(--red); }
  .iface { font-weight: 600; font-size: 15px; }
  .meta { color: var(--muted); font-size: 12px; margin-top: 2px; }
  .badge {
    font-size: 11px; padding: 2px 8px; border-radius: 999px;
    border: 1px solid var(--border); color: var(--muted);
  }
  .badge.primary { color: var(--accent); border-color: var(--accent); }
  button {
    background: transparent;
    border: 1px solid var(--border);
    color: var(--text);
    padding: 6px 12px;
    border-radius: 8px;
    font-size: 12px;
    cursor: pointer;
  }
  button:hover { border-color: var(--accent); color: var(--accent); }
  button:disabled { opacity: 0.35; cursor: default; }
  select {
    background: var(--bg);
    border: 1px solid var(--border);
    color: var(--text);
    padding: 5px 8px;
    border-radius: 8px;
    font-size: 12px;
  }
  .switch-controls { display: flex; align-items: center; gap: 6px; }
  .countdown { color: var(--muted); font-size: 11px; margin-top: 2px; }
  .mode-bar {
    display: flex; align-items: center; justify-content: space-between;
    background: var(--panel); border: 1px solid var(--border);
    border-radius: 12px; padding: 12px 18px; margin-bottom: 20px;
    font-size: 13px;
  }
  .mode-bar .status-text { color: var(--muted); }
  .mode-bar .status-text b { color: var(--text); }
  a.auto-link { color: var(--accent); text-decoration: none; font-size: 12px; cursor: pointer; }
  .updated { color: var(--muted); font-size: 11px; margin-top: 20px; text-align: center; }

  .switch-overlay {
    position: fixed; inset: 0;
    background: rgba(15,17,21,0.88);
    display: flex; align-items: center; justify-content: center;
    z-index: 999;
  }
  .switch-overlay.hidden { display: none; }
  .switch-card { text-align: center; }
  .switch-text { margin-top: 14px; color: var(--text); font-size: 14px; font-weight: 500; }
  .switch-sub { margin-top: 4px; color: var(--muted); font-size: 12px; }

  .cable-left { animation: slideLeft 1.3s ease-in-out infinite; }
  .cable-right { animation: slideRight 1.3s ease-in-out infinite; }
  @keyframes slideLeft {
    0%, 100% { transform: translateX(0); }
    50% { transform: translateX(14px); }
  }
  @keyframes slideRight {
    0%, 100% { transform: translateX(0); }
    50% { transform: translateX(-14px); }
  }
  .spark { animation: sparkPulse 1.3s ease-in-out infinite; }
  @keyframes sparkPulse {
    0%, 38% { opacity: 0; }
    48% { opacity: 1; }
    58%, 100% { opacity: 0; }
  }
  .wrench {
    transform-box: fill-box;
    transform-origin: 50% 50%;
    animation: wrenchWiggle 0.9s ease-in-out infinite;
  }
  @keyframes wrenchWiggle {
    0%, 100% { transform: rotate(-18deg); }
    50% { transform: rotate(18deg); }
  }
</style>
</head>
<body>
<div class="wrap">
  <h1>WAN Failover</h1>
  <div class="sub">eno4 primary &middot; auto failover with manual override</div>

  <div class="mode-bar">
    <div>
      <div class="status-text">Mode: <b id="mode-text">-</b></div>
      <div class="countdown" id="countdown-text"></div>
    </div>
    <a class="auto-link" id="auto-btn" onclick="setOverride('auto', 0)">Reset to Auto</a>
  </div>

  <div style="text-align:right; margin: -12px 0 16px;">
    <a class="auto-link" href="/logout">Log out</a>
  </div>

  <div id="cards"></div>

  <div class="updated" id="updated-text"></div>
</div>

<div id="switch-overlay" class="switch-overlay hidden">
  <div class="switch-card">
    <svg viewBox="0 0 200 120" width="180" height="108">
      <g class="cable-left">
        <rect x="8" y="52" width="66" height="16" rx="8" fill="#4f8cff"/>
        <circle cx="76" cy="60" r="9" fill="#4f8cff"/>
      </g>
      <g class="cable-right">
        <rect x="126" y="52" width="66" height="16" rx="8" fill="#33d17a"/>
        <circle cx="124" cy="60" r="9" fill="#33d17a"/>
      </g>
      <g class="spark">
        <circle cx="100" cy="60" r="15" fill="none" stroke="#ffd166" stroke-width="3"/>
        <circle cx="100" cy="60" r="5" fill="#ffd166"/>
      </g>
      <text class="wrench" x="100" y="28" font-size="22" text-anchor="middle">🔧</text>
    </svg>
    <div class="switch-text" id="switch-text">Switching network…</div>
    <div class="switch-sub">Reconnecting the link, hang tight</div>
  </div>
</div>

<script>
async function fetchStatus() {
  try {
    const res = await fetch('/api/status');
    const data = await res.json();
    render(data);
  } catch (e) {
    document.getElementById('cards').innerHTML =
      '<div class="card"><span class="meta">Could not reach status API</span></div>';
  }
}

const DURATIONS = [
  { label: '1 min', minutes: 1 },
  { label: '2 min', minutes: 2 },
  { label: '15 min', minutes: 15 },
  { label: '30 min', minutes: 30 },
  { label: '1 hour', minutes: 60 },
  { label: '3 hours', minutes: 180 },
  { label: '6 hours', minutes: 360 },
  { label: '12 hours', minutes: 720 },
  { label: '1 day', minutes: 1440 },
  { label: '3 days', minutes: 4320 },
  { label: '7 days', minutes: 10080 },
  { label: 'Until I reset it', minutes: 0 },
];

// Remembers what each interface's dropdown is set to, across the
// periodic re-renders (otherwise every 3s refresh would reset it).
const selectedDuration = {};

function durationOptions(iface) {
  const selected = selectedDuration[iface] !== undefined ? selectedDuration[iface] : 60;
  return DURATIONS.map(d =>
    `<option value="${d.minutes}" ${d.minutes === selected ? 'selected' : ''}>${d.label}</option>`
  ).join('');
}

function formatRemaining(seconds) {
  if (seconds <= 0) return '';
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

function render(data) {
  const active = data.active;
  const mode = data.mode || 'unknown';
  const override = data.override || 'auto';
  const expiresAt = data.override_expires_at || 0;
  const ifaces = data.interfaces || {};
  const order = ['eno4', 'eno1', 'eno2', 'eno3'];

  document.getElementById('mode-text').textContent =
    mode === 'auto' ? 'Automatic' :
    mode === 'manual' ? 'Manual (' + override + ')' :
    mode === 'manual-failed-over' ? 'Manual target down — auto-failed over' :
    mode;

  const countdownEl = document.getElementById('countdown-text');
  if (mode !== 'auto' && expiresAt > 0) {
    const remaining = expiresAt - Math.floor(Date.now() / 1000);
    countdownEl.textContent = remaining > 0
      ? `Reverts to Auto in ${formatRemaining(remaining)}`
      : 'Reverting to Auto shortly…';
  } else if (mode !== 'auto' && expiresAt === 0) {
    countdownEl.textContent = 'Staying on manual until you reset it';
  } else {
    countdownEl.textContent = '';
  }

  let html = '';
  order.forEach(iface => {
    const info = ifaces[iface] || {};
    const healthy = info.healthy === true || info.healthy === 'true';
    const isActive = iface === active;
    const dotClass = healthy ? 'green' : 'red';
    const selId = 'dur-' + iface;
    html += `
      <div class="card">
        <div class="left">
          <div class="dot ${dotClass}"></div>
          <div>
            <div class="iface">${iface} ${isActive ? '<span class="badge primary">ACTIVE</span>' : ''}</div>
            <div class="meta">${healthy ? 'healthy' : 'down / unreachable'}${info.gateway ? ' &middot; gw ' + info.gateway : ''}</div>
          </div>
        </div>
        <div class="switch-controls">
          <select id="${selId}" ${(!healthy || isActive) ? 'disabled' : ''}
                  onchange="selectedDuration['${iface}'] = parseInt(this.value, 10)">
            ${durationOptions(iface)}
          </select>
          <button ${(!healthy || isActive) ? 'disabled' : ''} onclick="switchWithDuration('${iface}', '${selId}')">
            ${isActive ? 'Active' : 'Switch here'}
          </button>
        </div>
      </div>`;
  });
  document.getElementById('cards').innerHTML = html;
  document.getElementById('updated-text').textContent =
    data.updated ? 'Last updated: ' + new Date(data.updated).toLocaleTimeString() : '';
}

function switchWithDuration(iface, selId) {
  const minutes = parseInt(document.getElementById(selId).value, 10) || 0;
  setOverride(iface, minutes);
}

function showSwitchOverlay(iface) {
  document.getElementById('switch-text').textContent =
    iface === 'auto' ? 'Switching to Auto (eno4 priority)…' : 'Switching to ' + iface + '…';
  document.getElementById('switch-overlay').classList.remove('hidden');
}

function hideSwitchOverlay() {
  document.getElementById('switch-overlay').classList.add('hidden');
}

async function setOverride(iface, minutes) {
  showSwitchOverlay(iface);
  try {
    await fetch('/api/switch/' + iface, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ minutes: minutes || 0 }),
    });
  } catch (e) {
    // fall through to polling regardless; it'll time out and hide itself
  }

  const start = Date.now();
  const poll = setInterval(async () => {
    let done = false;
    try {
      const res = await fetch('/api/status');
      const data = await res.json();
      render(data);
      done = iface === 'auto' ? data.mode === 'auto' : data.active === iface;
    } catch (e) {
      // ignore, will retry until timeout below
    }
    const timedOut = Date.now() - start > 20000;
    if (done || timedOut) {
      clearInterval(poll);
      hideSwitchOverlay();
    }
  }, 1000);
}

fetchStatus();
setInterval(fetchStatus, 3000);
</script>
</body>
</html>
"""

def mask_email(addr):
    try:
        local, domain = addr.split("@", 1)
    except ValueError:
        return addr
    if len(local) <= 2:
        masked = local[0] + "*" * max(len(local) - 1, 1)
    else:
        masked = local[0] + "*" * (len(local) - 2) + local[-1]
    return f"{masked}@{domain}"


def send_otp_email(code):
    text_body = f"""Your WAN Failover dashboard login code

{code}

This code expires in {OTP_TTL_SECONDS // 60} minutes. Enter it on the sign-in
page to access the dashboard.

If you didn't request this code, you can safely ignore this email — no
one can access the dashboard without it.
"""

    html_body = f"""<div style="font-family:Arial,Helvetica,sans-serif;background:#f4f5f7;padding:24px;">
  <div style="max-width:420px;margin:0 auto;background:#ffffff;border-radius:10px;overflow:hidden;border:1px solid #e5e7eb;">
    <div style="background:#4f8cff;padding:14px 20px;">
      <span style="color:#ffffff;font-size:12px;font-weight:700;letter-spacing:.6px;">SIGN-IN CODE</span>
    </div>
    <div style="padding:24px 20px;text-align:center;">
      <p style="margin:0 0 18px;font-size:14px;color:#4b5563;">
        Use this code to sign in to the WAN Failover dashboard:
      </p>
      <div style="display:inline-block;background:#f4f5f7;border:1px solid #e5e7eb;border-radius:10px;
                  padding:14px 28px;font-size:32px;font-weight:700;letter-spacing:8px;color:#111827;
                  font-family:'Courier New',monospace;">
        {code}
      </div>
      <p style="margin:18px 0 0;font-size:12px;color:#9ca3af;">
        Expires in {OTP_TTL_SECONDS // 60} minutes
      </p>
    </div>
    <div style="padding:14px 20px;background:#f9fafb;border-top:1px solid #e5e7eb;">
      <p style="margin:0;font-size:11px;color:#9ca3af;">
        If you didn't request this, you can ignore this email — no one can sign in without this code.
      </p>
    </div>
  </div>
</div>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Your WAN Failover login code"
    msg["From"] = MAIL_FROM
    msg["To"] = OTP_RECIPIENT
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(MAIL_FROM, [OTP_RECIPIENT], msg.as_string())


PUBLIC_PATHS = {"/login", "/api/request-otp", "/api/verify-otp", "/healthz"}


@app.before_request
def require_login():
    if request.path in PUBLIC_PATHS or request.path.startswith("/static"):
        return
    if not session.get("authenticated"):
        if request.path.startswith("/api/"):
            return jsonify({"error": "not authenticated"}), 401
        return redirect("/login")


@app.route("/login")
def login_page():
    if session.get("authenticated"):
        return redirect("/")
    html = LOGIN_HTML.replace("__OTP_RECIPIENT__", mask_email(OTP_RECIPIENT))
    return Response(html, mimetype="text/html")


@app.route("/api/request-otp", methods=["POST"])
def request_otp():
    now = time.time()
    if now - otp_state["last_sent"] < OTP_RESEND_COOLDOWN:
        wait = int(OTP_RESEND_COOLDOWN - (now - otp_state["last_sent"]))
        return jsonify({"ok": False, "error": f"Please wait {wait}s before requesting another code"}), 429

    code = f"{random.randint(0, 999999):06d}"
    otp_state["code"] = code
    otp_state["expires_at"] = now + OTP_TTL_SECONDS
    otp_state["attempts"] = 0
    otp_state["last_sent"] = now

    try:
        send_otp_email(code)
    except Exception as e:
        return jsonify({"ok": False, "error": f"Could not send email: {e}"}), 500

    return jsonify({"ok": True})


@app.route("/api/verify-otp", methods=["POST"])
def verify_otp():
    body = request.get_json(silent=True) or {}
    submitted = str(body.get("code", "")).strip()

    if not otp_state["code"]:
        return jsonify({"ok": False, "error": "No code requested yet"}), 400

    if time.time() > otp_state["expires_at"]:
        return jsonify({"ok": False, "error": "Code expired, request a new one"}), 400

    if otp_state["attempts"] >= OTP_MAX_ATTEMPTS:
        return jsonify({"ok": False, "error": "Too many attempts, request a new code"}), 429

    if submitted != otp_state["code"]:
        otp_state["attempts"] += 1
        return jsonify({"ok": False, "error": "Incorrect code"}), 400

    # success — clear the code so it can't be reused, start session
    otp_state["code"] = None
    otp_state["expires_at"] = 0
    otp_state["attempts"] = 0

    session.permanent = True
    session["authenticated"] = True
    return jsonify({"ok": True})


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


@app.route("/")
def index():
    return Response(INDEX_HTML, mimetype="text/html")

@app.route("/api/status")
def status():
    data = {"active": None, "mode": "unknown", "interfaces": {}, "updated": None}
    try:
        with open(STATUS_FILE) as f:
            data = json.load(f)
    except Exception:
        pass
    try:
        with open(OVERRIDE_FILE) as f:
            data["override"] = f.read().strip()
    except Exception:
        data["override"] = "auto"
    try:
        with open(OVERRIDE_EXPIRY_FILE) as f:
            data["override_expires_at"] = int(f.read().strip() or 0)
    except Exception:
        data["override_expires_at"] = 0
    return jsonify(data)

@app.route("/api/switch/<iface>", methods=["POST"])
def switch(iface):
    if iface != "auto" and iface not in VALID_IFACES:
        return jsonify({"error": "invalid interface"}), 400

    body = request.get_json(silent=True) or {}
    minutes = body.get("minutes", 0)
    try:
        minutes = int(minutes)
    except (TypeError, ValueError):
        minutes = 0

    if iface == "auto" or minutes <= 0:
        expires_at = 0   # 0 = permanent / no auto-revert (or "auto" itself)
    else:
        expires_at = int(time.time()) + minutes * 60

    try:
        with open(OVERRIDE_FILE, "w") as f:
            f.write(iface)
        with open(OVERRIDE_EXPIRY_FILE, "w") as f:
            f.write(str(expires_at))
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    return jsonify({"ok": True, "override": iface, "override_expires_at": expires_at})

@app.route("/healthz")
def healthz():
    return "ok"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
