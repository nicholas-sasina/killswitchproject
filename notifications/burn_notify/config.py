"""Notification configuration: destinations, routing, aggregation.

Loaded once per Lambda container from (first match):
  BURN_NOTIFY_CONFIG_JSON   the JSON document itself (set by the stack from a file)
  BURN_NOTIFY_CONFIG        path to a JSON file (local demo: config.example.json)

Secrets never appear here: a destination names a secret_ref (env:NAME locally,
aws-sm:<id>[#key] deployed). There is no public API to change this file; changing
routing is a reviewed config change and a redeploy.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from .incident import Incident, dashboard_link
from .policy import CATEGORIES, Severity
from .providers import REGISTRY, Destination

SEND, DIGEST = "send", "digest"


@dataclass(frozen=True)
class Config:
    destinations: tuple[Destination, ...]
    dashboard_url: str | None = None
    severity_overrides: dict[str, Severity] = field(default_factory=dict)
    aggregation_window_seconds: int = 300
    individual_per_window: int = 3
    digest_window_seconds: int = 3600
    delivery_ttl_days: int = 14
    allow_env_secrets: bool = True

    def destination(self, dest_id: str) -> Destination:
        for dest in self.destinations:
            if dest.id == dest_id:
                return dest
        raise KeyError(dest_id)


def _tuple(value, name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError(f"{name} must be a list of strings")
    return tuple(value)


def _destination(raw: dict) -> Destination:
    if not isinstance(raw, dict):
        raise ValueError("each destination must be an object")
    dest_id, provider = raw.get("id"), raw.get("provider")
    if not isinstance(dest_id, str) or not dest_id.replace("-", "").replace("_", "").isalnum() or len(dest_id) > 64:
        raise ValueError("destination id must be 1-64 chars of letters, digits, - or _")
    if provider not in REGISTRY:
        raise ValueError(f"destination {dest_id}: unknown provider {provider!r}")
    Severity.parse(raw.get("min_severity", "high"))
    categories = _tuple(raw.get("categories"), "categories")
    excluded = _tuple(raw.get("exclude_categories"), "exclude_categories")
    unknown = (set(categories) | set(excluded)) - set(CATEGORIES)
    if unknown:
        raise ValueError(f"destination {dest_id}: unknown categories {sorted(unknown)}")
    rate = raw.get("rate_per_minute", 20)
    if not isinstance(rate, int) or not 1 <= rate <= 600:
        raise ValueError(f"destination {dest_id}: rate_per_minute must be 1-600")
    dest = Destination(
        id=dest_id, provider=provider, enabled=raw.get("enabled", True) is True,
        min_severity=str(raw.get("min_severity", "high")).lower(), categories=categories,
        exclude_categories=excluded, roles=_tuple(raw.get("roles"), "roles"),
        agents=_tuple(raw.get("agents"), "agents"),
        recipient=None if raw.get("recipient") is None else str(raw["recipient"]),
        secret_ref=raw.get("secret_ref"), digest_low=raw.get("digest_low", False) is True,
        rate_per_minute=rate, options=dict(raw.get("options") or {}),
    )
    if dest.enabled:
        REGISTRY[provider].validate_destination(dest)
    return dest


def parse(doc: dict, *, environ=None) -> Config:
    environ = os.environ if environ is None else environ
    raw_dests = doc.get("destinations", [])
    if not isinstance(raw_dests, list):
        raise ValueError("destinations must be a list")
    dests = tuple(_destination(d) for d in raw_dests)
    ids = [d.id for d in dests]
    if len(ids) != len(set(ids)):
        raise ValueError("destination ids must be unique")
    overrides = {}
    for category, sev in (doc.get("severity_overrides") or {}).items():
        if category not in CATEGORIES:
            raise ValueError(f"severity_overrides: unknown category {category!r}")
        overrides[category] = Severity.parse(sev)
    agg = doc.get("aggregation") or {}
    origin = environ.get("BURN_DASHBOARD_ORIGIN") or doc.get("dashboard_origin")
    return Config(
        destinations=dests, dashboard_url=dashboard_link(origin), severity_overrides=overrides,
        aggregation_window_seconds=int(agg.get("window_seconds", 300)),
        individual_per_window=int(agg.get("individual_per_window", 3)),
        digest_window_seconds=int((doc.get("digest") or {}).get("window_seconds", 3600)),
        delivery_ttl_days=int(doc.get("delivery_ttl_days", 14)),
        allow_env_secrets=environ.get("BURN_NOTIFY_ALLOW_ENV_SECRETS", "1") == "1",
    )


def load(environ=None) -> Config:
    environ = os.environ if environ is None else environ
    if environ.get("BURN_NOTIFY_CONFIG_JSON"):
        return parse(json.loads(environ["BURN_NOTIFY_CONFIG_JSON"]), environ=environ)
    path = environ.get("BURN_NOTIFY_CONFIG")
    if not path:
        raise ValueError("set BURN_NOTIFY_CONFIG (path) or BURN_NOTIFY_CONFIG_JSON")
    with open(path, encoding="utf-8") as fh:
        return parse(json.load(fh), environ=environ)


def route(dest: Destination, incident: Incident) -> str | None:
    """SEND, DIGEST, or None (this destination does not want this incident)."""
    if not dest.enabled:
        return None
    if dest.categories and incident.category not in dest.categories:
        return None
    if incident.category in dest.exclude_categories:
        return None
    if dest.roles and incident.role not in dest.roles:
        return None
    if dest.agents and incident.agent_id not in dest.agents:
        return None
    if incident.severity >= Severity.parse(dest.min_severity):
        return SEND
    if dest.digest_low and incident.severity == Severity.LOW:
        return DIGEST
    return None
