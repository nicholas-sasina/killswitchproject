"""Measure actual handlers using local AWS emulation, never the deployed account.

Run from eval/: python -m probes references|canaries --source-root PATH --output PATH.
Install moto[dynamodb,secretsmanager,iam] in the local environment first.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


def run_probe(kind: str, source_root: Path, trials: int, *, negative_checks: bool = False) -> dict:
    if kind not in {"references", "canaries"} or trials < 1 or trials > 100:
        raise ValueError("Choose references or canaries and between 1 and 100 trials")
    command = [sys.executable, "-m", "probes.worker", kind, "--source-root", str(source_root.resolve()),
               "--trials", str(trials)]
    if negative_checks:
        if kind != "canaries":
            raise ValueError("negative checks are available only for canaries")
        command.append("--negative-checks")
    result = subprocess.run(command, cwd=Path(__file__).resolve().parents[1],
                            capture_output=True, text=True, timeout=300)
    if result.returncode:
        # Product exceptions or logs can contain private fixtures. Do not relay them.
        raise RuntimeError("Offline product probe failed; check the source checkout and moto dependency")
    evidence = json.loads(result.stdout)
    if not isinstance(evidence, dict) or not evidence.get("records"):
        raise RuntimeError("Product probe returned no observations")
    return evidence


def write_private(path: Path, evidence: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as output:
        json.dump(evidence, output, indent=2, allow_nan=False)
        output.write("\n")
    path.chmod(0o600)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("kind", choices=("references", "canaries"))
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--trials", type=int, default=5)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--negative-checks", action="store_true", help="also observe duplicate replay and injected rotation failure (canaries only)")
    args = parser.parse_args()
    if not 1 <= args.trials <= 100:
        parser.error("trials must be between 1 and 100")
    try:
        result = run_probe(args.kind, args.source_root, args.trials, negative_checks=args.negative_checks)
        write_private(args.output, result)
    except Exception as error:
        raise SystemExit(f"Offline probe did not complete ({type(error).__name__}); no successful measurement reported") from None
    print(json.dumps({"kind": args.kind, "scope": result["scope"], "source_commit": result["source_commit"],
                      "observations": len(result["records"]), "output": str(args.output)}))


if __name__ == "__main__":
    main()
