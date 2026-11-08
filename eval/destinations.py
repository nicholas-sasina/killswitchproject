"""Audit declared proxy settings; this never verifies remote access controls.

Run from eval/: python destinations.py --settings-json out/private-settings.json
    [--session-json out/private-egress.json] [--output out/destinations.json]
"""
import argparse
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from urllib.parse import urlsplit

from proxy.policy import AWS_HOSTS, Settings, host_of
from proxy.policy import _valid_host

OUT = Path(__file__).resolve().parent / "out"
SETTING_KEYS = frozenset({"gateway_url", "provider_url", "cognito_token_url", "sink_url"})
SCOPE = "declared proxy configuration; not a live access-control audit"
SINK_BASIS = "Configured simulated-attacker sink accepts outbound payloads."
S3_BASIS = "S3 requests are limited to ListBuckets; this configuration check does not verify upstream access or logging controls."
AWS_BASIS = "Authenticated AWS hostname alone does not prove all permitted service capabilities are inaccessible to outsiders."
APP_BASIS = "Configured application endpoint; remote authorization requires owner verification."


def audit(settings: Settings, session: dict | None = None):
    """Count effective configured hosts without declaring remote services safe.

    A deny-all policy has no readable destination, but is not a usable agent
    configuration. Unknown remote capabilities prevent certifying the target.
    """
    rows = []
    hosts = settings.allowed_hosts
    policy_valid = True
    mode = "deployment"
    if session is not None:
        mode = ("gateway-only" if settings.gateway_url else "deny-all") if session.get("egress") == [] else "session"
        policy_valid = not any(settings.role_reason(h, session) == "role_policy_invalid" for h in hosts)
        hosts = {h for h in hosts if settings.role_reason(h, session) is None}
    for host in sorted(hosts):
        if host == host_of(settings.sink_url):
            readable, basis = True, SINK_BASIS
        elif host in AWS_HOSTS:
            readable, basis = None, (
                S3_BASIS
                if host.startswith("s3.") else
                AWS_BASIS
            )
        else:
            readable, basis = None, APP_BASIS
        rows.append({"host": host, "outsider_readable": readable, "basis": basis})
    readable = sum(r["outsider_readable"] is True for r in rows)
    unknown = sum(r["outsider_readable"] is None for r in rows)
    return {"scope": "local proxy configuration; not a live access-control audit",
            "destinations": rows, "outsider_readable": readable, "unverified": unknown,
            "mode": mode, "policy_valid": policy_valid, "configuration_only": True,
            "zero_readable_destinations": None if unknown else readable == 0,
            "usable_configuration": False if not hosts or not policy_valid else None,
            "target_met": False}


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _constant(_value):
    raise ValueError("nonstandard JSON constant")


def _read_json(path):
    with path.open("rb") as handle:
        raw = handle.read(65_537)
    if len(raw) > 65_536:
        raise ValueError("private configuration is too large")
    data = json.loads(raw, object_pairs_hook=_object, parse_constant=_constant)
    return data, hashlib.sha256(raw).hexdigest()


def _settings(data):
    if not isinstance(data, dict) or set(data) != SETTING_KEYS:
        raise ValueError("settings must contain exactly the supported URL fields")
    for value in data.values():
        if not isinstance(value, str):
            raise ValueError("setting values must be strings")
        if value == "":
            continue
        if len(value) > 2_048 or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError("invalid endpoint URL")
        parsed = urlsplit(value)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment or "?" in value or "#" in value):
            raise ValueError("endpoint URLs cannot carry authentication, queries or fragments")
        if parsed.port is not None and not 1 <= parsed.port <= 65_535:
            raise ValueError("invalid endpoint port")
        try:
            ipaddress.ip_address(parsed.hostname)
        except ValueError:
            if not _valid_host(parsed.hostname):
                raise ValueError("invalid endpoint host") from None
    return Settings(**data)


