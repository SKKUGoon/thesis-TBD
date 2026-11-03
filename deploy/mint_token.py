"""Mint a short-lived local test-admin token from private deployment credentials."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from pathlib import Path
import time


def mint_token(deploy: Path) -> str:
    """Return an eight-hour evaluation token; never log or commit it."""
    config = dict(line.split("=", 1) for line in (deploy / ".env").read_text().splitlines() if "=" in line)
    def encode(value: dict) -> bytes:
        return base64.urlsafe_b64encode(json.dumps(value, separators=(",", ":")).encode()).rstrip(b"=")
    now = int(time.time())
    content = encode({"alg": "HS256", "typ": "JWT"})+b"."+encode({"sub": "local-test-admin", "iss": "paper-operations", "aud": "paper-api", "iat": now, "exp": now+28800})
    signature = base64.urlsafe_b64encode(hmac.new(config["JWT_SECRET"].encode(), content, hashlib.sha256).digest()).rstrip(b"=")
    return (content+b"."+signature).decode()


if __name__ == "__main__":
    root = Path(__file__).resolve().parent
    output = root / ".state" / "test-token"
    output.write_text(mint_token(root))
    output.chmod(0o600)
    print(str(output))
