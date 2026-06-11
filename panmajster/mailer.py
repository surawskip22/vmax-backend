import smtplib
from email.message import EmailMessage

from .config import get_settings


def send_email(to: str, subject: str, text: str) -> bool:
    settings = get_settings()
    if not settings.smtp_host:
        return False

    message = EmailMessage()
    message["From"] = settings.smtp_from
    message["To"] = to
    message["Subject"] = subject
    message.set_content(text)

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as smtp:
        if settings.smtp_starttls:
            smtp.starttls()
        if settings.smtp_user:
            smtp.login(settings.smtp_user, settings.smtp_password or "")
        smtp.send_message(message)
    return True


def send_otp(to: str, code: str) -> bool:
    return send_email(
        to,
        "Kod logowania do Pan Majster",
        (
            f"Twój kod logowania: {code}\n\n"
            "Kod jest ważny przez 10 minut. Jeśli to nie Ty, zignoruj wiadomość."
        ),
    )
