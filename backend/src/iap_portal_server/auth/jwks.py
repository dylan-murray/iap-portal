"""Signing/verification keys and the public JWKS used by apps.

JWKS publishes the current key (PORTAL_JWT_KID) plus any additional verification
keys found in PORTAL_JWT_ADDITIONAL_PUBLIC_KEYS_DIR as `<kid>.pem`. Rotation:

1. Add the new public key as `<new-kid>.pem` in that directory; wait for app JWKS
   caches (5 minutes) to pick it up.
2. Switch the private/public key and PORTAL_JWT_KID to the new pair, and keep the
   old public key as `<old-kid>.pem`. Tokens signed with either key verify.
3. After the JWT lifetime plus cache time has passed, remove the old key.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from iap_portal_server.config import Settings, get_settings

MIN_RSA_BITS = 2048
_KID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class KeyConfigError(RuntimeError):
    pass


@lru_cache(maxsize=8)
def _load_private(pem: str) -> rsa.RSAPrivateKey:
    key = serialization.load_pem_private_key(pem.encode(), password=None)
    if not isinstance(key, rsa.RSAPrivateKey):
        raise KeyConfigError("JWT signing key must be an RSA private key")
    return key


@lru_cache(maxsize=32)
def _load_public(pem: str) -> rsa.RSAPublicKey:
    key = serialization.load_pem_public_key(pem.encode())
    if not isinstance(key, rsa.RSAPublicKey):
        raise KeyConfigError("JWT verification keys must be RSA public keys")
    return key


def signing_key(settings: Settings | None = None) -> rsa.RSAPrivateKey:
    settings = settings or get_settings()
    return _load_private(settings.resolved_jwt_private_key_pem())


def verification_keys(settings: Settings | None = None) -> dict[str, rsa.RSAPublicKey]:
    """kid → public key for every key published in JWKS."""
    settings = settings or get_settings()
    keys = {settings.jwt_kid: _load_public(settings.resolved_jwt_public_key_pem())}
    if settings.jwt_additional_public_keys_dir:
        for path in sorted(Path(settings.jwt_additional_public_keys_dir).glob("*.pem")):
            kid = path.stem
            if not _KID_RE.match(kid):
                raise KeyConfigError(f"invalid kid in file name {path.name}")
            if kid in keys:
                raise KeyConfigError(f"duplicate kid {kid}")
            keys[kid] = _load_public(path.read_text())
    return keys


def key_problems(settings: Settings | None = None) -> list[str]:
    """Configuration errors for the signing setup (empty when consistent)."""
    settings = settings or get_settings()
    try:
        private = signing_key(settings)
        keys = verification_keys(settings)
    except (RuntimeError, OSError, ValueError) as exc:
        return [f"JWT keys: {exc}"]
    problems = []
    if not _KID_RE.match(settings.jwt_kid):
        problems.append("PORTAL_JWT_KID must be 1-64 characters of [A-Za-z0-9._-]")
    if private.key_size < MIN_RSA_BITS:
        problems.append(f"JWT signing key must be at least {MIN_RSA_BITS} bits")
    if private.public_key().public_numbers() != keys[settings.jwt_kid].public_numbers():
        problems.append("JWT public key does not match the signing key")
    return problems


def current_jwks() -> dict:
    out = []
    for kid, key in verification_keys().items():
        jwk = RSAAlgorithm.to_jwk(key, as_dict=True)
        jwk.update({"kid": kid, "use": "sig", "alg": "RS256"})
        out.append(jwk)
    return {"keys": out}
