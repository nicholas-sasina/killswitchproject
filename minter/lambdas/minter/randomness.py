"""Random credential material. secrets.choice draws from the OS CSPRNG."""
import secrets
import string

ALPHANUMERIC = string.ascii_letters + string.digits


def random_text(length: int) -> str:
    return "".join(secrets.choice(ALPHANUMERIC) for _ in range(length))
