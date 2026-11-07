"""Configuration audit, not a live verification of remote access controls."""
from proxy.policy import AWS_HOSTS, Settings, host_of


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
        mode = "deny-all" if session.get("egress") == [] else "session"
        policy_valid = not any(settings.role_reason(h, session) == "role_policy_invalid" for h in hosts)
        hosts = {h for h in hosts if settings.role_reason(h, session) is None}
    for host in sorted(hosts):
        if host == host_of(settings.sink_url):
            readable, basis = True, "Configured simulated-attacker sink accepts outbound payloads."
        elif host in AWS_HOSTS:
            readable, basis = None, (
                "S3 requests are limited to ListBuckets; this configuration check does not verify upstream access or logging controls."
                if host.startswith("s3.") else
                "Authenticated AWS hostname alone does not prove all permitted service capabilities are inaccessible to outsiders."
            )
        else:
            readable, basis = None, "Configured application endpoint; remote authorization requires owner verification."
        rows.append({"host": host, "outsider_readable": readable, "basis": basis})
    readable = sum(r["outsider_readable"] is True for r in rows)
    unknown = sum(r["outsider_readable"] is None for r in rows)
    return {"scope": "local proxy configuration; not a live access-control audit",
            "destinations": rows, "outsider_readable": readable, "unverified": unknown,
            "mode": mode, "policy_valid": policy_valid, "configuration_only": True,
            "zero_readable_destinations": None if unknown else readable == 0,
            "usable_configuration": False if not hosts or not policy_valid else None,
            "target_met": False}
