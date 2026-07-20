#!/bin/bash
#
# wan-failover.sh — multi-WAN active/backup failover, eno4 primary.
#
# Behaviour:
#   - eno4 always preferred (checked first every cycle -> auto failback).
#   - Falls back through eno1 -> eno2 -> eno3 on failure.
#   - No gateway IPs hardcoded: read live from `ip route show dev <iface>`.
#   - Skips physically unplugged interfaces (carrier check) instantly.
#   - Supports MANUAL override (set via web dashboard): if a manual
#     target is set and healthy, it's used. If the manual target goes
#     unhealthy, it automatically fails over using the normal priority
#     order (and flags mode as manual-failed-over) until you either
#     clear the override or the manual pick recovers.
#   - Writes live JSON status to $STATUS_FILE for the dashboard to read.
#   - Sends an email on every actual switch (if MAIL_ENABLED=true).
#
# Config for SMTP lives in /etc/wan-failover/mail.env (sourced below).

set -u

# ---- CONFIG ------------------------------------------------------------
PRIORITY=(eno4 eno1 eno2 eno3)

PING_TARGET="8.8.8.8"
PING_COUNT=3
CHECK_TIMEOUT=5
CYCLE_SLEEP=10
LOG_TAG="wan-failover"

STATE_DIR="/var/lib/wan-failover"     # persistent, safe for docker bind-mount
STATE_FILE="$STATE_DIR/current"
STATUS_FILE="$STATE_DIR/status.json"
OVERRIDE_FILE="$STATE_DIR/override"
OVERRIDE_EXPIRY_FILE="$STATE_DIR/override_expiry"   # unix epoch seconds, or 0 = no expiry

MAIL_ENV_FILE="/etc/wan-failover/mail.env"
# --------------------------------------------------------------------------

mkdir -p "$STATE_DIR"
[ -f "$OVERRIDE_FILE" ] || echo "auto" > "$OVERRIDE_FILE"
[ -f "$OVERRIDE_EXPIRY_FILE" ] || echo "0" > "$OVERRIDE_EXPIRY_FILE"

# shellcheck disable=SC1090
[ -r "$MAIL_ENV_FILE" ] && source "$MAIL_ENV_FILE"
MAIL_ENABLED="${MAIL_ENABLED:-false}"
export SMTP_HOST SMTP_PORT SMTP_USER SMTP_PASS MAIL_FROM MAIL_TO

log() {
    logger -t "$LOG_TAG" "$1"
    echo "$(date '+%Y-%m-%d %H:%M:%S') $1"
}

link_up() {
    local iface="$1"
    local f="/sys/class/net/$iface/carrier"
    [ -r "$f" ] && [ "$(cat "$f" 2>/dev/null)" = "1" ]
}

get_gateway() {
    local iface="$1"
    ip -4 route show dev "$iface" 2>/dev/null | awk '/^default/ {print $3; exit}'
}

check_iface() {
    local iface="$1"
    link_up "$iface" || return 1
    local gw
    gw="$(get_gateway "$iface")"
    [ -n "$gw" ] || return 1
    timeout "$CHECK_TIMEOUT" ping -I "$iface" -c "$PING_COUNT" -W 2 -q "$PING_TARGET" &>/dev/null
}

current_primary() {
    [ -f "$STATE_FILE" ] && cat "$STATE_FILE" || echo ""
}

MAIL_HELPER="/usr/local/bin/wan-mail-send.py"

human_time() {
    date '+%A, %d %b %Y at %H:%M:%S %Z'
}

expiry_human() {
    local expiry="$1"
    if [ -z "$expiry" ] || [ "$expiry" = "0" ]; then
        echo "No automatic expiry — stays on this interface until reset"
    else
        date -d "@$expiry" '+%A, %d %b %Y at %H:%M:%S %Z' 2>/dev/null || echo "in $expiry"
    fi
}

