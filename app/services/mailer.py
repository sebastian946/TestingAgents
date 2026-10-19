"""Outgoing email (WTA-28): verification links and password resets.

Two backends, chosen by `EMAIL_BACKEND`:
- `console`: the message is printed to the API log. Development and tests; the token in the
  link can be copied from there.
- `resend`: one HTTPS call to Resend (https://resend.com), the simplest transactional
  provider with a free tier. `RESEND_API_KEY` and a verified sender in `EMAIL_FROM`.

Routes call `send_email` through this module (`mailer.send_email(...)`), so tests replace
it with a recorder. Sending happens in a FastAPI background task: a provider outage must
not turn a registration into a 500.
"""
from dataclasses import dataclass

import requests

from config.variables import settings

RESEND_URL = "https://api.resend.com/emails"


class MailError(Exception):
    pass


@dataclass
class Email:
    to: str
    subject: str
    text: str
    html: str


def send_email(email: Email) -> None:
    if settings.email_backend == "resend":
        _send_resend(email)
    else:
        _send_console(email)


def _send_console(email: Email) -> None:
    print(f"[mail] to={email.to} subject={email.subject!r}\n{email.text}", flush=True)


def _send_resend(email: Email) -> None:
    if not settings.resend_api_key:
        raise MailError("EMAIL_BACKEND=resend but RESEND_API_KEY is empty.")
    response = requests.post(
        RESEND_URL,
        headers={"Authorization": f"Bearer {settings.resend_api_key}"},
        json={"from": settings.email_from, "to": [email.to], "subject": email.subject,
              "text": email.text, "html": email.html},
        timeout=15,
    )
    if response.status_code >= 400:
        raise MailError(f"Resend answered {response.status_code}: {response.text[:300]}")


# ----------------------------------------------------------------- messages

def _layout(title: str, body_html: str, link: str, button: str) -> str:
    return f"""<div style="font-family:system-ui,sans-serif;max-width:520px;margin:0 auto;padding:24px;color:#0f172a">
  <h2 style="margin:0 0 16px">{title}</h2>
  {body_html}
  <p style="margin:24px 0"><a href="{link}" style="background:#4f46e5;color:#fff;padding:10px 18px;border-radius:6px;text-decoration:none">{button}</a></p>
  <p style="font-size:13px;color:#64748b">If the button does not work, copy this link:<br>{link}</p>
</div>"""


def verification_email(to: str, token: str) -> Email:
    link = f"{settings.app_url}/verify?token={token}"
    hours = settings.auth_verify_token_hours
    return Email(
        to=to,
        subject="Verify your email for Testing Agents",
        text=(f"Welcome to Testing Agents.\n\nConfirm your email to start creating jobs:\n{link}\n\n"
              f"The link works for {hours} hours. If you did not sign up, ignore this message."),
        html=_layout("Confirm your email",
                     f"<p>Welcome to Testing Agents. Confirm your email to start creating jobs. "
                     f"The link works for {hours} hours.</p>", link, "Verify email"),
    )


def password_reset_email(to: str, token: str) -> Email:
    link = f"{settings.app_url}/reset-password?token={token}"
    minutes = settings.auth_reset_token_minutes
    return Email(
        to=to,
        subject="Reset your Testing Agents password",
        text=(f"Someone asked to reset the password of this account.\n\nChoose a new one here:\n{link}\n\n"
              f"The link works for {minutes} minutes. If it was not you, ignore this message: "
              f"your password stays the same."),
        html=_layout("Reset your password",
                     f"<p>Someone asked to reset the password of this account. The link works for "
                     f"{minutes} minutes. If it was not you, ignore this message.</p>", link, "Choose a new password"),
    )
