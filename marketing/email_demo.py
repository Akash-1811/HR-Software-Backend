"""Send inbox notification when someone books a demo from the marketing site."""

import smtplib
from pathlib import Path

from django.conf import settings
from django.template.loader import render_to_string
from django.utils import timezone
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from Reports.email_report import _find_logo_path


def _send_multipart_related_html_email(
    *,
    subject: str,
    to_emails: list[str],
    reply_to: str | None,
    html_body: str,
    logo_path: Path | None,
):
    """HTML + optional inline logo; same SMTP / console fallback as Reports.send_report_email."""
    if not to_emails:
        return

    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject
    msg["From"] = settings.DEFAULT_FROM_EMAIL
    msg["To"] = ", ".join(to_emails)
    if reply_to:
        msg["Reply-To"] = reply_to

    html_part = MIMEText(html_body, "html")
    html_part.add_header("Content-ID", "<html_root>")
    body_related = MIMEMultipart("related")
    body_related.set_param("start", "<html_root>")
    body_related.attach(html_part)

    if logo_path:
        ext = logo_path.suffix.lower()
        subtype = "png" if ext == ".png" else "jpeg"
        with open(logo_path, "rb") as f:
            logo_img = MIMEImage(f.read(), _subtype=subtype)
            logo_img.add_header("Content-ID", "<attenova_logo>")
            logo_img.add_header("Content-Disposition", "inline")
            body_related.attach(logo_img)

    msg.attach(body_related)

    if getattr(settings, "EMAIL_BACKEND", "") == "django.core.mail.backends.smtp.EmailBackend":
        use_tls = getattr(settings, "EMAIL_USE_TLS", True)
        use_ssl = getattr(settings, "EMAIL_USE_SSL", False)
        port = getattr(settings, "EMAIL_PORT", 587)
        host = getattr(settings, "EMAIL_HOST", "smtp.gmail.com")
        user = getattr(settings, "EMAIL_HOST_USER", "") or None
        password = getattr(settings, "EMAIL_HOST_PASSWORD", "") or None
        smtp_cls = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
        with smtp_cls(host, port) as smtp:
            if use_tls and not use_ssl:
                smtp.starttls()
            if user and password:
                smtp.login(user, password)
            smtp.sendmail(settings.DEFAULT_FROM_EMAIL, to_emails, msg.as_bytes())
    else:
        from django.core.mail import get_connection

        class _RawMessage:
            def __init__(self, mime_msg, from_email, to_list, subject):
                self._msg = mime_msg
                self.from_email = from_email
                self.to = to_list
                self.subject = subject
                self.encoding = None

            def message(self):
                return self._msg

            def recipients(self):
                return self.to

        connection = get_connection()
        raw = _RawMessage(msg, settings.DEFAULT_FROM_EMAIL, to_emails, subject)
        connection.send_messages([raw])


def send_book_demo_notification(
    *,
    name: str,
    company_email: str,
    company_name: str,
    contact_number: str,
    message: str,
):
    inbox = getattr(settings, "DEMO_BOOKING_INBOX", "") or ""
    inbox = inbox.strip()
    if not inbox:
        raise ValueError("DEMO_BOOKING_INBOX is empty; configure it in settings or DEMO_BOOKING_INBOX env.")

    logo_path = _find_logo_path()
    has_logo = logo_path is not None
    now = timezone.now()
    submitted_at_display = timezone.localtime(now).strftime("%b %d, %Y · %I:%M %p %Z")

    ctx = {
        "name": name,
        "company_email": company_email,
        "company_name": company_name,
        "contact_number": contact_number,
        "message": message.strip() if message else "",
        "has_logo": has_logo,
        "year": now.year,
        "submitted_at_display": submitted_at_display,
    }
    html_body = render_to_string("marketing/book_demo_email.html", ctx)

    safe_company = company_name.replace("\n", " ").strip() or "Prospect"
    subject = f"[Attenova] Demo request · {safe_company[:80]}"

    _send_multipart_related_html_email(
        subject=subject,
        to_emails=[inbox],
        reply_to=company_email if company_email else None,
        html_body=html_body,
        logo_path=logo_path if has_logo else None,
    )
