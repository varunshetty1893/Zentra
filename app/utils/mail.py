"""
Email sending for Zentra, via Gmail SMTP (Flask-Mail).

Setup (one-time):
  1. Enable 2-Step Verification on the Gmail account you want to send from.
  2. Google Account -> Security -> App Passwords -> generate one for "Mail".
  3. Put these in your .env (see .env.example):
       MAIL_USERNAME=youraddress@gmail.com
       MAIL_PASSWORD=<16-char app password, no spaces>
       MAIL_DEFAULT_SENDER=youraddress@gmail.com
       MAIL_ADMIN_ADDRESS=youraddress@gmail.com   (where admin alerts go)

Gmail's free-tier limits (~500/day on a regular account, ~2000/day on
Workspace) apply — fine for OTPs/notifications on a small app, not for
bulk/marketing mail.

Every use case below (OTP, password reset, registration, notifications,
security alerts, contact form, receipts, admin alerts) is just a call to
send_email() with different subject/body — that's the one function that
actually talks to SMTP. Everything else is a thin, readable wrapper around
it so call sites read like what they do.
"""
import logging

from flask import current_app
from flask_mail import Message

from app import mail

logger = logging.getLogger(__name__)


def send_email(subject, recipients, body, html=None):
    """Low-level send. recipients: str or list[str]. Never raises —
    logs and returns False on failure so a flaky SMTP call never 500s
    a request (e.g. signup should still succeed even if the welcome
    email fails to send).
    """
    if isinstance(recipients, str):
        recipients = [recipients]

    if current_app.config.get("MAIL_SUPPRESS_SEND"):
        logger.info("MAIL_SUPPRESS_SEND=true — not sending. To=%s Subject=%s\n%s",
                     recipients, subject, body)
        return True

    if not current_app.config.get("MAIL_USERNAME"):
        logger.warning("MAIL_USERNAME not configured — skipping email. Subject=%s", subject)
        return False

    try:
        msg = Message(
            subject=f"Zentra — {subject}",
            recipients=recipients,
            body=body,
            html=html,
            sender=current_app.config.get("MAIL_DEFAULT_SENDER"),
        )
        mail.send(msg)
        return True
    except Exception:
        logger.exception("Failed to send email: subject=%s to=%s", subject, recipients)
        return False


# ---------------------------------------------------------------------------
# 🔐 Login / account verification — OTP or verification link
# ---------------------------------------------------------------------------
def send_otp_email(to_email, otp_code, purpose="verify your account"):
    subject = "Your verification code"
    body = (
        f"Your Zentra verification code is: {otp_code}\n\n"
        f"Use this code to {purpose}. It expires in 10 minutes.\n\n"
        f"If you didn't request this, you can ignore this email."
    )
    return send_email(subject, to_email, body)


# ---------------------------------------------------------------------------
# 🔑 Forgot password — password-reset link
# ---------------------------------------------------------------------------
def send_password_reset_email(to_email, reset_link):
    subject = "Reset your password"
    body = (
        f"We received a request to reset your Zentra password.\n\n"
        f"Reset it here (valid for 1 hour): {reset_link}\n\n"
        f"If you didn't request this, you can safely ignore this email — "
        f"your password won't change."
    )
    return send_email(subject, to_email, body)


# ---------------------------------------------------------------------------
# 👤 User registration — welcome email
# ---------------------------------------------------------------------------
def send_welcome_email(to_email, full_name):
    subject = "Welcome to Zentra"
    body = (
        f"Hi {full_name},\n\n"
        f"Welcome to Zentra! Your account has been created successfully.\n\n"
        f"You can log in any time to build your profile, apply to jobs, "
        f"and track your applications."
    )
    return send_email(subject, to_email, body)


# ---------------------------------------------------------------------------
# 📢 Notifications — important actions
# ---------------------------------------------------------------------------
def send_notification_email(to_email, title, message):
    return send_email(title, to_email, message)


# ---------------------------------------------------------------------------
# 🛡️ Security alerts — new login, password changed, etc.
# ---------------------------------------------------------------------------
def send_security_alert_email(to_email, event_description, ip_address=None):
    subject = "Security alert on your account"
    body = f"{event_description}\n"
    if ip_address:
        body += f"IP address: {ip_address}\n"
    body += "\nIf this wasn't you, reset your password immediately."
    return send_email(subject, to_email, body)


# ---------------------------------------------------------------------------
# 📧 Contact / feedback form — forward submitted message to admin
# ---------------------------------------------------------------------------
def send_contact_form_email(from_name, from_email, message):
    admin_address = current_app.config.get("MAIL_ADMIN_ADDRESS")
    subject = f"Contact form message from {from_name}"
    body = f"From: {from_name} <{from_email}>\n\n{message}"
    return send_email(subject, admin_address, body)


# ---------------------------------------------------------------------------
# 🧾 Reports / receipts — confirmations
# ---------------------------------------------------------------------------
def send_receipt_email(to_email, summary_lines):
    """summary_lines: list[str], e.g. ['Plan: Pro', 'Amount: $19.00']."""
    subject = "Your receipt"
    body = "Here's your confirmation:\n\n" + "\n".join(summary_lines)
    return send_email(subject, to_email, body)


# ---------------------------------------------------------------------------
# 👨‍💼 Admin notifications — tell the admin when a user does something important
# ---------------------------------------------------------------------------
def send_admin_notification_email(subject, message):
    admin_address = current_app.config.get("MAIL_ADMIN_ADDRESS")
    return send_email(subject, admin_address, message)
