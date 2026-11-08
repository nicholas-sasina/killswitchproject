"""Presets for well-known harnesses: what a command needs in its new HOME to work under `burn run` at once.

`burn run` gives every agent an empty HOME and strips the host's environment, so a harness such as Claude Code
starts with no model, no sign-in and no Burn tools, and asks its first-run questions every time. A preset fixes
that for one harness, chosen by the command's basename: it adds non-secret environment variables and writes
files into the instance's new HOME before the command starts. It never reads or writes the user's own HOME, and
it never writes a secret: a file names the variables that hold one (`${BURN_LOCAL_TOKEN}`) and the harness
expands them. A command without a preset runs exactly as it would without this module.
"""
import json
import os
from dataclasses import dataclass
from typing import Callable

# The one model the stack lets an instance's own AWS key call, and where.
BEDROCK_MODEL = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
BEDROCK_REGION = "us-east-1"


@dataclass(frozen=True)
class Preset:
    name: str
    summary: str  # one line for burn run's stderr: what the preset set up
    env: dict  # non-secret variables; burn run's own values, and --allow-env pass-throughs, win over these
    files: Callable[[str], dict]  # the agent's working directory -> {path relative to HOME: JSON content}
    needs_aws_key: bool = False  # the model is called with the instance's own AWS key


def _claude_files(cwd: str) -> dict:
    burn = {"type": "http", "url": "${BURN_MCP_URL}", "headers": {"Authorization": "Bearer ${BURN_LOCAL_TOKEN}"}}
    return {
        # User scope: Claude Code connects to these without the approval .mcp.json servers need, and expands
        # ${VAR} in url and headers, so the per-run token stays in the environment and out of the file.
        ".claude.json": {"hasCompletedOnboarding": True,  # no theme picker or welcome screens
                         "projects": {cwd: {"hasTrustDialogAccepted": True}},  # no folder-trust prompt here
                         "mcpServers": {"burn": burn}},
        # The role already decides which of Burn's tools the instance may call; `-p` would deny them all otherwise.
        ".claude/settings.json": {"permissions": {"allow": ["mcp__burn"]}},
    }


CLAUDE = Preset(
    name="claude",
    summary=f"Claude Code on Bedrock ({BEDROCK_MODEL}, the instance's own AWS key) with Burn's tools",
    env={
        "CLAUDE_CODE_USE_BEDROCK": "1",
        "AWS_REGION": BEDROCK_REGION,
        "ANTHROPIC_MODEL": BEDROCK_MODEL,
        # Every alias resolves to the one allowed model, so a subagent or /model never asks Bedrock for another.
        # ANTHROPIC_SMALL_FAST_MODEL is the deprecated name of ANTHROPIC_DEFAULT_HAIKU_MODEL; older versions read it.
        "ANTHROPIC_SMALL_FAST_MODEL": BEDROCK_MODEL,
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": BEDROCK_MODEL,
        "ANTHROPIC_DEFAULT_SONNET_MODEL": BEDROCK_MODEL,
        "ANTHROPIC_DEFAULT_OPUS_MODEL": BEDROCK_MODEL,
        # No auto-update into a HOME that is thrown away at exit, and no telemetry the egress proxy would refuse.
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        # Burn's few tools go straight into the request. Behind tool search, Haiku on Bedrock found them but
        # could not call them.
        "ENABLE_TOOL_SEARCH": "false",
    },
    files=_claude_files,
    needs_aws_key=True,
)

PRESETS = {CLAUDE.name: CLAUDE}


def preset(command: list[str]) -> Preset | None:
    """The preset for a command, by its basename (`claude`, `/usr/local/bin/claude`), or None."""
    return PRESETS.get(os.path.basename(command[0])) if command else None


def write_files(preset: Preset, home: str, cwd: str) -> list[str]:
    """Write the preset's files into a new HOME, before the agent starts; returns the paths made, parents first.

    Directories are 0700 and files 0600. Nothing is overwritten and no link is followed: the HOME is new, so
    anything already at a path means something else put it there.
    """
    made = []
    for relative, content in preset.files(cwd).items():
        parent = home
        for part in filter(None, os.path.dirname(relative).split("/")):
            parent = os.path.join(parent, part)
            if not os.path.lexists(parent):
                os.mkdir(parent, 0o700)
                made.append(parent)
        path = os.path.join(home, relative)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(content, f, indent=2)
        made.append(path)
    return made
