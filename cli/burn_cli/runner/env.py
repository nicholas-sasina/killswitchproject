"""The environment an agent process gets: an allow-list of the host's, plus its own values."""
import json
import re
from urllib.parse import quote, urlsplit, urlunsplit

from burn_contracts.names import JOIN_TOKEN_RE, MAX_PROGRESS_ITEMS, PROGRESS_URL_ENV, RESUME_ENV, TASK_ENV, is_item_id

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

# What --allow-env may not pass through: the names burn run sets itself. A host copy would replace the instance's
# own key, identity or endpoints, point its tools back at your real HOME, or route around the egress proxy's
# attribution.
RESERVED_PREFIXES = ("BURN_", "AWS_", "XDG_")
RESERVED_NAMES = {"HOME", "PATH", "NO_PROXY", "no_proxy", *PROXY_NAMES}
VARIABLE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def pass_through_name(name: str) -> str:
    """NAME for --allow-env, checked; ValueError if it is not a variable name or burn run sets it itself."""
    if not VARIABLE_NAME.match(name):
        raise ValueError(f"{name!r} is not an environment variable name")
    if name in RESERVED_NAMES or name.startswith(RESERVED_PREFIXES):
        raise ValueError(f"burn run sets {name} itself, so it cannot be passed through "
                         f"(reserved: {', '.join(p + '*' for p in RESERVED_PREFIXES)}, HOME, PATH and the proxy variables)")
    return name


# A session token (bst_...) authenticates the supervisor's own calls. It must never be in the agent's environment,
# whatever variable it is in, so this is checked by pattern on top of the allow-list.
SESSION_TOKEN = re.compile(r"bst_[A-Za-z0-9_-]{43}")


# The task a launcher hands an instance, and where a replacement resumes it. Neither is a secret, but both reach
# the agent only after they are checked to hold item ids and nothing else (no free text, no session token).
TASK_NAMES = (TASK_ENV, RESUME_ENV)


# A held secret this long is refused anywhere inside an item; a shorter one only as the whole item, so that a
# short value cannot refuse ordinary item ids.
MIN_CONTAINED = 12
JOIN_TOKEN_SHAPE = re.compile(JOIN_TOKEN_RE.pattern, re.IGNORECASE)


class BadTask(ValueError):
    """BURN_TASK or BURN_RESUME holds something other than item ids, so burn run refuses to start."""


def _json(raw: str, name: str):
    try:
        return json.loads(raw)
    except ValueError:
        raise BadTask(f"{name} is not valid JSON") from None


def _secret_parts(secrets) -> set[str]:
    """Each held secret, and a join token's secret part (after the dot), lowercased."""
    parts = set()
    for value in filter(None, secrets):
        parts.add(value.lower())
        if match := JOIN_TOKEN_RE.match(value):
            parts.add(match["secret"].lower())
    return parts


def _holds_secret(item: str, parts: set[str]) -> bool:
    item = item.lower()
    return any(item == part or (len(part) >= MIN_CONTAINED and part in item) for part in parts)


def _item_ids(value, name: str, secrets: set[str]) -> list[str]:
    if not isinstance(value, list) or not all(is_item_id(item) for item in value):
        raise BadTask(f"{name} must be a list of item ids (each matching ^[A-Za-z0-9_.:-]{{1,64}}$)")
    if len(value) > MAX_PROGRESS_ITEMS:
        raise BadTask(f"{name} has more than {MAX_PROGRESS_ITEMS} items, the most one instance can report")
    # Credentials can fit the item id rule (a join token is exactly 64 characters). The messages never echo them.
    if any(SESSION_TOKEN.search(item) for item in value):
        raise BadTask(f"{name} holds something that looks like a session token")
    if any(JOIN_TOKEN_SHAPE.match(item) for item in value):
        raise BadTask(f"{name} holds something that looks like a join token")
    if any(_holds_secret(item, secrets) for item in value):
        raise BadTask(f"{name} holds a credential this burn run was given (the admin or the join token)")
    return value


def task_values(host_env: dict, secrets=()) -> dict:
    """BURN_TASK and BURN_RESUME for the agent, re-encoded from what was checked. Empty or unset: not passed on.

    BURN_TASK is a JSON list of item ids; BURN_RESUME is {"done": [ids], "quarantined": [ids]}. `secrets` are
    credential values this burn run holds: no item may be one, in any case, or hold one (or a join token's secret
    part) inside it.
    """
    out, secrets = {}, _secret_parts(secrets)
    if raw := host_env.get(TASK_ENV):
        out[TASK_ENV] = json.dumps(_item_ids(_json(raw, TASK_ENV), TASK_ENV, secrets))
    if raw := host_env.get(RESUME_ENV):
        resume = _json(raw, RESUME_ENV)
        if not isinstance(resume, dict) or set(resume) != {"done", "quarantined"}:
            raise BadTask(f'{RESUME_ENV} must be {{"done": [...], "quarantined": [...]}} with item ids only')
        out[RESUME_ENV] = json.dumps({key: _item_ids(resume[key], f"{RESUME_ENV}.{key}", secrets)
                                      for key in ("done", "quarantined")})
    return out


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
              proxy_base: str = "", home: str = "", local_token: str = "", progress_url: str = "",
              task: dict | None = None, allow: tuple = ()) -> dict:
    """Everything the agent process runs with: the allow-listed host environment plus its own values.

    `allow` names host variables the user passed through with --allow-env (checked by pass_through_name). They
    come before the instance's own values, which nothing replaces.
    """
    env = scrub(host_env)
    env.update({k: host_env[k] for k in allow if k in host_env and not SESSION_TOKEN.search(str(host_env[k]))})
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
    env.update(task or {})  # only what task_values checked; scrub() already removed the host's own copies
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
    removed = sorted(k for k in host_env if is_scrubbed(k) and k not in TASK_NAMES)
    kept = sorted(k for k in host_env if not is_scrubbed(k) or k in TASK_NAMES)
    lines = ["# removed from the host environment (names only)"]
    lines += [f"  {k}" for k in removed] or ["  (none)"]
    lines += ["", "# kept from the host" + ("" if show else " (values hidden; use --show)")]
    lines += [f"  {k}={host_env[k] if show else '<redacted>'}" for k in kept]
    lines += ["", "# set for the instance when it starts",
              "  BURN_SESSION_ID=<its session id>", "  BURN_AGENT_ID=<its agent id>", "  BURN_ROLE=<its role>",
              "  BURN_MCP_URL=http://127.0.0.1:<port>/...  (adds the badge token; the agent never sees the badge)",
              "  BURN_SPAWN_URL=http://127.0.0.1:<port>/spawn",
              f"  {PROGRESS_URL_ENV}=http://127.0.0.1:<port>/progress  (POST {{item, state}}: task progress)",
              f"  {TASK_ENV} / {RESUME_ENV}=<passed on from the host when set, if they hold item ids only>",
              "  BURN_LOCAL_TOKEN=<random per run; the bearer both local endpoints require>",
              "  AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY=<its own AWS key>",
              "  BURN_API_KEY=<its own provider key>"]
    if proxy_base:
        parts = urlsplit(proxy_base)
        shown = proxy_base if parts.username else f"{parts.scheme}://<agent id>:<session id>@{parts.netloc}"
        lines.append(f"  HTTPS_PROXY / HTTP_PROXY={shown}  (a convenience, not containment)")
    return lines