# Builds and sends the switch-alert email. Content differs depending on
# WHY the switch happened (mode), so "auto" vs "manual" reads completely
# differently, per the requirement to make that distinction clear.
send_mail() {
    local iface="$1" mode="$2" previous="$3" override_target="$4" expiry="$5"
    [ "$MAIL_ENABLED" = "true" ] || return 0
    [ -n "${SMTP_HOST:-}" ] && [ -n "${MAIL_TO:-}" ] || return 0

    local gw="${GW[$iface]:-unknown}"
    local prev_label="${previous:-none (first check since service started)}"
    local now_h; now_h="$(human_time)"
    local host_h; host_h="$(hostname)"

    local badge_color="#4f8cff"     # blue = neutral/manual
    local badge_text="MANUAL SWITCH"
    local subject=""
    local headline=""
    local explanation=""

    case "$mode" in
        auto)
            if [ "$iface" = "eno4" ] && [ -n "$previous" ] && [ "$previous" != "eno4" ]; then
                badge_color="#33d17a"; badge_text="BACK ON PRIMARY"
                subject="✅ WAN Failover: Back on Primary (eno4)"
                headline="Traffic has returned to eno4"
                explanation="eno4 recovered and passed its health check, so traffic automatically switched back from <b>$previous</b> to the primary link (<b>eno4</b>), as configured. No action needed."
            elif [ -z "$previous" ]; then
                badge_color="#4f8cff"; badge_text="AUTOMATIC"
                subject="ℹ️ WAN Failover: Monitoring started, active on $iface"
                headline="Initial route selected: $iface"
                explanation="The failover service just started and picked <b>$iface</b> as the active connection based on the priority order (eno4 → eno1 → eno2 → eno3)."
            else
                badge_color="#f2495c"; badge_text="AUTOMATIC FAILOVER"
                subject="⚠️ WAN Failover: Automatic failover to $iface"
                headline="Traffic automatically moved to $iface"
                explanation="<b>$previous</b> stopped responding to health checks (no reply from 8.8.8.8), so traffic was automatically failed over to the next healthy connection in priority order: <b>$iface</b>. It will switch back automatically the moment a higher-priority link recovers."
            fi
            ;;
        manual)
            badge_color="#4f8cff"; badge_text="MANUAL SWITCH"
            subject="🔧 WAN Failover: Manually switched to $iface"
            headline="Traffic manually pinned to $iface"
            explanation="This was a manual switch (via the dashboard or SSH), moving traffic from <b>$prev_label</b> to <b>$iface</b>.<br><br><b>$(expiry_human "$expiry")</b>"
            ;;
        manual-failed-over)
            badge_color="#f2495c"; badge_text="MANUAL PICK DOWN"
            subject="⚠️ WAN Failover: Manual pick down — failed over to $iface"
            headline="Manually-pinned interface went down"
            explanation="Your manual pin to <b>${override_target:-the selected interface}</b> stopped passing health checks, so traffic automatically failed over to <b>$iface</b> to keep you online. It will switch back to <b>${override_target:-your pick}</b> automatically as soon as that interface is healthy again, since your manual override is still active."
            ;;
        *)
            subject="WAN Failover: Switched to $iface"
            headline="Traffic switched to $iface"
            explanation="Mode: $mode"
            ;;
    esac

    local text_body html_body
    local explanation_plain
    explanation_plain="$(echo "$explanation" | sed -e 's/<br>/\n/g' -e 's/<[^>]*>//g')"
    text_body="$headline

$explanation_plain

Active interface : $iface
Gateway           : $gw
Previous          : $prev_label
Mode              : $mode
Time              : $now_h
Host              : $host_h"

    local dash_link=""
    if [ -n "${DASHBOARD_URL:-}" ]; then
        dash_link="<div style=\"margin-top:18px;\"><a href=\"${DASHBOARD_URL}\" style=\"display:inline-block;background:${badge_color};color:#ffffff;text-decoration:none;font-size:13px;font-weight:600;padding:9px 16px;border-radius:8px;\">Open Dashboard</a></div>"
    fi

    html_body="<div style=\"font-family:Arial,Helvetica,sans-serif;background:#f4f5f7;padding:24px;\">
  <div style=\"max-width:480px;margin:0 auto;background:#ffffff;border-radius:10px;overflow:hidden;border:1px solid #e5e7eb;\">
    <div style=\"background:${badge_color};padding:14px 20px;\">
      <span style=\"color:#ffffff;font-size:12px;font-weight:700;letter-spacing:.6px;\">${badge_text}</span>
    </div>
    <div style=\"padding:22px 20px;\">
      <h2 style=\"margin:0 0 10px;font-size:18px;color:#111827;\">${headline}</h2>
      <p style=\"margin:0 0 18px;font-size:14px;color:#4b5563;line-height:1.6;\">${explanation}</p>
      <table style=\"width:100%;border-collapse:collapse;font-size:13px;color:#374151;\">
        <tr><td style=\"padding:6px 0;color:#9ca3af;\">Active interface</td><td style=\"padding:6px 0;text-align:right;font-weight:600;\">${iface}</td></tr>
        <tr><td style=\"padding:6px 0;color:#9ca3af;\">Gateway</td><td style=\"padding:6px 0;text-align:right;\">${gw}</td></tr>
        <tr><td style=\"padding:6px 0;color:#9ca3af;\">Previous interface</td><td style=\"padding:6px 0;text-align:right;\">${prev_label}</td></tr>
        <tr><td style=\"padding:6px 0;color:#9ca3af;\">Time</td><td style=\"padding:6px 0;text-align:right;\">${now_h}</td></tr>
        <tr><td style=\"padding:6px 0;color:#9ca3af;\">Host</td><td style=\"padding:6px 0;text-align:right;\">${host_h}</td></tr>
      </table>
      ${dash_link}
    </div>
    <div style=\"padding:12px 20px;background:#f9fafb;border-top:1px solid #e5e7eb;\">
      <p style=\"margin:0;font-size:11px;color:#9ca3af;\">Automated alert from your WAN Failover monitor. No action required unless you want to intervene.</p>
    </div>
  </div>
