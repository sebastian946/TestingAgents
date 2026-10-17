"""Login credentials a user hands us for one job (WTA-29, ADR-08).

They are a secret we hold for minutes, not data we keep: encrypted at rest with Fernet
(AES-128-CBC + HMAC) under `CREDENTIALS_KEY`, decrypted only inside the explorer node,
and deleted from the job row as soon as the crawl is over (and again, as a safety net,
when the job finishes). They never go in the state, the logs, the traces, the API
responses or the LLM prompt.

Generate the key once per deployment:
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
Without it, jobs with a login are refused (503); plain jobs still work.
"""
import json
from dataclasses import dataclass

from cryptography.fernet import Fernet, InvalidToken

from config.variables import settings


@dataclass(frozen=True)
class LoginSpec:
    """What the explorer needs to sign in before crawling."""

    login_url: str
    username: str
    password: str

    def __repr__(self) -> str:  # keep the password out of tracebacks and logs
        return f"LoginSpec(login_url={self.login_url!r}, username={self.username!r}, password='***')"


class CredentialsError(Exception):
    pass


def configured() -> bool:
    return bool(settings.credentials_key)


def _fernet() -> Fernet:
    if not settings.credentials_key:
        raise CredentialsError("CREDENTIALS_KEY is not set: this server cannot store login credentials.")
    try:
        return Fernet(settings.credentials_key.encode())
    except ValueError as exc:
        raise CredentialsError("CREDENTIALS_KEY is not a valid Fernet key.") from exc


def encrypt(spec: LoginSpec) -> str:
    payload = json.dumps({"login_url": spec.login_url, "username": spec.username, "password": spec.password})
    return _fernet().encrypt(payload.encode()).decode()


def decrypt(blob: str) -> LoginSpec:
    try:
        data = json.loads(_fernet().decrypt(blob.encode()))
    except InvalidToken as exc:
        raise CredentialsError("Stored credentials cannot be decrypted (key changed?).") from exc
    return LoginSpec(login_url=data["login_url"], username=data["username"], password=data["password"])
