"""Where the CLI finds its settings: environment first, then the repo-root .env."""
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


class ConfigError(Exception):
    pass


def _read_dotenv(path: Path) -> dict[str, str]:
    values = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip().strip('"')
    return values


def get(name: str, dotenv: Path | None = None) -> str:
    """One setting: environment first, then the .env file. Empty string when unset."""
    return os.environ.get(name) or _read_dotenv(dotenv or REPO_ROOT / ".env").get(name, "")


def load(dotenv: Path | None = None) -> tuple[str, str]:
    """Return (api_url, demo_token). The demo token is needed (and read) only with BURN_LOCAL_DEV=1; otherwise
    people sign in with `burn login` and the token is "". Raises ConfigError with a hint when something is missing."""
    url = get("BURN_API_URL", dotenv)
    if not url:
        raise ConfigError("BURN_API_URL not set. Export it or run `make env` to write .env.")
    if get("BURN_LOCAL_DEV", dotenv) != "1":
        return url.rstrip("/"), ""
    token = get("BURN_DEMO_TOKEN", dotenv)
    if not token:
        raise ConfigError("BURN_LOCAL_DEV=1 needs BURN_DEMO_TOKEN (any value for the mock servers).")
    return url.rstrip("/"), token
