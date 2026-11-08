"""Where the CLI finds the API URL and demo token: environment first, then the repo-root .env."""
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


def save(api_url: str, token: str, dotenv: Path | None = None) -> Path:
    """Write the API URL and token into the .env file, keeping its other lines. The file is made private."""
    path = dotenv or REPO_ROOT / ".env"
    updates = {"BURN_API_URL": api_url.rstrip("/"), "BURN_DEMO_TOKEN": token}
    lines = path.read_text().splitlines() if path.exists() else []
    kept = [ln for ln in lines if ln.split("=", 1)[0].strip() not in updates]
    path.touch(mode=0o600)
    path.chmod(0o600)
    path.write_text("\n".join(kept + [f"{k}={v}" for k, v in updates.items()]) + "\n")
    return path


def load(dotenv: Path | None = None) -> tuple[str, str]:
    """Return (api_url, token). Raises ConfigError with a hint when either is missing."""
    file_values = _read_dotenv(dotenv or REPO_ROOT / ".env")

    def get(name: str) -> str:
        return os.environ.get(name) or file_values.get(name, "")

    url, token = get("BURN_API_URL"), get("BURN_DEMO_TOKEN")
    missing = [n for n, v in (("BURN_API_URL", url), ("BURN_DEMO_TOKEN", token)) if not v]
    if missing:
        raise ConfigError(f"{' and '.join(missing)} not set. Export them or run `make env` to write .env.")
    return url.rstrip("/"), token
