"""Read-only scan of launchd plists. Code, not a model, decides what is orphaned."""

from __future__ import annotations

import glob
import os
import plistlib
import re
import shlex
import subprocess
import time
from dataclasses import asdict, dataclass, field
from typing import Callable, Dict, List, Optional, Set, Tuple

ORPHANED = "ORPHANED"      # the program launchd would start is gone
HALF_GHOST = "HALF-GHOST"  # program still on disk, but the app that owns it is gone
ALIVE = "ALIVE"
UNKNOWN = "UNKNOWN"        # could not prove either way (malformed, relative, unmounted...)

DEFAULT_DIRS: List[Tuple[str, str]] = [
    ("user", "~/Library/LaunchAgents"),
    ("system-agent", "/Library/LaunchAgents"),
    ("system-daemon", "/Library/LaunchDaemons"),
]

# Folders that hold *.app directly or one level down (vendor folders such as /Applications/Epson Software/).
APP_DIRS = ["/Applications", "~/Applications", "/System/Applications", "/System/Applications/Utilities"]
# Apps that are installed inside something that is not an app folder (e.g. macFUSE's filesystem bundle).
EXTRA_APP_GLOBS = ["/Library/Filesystems/*/Contents/Resources/*.app", "/Library/Application Support/*/*.app"]
# An index with fewer apps than this means we could not read the app folders; never guess from it.
MIN_APPS = 5

# launchd's own default PATH, used for bare command names.
LAUNCHD_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"
HELPER_DIR = "/Library/PrivilegedHelperTools/"

INTERPRETERS = {
    "sh", "bash", "zsh", "dash", "env", "python", "python3", "node", "ruby", "perl",
    "osascript", "php", "java", "open",
}
SHELLS = {"sh", "bash", "zsh", "dash"}


@dataclass
class Agent:
    path: str
    source: str
    label: Optional[str] = None
    program: Optional[str] = None       # Program or ProgramArguments[0], ~ expanded
    target: Optional[str] = None        # what we actually test for existence
    target_kind: Optional[str] = None   # "binary" | "script"
    target_exists: Optional[bool] = None
    app_bundle: Optional[str] = None    # outermost .app in the target path, if any
    app_exists: Optional[bool] = None
    associated_ids: List[str] = field(default_factory=list)
    associated_installed: Optional[bool] = None
    mtime: float = 0.0
    age_days: int = 0
    run_at_load: Optional[bool] = None
    keep_alive: Optional[bool] = None
    disabled: Optional[bool] = None
    team: Optional[str] = None
    loaded: Optional[bool] = None
    last_exit: Optional[int] = None
    verdict: str = UNKNOWN
    reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------- helpers

def _expand(p: str) -> str:
    return os.path.expanduser(p)


def _stat_state(path: str) -> Optional[bool]:
    """True = exists, False = definitely missing, None = cannot tell (permissions etc.)."""
    try:
        os.stat(path)
        return True
    except (FileNotFoundError, NotADirectoryError):
        return False
    except OSError:
        return None


def _bundle_of(path: str) -> Optional[str]:
    """Outermost *.app directory in an absolute path."""
    parts = path.split("/")
    for i, part in enumerate(parts):
        if part.endswith(".app") and len(part) > 4:
            return "/".join(parts[: i + 1])
    return None


def _which(name: str, path_var: str) -> Optional[str]:
    for d in path_var.split(":"):
        cand = os.path.join(_expand(d), name)
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return None


def _pathish(tok: str) -> bool:
    return tok.startswith("/") or tok.startswith("~")


INLINE_FLAGS = {"-c", "-e", "-E", "-m", "-p", "-pe", "-ne", "-i", "--eval", "--print", "--module"}
VALUE_FLAGS = {"-cp", "-classpath", "--class-path", "--module-path", "-r", "--require", "-I", "-W", "-C", "-x"}


def _script_target(base: str, args: List[str]) -> Optional[str]:
    """For interpreters, find the script they are asked to run. None = inline code or not sure."""
    rest = args[1:]
    if base in SHELLS:
        for i, tok in enumerate(rest):
            if re.fullmatch(r"-[a-z]*c", tok):
                if i + 1 >= len(rest):
                    return None
                try:
                    parts = shlex.split(rest[i + 1])
                except ValueError:
                    return None
                for w in parts:  # skip leading VAR=value words
                    if "=" in w and not _pathish(w):
                        continue
                    return _expand(w) if _pathish(w) else None
                return None
    skip = False
    for tok in rest:
        if skip:
            skip = False
            continue
        if base == "env" and "=" in tok and not _pathish(tok):
            continue
        if tok in INLINE_FLAGS:
            return None  # python -c / node -e / ruby -e ...: code, not a file
        if tok in VALUE_FLAGS:
            skip = True
            continue
        if tok.startswith("-"):
            continue
        if _pathish(tok):
            return _expand(tok)
        if base == "env":
            continue
        return None  # first positional is not a path (relative script, `-a Name`...): not sure
    return None


# ---------------------------------------------------------------- indexes