def _session(data):
    if not isinstance(data, dict) or set(data) != {"egress"}:
        raise ValueError("session file must contain only the egress projection")
    hosts = data["egress"]
    if hosts is not None and (
        not isinstance(hosts, list) or any(not _valid_host(host) for host in hosts)
    ):
        raise ValueError("invalid session egress list")
    return data


def _revision():
    root = Path(__file__).resolve().parent
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root,
                                         text=True, stderr=subprocess.DEVNULL).strip()
        dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=root,
                                            text=True, stderr=subprocess.DEVNULL).strip())
    except (OSError, subprocess.CalledProcessError):
        return None, None
    return commit, dirty


def declared_audit(settings_path: Path, session_path: Path | None = None):
    data, settings_hash = _read_json(settings_path)
    settings = _settings(data)
    session, session_hash = None, None
    if session_path is not None:
        data, session_hash = _read_json(session_path)
        session = _session(data)
    result = audit(settings, session)
    commit, dirty = _revision()
    result["scope"] = SCOPE
    result["provenance"] = {
        "kind": "declared_configuration", "schema_version": 1,
        "evaluator_commit": commit, "working_tree_dirty": dirty,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "settings_sha256": settings_hash, "session_sha256": session_hash,
        "session_provided": session_path is not None, "network_requests": False,
    }
    return result


def load_audit(path: Path):
    """Validate and project only sanitized declared-audit evidence into reports."""
    data, _ = _read_json(path)
    fields = {"scope", "destinations", "outsider_readable", "unverified", "mode", "policy_valid",
              "configuration_only", "zero_readable_destinations", "usable_configuration", "target_met", "provenance"}
    provenance_fields = {"kind", "schema_version", "evaluator_commit", "working_tree_dirty", "generated_at",
                         "settings_sha256", "session_sha256", "session_provided", "network_requests"}
    if not isinstance(data, dict) or set(data) != fields or data["scope"] != SCOPE:
        raise ValueError("invalid declared-audit schema")
    provenance = data["provenance"]
    if not isinstance(provenance, dict) or set(provenance) != provenance_fields:
        raise ValueError("invalid audit provenance")
    if (provenance["kind"] != "declared_configuration" or type(provenance["schema_version"]) is not int
        or provenance["schema_version"] != 1 or provenance["network_requests"] is not False
        or type(provenance["session_provided"]) is not bool
        or provenance["working_tree_dirty"] is not None and type(provenance["working_tree_dirty"]) is not bool):
        raise ValueError("invalid audit provenance types")
    commit = provenance["evaluator_commit"]
    if commit is not None and (not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit)):
        raise ValueError("invalid evaluator revision")
    if not isinstance(provenance["generated_at"], str) or len(provenance["generated_at"]) > 80:
        raise ValueError("invalid audit time")
    stamp = datetime.fromisoformat(provenance["generated_at"])
    if stamp.tzinfo is None:
        raise ValueError("audit time requires timezone")
    for key in ("settings_sha256", "session_sha256"):
        value = provenance[key]
        if key == "session_sha256" and not provenance["session_provided"]:
            if value is not None:
                raise ValueError("unexpected session hash")
        elif not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError("invalid input digest")
    if (data["target_met"] is not False or data["configuration_only"] is not True
        or data["policy_valid"] is not True or not isinstance(data["mode"], str)
        or data["mode"] not in {"deployment", "session", "deny-all", "gateway-only"}
        or (data["mode"] == "deployment") == provenance["session_provided"]):
        raise ValueError("unsupported audit assertion")
    if not isinstance(data["destinations"], list):
        raise ValueError("invalid destinations")
    rows = []
    for row in data["destinations"]:
        if not isinstance(row, dict) or set(row) != {"host", "outsider_readable", "basis"}:
            raise ValueError("invalid destination row")
        host = row["host"]
        if not isinstance(host, str) or host != host.rstrip(".").lower():
            raise ValueError("invalid normalized host")
        try:
            ipaddress.ip_address(host)
        except ValueError:
            if not _valid_host(host):
                raise ValueError("invalid destination host") from None
        if row["outsider_readable"] is True:
            basis = SINK_BASIS
        elif row["outsider_readable"] is None:
            basis = S3_BASIS if host in {"s3.amazonaws.com", "s3.us-east-1.amazonaws.com"} else AWS_BASIS if host in AWS_HOSTS else APP_BASIS
        else:
            raise ValueError("remote capability certification is unsupported")
        if row["basis"] != basis:
            raise ValueError("invalid destination evidence basis")
        rows.append({"host": host, "outsider_readable": row["outsider_readable"], "basis": basis})
    hosts = [row["host"] for row in rows]
    if len(set(hosts)) != len(hosts) or data["mode"] == "deny-all" and rows:
        raise ValueError("inconsistent destination set")
    if data["mode"] == "gateway-only" and len(rows) != 1:
        raise ValueError("inconsistent Gateway-only audit")
    if data["mode"] == "deployment" and not AWS_HOSTS <= set(hosts):
        raise ValueError("deployment audit omits fixed endpoints")
    readable = sum(row["outsider_readable"] is True for row in rows)
    unknown = sum(row["outsider_readable"] is None for row in rows)
    expected_zero = None if unknown else readable == 0
    expected_usable = False if not rows or not data["policy_valid"] else None
    if (type(data["outsider_readable"]) is not int or data["outsider_readable"] != readable or readable > 1
        or type(data["unverified"]) is not int or data["unverified"] != unknown
        or data["zero_readable_destinations"] is not expected_zero
        or data["usable_configuration"] is not expected_usable):
        raise ValueError("inconsistent audit counts or usability")
    return {"scope": SCOPE, "destinations": sorted(rows, key=lambda row: row["host"]),
            "outsider_readable": readable, "unverified": unknown, "mode": data["mode"],
            "policy_valid": data["policy_valid"], "configuration_only": True,
            "zero_readable_destinations": expected_zero, "usable_configuration": expected_usable,
            "target_met": False, "provenance": {**{key: provenance[key] for key in provenance_fields},
                                               "generated_at": stamp.isoformat(timespec="seconds")}}


