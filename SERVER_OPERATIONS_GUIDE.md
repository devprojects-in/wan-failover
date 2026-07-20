# WAN Failover System — Complete Operations Guide

Server: `voserver` &middot; 4 physical uplinks: `eno1`, `eno2`, `eno3`, `eno4` (primary)

---

## 1. What's installed on this server

### A. Host-side (runs directly on Ubuntu, outside Docker)

| Component | Path | Purpose |
|---|---|---|
| Failover engine | `/usr/local/bin/wan-failover.sh` | Bash script, checks all 4 interfaces every 10s, switches the default route |
| systemd service | `/etc/systemd/system/wan-failover.service` | Keeps the script running, auto-starts on boot, restarts on crash |
| Resume hook | `/usr/lib/systemd/system-sleep/wan-failover-resume.sh` | Re-checks routes if the box ever suspends/resumes |
| SMTP config | `/etc/wan-failover/mail.env` | Credentials + recipient for **switch-alert emails** (the "network changed" notification) |
| State directory | `/var/lib/wan-failover/` | Live status + control files (see section 4) |
| Mail send log | `/var/log/wan-failover-mail.log` | Log of every alert-email attempt |

**Why it has to run on the host and not in Docker:** only a process in the
host's network namespace can see and modify `eno1`–`eno4` and change the
kernel routing table. Nothing in Docker has this access, by design.

### B. Docker (dashboard + login)

| Component | Path | Purpose |
|---|---|---|
| Project folder | `/opt/wan-dashboard/` | `app.py`, `Dockerfile`, `docker-compose.yml`, `requirements.txt` |
| Container | `wan-dashboard` (docker container name) | Flask app: status dashboard + OTP login |
| Docker network | `nginx-proxy-manager_default` (external, shared with your existing NPM stack) | Lets NPM reach the container by name, no port published to the host |
| Internal port | `5000` | What NPM's Proxy Host forwards to |

The dashboard container **cannot** touch routing itself — it only reads/
writes files in `/var/lib/wan-failover/` (bind-mounted in as `/data`). The
host script is what actually acts on those files.

### C. Nginx Proxy Manager

- Whatever domain you mapped (e.g. `wan.yourdomain.com`) → Proxy Host →
  forwards to `wan-dashboard:5000` over the shared docker network.
- TLS/HTTPS handled by NPM's Let's Encrypt integration, if you enabled it.

### D. Email / auth accounts in use

| Purpose | Sends from | Sends to |
|---|---|---|
| Switch alerts (host script) | `noreply@example.in` via `mail.example.in:587` | whatever you set in `MAIL_TO` in `/etc/wan-failover/mail.env` |
| Login OTP codes (dashboard) | `noreply@example.in` via `mail.example.in:587` | `karanbir@virtualoplossing.com` |

---

## 2. How to connect

### SSH (server administration)

```bash
ssh voserver@<server-ip>
```

Use whichever interface's IP you manage the box through — from earlier
diagnostics, the interfaces on this server carry:

| Interface | Address seen in config |
|---|---|
| `eno4` (LAN/local) | `192.222.29.10` |
| `eno1` | `221.222.67.118` |
| `eno2` | `221.222.67.19` |
| `eno3` | `221.222.67.116` |

If you normally manage this box over the local LAN, that's `eno4`
(`192.168.29.10`). Confirm with whoever set up SSH access if you're
unsure which one is actually open to your management network — not all
of these are necessarily meant to accept SSH from the internet.

### Web dashboard (day-to-day monitoring/switching)

1. Go to `https://<your-mapped-domain>` (the one configured in NPM).
2. You'll land on `/login` — click **Send code**, check
   `karanbir@virtualoplossing.com` for a 6-digit code, enter it.
3. You're in for 12 hours per login.

---

## 3. Manual network switching via SSH — the right way

**Recommended method:** write to the same control files the dashboard
uses. This keeps the automated failover script "in the loop," so it
won't fight you, and your manual pick still auto-fails-over if that
interface actually goes down.

```bash
# Example: manually pin traffic to eno1 for 1 hour
echo "eno1" | sudo tee /var/lib/wan-failover/override
echo "$(( $(date +%s) + 3600 ))" | sudo tee /var/lib/wan-failover/override_expiry
```

The script re-reads these files every 10 seconds and will apply the
switch on its next cycle (so allow up to ~10s to see it take effect).

**Duration cheatsheet** (add to `$(date +%s)`):

| Duration | Seconds to add |
|---|---|
| 1 minute | `60` |
| 15 minutes | `900` |
| 1 hour | `3600` |
| 6 hours | `21600` |
| 1 day | `86400` |
| 7 days | `604800` |
| Forever (until you reset it) | use `0` instead of a future timestamp |

