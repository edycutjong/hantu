"""The clean path. Dry-run by default; real action needs --clean --yes.

Scope is deliberately small: only ORPHANED agents in the user's own LaunchAgents folder. System
folders need root and are never touched. Files are moved to a quarantine folder, not deleted, so
every action can be undone with `mv`.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence

from .scan import ORPHANED, Agent

Runner = Callable[[Sequence[str]], int]


def _run(cmd: Sequence[str]) -> int:
    return subprocess.run(list(cmd), capture_output=True, timeout=20).returncode


@dataclass
class Action:
    label: Optional[str]
    plist: str
    quarantine_to: str
    bootout: Optional[List[str]]
    done: bool = False
    note: str = ""


def plan(agents: List[Agent], user_dir: str, qdir: str) -> List[Action]:
    user_dir = os.path.realpath(os.path.expanduser(user_dir))
    actions = []
    for a in agents:
        if a.verdict != ORPHANED or a.source != "user":
            continue
        if os.path.realpath(os.path.dirname(a.path)) != user_dir:
            continue
        uid = os.getuid()
        boot = ["launchctl", "bootout", f"gui/{uid}/{a.label}"] if a.label and a.loaded else None
        actions.append(Action(a.label, a.path, os.path.join(qdir, os.path.basename(a.path)), boot))
    return actions


def default_quarantine() -> str:
    return os.path.expanduser(os.path.join("~/.hantu/quarantine", time.strftime("%Y%m%d-%H%M%S")))


def apply(actions: List[Action], qdir: str, *, runner: Runner = _run) -> List[Action]:
    """Perform the plan. Caller must have checked --yes."""
    if not actions:
        return actions
    os.makedirs(qdir, exist_ok=True)
    for act in actions:
        try:
            shutil.move(act.plist, act.quarantine_to)
            act.done = True
        except OSError as e:
            act.note = f"move failed: {e}"
            continue  # leave a still-present plist loaded rather than half-clean it
        if act.bootout:
            try:
                runner(act.bootout)  # best effort: an agent that never loaded is fine
            except (OSError, subprocess.SubprocessError):
                act.note = "bootout failed (continuing)"
    with open(os.path.join(qdir, "manifest.json"), "w") as f:
        json.dump([a.__dict__ for a in actions], f, indent=2)
    return actions
