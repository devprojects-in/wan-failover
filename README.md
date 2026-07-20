# WAN Failover — complete install map

## Exactly what goes where

| File you have | Exact destination on the server | Command |
|---|---|---|
| `wan-failover.sh` | `/usr/local/bin/wan-failover.sh` | `sudo cp wan-failover.sh /usr/local/bin/wan-failover.sh && sudo chmod +x /usr/local/bin/wan-failover.sh` |
| `wan-failover.service` | `/etc/systemd/system/wan-failover.service` | `sudo cp wan-failover.service /etc/systemd/system/wan-failover.service` |
| `mail.env` | `/etc/wan-failover/mail.env` | `sudo mkdir -p /etc/wan-failover && sudo cp mail.env /etc/wan-failover/mail.env && sudo chmod 600 /etc/wan-failover/mail.env` |
| `wan-failover-resume.sh` (optional, suspend/resume) | `/usr/lib/systemd/system-sleep/wan-failover-resume.sh` | `sudo cp wan-failover-resume.sh /usr/lib/systemd/system-sleep/wan-failover-resume.sh && sudo chmod +x /usr/lib/systemd/system-sleep/wan-failover-resume.sh` |
| `docker/` (whole folder) | anywhere permanent, e.g. `/opt/wan-dashboard/` | `sudo mkdir -p /opt/wan-dashboard && sudo cp -r docker/* /opt/wan-dashboard/` |

Nothing else needs to be copied anywhere — `/var/lib/wan-failover/` (status,
override, override_expiry files) is created automatically by the script the
first time it runs.

## Full install, in order

```bash
# 1. Host script
sudo mkdir -p /var/lib/wan-failover
sudo cp wan-failover.sh /usr/local/bin/wan-failover.sh
sudo chmod +x /usr/local/bin/wan-failover.sh

# 2. systemd service
sudo cp wan-failover.service /etc/systemd/system/wan-failover.service
sudo systemctl daemon-reload

# 3. Email config (already filled with your SMTP — just set MAIL_TO)
sudo mkdir -p /etc/wan-failover
sudo cp mail.env /etc/wan-failover/mail.env
sudo nano /etc/wan-failover/mail.env      # set MAIL_TO to the real recipient
sudo chmod 600 /etc/wan-failover/mail.env

# 4. Start it
sudo systemctl enable --now wan-failover
sudo systemctl restart wan-failover
journalctl -t wan-failover -f

# 5. (optional) suspend/resume hook
sudo cp wan-failover-resume.sh /usr/lib/systemd/system-sleep/wan-failover-resume.sh
sudo chmod +x /usr/lib/systemd/system-sleep/wan-failover-resume.sh

# 6. Dashboard container
sudo mkdir -p /opt/wan-dashboard
sudo cp -r docker/* /opt/wan-dashboard/
cd /opt/wan-dashboard
docker compose up -d --build
docker compose logs -f
```

## About the SMTP details you sent

Mapped into `mail.env` like this:

| Your value | Goes into |
|---|---|
| `MAIL_HOST=mail.example.in` | `SMTP_HOST` / `SMTP_URL` |
| `MAIL_PORT=587` | `SMTP_PORT` — 587 means STARTTLS, so `SMTP_URL=smtp://...` + `SMTP_SSL_REQD=1` (already set) |
| `MAIL_USERNAME=noreply@example.in` | `SMTP_USER` |
| `MAIL_PASSWORD` | `SMTP_PASS` — quoted in the file since it contains a comma/dash |
| `MAIL_FROM_ADDRESS` | `MAIL_FROM` |

**Two things to double check yourself:**
1. Your password as pasted was `" -,hESExT,28"` (looked like it might have a
   leading space). I stripped that assuming it was just formatting when you
   pasted it — if the real password does start with a space, edit
   `SMTP_PASS` in `mail.env` accordingly.
2. **`MAIL_TO` is currently a placeholder** (`you@yourdomain.com`) — I don't
   have the address you want alerts delivered *to*. Set that in
   `/etc/wan-failover/mail.env` before it'll actually send anywhere useful.

Test it after setup:
```bash
tail -f /var/log/wan-failover-mail.log
```

## Timed manual override (new)

When you click **"Switch here"** on the dashboard, you now pick how long it
should stay pinned to that interface from a dropdown next to the button:

`15 min · 30 min · 1 hour · 3 hours · 6 hours · 12 hours · 1 day · 3 days · 7 days · Until I reset it`

- Once that timer runs out, the host script automatically reverts to **Auto**
  mode on its own — no dashboard interaction needed — and Auto always means
  "prefer eno4, then eno1, then eno2, then eno3," exactly like normal.
- The mode bar at the top shows a live countdown ("Reverts to Auto in 2h 14m").
- If you pick "Until I reset it," it stays manual forever until you click
  Reset to Auto yourself.
- If your manually-picked interface dies before the timer even runs out, it
  still fails over immediately through the normal priority order (it doesn't
  wait for the timer) — the timer only governs the return-to-Auto behavior
  for a healthy manual pick.

## Nginx Proxy Manager mapping (unchanged from before)

Proxy Hosts → Add Proxy Host:

| Field | Value |
|---|---|
| Domain Names | `wan.yourdomain.com` |
| Scheme | `http` |
| Forward Hostname/IP | `wan-dashboard` |
| Forward Port | `5000` |
| Websockets Support | off |

SSL tab → Request Let's Encrypt cert, Force SSL on.

No port is published from the container — NPM reaches it over
`nginx-proxy-manager_default` by container name.
