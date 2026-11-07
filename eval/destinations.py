"""Configuration audit, not a live verification of remote access controls."""
from proxy.policy import AWS_HOSTS, Settings, host_of


def audit(settings: Settings):
    rows = []
    for host in sorted(settings.allowed_hosts):
        if host == host_of(settings.sink_url):
            readable, basis = True, "Configured simulated-attacker sink accepts outbound payloads."
        elif host in AWS_HOSTS:
            readable, basis = False, "Fixed authenticated AWS endpoint; S3 is restricted to ListBuckets."
        else:
            readable, basis = None, "Configured application endpoint; remote authorization requires owner verification."
        rows.append({"host": host, "outsider_readable": readable, "basis": basis})
    readable = sum(r["outsider_readable"] is True for r in rows)
    unknown = sum(r["outsider_readable"] is None for r in rows)
    return {"scope": "local proxy configuration; not a live access-control audit",
            "destinations": rows, "outsider_readable": readable, "unverified": unknown,
            "target_met": readable == 0 and unknown == 0}
