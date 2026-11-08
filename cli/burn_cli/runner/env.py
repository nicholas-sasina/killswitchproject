"""The environment an agent process gets: an allow-list of the host's, plus its own values."""
import re
from urllib.parse import quote, urlsplit, urlunsplit

from burn_contracts.names import PROGRESS_URL_ENV

# The agent's environment is an allow-list: a variable reaches it only if it is listed here. A deny-list has to
# know every credential name (PGPASSWORD, MYSQL_PWD, SENTRY_DSN, a proxy URL with a password in it...) and misses some.
# AWS_REGION and AWS_DEFAULT_REGION hold no credential and boto3 needs a region; the CA-bundle variables let TLS
# work behind the proxy. Everything burn run sets itself is added after this filter.
ALLOWED_NAMES = {
    "PATH", "LANG", "LANGUAGE", "TZ", "TERM", "COLORTERM", "TMPDIR",
    "AWS_REGION", "AWS_DEFAULT_REGION",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "AWS_CA_BUNDLE", "NODE_EXTRA_CA_CERTS",
}
ALLOWED_PREFIXES = ("LC_",)
PROXY_NAMES = ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy")
LOOPBACK = "127.0.0.1,localhost"


# A session token (bst_...) authenticates the supervisor's own calls. It must never be in the agent's environment,
# whatever variable it is in, so this is checked by pattern on top of the allow-list.
SESSION_TOKEN = re.compile(r"bst_[A-Za-z0-9_-]{43}")


def is_scrubbed(name: str) -> bool:
    """True for every host variable the agent does not get."""
    return name not in ALLOWED_NAMES and not name.startswith(ALLOWED_PREFIXES)


def scrub(host_env: dict) -> dict:
    """Only the allow-listed part of the host environment."""
    return {k: v for k, v in host_env.items() if not is_scrubbed(k) and not SESSION_TOKEN.search(str(v))}


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
              proxy_base: str = "", home: str = "", local_token: str = "", progress_url: str = "") -> dict:
    """Everything the agent process runs with: the allow-listed host environment plus its own values."""
    env = scrub(host_env)
    if home:  # a private HOME, so SDKs do not find ~/.aws, ~/.config/gh, ~/.docker, ~/.netrc and the like
        env.update({"HOME": home, "XDG_CONFIG_HOME": f"{home}/.config", "XDG_CACHE_HOME": f"{home}/.cache",
                    "XDG_DATA_HOME": f"{home}/.local/share", "XDG_STATE_HOME": f"{home}/.local/state"})
    env.update({
        "BURN_SESSION_ID": session["session_id"], "BURN_AGENT_ID": session["agent_id"],
        "BURN_ROLE": session.get("role", ""), "BURN_MCP_URL": mcp_url, "BURN_SPAWN_URL": spawn_url})
    if local_token:
        env["BURN_LOCAL_TOKEN"] = local_token
    if progress_url:
        env[PROGRESS_URL_ENV] = progress_url
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
    leaked = [k for k, v in env.items() if SESSION_TOKEN.search(str(v))]
    if leaked:
        raise ValueError(f"refusing to give the agent a session token (in {', '.join(sorted(leaked))})")
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
              f"  {PROGRESS_URL_ENV}=http://127.0.0.1:<port>/progress  (POST {{item, state}}: task progress)",
              "  BURN_LOCAL_TOKEN=<random per run; the bearer both local endpoints require>",
              "  AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY=<its own AWS key>",
              "  BURN_API_KEY=<its own provider key>"]
    if proxy_base:
        parts = urlsplit(proxy_base)
        shown = proxy_base if parts.username else f"{parts.scheme}://<agent id>:<session id>@{parts.netloc}"
        lines.append(f"  HTTPS_PROXY / HTTP_PROXY={shown}  (a convenience, not containment)")
    return lines
