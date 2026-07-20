#!/bin/bash
# systemd-sleep hook: restarts wan-failover right after resume so it
# re-checks all interfaces immediately instead of waiting for the next
# 10s cycle (interfaces can take a moment to renegotiate after sleep).
#
# Install to: /usr/lib/systemd/system-sleep/wan-failover-resume.sh
# (must be executable: chmod +x)

case "$1" in
    post)
        sleep 3   # give NICs a moment to renegotiate link after resume
        systemctl restart wan-failover
        ;;
esac
