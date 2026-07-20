# WAN Failover — Automatic Multi-WAN Internet Failover for Linux Servers

**Keep your server online even when your primary internet connection drops.**
A lightweight, self-hosted failover system for Linux servers with multiple
internet uplinks (multi-WAN / dual-WAN / quad-WAN setups). Automatically
detects when your primary connection goes down, switches traffic to a
backup line, and switches back the moment the primary recovers — with
email alerts and a web dashboard for manual control.

[![Shell Script](https://img.shields.io/badge/Shell-Bash-4EAA25?logo=gnu-bash&logoColor=white)](https://www.gnu.org/software/bash/)
[![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Docker](https://img.shields.io/badge/Docker-Ready-2496ED?logo=docker&logoColor=white)](https://www.docker.com/)
[![Flask](https://img.shields.io/badge/Flask-Dashboard-000000?logo=flask&logoColor=white)](https://flask.palletsprojects.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## What is this?

If your server has more than one internet connection — multiple ISPs,
multiple physical NICs, or a mix of fiber/broadband/LTE for redundancy —
this project keeps it online automatically. It's a practical alternative
to expensive dedicated multi-WAN routers (like Peplink or Cisco failover
appliances) for anyone comfortable running a script on their own Linux box.

**In plain terms:** it watches your internet connections, and the instant
one stops working, it silently reroutes your server's traffic through a
working one — no downtime, no manual intervention, and you get an email
the moment it happens.

## Who this is for

- Self-hosters and homelabbers running a server with 2+ internet lines
- Small businesses/offices with a primary ISP and a backup line
- Anyone running a VoIP server, game server, or public-facing service
  who can't afford a single ISP outage to mean total downtime
- Sysadmins who want multi-WAN failover without buying dedicated hardware

## Features

- 🔁 **Automatic failover & failback** — always prefers your primary
  connection, falls back through however many backup connections you
  have, and automatically switches back the instant the primary recovers.
- 🌐 **Zero hardcoded network config** — reads gateways live from the
  routing table and detects unplugged cables via link state, so it adapts
  to DHCP changes or physical changes without editing the script.
- 📧 **Email alerts** — a clearly-written email every time traffic
  switches, explaining *why* (automatic failover vs. manual override)
  and *what happened*, not just a bare status code.
- 🖥️ **Web dashboard** — see which connection is active at a glance
  (green/red status), and manually pin traffic to a specific connection
  for a chosen duration (minutes to days) if you need to.
- 🔐 **Email OTP login** — the dashboard is protected by a one-time-code
  login sent to your inbox, no separate password to manage or leak.
- 🐳 **Docker-based dashboard** — deploys behind your existing reverse
  proxy (Nginx Proxy Manager, Traefik, Caddy) with no extra ports to open.
- ⏱️ **Timed manual override** — pin traffic to a specific connection
  for 1 minute up to 7 days, or indefinitely, with automatic revert to
  the default failover logic when the timer runs out.
- 🪶 **Lightweight** — a Bash script + a small Flask app. No heavyweight
  network stack, no proprietary firmware, runs on any modern Ubuntu/Debian
  server.

## How it works

```
┌─────────┐     ┌─────────┐     ┌─────────┐     ┌─────────┐
│  eth0   │     │  eth1   │     │  eth2   │     │  eth3   │   <- your physical
│(primary)│     │(backup1)│     │(backup2)│     │(backup3)│      uplinks
└────┬────┘     └────┬────┘     └────┬────┘     └────┬────┘
     │               │               │               │
     └───────────────┴───────┬───────┴───────────────┘
                              │
                    ┌─────────▼─────────┐
                    │  wan-failover.sh   │  <- checks every 10s:
                    │  (systemd service) │     link up? gateway alive?
                    └─────────┬─────────┘     ping reachable?
                              │
              ┌───────────────┼───────────────┐
              │               │               │
    ┌─────────▼──────┐ ┌──────▼──────┐ ┌──────▼───────┐
    │ ip route switch │ │ email alert │ │ status.json  │
    │ (kernel routing)│ │  (SMTP)     │ │ (for dashboard)│
    └──────────────────┘ └─────────────┘ └──────┬───────┘
                                                  │
                                        ┌─────────▼─────────┐
                                        │  Web Dashboard      │
                                        │  (Docker + Flask)   │
                                        │  OTP login + manual │
                                        │  switch controls     │
                                        └─────────────────────┘
```

Every 10 seconds, the script pings out through each interface, in
priority order. The first healthy one becomes the active default route.
If your top-priority connection recovers later, it's re-checked first
every cycle — so traffic always returns to it automatically.

## Quick start

### 1. Install the failover script (on the host)

```bash
sudo mkdir -p /var/lib/wan-failover
sudo cp scripts/wan-failover.sh /usr/local/bin/wan-failover.sh
sudo cp scripts/wan-mail-send.py /usr/local/bin/wan-mail-send.py
sudo chmod +x /usr/local/bin/wan-failover.sh /usr/local/bin/wan-mail-send.py

sudo cp scripts/wan-failover.service /etc/systemd/system/wan-failover.service
sudo systemctl daemon-reload
```

### 2. Configure your interface priority

Open `scripts/wan-failover.sh` and edit the `PRIORITY` array near the top
to match your actual interface names (find them with `ip link`):

```bash
PRIORITY=(eth0 eth1 eth2 eth3)   # first = primary, rest = backup order
```

### 3. Configure email alerts

```bash
sudo mkdir -p /etc/wan-failover
sudo cp config/mail.env.example /etc/wan-failover/mail.env
sudo nano /etc/wan-failover/mail.env      # fill in your SMTP details
sudo chmod 600 /etc/wan-failover/mail.env
```

### 4. Start it

```bash
sudo systemctl enable --now wan-failover
journalctl -t wan-failover -f
```

### 5. (Optional) Deploy the web dashboard

```bash
cd docker
cp docker-compose.example.yml docker-compose.yml
nano docker-compose.yml      # fill in SMTP + OTP recipient + a random session key
docker compose up -d --build
```

Point your reverse proxy at the `wan-dashboard` container on port `5000`.

Full setup, troubleshooting, and manual-switch-via-SSH instructions are in
[`docs/`](docs/).

## Manually switching connections via SSH

You don't need the dashboard for manual control — you can pin traffic to
any interface directly:

```bash
echo "eth1" | sudo tee /var/lib/wan-failover/override
echo "0" | sudo tee /var/lib/wan-failover/override_expiry   # 0 = until you reset it
```

Revert to automatic mode:
```bash
echo "auto" | sudo tee /var/lib/wan-failover/override
```

## FAQ

**How is this different from a multi-WAN router?**
Dedicated multi-WAN hardware (Peplink, pfSense with multi-WAN, etc.) does
more (like per-flow load balancing), but costs money and is another box
to manage. This is free, runs on the server you already have, and does
one thing well: active/backup failover with visibility and alerts.

**Does this load-balance traffic across connections?**
No — this is active/backup failover, not load balancing. One connection
is "active" at a time; the others sit ready as backups. This is
intentional: it keeps behavior predictable (no split sessions across
different public IPs) which matters for things like VoIP, gaming, or
stateful services.

**What OS does this run on?**
Built and tested for Ubuntu Server, should work on any modern
systemd-based Linux distro (Debian, Ubuntu variants) with `ip`, `ping`,
`curl`, `python3`, and `systemd` available.

**Can I use this without the Docker dashboard?**
Yes — the dashboard is entirely optional. The core failover script runs
standalone with just systemd; the dashboard adds a UI and manual controls
on top of it.

## Contributing

Issues and pull requests welcome. If you extend this for other distros,
add DNS-based health checks, or add load-balancing support, open a PR.

## License

MIT — see [LICENSE](LICENSE).

---

*Keywords: multi-wan failover linux, dual wan failover script, automatic
ISP failover, network redundancy self-hosted, ubuntu failover router,
internet connection monitor dashboard, self-hosted network failover,
docker network dashboard, systemd network failover script.*
