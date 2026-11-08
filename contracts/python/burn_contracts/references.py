"""Secret references: what an agent gets instead of a credential Burn recognizes.

The Gateway swaps each located credential in tool output for {{burn:ref:<secret_ref>}}, so
the agent never holds the value and no encoding of what it holds can leak it. Canaries are
never replaced. A role chooses the mode (role field `references`):
    replace  swap the value for a reference (default)
    report   record what would have been replaced, change nothing
    off      neither
"""
import re

MODES = ("replace", "report", "off")
DEFAULT_MODE = "replace"
EXPOSURE_STATUSES = ("seen", "referenced")  # burn-exposures `status`: did plaintext reach the agent?

REF_RE = re.compile(r"\{\{burn:ref:(?P<secret_ref>burn/(?:agent-[a-z0-9-]{1,40}|shared)/[a-z0-9_]{1,40})\}\}")


def token(secret_ref: str) -> str:
    """The reference that replaces a credential, e.g. {{burn:ref:burn/shared/crm_db_url}}."""
    value = "{{burn:ref:" + secret_ref + "}}"
    if not REF_RE.fullmatch(value):
        raise ValueError(f"not a burn secret_ref: {secret_ref!r}")
    return value


def mode(session: dict) -> str:
    """The session's references mode; unknown or missing values fall back to the default."""
    value = (session or {}).get("references")
    return value if value in MODES else DEFAULT_MODE