</div>"

    local tmp_text tmp_html
    tmp_text="$(mktemp)"; tmp_html="$(mktemp)"
    printf '%s' "$text_body" > "$tmp_text"
    printf '%s' "$html_body" > "$tmp_html"

    if python3 "$MAIL_HELPER" --subject "$subject" --text-body-file "$tmp_text" --html-body-file "$tmp_html" >> /var/log/wan-failover-mail.log 2>&1; then
        log "Email alert sent for switch to $iface ($mode)"
    else
        log "WARNING: email alert FAILED to send (check /var/log/wan-failover-mail.log)"
    fi
    rm -f "$tmp_text" "$tmp_html"
}

switch_to() {
    local iface="$1" mode="$2" previous="$3" override_target="$4" expiry="$5"
    local gw
    gw="$(get_gateway "$iface")"
    if [ -z "$gw" ]; then
        log "ERROR: tried to switch to $iface but it has no gateway right now"
        return 1
    fi
    if ip route replace default via "$gw" dev "$iface"; then
        echo "$iface" > "$STATE_FILE"
        log "SWITCHED primary WAN -> $iface (via $gw) [mode: $mode]"
        send_mail "$iface" "$mode" "$previous" "$override_target" "$expiry"
    else
        log "ERROR: failed to switch default route to $iface"
    fi
}

write_status() {
    local mode="$1" override="$2" expiry="$3"
    local now
    now="$(date -Iseconds)"
    local tmp="$STATUS_FILE.tmp"
    {
        echo "{"
        echo "  \"active\": \"$(current_primary)\","
        echo "  \"mode\": \"$mode\","
        echo "  \"override\": \"$override\","
        echo "  \"override_expires_at\": $expiry,"
        echo "  \"updated\": \"$now\","
        echo "  \"interfaces\": {"
        local n=${#PRIORITY[@]} i=0
        for iface in "${PRIORITY[@]}"; do
            i=$((i+1))
            comma=","; [ "$i" -eq "$n" ] && comma=""
            echo "    \"$iface\": {\"healthy\": ${HEALTH[$iface]}, \"link\": ${LINK[$iface]}, \"gateway\": \"${GW[$iface]}\"}$comma"
        done
        echo "  }"
        echo "}"
    } > "$tmp" && mv "$tmp" "$STATUS_FILE"
}

log "wan-failover started. Priority: ${PRIORITY[*]}"

while true; do
    declare -A HEALTH GW LINK
    for iface in "${PRIORITY[@]}"; do
        LINK[$iface]=false; GW[$iface]=""; HEALTH[$iface]=false
        link_up "$iface" && LINK[$iface]=true
        gw="$(get_gateway "$iface")"; [ -n "$gw" ] && GW[$iface]="$gw"
        check_iface "$iface" && HEALTH[$iface]=true
    done

    override="$(cat "$OVERRIDE_FILE" 2>/dev/null || echo auto)"
    expiry="$(cat "$OVERRIDE_EXPIRY_FILE" 2>/dev/null || echo 0)"
    now_epoch="$(date +%s)"

    # Timed manual override: if an expiry is set (non-zero) and it has
    # passed, revert to Auto mode right here before doing anything else.
    if [ "$override" != "auto" ] && [ "$expiry" != "0" ] && [ "$now_epoch" -ge "$expiry" ] 2>/dev/null; then
        log "Manual override on $override expired -> reverting to Auto (eno4 priority)"
        echo "auto" > "$OVERRIDE_FILE"
        echo "0" > "$OVERRIDE_EXPIRY_FILE"
        override="auto"
        expiry="0"
    fi

    target="" mode="auto"

    if [ "$override" != "auto" ] && [ -n "${HEALTH[$override]+x}" ]; then
        if [ "${HEALTH[$override]}" = "true" ]; then
            target="$override"; mode="manual"
        else
            for iface in "${PRIORITY[@]}"; do
                if [ "${HEALTH[$iface]}" = "true" ]; then target="$iface"; break; fi
            done
            mode="manual-failed-over"
        fi
    else
        for iface in "${PRIORITY[@]}"; do
            if [ "${HEALTH[$iface]}" = "true" ]; then target="$iface"; break; fi
        done
        mode="auto"
    fi

    if [ -z "$target" ]; then
        log "WARNING: all interfaces failed health check this cycle"
    elif [ "$(current_primary)" != "$target" ]; then
        previous="$(current_primary)"
        override_target=""
        [ "$override" != "auto" ] && override_target="$override"
        switch_to "$target" "$mode" "$previous" "$override_target" "$expiry"
    fi

    write_status "$mode" "$override" "$expiry"
    sleep "$CYCLE_SLEEP"
done
