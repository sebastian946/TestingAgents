"""Password policy (WTA-30): at least 8 characters (the Pydantic model) and not a known
leaked password.

The leak check uses the Have I Been Pwned "range" API with k-anonymity: only the first 5
hex characters of the SHA-1 are sent, the API returns every hash suffix with that prefix,
and the comparison happens here. The password never leaves the server. The check fails
open: if the API is down or slow, the password is accepted (and the failure is logged),
because a third-party outage must not block sign-ups.
"""
import hashlib

import requests

from config.variables import settings

HIBP_RANGE_URL = "https://api.pwnedpasswords.com/range/"
TIMEOUT = 3


def is_pwned(password: str) -> bool:
    if not settings.pwned_passwords_check:
        return False
    digest = hashlib.sha1(password.encode()).hexdigest().upper()
    prefix, suffix = digest[:5], digest[5:]
    try:
        response = requests.get(
            HIBP_RANGE_URL + prefix, headers={"Add-Padding": "true", "User-Agent": "TestingAgents"}, timeout=TIMEOUT,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        print(f"[passwords] leak check unavailable, accepting password: {exc}", flush=True)
        return False
    for line in response.text.splitlines():
        hash_suffix, _, count = line.partition(":")
        if hash_suffix == suffix and count.strip() not in ("", "0"):  # padding entries have count 0
            return True
    return False


PWNED_MESSAGE = "This password appears in known data breaches. Choose a different one."
