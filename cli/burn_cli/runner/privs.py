"""Running the agent as an unprivileged user of its own.

When `burn run` is root (a container's PID 1), its environment holds the API token and the join token. An agent
started under the same uid could read them from /proc/<pid>/environ, so the agent is started as another user.

Every instance gets its own numeric uid from a reserved range, so one agent cannot read another's environment or
HOME either: a child never reads its parent's credentials. Popen accepts a numeric uid and gid, so no passwd entry
is needed.
"""
import os
import pwd
import random
import signal
import tempfile
from dataclasses import dataclass

UID_BASE, UID_COUNT = 100_000, 60_000  # the reserved range instance uids are drawn from


@dataclass(frozen=True)
class Drop:
    name: str
    uid: int
    gid: int
    claim: str | None = None  # the marker that keeps another instance from drawing the same uid

    def popen_args(self) -> dict:
        return {"user": self.uid, "group": self.gid, "extra_groups": []}


def _claim_dir() -> str:
    path = os.path.join("/run" if os.path.isdir("/run") else tempfile.gettempdir(), "burn-uids")
    os.makedirs(path, mode=0o700, exist_ok=True)
    info = os.stat(path)
    if info.st_uid != 0 or info.st_mode & 0o077:  # a pre-made directory could be someone else's
        raise ValueError(f"{path} must be owned by root and private")
    return path


def allocate(rng=random.SystemRandom()) -> Drop:
    """A uid (and gid) no other instance in this container holds. Claims are atomic directory creations."""
    base = _claim_dir()
    for _ in range(100):
        uid = UID_BASE + rng.randrange(UID_COUNT)
        marker = os.path.join(base, str(uid))
        try:
            os.mkdir(marker, 0o700)
        except FileExistsError:
            continue
        return Drop(f"burn-{uid}", uid, uid, marker)
    raise ValueError("no free instance uid")


def release(drop: Drop | None) -> None:
    if drop and drop.claim:
        try:
            os.rmdir(drop.claim)
        except OSError:
            pass


def kill_all(uid: int) -> None:
    """SIGKILL every process the uid owns, wherever it is in the process tree (Linux)."""
    try:
        entries = os.listdir("/proc")
    except OSError:
        return
    for entry in entries:
        if entry.isdigit():
            try:
                if os.stat(f"/proc/{entry}").st_uid == uid:
                    os.kill(int(entry), signal.SIGKILL)
            except OSError:
                pass


def resolve(user: str | None = None) -> Drop | None:
    """Who the agent runs as. None when `burn run` is not root: it cannot switch users, and says so in the docs.

    By default each instance gets a uid of its own. `--user NAME` runs it as that existing user instead, which every
    instance given the same name then shares.
    """
    if os.geteuid() != 0:
        if user:
            raise ValueError("--user needs burn run to be root")
        return None
    if not user:
        return allocate()
    try:
        entry = pwd.getpwnam(user)
    except KeyError:
        raise ValueError(f"user {user!r} does not exist") from None
    if entry.pw_uid == 0:
        raise ValueError(f"refusing to run the agent as {user!r}: that is root")
    return Drop(user, entry.pw_uid, entry.pw_gid)
