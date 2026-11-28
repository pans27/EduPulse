"""Recover a resource's outbound token from its encrypted database value."""

from pathlib import Path
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken


def load_cipher() -> Fernet | None:
    # Local stacks can check public resources without a secret.
    secret = Path("/run/secrets/resource_token_key")
    return Fernet(secret.read_bytes().strip()) if secret.exists() else None


def decrypt_token(cipher: Fernet | None, resource_id: UUID, url: str, ciphertext: bytes) -> str:
    # Check the resource and URL binding before returning the token.
    if cipher is None:
        raise InvalidToken
    payload = cipher.decrypt(ciphertext)
    binding = resource_id.bytes + url.encode() + b"\x00"
    if not payload.startswith(binding):
        raise InvalidToken
    return payload[len(binding):].decode("ascii")
