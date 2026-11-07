"""False positives: normal traffic, checked against a registry full of decoy credentials.

Share of payloads that produce any finding. A decoy registry is used so the
scanner has real fingerprints to (wrongly) match against.
"""
from burn_detect import Scanner, SessionWindow

from payloads.benign import generate
from payloads.credentials import Registry


def run(trials: int = 10) -> dict:
    flagged: dict[str, int] = {}
    names: list[str] = []
    runs = 0
    for _ in range(trials):
        registry = Registry()
        for _ in range(5):          # decoys: registered, but never sent anywhere
            registry.fresh_set()
        scanner = Scanner(key=registry.key, lookup=registry.lookup)
        window = SessionWindow()   # one session sees all the normal traffic
        for name, payload in generate():
            if name not in names:
                names.append(name)
            runs += 1
            findings = scanner.scan(payload, window=window)
            if findings:
                flagged[name] = flagged.get(name, 0) + 1
    total_flagged = sum(flagged.values())
    return {
        "trials": trials,
        "payload_types": len(names),
        "total_payload_runs": runs,
        "flagged": total_flagged,
        "false_positive_rate": total_flagged / runs,
        "flagged_by_payload": flagged,
    }
