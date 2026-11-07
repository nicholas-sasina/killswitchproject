"""Run disposable Docker containment tests and remove only their resources."""
from pathlib import Path
import shutil
import subprocess

from prepare_build import prepare


def main():
    root = Path(__file__).resolve().parents[2]
    prepare(root, root / ".run" / "detection-build")
    executable = ["docker", "compose"]
    if subprocess.run(executable + ["version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
        executable = [shutil.which("docker-compose") or "docker-compose"]
    command = executable + ["-p", "burn-detection-test", "-f", str(root / "detection" / "docker-compose.test.yml")]
    try:
        subprocess.run(command + ["down", "-v"], check=True)
        subprocess.run(command + ["up", "--build", "-d", "proxy", "sink"], check=True)
        subprocess.run(command + ["run", "--build", "--rm", "--no-deps", "test-client"], check=True)
    finally:
        subprocess.run(command + ["down", "-v"], check=True)


if __name__ == "__main__":
    main()
