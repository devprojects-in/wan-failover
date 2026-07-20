# Deploy / Update Commands — WAN Failover Project

Run these from wherever you downloaded the files, e.g. `~/Downloads/files/`
(with the `docker/` subfolder inside it, same as before).

---

## A. Code files — always safe to re-copy, never contain your custom settings

```bash
cd ~/Downloads/files

# Host script + mail helper
sudo cp wan-failover.sh /usr/local/bin/wan-failover.sh
sudo chmod +x /usr/local/bin/wan-failover.sh

sudo cp wan-mail-send.py /usr/local/bin/wan-mail-send.py
sudo chmod +x /usr/local/bin/wan-mail-send.py

# systemd unit + resume hook (rarely change, but harmless to re-copy)
sudo cp wan-failover.service /etc/systemd/system/wan-failover.service
sudo cp wan-failover-resume.sh /usr/lib/systemd/system-sleep/wan-failover-resume.sh
sudo chmod +x /usr/lib/systemd/system-sleep/wan-failover-resume.sh

# Reload systemd + restart the failover service to pick up script changes
sudo systemctl daemon-reload
sudo systemctl restart wan-failover
journalctl -t wan-failover -f      # Ctrl+C once you see it started cleanly
```

```bash
# Dashboard app code (NOT docker-compose.yml — see section B)
sudo cp docker/app.py /opt/wan-dashboard/app.py
sudo cp docker/Dockerfile /opt/wan-dashboard/Dockerfile
sudo cp docker/requirements.txt /opt/wan-dashboard/requirements.txt

cd /opt/wan-dashboard
sudo docker compose up -d --build
docker compose logs -f             # Ctrl+C once it's up
```

---

## B. Config files — DO NOT blindly overwrite, diff first

These contain settings you've already customized (real `MAIL_TO`, real
`FLASK_SECRET_KEY`). If Claude gives you an updated version of these (e.g.
a new optional setting was added), compare before copying:

```bash
# See what's actually different before overwriting
diff ~/Downloads/files/mail.env /etc/wan-failover/mail.env
diff ~/Downloads/files/docker/docker-compose.yml /opt/wan-dashboard/docker-compose.yml
```

If the diff only shows new lines/features (not your `MAIL_TO` or
`FLASK_SECRET_KEY` being reset to placeholders), then it's safe:

```bash
sudo cp ~/Downloads/files/mail.env /etc/wan-failover/mail.env
sudo chmod 600 /etc/wan-failover/mail.env
sudo systemctl restart wan-failover

sudo cp ~/Downloads/files/docker/docker-compose.yml /opt/wan-dashboard/docker-compose.yml
sudo chmod 600 /opt/wan-dashboard/docker-compose.yml
cd /opt/wan-dashboard && sudo docker compose up -d --build
```

**If the diff shows your `MAIL_TO` or `FLASK_SECRET_KEY` reverted to a
placeholder** — don't copy the whole file. Instead manually merge just the
new lines into your existing file with `nano`/`vim`, keeping your real
values intact.

---

## C. Full "update everything" one-liner (code files only, config files skipped)

For convenience, once you trust this pattern:

```bash
cd ~/Downloads/files && \
sudo cp wan-failover.sh /usr/local/bin/wan-failover.sh && \
sudo cp wan-mail-send.py /usr/local/bin/wan-mail-send.py && \
sudo chmod +x /usr/local/bin/wan-failover.sh /usr/local/bin/wan-mail-send.py && \
sudo cp wan-failover.service /etc/systemd/system/wan-failover.service && \
sudo systemctl daemon-reload && \
sudo systemctl restart wan-failover && \
sudo cp docker/app.py docker/Dockerfile docker/requirements.txt /opt/wan-dashboard/ && \
cd /opt/wan-dashboard && \
sudo docker compose up -d --build && \
echo "All code files updated and services restarted." && \
journalctl -t wan-failover -n 10 --no-pager
```

---

## D. Quick verification after any update

```bash
systemctl status wan-failover              # host service healthy?
docker compose -f /opt/wan-dashboard/docker-compose.yml ps   # container up?
cat /var/lib/wan-failover/status.json      # live failover state
tail -20 /var/log/wan-failover-mail.log    # last email attempts
```