**Pin permanently (no auto-revert) to eno2, for example:**
```bash
echo "eno2" | sudo tee /var/lib/wan-failover/override
echo "0" | sudo tee /var/lib/wan-failover/override_expiry
```

**Revert to normal automatic mode (eno4 → eno1 → eno2 → eno3):**
```bash
echo "auto" | sudo tee /var/lib/wan-failover/override
echo "0" | sudo tee /var/lib/wan-failover/override_expiry
```

**Check what it's currently doing:**
```bash
cat /var/lib/wan-failover/status.json
```
This shows the active interface, current mode (`auto` / `manual` /
`manual-failed-over`), per-interface health, and when the override
expires.

### Emergency fallback — if the systemd service itself is stopped/broken

If `wan-failover.sh` isn't running at all (check with
`systemctl status wan-failover`), the override files won't do anything
since nothing is reading them. In that case, switch the route directly:

```bash
# Find the current gateway for the interface you want:
ip route show dev eno1

# Then force it as the default route:
sudo ip route replace default via <gateway-ip> dev eno1
```

This takes effect immediately but is **not persistent** — if the service
starts again later, it will resume managing the route on its own logic.
Get the service running again as soon as you can:
```bash
sudo systemctl restart wan-failover
journalctl -t wan-failover -f
```

---

## 4. State files reference (`/var/lib/wan-failover/`)

| File | Contents | Who writes it |
|---|---|---|
| `current` | Name of the interface currently set as default route | host script |
| `status.json` | Full live status: active iface, mode, per-iface health/gateway, override info | host script (every 10s) |
| `override` | `auto` or a specific interface name you want pinned | you (via SSH) or the dashboard |
| `override_expires_at`/`override_expiry` | Unix epoch timestamp when the pin auto-reverts, or `0` for no expiry | you (via SSH) or the dashboard |

---

## 5. Common commands

**Check which interface is active right now:**
```bash
ip route | head -1
# or
cat /var/lib/wan-failover/current
```

**Watch the failover script's live log:**
```bash
journalctl -t wan-failover -f
```

**Check the systemd service status:**
```bash
systemctl status wan-failover
```

**Restart the failover service:**
```bash
sudo systemctl restart wan-failover
```

**Check email alert log:**
```bash
tail -f /var/log/wan-failover-mail.log
```

**Check/restart the dashboard container:**
```bash
cd /opt/wan-dashboard
docker compose ps
docker compose logs -f
docker compose up -d --build     # after any file update
```

**Test connectivity through a specific interface manually:**
```bash
ping -I eno1 -c 3 8.8.8.8
```

**Check physical link state on an interface:**
```bash
cat /sys/class/net/eno1/carrier    # 1 = cable connected, 0 = unplugged
```

---

## 6. Troubleshooting

| Symptom | Likely cause / check |
|---|---|
| Dashboard shows all interfaces red | Check `journalctl -t wan-failover -f` — may be a genuine outage, or the script isn't running (`systemctl status wan-failover`) |
| Manual override via SSH doesn't seem to apply | Confirm the service is actually running; the override files only work if `wan-failover.sh` is reading them every cycle |
| No switch-alert emails arriving | Check `MAIL_TO` is set (not the placeholder) in `/etc/wan-failover/mail.env`, then check `/var/log/wan-failover-mail.log` for the actual SMTP error |
| No OTP login email arriving | Check `docker compose logs` for the `wan-dashboard` container for the SMTP error; same `mail.example.in` account is used |
| Logged out every time container restarts | `FLASK_SECRET_KEY` in `docker-compose.yml` is still the placeholder — set a fixed random value |
| `docker compose` permission denied | Either run with `sudo`, or `sudo chown -R $USER:$USER /opt/wan-dashboard` |
| Dashboard unreachable via domain | Check the NPM Proxy Host config: forward hostname must be `wan-dashboard`, port `5000`, and both containers must be on `nginx-proxy-manager_default` |

---

## 7. Security notes

- `/etc/wan-failover/mail.env` and `/opt/wan-dashboard/docker-compose.yml`
  both contain SMTP credentials in plain text — keep them `chmod 600`,
  root-owned.
- The dashboard is gated by email OTP (12-hour sessions); anyone with
  access to `karanbir@virtualoplossing.com` can log in.
- The dashboard container has no ability to run arbitrary commands on the
  host — it can only request a switch by writing to the override file;
  the host script is what decides whether to honor it (won't switch to a
  genuinely dead interface even if asked).
