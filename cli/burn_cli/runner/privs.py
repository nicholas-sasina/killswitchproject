"""Running the agent as an unprivileged user.

When `burn run` is root (a container's PID 1), its environment holds the API token and the join token. An agent
started under the same uid could read them from /proc/<pid>/environ, so the agent is started as another user.
"""
import os
import pwd
from dataclasses import dataclass


@dataclass(frozen=True)
class Drop:
    name: str
    uid: int
    gid: int

    def popen_args(self) -> dict:
        return {"user": self.uid, "group": self.gid, "extra_groups": []}


def resolve(user: str | None = None) -> Drop | None:
    """Who the agent runs as. None when `burn run` is not root: it cannot switch users, and says so in the docs.

    The default is "agent" if that user exists, else "nobody".
    """
    if os.geteuid() != 0:
        if user:
            raise ValueError("--user needs burn run to be root")
        return None
    candidates = [user] if user else ["agent", "nobody"]
    for name in candidates:
        try:
            entry = pwd.getpwnam(name)
        except KeyError:
            continue
        if entry.pw_uid == 0:
            raise ValueError(f"refusing to run the agent as {name!r}: that is root")
        return Drop(name, entry.pw_uid, entry.pw_gid)
    raise ValueError(f"user {user!r} does not exist" if user else "neither 'agent' nor 'nobody' exists; pass --user")
