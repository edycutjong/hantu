"""Markdown reports. `redact=True` is publication-safe: counts, ages, <=3 well-known vendors."""

from __future__ import annotations

import os
import re
from typing import List, Optional

from .explain import ExplainResult, rank
from .scan import ALIVE, HALF_GHOST, ORPHANED, UNKNOWN, Agent, summarize

# Vendors we are comfortable naming in public examples. Anything else stays out of redacted output.
WELL_KNOWN = (
    "com.google.", "com.microsoft.", "com.adobe.", "com.dropbox.", "org.mozilla.", "com.spotify.",
    "com.docker.", "us.zoom.", "com.valvesoftware.", "com.logitech.", "com.jetbrains.", "com.skype.",
    "io.macfuse.", "com.xk72.", "com.oracle.", "com.teamviewer.", "com.parallels.", "com.vmware.", "com.nvidia.", "com.epicgames.",
)


def home_to_tilde(s: str) -> str:
    s = s.replace(os.path.expanduser("~"), "~")
    return re.sub(r"/Users/[^/\s]+", "~", s)


def _ghosts(agents: List[Agent]) -> List[Agent]:
    return [a for a in agents if a.verdict in (ORPHANED, HALF_GHOST)]


def public_examples(agents: List[Agent], n: int = 3) -> List[Agent]:
    cands = [a for a in _ghosts(agents) if a.label and a.label.lower().startswith(WELL_KNOWN)]
    cands.sort(key=lambda a: -a.age_days)
    seen, out = set(), []
    for a in cands:  # one example per vendor prefix keeps the list varied
        vendor = ".".join(a.label.split(".")[:2])
        if vendor not in seen:
            seen.add(vendor)
            out.append(a)
        if len(out) == n:
            break
    return out


def _median(xs: List[int]) -> Optional[int]:
    if not xs:
        return None
    s = sorted(xs)
    return s[len(s) // 2]


def render_markdown(
    agents: List[Agent],
    *,
    redact: bool = False,
    explained: Optional[ExplainResult] = None,
    examples: int = 3,
    title: str = "hantu scan",
) -> str:
    sm = summarize(agents)
    c = sm["counts"]
    ghosts = _ghosts(agents)
    by_src = {}
    for a in agents:
        by_src.setdefault(a.source, []).append(a)
    L = [f"# {title}", ""]
    L += [f"- Launch plists scanned: **{sm['total']}**"]
    L += [f"- ORPHANED (program gone): **{c[ORPHANED]}**",
          f"- HALF-GHOST (program present, no installed app claims it): **{c[HALF_GHOST]}**",
          f"- ALIVE: **{c[ALIVE]}**",
          f"- UNKNOWN (could not prove either way): **{c[UNKNOWN]}**"]
    if ghosts:
        ages = [a.age_days for a in ghosts]
        L += [f"- Oldest ghost: **{max(ages)} days** since its plist was last modified; median {_median(ages)} days"]
        root = sum(1 for a in ghosts if a.runs_as_root)
        if root:
            L += [f"- Ghosts that are launch daemons running as root (no UserName in the plist): **{root}**"]
        helpers = sum(1 for a in ghosts if (a.target or "").startswith("/Library/PrivilegedHelperTools/"))
        if helpers:
            L += [f"- Ghost programs sitting in /Library/PrivilegedHelperTools: **{helpers}**"]
        L += [f"- Ghosts set to run at load: {sum(1 for a in ghosts if a.run_at_load)}; KeepAlive: {sum(1 for a in ghosts if a.keep_alive)}"]
        known = [a for a in ghosts if a.loaded is not None]
        if known:
            failing = sum(1 for a in known if a.loaded and a.last_exit not in (0, None))
            L += [f"- Ghosts currently loaded in launchd: {sum(1 for a in known if a.loaded)} of {len(known)} user-session ghosts"
                  f" ({failing} with a non-zero last exit status)"]
    L += ["", "## By folder", "", "| Folder | Plists | Ghosts |", "|---|---|---|"]
    names = {"user": "~/Library/LaunchAgents", "system-agent": "/Library/LaunchAgents",
             "system-daemon": "/Library/LaunchDaemons"}
    for src, items in by_src.items():
        L.append(f"| {names.get(src, src)} | {len(items)} | {len(_ghosts(items))} |")

    if redact:
        ex = public_examples(agents, examples)
        L += ["", "## Examples (well-known vendors only; paths rewritten)", ""]
        if ex:
            for a in ex:
                L.append(f"- `{a.label}` — {a.verdict}, {a.age_days} days old — `{home_to_tilde(a.target or a.program or '?')}`")
                e = explained.items.get(a.path) if explained else None
                if e:
                    L.append(f"  - Gemma (risk {e.risk}/5): {home_to_tilde(e.explanation)}")
        else:
            L.append("- none of the ghosts belong to a vendor on the public allowlist")
        if explained and explained.model_used:
            L.append(f"\n_Explanations by {', '.join(explained.model_used)}; classification by code._")
        return "\n".join(L) + "\n"

    ordered = rank(ghosts, explained) if explained else sorted(ghosts, key=lambda a: -a.age_days)
    L += ["", "## Ghosts", ""]
    if not ordered:
        L.append("No ghosts found.")
    for a in ordered:
        L.append(f"### `{a.label or os.path.basename(a.path)}` — {a.verdict}")
        L.append(f"- plist: `{a.path}` ({a.age_days} days old)")
        L.append(f"- target: `{a.target}` ({a.reason})")
        flags = [n for n, v in (("RunAtLoad", a.run_at_load), ("KeepAlive", a.keep_alive)) if v]
        if flags:
            L.append(f"- {', '.join(flags)}")
        ex = explained.items.get(a.path) if explained else None
        if ex:
            L.append(f"- Gemma (risk {ex.risk}/5): {ex.explanation} _{ex.risk_reason}_")
        L.append("")
    if explained and explained.model_used:
        L.append(f"_Explanations by {', '.join(explained.model_used)}; classification by code._")
    return "\n".join(L).rstrip() + "\n"
