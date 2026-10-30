"""hantu: find launch agents for apps you already deleted. Read-only unless --clean --yes."""

from __future__ import annotations

import argparse
import json
import re
import shlex
import shutil
import sys
from typing import List, Optional

from . import __version__, clean as cleanmod
from .explain import ExplainError, explain, rank
from .report import home_to_tilde, public_examples, render_markdown
from .scan import ALIVE, HALF_GHOST, ORPHANED, UNKNOWN, Agent, scan, summarize

COLORS = {ORPHANED: "31", HALF_GHOST: "33", ALIVE: "32", UNKNOWN: "90"}
WITHHELD = "[label withheld]"
NARROW = 100  # below this many columns, stack each row instead of one long line


def _width() -> int:
    return shutil.get_terminal_size((120, 24)).columns


def _wrap(text: str, width: int, indent: str = "", hang: Optional[str] = None, slash: bool = True) -> List[str]:
    """Greedy wrap that may also break after '/', so long paths fold instead of overflowing."""
    lines, cur = [], indent
    for piece in re.split(r"(?<=[/ ])" if slash else r"(?<= )", text):
        if cur.strip() and len(cur) + len(piece.rstrip()) > width:
            lines.append(cur.rstrip())
            cur = indent if hang is None else hang
        cur += piece
    if cur.strip():
        lines.append(cur.rstrip())
    return lines or [indent.rstrip()]


def _paint(text: str, verdict: str, tty: bool) -> str:
    return f"\033[{COLORS[verdict]}m{text}\033[0m" if tty else text


def _do_scan(args) -> List[Agent]:
    dirs = [("custom", d) for d in args.dir] if args.dir else None
    return scan(dirs, app_dirs=args.app_dir or None, use_codesign=not args.no_codesign,
                use_launchctl=not args.no_launchctl and not args.dir)


def _print_table(agents: List[Agent], only_ghosts: bool, redact: bool = False, width: Optional[int] = None) -> None:
    tty = sys.stdout.isatty()
    width = width or _width()
    public = {a.path for a in public_examples(agents)} if redact else set()
    rows = [a for a in agents if not only_ghosts or a.verdict in (ORPHANED, HALF_GHOST)]
    rows.sort(key=lambda a: (a.verdict != ORPHANED, a.verdict != HALF_GHOST, -a.age_days))
    for a in rows:
        head = f"{_paint(a.verdict.ljust(10), a.verdict, tty)} {str(a.age_days).rjust(5)}d  "
        if redact and a.path not in public:  # label, path and reason can all name the app
            print(head + WITHHELD)
            continue
        label, target = a.label or "(no label)", a.target or a.program or "?"
        reason = a.reason if a.verdict != ALIVE else ""
        if redact:
            target, reason = home_to_tilde(target), home_to_tilde(reason)
        if width >= NARROW:
            print(f"{head}{label}  ->  {target}")
            if reason:
                print(f"{' ' * 18}{reason}")
        else:
            print(head + label)
            for line in _wrap(f"-> {target}", width, " " * 3, " " * 6) + (_wrap(reason, width, " " * 3) if reason else []):
                print(line)
    sm = summarize(agents)
    c = sm["counts"]
    counts = (f"{sm['total']} plists: {c[ORPHANED]} orphaned, {c[HALF_GHOST]} half-ghost, ",
              f"{c[ALIVE]} alive, {c[UNKNOWN]} unknown. ", "Nothing was changed.")
    print("\n" + ("".join(counts) if width >= NARROW else "\n".join(x.strip() for x in counts)))


def cmd_scan(args) -> int:
    agents = _do_scan(args)
    if args.json:
        json.dump({"summary": summarize(agents), "agents": [a.to_dict() for a in agents]}, sys.stdout, indent=2)
        print()
    else:
        _print_table(agents, args.ghosts, redact=args.redact)
    if args.clean:
        qdir = cleanmod.default_quarantine()
        acts = cleanmod.plan(agents, "~/Library/LaunchAgents", qdir)
        if not acts:
            print("\n--clean: nothing to do (only ORPHANED agents in ~/Library/LaunchAgents qualify).")
            return 0
        print(f"\n--clean plan ({len(acts)} agents; moved to {qdir}, never deleted):")
        for a in acts:
            print(f"  mv {shlex.quote(a.plist)} {shlex.quote(a.quarantine_to)}" + (f"  &&  {' '.join(a.bootout)}" if a.bootout else ""))
        if not args.yes:
            print("\nDry run. Re-run with --clean --yes to do it.")
            return 0
        done = cleanmod.apply(acts, qdir)
        print(f"\nQuarantined {sum(a.done for a in done)} of {len(done)}. Undo: mv {qdir}/*.plist back.")
    return 0


