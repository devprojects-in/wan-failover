#!/usr/bin/env python3
"""
wan-mail-send.py — sends a multipart (plain text + HTML) email via SMTP.
Called by wan-failover.sh for switch-alert emails. Credentials come from
environment variables (already exported by wan-failover.sh after sourcing
/etc/wan-failover/mail.env).

Usage:
  wan-mail-send.py --subject "..." --text-body-file /tmp/x.txt --html-body-file /tmp/x.html

Required env vars: SMTP_HOST, SMTP_USER, SMTP_PASS, MAIL_TO
Optional env vars: SMTP_PORT (default 587), MAIL_FROM (default SMTP_USER)
"""
import argparse
import os
import smtplib
import ssl
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--subject", required=True)
    parser.add_argument("--text-body-file", required=True)
    parser.add_argument("--html-body-file", required=True)
    args = parser.parse_args()

    host = os.environ["SMTP_HOST"]
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ["SMTP_USER"]
    password = os.environ["SMTP_PASS"]
    mail_from = os.environ.get("MAIL_FROM", user)
    mail_to = os.environ["MAIL_TO"]

    with open(args.text_body_file, "r", encoding="utf-8") as f:
        text_body = f.read()
    with open(args.html_body_file, "r", encoding="utf-8") as f:
        html_body = f.read()

    msg = MIMEMultipart("alternative")
    msg["Subject"] = args.subject
    msg["From"] = mail_from
    msg["To"] = mail_to
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP(host, port, timeout=15) as server:
        server.starttls(context=ssl.create_default_context())
        server.login(user, password)
        server.sendmail(mail_from, [mail_to], msg.as_string())


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
