"""Where the CLI finds its settings: environment first, then the repo-root .env."""
import os
from pathlib import Path

from burn_contracts import automation, names

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


def people_sign_in_on(dotenv: Path | None = None) -> bool:
    """Whether the stack requires a person's sign-in: BURN_PEOPLE_ENABLED (`make env` writes it), else SSM, else no."""
    value = get(names.PEOPLE_ENABLED_ENV, dotenv)
    return value == "on" if value else automation.people_sign_in_on({})


def load(dotenv: Path | None = None) -> tuple[str, str]:
    """Return (api_url, demo_token). demo_token is "" when people sign in (`burn login`), which is once the stack
    requires it. Before that, and always with BURN_LOCAL_DEV=1, the API takes only the demo token, so it is
    required. Raises ConfigError with a hint when something is missing."""
    url = get("BURN_API_URL", dotenv)
    if not url:
        raise ConfigError("BURN_API_URL not set. Export it or run `make env` to write .env.")
    local = get(names.LOCAL_DEV_ENV, dotenv) == "1"
    if not local and people_sign_in_on(dotenv):
        return url.rstrip("/"), ""
    token = get("BURN_DEMO_TOKEN", dotenv)
    if not token and local:
        raise ConfigError("BURN_LOCAL_DEV=1 needs BURN_DEMO_TOKEN (any value for the mock servers).")
    if not token:
        raise ConfigError("this stack does not require sign-in yet, so it needs BURN_DEMO_TOKEN. "
                          "Run `make env` to write .env.")
    return url.rstrip("/"), token
