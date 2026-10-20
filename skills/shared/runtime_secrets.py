#!/usr/bin/env python3
"""Read runtime secrets from environment or OpenClaw's local configuration.

This is also where Telegram sending is fenced off during tests, because it is the one
place every sender already passes through.

WHY HERE (2026-10-06): 61 scripts POST to api.telegram.org themselves rather than going
through shared/send_telegram.py, so that module's TELEGRAM_SEND_DISABLED tripwire only
ever covered the few that used it. On 2026-09-28 a wardrobe test sent the user a fake
packing list and the tripwire was added there; on 2026-10-06 the nightly-sync tests sent
him four real alerts, having never touched that module. Guarding one exit does nothing
while the capability — the bot token — is handed to anyone who asks for it.

So the guard moved to where the capability is ISSUED. 56 of those 61 already call
require_secret("TELEGRAM_BOT_TOKEN"), including every sender written in the last month,
and a script that does not exist yet is covered the moment it asks for the token.
"""
import json
import os
from pathlib import Path

TELEGRAM_TOKEN_NAME = "TELEGRAM_BOT_TOKEN"
BLOCKED_TOKEN_VALUE = "TELEGRAM-SENDING-DISABLED-IN-THIS-ENVIRONMENT"


class BlockedTelegramToken(str):
    """A token that is safe to hold and refuses to send while the fence is up.

    It cannot simply raise on creation: nearly every sender does
    `TG_TOKEN = require_secret(...)` at module scope, so refusing outright would break
    any test that merely imports one — including tests of logic that never sends.

    Nor can it decide once, at issue time. A module binds its token on import, and a
    test may legitimately clear TELEGRAM_SEND_DISABLED afterwards to exercise the real
    send path with urlopen stubbed (test_send_telegram_tracking does exactly that). So
    the flag is read at the moment of USE, and the real token is handed over if the
    fence has come down by then.

    Its own string value stays the sentinel, never the real token, so the one path that
    cannot be intercepted — str.join, which reads the raw value — produces a URL that
    404s instead of one that reaches the user.
    """

    def __new__(cls, real_token: str):
        blocked = super().__new__(cls, BLOCKED_TOKEN_VALUE)
        blocked._real = real_token
        return blocked

    def _resolve(self) -> str:
        if telegram_sending_disabled():
            raise RuntimeError(
                "Telegram sending is disabled in this environment "
                "(TELEGRAM_SEND_DISABLED=1). A script reached the point of building a "
                "Telegram URL anyway, which means a missing guard — see "
                "shared/runtime_secrets.py."
            )
        return self._real

    # Every way a URL gets built from a token. __rmod__ matters because in
    # `"bot%s/send" % token` the hook belongs to the format string, not the token.
    def __format__(self, spec):
        return format(self._resolve(), spec)

    def __add__(self, other):
        return self._resolve() + other

    def __radd__(self, other):
        return other + self._resolve()

    def __mod__(self, other):
        return self._resolve() % other

    def __rmod__(self, other):
        return other % self._resolve()


def telegram_sending_disabled() -> bool:
    return os.environ.get("TELEGRAM_SEND_DISABLED") == "1"


def get_secret(name: str, default: str = "") -> str:
    if name == TELEGRAM_TOKEN_NAME and telegram_sending_disabled():
        return BlockedTelegramToken(_resolve_raw(name, default))
    return _resolve_raw(name, default)


def _resolve_raw(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    if value:
        return value
    sandbox = os.environ.get("OPENCLAW_HOME")
    root = Path(sandbox or (Path.home() / ".openclaw"))
    try:
        config = json.loads((root / "openclaw.json").read_text())
        return str(config.get("env", {}).get("vars", {}).get(name, default))
    except (FileNotFoundError, json.JSONDecodeError, TypeError):
        if sandbox:
            # A test sandbox (OPENCLAW_HOME set) never reads the real openclaw.json. A
            # module that needs a token at import time gets an obviously fake one; the
            # Telegram tripwire (TELEGRAM_SEND_DISABLED) stops any send regardless.
            return default or f"sandbox-{name.lower()}"
        return default


def require_secret(name: str) -> str:
    value = get_secret(name)
    if not value:
        raise RuntimeError(f"missing required runtime secret: {name}")
    return value
