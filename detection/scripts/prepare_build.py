"""Create a small Docker context containing only non-ignored product files."""
from pathlib import Path
import shutil
import subprocess


def prepare(repo: Path, target: Path) -> None:
    files = subprocess.check_output(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard",
                                     "--", "contracts", "detection"], cwd=repo).decode().split("\0")
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, mode=0o700)
    for name in files:
        if not name:
            continue
        source = repo / name
        if source.is_symlink() or not source.is_file():
            continue
        path = target / name
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, path)


def main():
    repo = Path(__file__).resolve().parents[2]
    prepare(repo, repo / ".run" / "detection-build")
    print("Prepared .run/detection-build from non-ignored product files.")


if __name__ == "__main__":
    main()