@dataclass
class AppIndex:
    ids: Set[str] = field(default_factory=set)       # CFBundleIdentifier of installed apps
    helpers: Set[str] = field(default_factory=set)   # privileged helper labels an installed app declares
    apps: int = 0                                    # how many Info.plists were readable


def _app_paths(app_dirs: Optional[List[str]]) -> List[str]:
    if app_dirs is None:
        dirs, extra = APP_DIRS, EXTRA_APP_GLOBS
    else:
        dirs, extra = app_dirs, []
    found: List[str] = []
    for d in dirs:
        d = glob.escape(_expand(d))
        found += glob.glob(os.path.join(d, "*.app")) + glob.glob(os.path.join(d, "*", "*.app"))
    for pat in extra:
        found += glob.glob(pat)
    return sorted(set(found))


def build_app_index(app_dirs: Optional[List[str]] = None) -> AppIndex:
    """What is installed, read from Info.plist files (no Spotlight needed)."""
    idx = AppIndex()
    for app in _app_paths(app_dirs):
        esc = glob.escape(app)
        plists = [os.path.join(app, "Contents", "Info.plist")]
        for sub in ("Library/LoginItems", "Helpers", "Applications"):
            plists += glob.glob(os.path.join(esc, "Contents", sub, "*.app", "Contents", "Info.plist"))
        for plist in plists:
            try:
                with open(plist, "rb") as f:
                    info = plistlib.load(f)
            except Exception:  # unreadable or non-plist Info.plist: skip that app
                continue
            idx.apps += 1
            bid = info.get("CFBundleIdentifier")
            if isinstance(bid, str):
                idx.ids.add(bid.lower())
            sm = info.get("SMPrivilegedExecutables")
            if isinstance(sm, dict):
                idx.helpers.update(k for k in sm if isinstance(k, str))
        # SMJobBless apps also ship the helper inside the bundle
        for h in glob.glob(os.path.join(esc, "Contents", "Library", "LaunchServices", "*")):
            idx.helpers.add(os.path.basename(h))
    return idx


def loaded_labels() -> Dict[str, Optional[int]]:
    """Read-only `launchctl list`: label -> last exit status. Empty dict if unavailable."""
    try:
        out = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    res: Dict[str, Optional[int]] = {}
    for line in out.splitlines()[1:]:
        cols = line.split("\t")
        if len(cols) == 3:
            try:
                res[cols[2]] = int(cols[1])
            except ValueError:
                res[cols[2]] = None
    return res


def codesign_team(path: str) -> Optional[str]:
    """TeamIdentifier from `codesign -dv`; None if unsigned or unavailable."""
    try:
        r = subprocess.run(["codesign", "-dv", "--verbose=2", path], capture_output=True, text=True, timeout=8)
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"^TeamIdentifier=(\S+)", r.stderr, re.M)
    if m and m.group(1) != "not":
        return m.group(1)
    return None


# ---------------------------------------------------------------- core

