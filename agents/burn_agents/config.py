"""Everything an agent container needs, read once from environment variables.

run_demo.py writes these into .run/<agent_id>.env (chmod 600). Nothing here
has a default for a secret or a URL: a missing value fails fast at boot.
"""
import os
from dataclasses import dataclass

DEFAULT_MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"


def _required(env: dict, name: str) -> str:
    value = env.get(name, "")
    if not value:
        raise SystemExit(f"missing required environment variable {name}")
    return value


@dataclass(frozen=True)
class Config:
    agent_id: str
    scenario_file: str
    gateway_url: str
    provider_url: str
    model_id: str
    # The agent's badge: OAuth client credentials against the badge issuer.
    token_url: str
    client_id: str
    client_secret: str
    scope: str
    # The agent's own credentials (never shared with other agents). Required: without
    # an own AWS key boto3 would silently fall back to whatever the host has.
    api_key: str
    aws_access_key_id: str
    aws_secret_access_key: str
    # Pacing, so the demo has agents "mid-task" for a few minutes.
    ticket_pause: float
    poll_seconds: float
    heartbeat_seconds: float
    repeat_queue: bool
    step_file: str  # when set, the ticket reader waits for this file between tickets


def load_config(env: dict | None = None) -> Config:
    env = dict(os.environ) if env is None else env
    return Config(
        agent_id=_required(env, "AGENT_ID"),
        scenario_file=_required(env, "BURN_SCENARIO_FILE"),
        gateway_url=_required(env, "BURN_GATEWAY_URL"),
        provider_url=env.get("BURN_PROVIDER_URL", ""),
        model_id=env.get("BURN_MODEL_ID") or DEFAULT_MODEL_ID,
        token_url=_required(env, "BURN_TOKEN_URL"),
        client_id=_required(env, "BURN_CLIENT_ID"),
        client_secret=_required(env, "BURN_CLIENT_SECRET"),
        scope=env.get("BURN_SCOPE", ""),
        api_key=_required(env, "BURN_API_KEY"),
        aws_access_key_id=_required(env, "BURN_AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=_required(env, "BURN_AWS_SECRET_ACCESS_KEY"),
        ticket_pause=float(env.get("BURN_TICKET_PAUSE", "20")),
        poll_seconds=float(env.get("BURN_POLL_SECONDS", "5")),
        heartbeat_seconds=float(env.get("BURN_HEARTBEAT_SECONDS", "300")),
        repeat_queue=env.get("BURN_REPEAT_QUEUE", "1") == "1",
        step_file=env.get("BURN_STEP_FILE", ""),
    )
