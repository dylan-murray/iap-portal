#!/usr/bin/env python3
"""Generate an RSA keypair for local JWT signing if one doesn't exist yet.

Writes to backend/.dev-keys/{private,public}.pem and prints env-var exports
for use by `make portal-dev`. The keys are gitignored.
"""

from __future__ import annotations

import pathlib
import sys

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def main() -> None:
    root = pathlib.Path(__file__).resolve().parent.parent
    out = root / "backend" / ".dev-keys"
    out.mkdir(parents=True, exist_ok=True)
    priv_path = out / "private.pem"
    pub_path = out / "public.pem"

    if not priv_path.exists():
        priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        priv_path.write_bytes(
            priv.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )
        pub_path.write_bytes(
            priv.public_key().public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            )
        )
        print(f"wrote {priv_path} + {pub_path}", file=sys.stderr)

    # Emit shell exports so `make` can eval them.
    print(f'export PORTAL_JWT_PRIVATE_KEY_PEM="$(cat {priv_path})"')
    print(f'export PORTAL_JWT_PUBLIC_KEY_PEM="$(cat {pub_path})"')


if __name__ == "__main__":
    main()
