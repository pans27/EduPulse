"""Encrypt outbound resource tokens with the API and worker's Docker secret."""

import os
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.fernet import Fernet


def load_cipher() -> Fernet | None:
    # The default secret is optional for local stacks that only check public resources.
    secret = Path("/run/secrets/resource_token_key")
    return Fernet(secret.read_bytes().strip()) if secret.exists() else None


def accepts_token(url: str) -> bool:
    # Permit cleartext only for the explicitly enabled local Compose fixture.
    target = urlsplit(url)
    return target.scheme == "https" or (
        os.getenv("EDUPULSE_LOCAL_TESTING") == "1"
        and target.scheme == "http" and target.netloc == "fake-site:8000"
    )
