"""The environment an agent process gets: the host's credentials removed, its own values set."""
import re
from urllib.parse import quote, urlsplit, urlunsplit

# Credential-bearing names removed from the host environment. AWS_REGION, AWS_DEFAULT_REGION and AWS_CA_BUNDLE
# are the only AWS_ variables kept: they hold no credential and boto3 needs a region.
KEEP_AWS = {"AWS_REGION", "AWS_DEFAULT_REGION", "AWS_CA_BUNDLE"}
SCRUB_NAMES = {
    "BURN_JOIN_TOKEN", "BURN_DEMO_TOKEN", "BURN_API_KEY", "BURN_API_URL", "BURN_CLIENT_ID", "BURN_CLIENT_SECRET",
    "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY", "GH_TOKEN", "GITHUB_TOKEN",
    "GITLAB_TOKEN", "HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "NPM_TOKEN", "PYPI_TOKEN", "SLACK_BOT_TOKEN",
    "SLACK_TOKEN", "STRIPE_API_KEY", "DATABASE_URL", "REDIS_URL", "GOOGLE_APPLICATION_CREDENTIALS",
    "AZURE_CLIENT_SECRET", "SSH_AUTH_SOCK", "KUBECONFIG", "DOCKER_CONFIG",
}
SCRUB_PATTERN = re.compile(
    r"(^AWS_)|(_TOKEN$)|(_SECRET$)|(_SECRET_KEY$)|(_SECRET_ACCESS_KEY$)|(_ACCESS_KEY$)|(_PASSWORD$)|(_PASSWD$)"
    r"|(_API_KEY$)|(_APIKEY$)|(_CREDENTIALS$)|(_PRIVATE_KEY$)|(_AUTH$)", re.IGNORECASE)
PROXY_NAMES = ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy")
LOOPBACK = "127.0.0.1,localhost"


def is_scrubbed(name: str) -> bool:
    if name in KEEP_AWS:
        return False
    return name in SCRUB_NAMES or bool(SCRUB_PATTERN.search(name))


def scrub(host_env: dict) -> dict:
    """The host environment without anything that looks like a credential."""
    return {k: v for k, v in host_env.items() if not is_scrubbed(k)}


def proxy_url(base: str, agent_id: str, session_id: str) -> str:
    """The proxy URL an instance uses. The egress proxy attributes traffic by the user and password in
    this URL, the same convention the demo agents use (agent id and session id)."""
    parts = urlsplit(base)
    if parts.username or not parts.hostname:
        return base
    host = parts.hostname + (f":{parts.port}" if parts.port else "")
    return urlunsplit((parts.scheme, f"{quote(agent_id, safe='-')}:{quote(session_id, safe='_-')}@{host}",
                       parts.path, parts.query, parts.fragment))


def child_env(host_env: dict, *, session: dict, credentials, mcp_url: str, spawn_url: str,
              proxy_base: str = "", home: str = "") -> dict:
    """Everything the agent process runs with: the scrubbed host environment plus its own values."""
    env = scrub(host_env)
    if home:  # a private HOME, so SDKs do not find ~/.aws, ~/.config/gh, ~/.docker, ~/.netrc and the like
        env.update({"HOME": home, "XDG_CONFIG_HOME": f"{home}/.config", "XDG_CACHE_HOME": f"{home}/.cache",
                    "XDG_DATA_HOME": f"{home}/.local/share", "XDG_STATE_HOME": f"{home}/.local/state"})
    env.update({
        "BURN_SESSION_ID": session["session_id"], "BURN_AGENT_ID": session["agent_id"],
        "BURN_ROLE": session.get("role", ""), "BURN_MCP_URL": mcp_url, "BURN_SPAWN_URL": spawn_url})
    if credentials.aws_access_key_id:
        env["AWS_ACCESS_KEY_ID"] = credentials.aws_access_key_id
        env["AWS_SECRET_ACCESS_KEY"] = credentials.aws_secret_access_key
    if credentials.api_key:
        env["BURN_API_KEY"] = credentials.api_key
    if proxy_base:
        url = proxy_url(proxy_base, session["agent_id"], session["session_id"])
        for name in PROXY_NAMES:
            env[name] = url
        # The local MCP and spawn endpoints are on 127.0.0.1; they must never be sent to the proxy.
        existing = host_env.get("NO_PROXY") or host_env.get("no_proxy") or ""
        env["NO_PROXY"] = env["no_proxy"] = ",".join(x for x in (existing, LOOPBACK) if x)
    return env


def describe(host_env: dict, proxy_base: str = "", show: bool = False) -> list[str]:
    """Lines for `burn env`: what an instance's environment would contain, without starting one."""
    removed = sorted(k for k in host_env if is_scrubbed(k))
    kept = sorted(k for k in host_env if not is_scrubbed(k))
    lines = ["# removed from the host environment (names only)"]
    lines += [f"  {k}" for k in removed] or ["  (none)"]
    lines += ["", "# kept from the host" + ("" if show else " (values hidden; use --show)")]
    lines += [f"  {k}={host_env[k] if show else '<redacted>'}" for k in kept]
    lines += ["", "# set for the instance when it starts",
              "  BURN_SESSION_ID=<its session id>", "  BURN_AGENT_ID=<its agent id>", "  BURN_ROLE=<its role>",
              "  BURN_MCP_URL=http://127.0.0.1:<port>/...  (adds the badge token; the agent never sees the badge)",
              "  BURN_SPAWN_URL=http://127.0.0.1:<port>/spawn",
              "  AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY=<its own AWS key>",
              "  BURN_API_KEY=<its own provider key>"]
    if proxy_base:
        parts = urlsplit(proxy_base)
        shown = proxy_base if parts.username else f"{parts.scheme}://<agent id>:<session id>@{parts.netloc}"
        lines.append(f"  HTTPS_PROXY / HTTP_PROXY={shown}  (a convenience, not containment)")
    return lines
