"""Verify the transferred bundle and create a private token without model downloads."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import secrets


def prepare(root: Path) -> None:
    """Check immutable bundle files, then initialize credentials only if absent."""
    manifest = json.loads((root / 'manifest.json').read_text())
    # config.json is intentionally editable; immutable files must still match.
    for name, expected in manifest['files'].items():
        if name == 'config.json':
            continue
        path = root / name
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('BUNDLE_HASH_MISMATCH:' + name)
    private = root / 'private'
    # Container UID 10001 must traverse this read-only mount. Host bundle stays private.
    private.chmod(0o755)
    token = private / 'access-token'
    if not token.exists():
        token.write_text(secrets.token_urlsafe(48))
    token.chmod(0o644)
    print('검증 완료. docker compose up -d --build 로 시작하십시오.')
    print('접속 토큰 파일: ' + str(token))


if __name__ == '__main__':
    prepare(Path(__file__).resolve().parent)
