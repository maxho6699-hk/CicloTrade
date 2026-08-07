# -*- coding: utf-8 -*-
"""SMTP 邮件发送。"""

from __future__ import annotations

from email.message import EmailMessage
import os
import smtplib
import ssl


def smtp_configured() -> bool:
    return bool(os.getenv("SMTP_HOST") and os.getenv("SMTP_USER") and os.getenv("SMTP_PASSWORD"))


def send_email(to_address: str, subject: str, body: str, html_body: str | None = None) -> None:
    if not smtp_configured():
        raise RuntimeError("SMTP 尚未配置。")
    host = os.environ["SMTP_HOST"]
    try:
        port = int(os.getenv("SMTP_PORT", "587"))
    except ValueError as exc:
        raise RuntimeError("SMTP_PORT 配置无效。") from exc
    if not 1 <= port <= 65535:
        raise RuntimeError("SMTP_PORT 配置无效。")
    user = os.environ["SMTP_USER"]
    message = EmailMessage()
    message["From"] = os.getenv("SMTP_FROM", user).strip() or user
    message["To"] = to_address
    message["Subject"] = subject
    message.set_content(body)
    if html_body:
        message.add_alternative(html_body, subtype="html")
    try:
        with smtplib.SMTP(host, port, timeout=20) as client:
            client.starttls(context=ssl.create_default_context())
            client.login(user, os.environ["SMTP_PASSWORD"])
            client.send_message(message)
    except (OSError, smtplib.SMTPException) as exc:
        raise RuntimeError("SMTP 邮件发送失败。") from exc