def classify(
    plist_path: str,
    source: str,
    index: Optional[AppIndex],
    *,
    now: Optional[float] = None,
    signer: Optional[Callable[[str], Optional[str]]] = codesign_team,
    loaded: Optional[Dict[str, Optional[int]]] = None,
) -> Agent:
    now = time.time() if now is None else now
    ag = Agent(path=plist_path, source=source)
    try:
        st = os.stat(plist_path)
        ag.mtime = st.st_mtime
        ag.age_days = max(0, int((now - st.st_mtime) // 86400))
    except OSError:
        pass
    try:
        with open(plist_path, "rb") as f:
            data = plistlib.load(f)
        if not isinstance(data, dict):
            raise ValueError("top level is not a dict")
    except Exception as e:  # malformed, unreadable, binary garbage
        ag.verdict, ag.reason = UNKNOWN, f"unreadable plist ({type(e).__name__})"
        return ag

    label = data.get("Label")
    ag.label = label if isinstance(label, str) else None
    ag.run_at_load = data.get("RunAtLoad") if isinstance(data.get("RunAtLoad"), bool) else None
    ka = data.get("KeepAlive")
    ag.keep_alive = bool(ka) if isinstance(ka, (bool, dict)) else None
    ag.disabled = data.get("Disabled") if isinstance(data.get("Disabled"), bool) else None
    ids = data.get("AssociatedBundleIdentifiers")
    if isinstance(ids, str):
        ids = [ids]
    ag.associated_ids = [i for i in ids if isinstance(i, str)] if isinstance(ids, list) else []
    if loaded is not None and ag.label and source != "system-daemon":
        ag.loaded = ag.label in loaded
        ag.last_exit = loaded.get(ag.label)

    args = data.get("ProgramArguments")
    args = [a for a in args if isinstance(a, str)] if isinstance(args, list) else []
    prog = data.get("Program") if isinstance(data.get("Program"), str) else None
    first = prog or (args[0] if args else None)
    if not first:
        if not data:
            ag.verdict, ag.reason = UNKNOWN, "empty stub plist (nothing to launch; vendors blank these on purpose)"
        elif data.get("BundleProgram"):
            ag.verdict, ag.reason = UNKNOWN, "BundleProgram (resolved inside the owning app by launchd)"
        else:
            ag.verdict, ag.reason = UNKNOWN, "no Program or ProgramArguments"
        return ag

    first = _expand(first)
    ag.program = first
    wd = data.get("WorkingDirectory") if isinstance(data.get("WorkingDirectory"), str) else None
    env = data.get("EnvironmentVariables") if isinstance(data.get("EnvironmentVariables"), dict) else {}

    if not first.startswith("/"):
        if "/" in first:
            if wd and _expand(wd).startswith("/"):
                first = os.path.normpath(os.path.join(_expand(wd), first))
                ag.program = first
            else:
                ag.verdict, ag.reason = UNKNOWN, "relative program path and no absolute WorkingDirectory"
                return ag
        else:
            found = _which(first, str(env.get("PATH", LAUNCHD_PATH)))
            if not found:
                ag.verdict, ag.reason = UNKNOWN, f"bare command '{first}' not found on launchd's PATH"
                return ag
            first = found
            ag.program = first

    # interpreter? then the thing it runs is what matters (the interpreter itself must exist too).
    base = os.path.basename(first)
    target, kind = first, "binary"
    if base in INTERPRETERS and _stat_state(first) is not False and args:
        script = _script_target(base, args)
        if script and script.startswith("/"):
            target, kind = script, "script"
    ag.target, ag.target_kind = target, kind
    ag.app_bundle = _bundle_of(target)

    state = _stat_state(target)
    ag.target_exists = state
    if ag.app_bundle:
        ag.app_exists = _stat_state(ag.app_bundle)

    if state is None:
        ag.verdict, ag.reason = UNKNOWN, "cannot stat target (permission denied?)"
        return ag
    if state is False:
        if kind == "script" and any(c in target for c in "*?[:"):
            ag.verdict, ag.reason = UNKNOWN, "script argument looks like a pattern or classpath, not a file"
            return ag
        if target.startswith("/Volumes/"):
            ag.verdict, ag.reason = UNKNOWN, "target is on /Volumes (volume may just be unmounted)"
            return ag
        ag.verdict = ORPHANED
        ag.reason = (
            f"{'script' if kind == 'script' else 'program'} missing"
            + (f"; owning app {os.path.basename(ag.app_bundle)} is gone" if ag.app_bundle and ag.app_exists is False else "")
        )
        return ag

    # target exists: is it a half-ghost?
    if kind == "binary" and signer:
        ag.team = signer(target)
    if index is not None and not (ag.app_bundle and ag.app_exists):
        if ag.associated_ids:
            if any(i.lower() in index.ids or i.lower().startswith("com.apple.") for i in ag.associated_ids):
                ag.associated_installed = True
            else:
                ag.associated_installed = False
                ag.verdict = HALF_GHOST
                ag.reason = "program still on disk, but no installed app matches " + ", ".join(ag.associated_ids)
                return ag
        elif target.startswith(HELPER_DIR) and os.path.basename(target) not in index.helpers:
            ag.associated_installed = False
            ag.verdict = HALF_GHOST
            ag.reason = "privileged helper still on disk, but no installed app declares it (SMPrivilegedExecutables)"
            return ag
    ag.verdict, ag.reason = ALIVE, "program exists"
    return ag


def scan(
    dirs: Optional[List[Tuple[str, str]]] = None,
    *,
    app_dirs: Optional[List[str]] = None,
    use_codesign: bool = True,
    use_launchctl: bool = True,
    now: Optional[float] = None,
    min_apps: int = MIN_APPS,
) -> List[Agent]:
    index: Optional[AppIndex] = build_app_index(app_dirs)
    if index is not None and index.apps < min_apps:
        index = None  # could not read the app folders: judge by missing programs only
    loaded = loaded_labels() if use_launchctl else None
    out: List[Agent] = []
    for source, d in dirs if dirs is not None else DEFAULT_DIRS:
        d = _expand(d)
        try:
            names = sorted(n for n in os.listdir(d) if n.endswith(".plist"))
        except OSError:
            continue
        for n in names:
            out.append(
                classify(os.path.join(d, n), source, index, now=now,
                         signer=codesign_team if use_codesign else None, loaded=loaded or None)
            )
    return out


def summarize(agents: List[Agent]) -> Dict[str, object]:
    counts: Dict[str, int] = {ORPHANED: 0, HALF_GHOST: 0, ALIVE: 0, UNKNOWN: 0}
    for a in agents:
        counts[a.verdict] += 1
    ghosts = [a for a in agents if a.verdict in (ORPHANED, HALF_GHOST)]
    oldest = max(ghosts, key=lambda a: a.age_days) if ghosts else None
    return {
        "total": len(agents),
        "counts": counts,
        "ghosts": len(ghosts),
        "oldest_ghost_days": oldest.age_days if oldest else None,
        "oldest_ghost_label": oldest.label if oldest else None,
    }