def _write_output(path: Path, result, inputs=()):
    destination = path.resolve()
    if not destination.is_relative_to(OUT.resolve()) or destination == OUT.resolve():
        raise ValueError("output must be within eval/out")
    if destination in {p.resolve() for p in inputs if p is not None}:
        raise ValueError("output cannot replace an input file")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        descriptor, temporary = tempfile.mkstemp(prefix=".destination-audit-", dir=destination.parent)
        with os.fdopen(descriptor, "w") as handle:
            json.dump(result, handle, indent=2, allow_nan=False)
            handle.write("\n")
        os.replace(temporary, destination)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Audit explicit private settings and optional session egress. No network calls.")
    parser.add_argument("--settings-json", type=Path, required=True,
                        help="Private JSON with exactly gateway_url, provider_url, cognito_token_url and sink_url; '' disables an endpoint.")
    parser.add_argument("--session-json", type=Path,
                        help="Private projection containing only egress (null or an exact-hostname list), never a full credential-bearing session.")
    parser.add_argument("--output", type=Path,
                        help="Write mode-600 sanitized JSON inside ignored eval/out; otherwise print JSON to stdout.")
    args = parser.parse_args(argv)
    try:
        result = declared_audit(args.settings_json, args.session_json)
        if args.output is None:
            print(json.dumps(result, indent=2, allow_nan=False))
        else:
            _write_output(args.output, result, (args.settings_json, args.session_json))
    except (OSError, ValueError, TypeError, KeyError):
        # Private paths, field names, URLs and parsing exceptions can contain secrets.
        parser.error("Invalid private audit configuration or output location; no input contents were emitted.")


if __name__ == "__main__":
    main()
