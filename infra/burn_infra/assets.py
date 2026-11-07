"""Build Lambda code folders at synth time, without Docker.

Each Lambda is plain Python (stdlib + boto3, which the Lambda runtime
already has). We copy the lane's handler folder plus the shared packages
(burn_contracts, and burn_detect when asked) into infra/.build/<name>/ and
point CDK at that folder. Simple to explain, nothing to install.
"""
import pathlib
import shutil

REPO = pathlib.Path(__file__).resolve().parents[2]
BUILD = REPO / "infra" / ".build"

SHARED = {
    "burn_contracts": REPO / "contracts" / "python" / "burn_contracts",
    "burn_detect": REPO / "detection" / "burn_detect",
}


def lambda_asset(name: str, source_dir: str, shared: tuple[str, ...] = ("burn_contracts",)) -> str:
    """Return a folder containing source_dir's files plus the named shared packages.

    source_dir is relative to the repo root, e.g. "minter/lambdas/minter".
    """
    out = BUILD / name
    if out.exists():
        shutil.rmtree(out)
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", "tests", ".pytest_cache")
    shutil.copytree(REPO / source_dir, out, ignore=ignore)
    for package in shared:
        shutil.copytree(SHARED[package], out / package, ignore=ignore)
    return str(out)