def cmd_explain(args) -> int:
    agents = _do_scan(args)
    ghosts = [a for a in agents if a.verdict in (ORPHANED, HALF_GHOST)]
    if not ghosts:
        print("No ghosts found. Nothing to explain.")
        return 0
    n_all = len(ghosts)
    # --redact: only what may be named in public leaves the machine
    ghosts = public_examples(agents) if args.redact else ghosts[: args.limit]
    if not ghosts:
        print(f"--redact: none of the {n_all} ghosts is on the public vendor list; nothing was sent.")
        return 0
    try:
        res = explain(ghosts, models=[args.model] if args.model else None)
    except ExplainError as e:
        print(f"hantu: {e}", file=sys.stderr)
        return 2
    ordered = rank(ghosts, res)
    if args.json:
        json.dump({"model_used": res.model_used, "problems": res.problems,
                   "ghosts": [{**a.to_dict(), "gemma": (res.items[a.path].__dict__ if a.path in res.items else None)}
                              for a in ordered]}, sys.stdout, indent=2)
        print()
        return 0
    clean = home_to_tilde if args.redact else (lambda t: t)
    width = _width()
    for a in ordered:
        ex = res.items.get(a.path)
        print(f"[{ex.risk if ex else '-'}/5] {a.verdict}  {a.label}  ({a.age_days}d)")
        for line in _wrap(clean(ex.explanation) if ex else "(no valid explanation)", max(width, 40), " " * 6):
            print(line)
    tail = [f"classification by code; explanation by {', '.join(res.model_used) or 'no model'}."]
    if args.redact:
        tail.append(f"--redact: sent {len(ghosts)} of {n_all} ghosts (public vendor list); "
                    f"{n_all - len(ghosts)} never left this Mac.")
    print()
    for t in tail:
        print("\n".join(_wrap(t, max(width, 40), slash=False)))
    for p in res.problems:
        print(f"note: {clean(p)}", file=sys.stderr)
    return 0


def cmd_report(args) -> int:
    agents = _do_scan(args)
    res = None
    if args.explain:
        ghosts = [a for a in agents if a.verdict in (ORPHANED, HALF_GHOST)][: args.limit]
        if args.redact:
            ghosts = public_examples(agents)  # only what the redacted report may name leaves the machine
        try:
            res = explain(ghosts)
        except ExplainError as e:
            print(f"hantu: {e}", file=sys.stderr)
    md = render_markdown(agents, redact=args.redact, explained=res)
    if args.out:
        with open(args.out, "w") as f:
            f.write(md)
    else:
        sys.stdout.write(md)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="hantu", description=__doc__)
    p.add_argument("--version", action="version", version=f"hantu {__version__}")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--dir", action="append", metavar="PATH", help="scan this folder instead of the three launchd folders (repeatable)")
    common.add_argument("--app-dir", action="append", metavar="PATH", help="where installed apps live (default /Applications etc.)")
    common.add_argument("--no-codesign", action="store_true", help="skip `codesign -dv` on existing programs")
    common.add_argument("--no-launchctl", action="store_true", help="skip the read-only `launchctl list`")
    sub = p.add_subparsers(dest="cmd")
    s = sub.add_parser("scan", parents=[common], help="read-only scan (default)")
    s.add_argument("--json", action="store_true")
    s.add_argument("--ghosts", action="store_true", help="only list ghosts")
    s.add_argument("--clean", action="store_true", help="plan to quarantine ORPHANED user agents (dry run unless --yes)")
    s.add_argument("--yes", action="store_true", help="with --clean: actually do it")
    s.add_argument("--redact", action="store_true", help="screen-safe: name only well-known vendors, home path as ~")
    s.set_defaults(fn=cmd_scan)
    e = sub.add_parser("explain", parents=[common], help="Gemma explains and ranks the ghosts")
    e.add_argument("--model", help="OpenRouter model id (default: Gemma 4 31B, then Gemma 4 26B)")
    e.add_argument("--limit", type=int, default=40)
    e.add_argument("--json", action="store_true")
    e.add_argument("--redact", action="store_true", help="send and show only well-known vendors (one per vendor)")
    e.set_defaults(fn=cmd_explain)
    r = sub.add_parser("report", parents=[common], help="Markdown report")
    r.add_argument("--md", action="store_true", help="(default) Markdown output")
    r.add_argument("--redact", action="store_true", help="publication-safe: counts, ages, well-known vendors only")
    r.add_argument("--explain", action="store_true", help="add Gemma explanations (with --redact: only for the public examples)")
    r.add_argument("--limit", type=int, default=40)
    r.add_argument("-o", "--out")
    r.set_defaults(fn=cmd_report)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0].startswith("-") and argv[0] not in ("-h", "--help", "--version"):
        argv = ["scan"] + argv
    args = build_parser().parse_args(argv)
    if not getattr(args, "fn", None):
        build_parser().print_help()
        return 0
    if getattr(args, "yes", False) and not getattr(args, "clean", False):
        print("hantu: --yes only means something with --clean", file=sys.stderr)
        return 2
    if getattr(args, "redact", False) and args.cmd in ("scan", "explain") and (
            getattr(args, "json", False) or getattr(args, "clean", False)):
        print("hantu: --redact is for the screen output; it does not combine with --json or --clean", file=sys.stderr)
        return 2
    return args.fn(args)
