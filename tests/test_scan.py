import os

import pytest

from hantu import scan as S
from hantu.scan import ALIVE, HALF_GHOST, ORPHANED, UNKNOWN, AppIndex, classify

from conftest import NOW, make_exe, write_plist


def run(path, index=None, **kw):
    kw.setdefault("signer", None)
    return classify(path, "user", index if index is not None else AppIndex(), now=NOW, **kw)


# ---- the three verdicts -------------------------------------------------

def test_orphaned_program_missing(agents_dir, tmp_path):
    p = write_plist(agents_dir, "a.plist", {"Label": "com.x.gone", "ProgramArguments": [str(tmp_path / "nope" / "bin")]}, age_days=400)
    a = run(p)
    assert a.verdict == ORPHANED
    assert a.age_days == 400
    assert a.target_exists is False


def test_orphaned_names_the_missing_app(agents_dir, tmp_path):
    missing = str(tmp_path / "Applications" / "Old.app" / "Contents" / "MacOS" / "old")
    a = run(write_plist(agents_dir, "a.plist", {"Label": "com.old.agent", "Program": missing}))
    assert a.verdict == ORPHANED
    assert a.app_bundle.endswith("Old.app") and a.app_exists is False
    assert "Old.app is gone" in a.reason


def test_alive_program_exists(agents_dir, tmp_path):
    exe = make_exe(tmp_path / "bin" / "tool")
    a = run(write_plist(agents_dir, "a.plist", {"Label": "com.x.ok", "ProgramArguments": [exe], "RunAtLoad": True, "KeepAlive": True}))
    assert a.verdict == ALIVE
    assert a.run_at_load is True and a.keep_alive is True


def test_alive_inside_existing_app_even_if_ids_unknown(agents_dir, tmp_path):
    exe = make_exe(tmp_path / "Apps" / "Real.app" / "Contents" / "MacOS" / "real")
    a = run(write_plist(agents_dir, "a.plist", {"Label": "l", "Program": exe, "AssociatedBundleIdentifiers": ["com.nobody.has.this"]}),
            AppIndex(ids={"com.other"}))
    assert a.verdict == ALIVE and a.app_exists is True


def test_half_ghost_associated_app_not_installed(agents_dir, tmp_path):
    exe = make_exe(tmp_path / "support" / "helper")
    p = write_plist(agents_dir, "a.plist", {"Label": "com.v.helper", "Program": exe, "AssociatedBundleIdentifiers": ["com.v.app"]})
    assert run(p, AppIndex(ids={"com.other.app"})).verdict == HALF_GHOST
    assert run(p, AppIndex(ids={"com.v.app"})).verdict == ALIVE
    mixed = write_plist(agents_dir, "m.plist", {"Label": "l", "Program": exe, "AssociatedBundleIdentifiers": ["Com.V.App"]})
    assert run(mixed, AppIndex(ids={"com.v.app"})).verdict == ALIVE  # case-insensitive match
    assert classify(p, "user", None, now=NOW, signer=None).verdict == ALIVE  # no app index available: never guess


def test_apple_associated_ids_are_never_ghosts(agents_dir, tmp_path):
    exe = make_exe(tmp_path / "h")
    p = write_plist(agents_dir, "a.plist", {"Label": "l", "Program": exe, "AssociatedBundleIdentifiers": ["com.apple.Safari"]})
    assert run(p, AppIndex()).verdict == ALIVE


def test_half_ghost_privileged_helper_without_owner(agents_dir, tmp_path, monkeypatch):
    helpers = tmp_path / "PrivilegedHelperTools"
    monkeypatch.setattr(S, "HELPER_DIR", str(helpers) + "/")
    h = make_exe(helpers / "com.v.helper")
    p = write_plist(agents_dir, "a.plist", {"Label": "com.v.helper", "Program": h})
    assert run(p, AppIndex(helpers={"com.other.helper"})).verdict == HALF_GHOST
    assert run(p, AppIndex(helpers={"com.v.helper"})).verdict == ALIVE


# ---- awkward plists -----------------------------------------------------

def test_malformed_plist_is_unknown_not_a_crash(agents_dir):
    agents_dir.mkdir(parents=True)
    bad = agents_dir / "bad.plist"
    bad.write_bytes(b"<?xml version='1.0'?><plist><dict><key>Label</key>")
    a = run(str(bad))
    assert a.verdict == UNKNOWN and "unreadable" in a.reason


def test_non_dict_plist_is_unknown(agents_dir):
    p = write_plist(agents_dir, "list.plist", ["not", "a", "dict"])
    assert run(p).verdict == UNKNOWN


def test_empty_stub_and_missing_program(agents_dir):
    assert "stub" in run(write_plist(agents_dir, "e.plist", {})).reason
    a = run(write_plist(agents_dir, "n.plist", {"Label": "x"}))
    assert a.verdict == UNKNOWN and "no Program" in a.reason
    b = run(write_plist(agents_dir, "b.plist", {"Label": "x", "BundleProgram": "Contents/MacOS/x"}))
    assert b.verdict == UNKNOWN and "BundleProgram" in b.reason


def test_relative_path_without_workdir_is_unknown(agents_dir):
    a = run(write_plist(agents_dir, "r.plist", {"Label": "x", "ProgramArguments": ["bin/tool"]}))
    assert a.verdict == UNKNOWN and "relative" in a.reason


def test_relative_path_resolved_against_workdir(agents_dir, tmp_path):
    make_exe(tmp_path / "wd" / "bin" / "tool")
    p = write_plist(agents_dir, "r.plist", {"Label": "x", "WorkingDirectory": str(tmp_path / "wd"), "ProgramArguments": ["bin/tool"]})
    a = run(p)
    assert a.verdict == ALIVE and a.target == str(tmp_path / "wd" / "bin" / "tool")
    gone = write_plist(agents_dir, "g.plist", {"Label": "x", "WorkingDirectory": str(tmp_path / "wd"), "ProgramArguments": ["bin/missing"]})
    assert run(gone).verdict == ORPHANED


def test_bare_command_uses_launchd_path(agents_dir):
    assert run(write_plist(agents_dir, "a.plist", {"Label": "x", "ProgramArguments": ["sh"]})).verdict == ALIVE
    a = run(write_plist(agents_dir, "b.plist", {"Label": "x", "ProgramArguments": ["definitely-not-a-real-cmd-hantu"]}))
    assert a.verdict == UNKNOWN


def test_tilde_is_expanded(agents_dir, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    make_exe(tmp_path / "bin" / "mine")
    a = run(write_plist(agents_dir, "t.plist", {"Label": "x", "ProgramArguments": ["~/bin/mine"]}))
    assert a.verdict == ALIVE and a.target == str(tmp_path / "bin" / "mine")
    b = run(write_plist(agents_dir, "u.plist", {"Label": "x", "ProgramArguments": ["~/bin/gone"]}))
    assert b.verdict == ORPHANED


def test_missing_target_on_volumes_is_unknown(agents_dir):
    a = run(write_plist(agents_dir, "v.plist", {"Label": "x", "ProgramArguments": ["/Volumes/HantuTestDisk/bin/tool"]}))
    assert a.verdict == UNKNOWN and "Volumes" in a.reason


def test_program_key_wins_over_argv0(agents_dir, tmp_path):
    exe = make_exe(tmp_path / "real")
    a = run(write_plist(agents_dir, "p.plist", {"Label": "x", "Program": exe, "ProgramArguments": ["fake-argv0", "--flag"]}))
    assert a.verdict == ALIVE and a.program == exe


# ---- interpreters: judge the script, not /bin/sh ------------------------

def test_shell_c_script_missing_is_orphaned(agents_dir, tmp_path):
    p = write_plist(agents_dir, "s.plist", {"Label": "x", "ProgramArguments": ["/bin/sh", "-c", f"{tmp_path}/gone.sh --now"]})
    a = run(p)
    assert a.verdict == ORPHANED and a.target_kind == "script" and a.target == f"{tmp_path}/gone.sh"


def test_shell_c_inline_command_is_alive(agents_dir):
    a = run(write_plist(agents_dir, "s.plist", {"Label": "x", "ProgramArguments": ["/bin/sh", "-c", "echo hi"]}))
    assert a.verdict == ALIVE and a.target == "/bin/sh"


def test_interpreter_script_exists_and_missing(agents_dir, tmp_path):
    script = make_exe(tmp_path / "job.py")
    interp = make_exe(tmp_path / "bin" / "python3")
    ok = run(write_plist(agents_dir, "a.plist", {"Label": "x", "ProgramArguments": [interp, "-u", script]}))
    assert ok.verdict == ALIVE and ok.target == script
    gone = run(write_plist(agents_dir, "b.plist", {"Label": "x", "ProgramArguments": [interp, str(tmp_path / "old.py")]}))
    assert gone.verdict == ORPHANED


def test_interpreter_itself_missing_is_orphaned(agents_dir, tmp_path):
    p = write_plist(agents_dir, "i.plist", {"Label": "x", "ProgramArguments": [str(tmp_path / "gone" / "python3"), "/bin/sh"]})
    a = run(p)
    assert a.verdict == ORPHANED and a.target_kind == "binary"


def test_env_wrapper_skips_assignments(agents_dir, tmp_path):
    script = make_exe(tmp_path / "x.sh")
    p = write_plist(agents_dir, "e.plist", {"Label": "x", "ProgramArguments": ["/usr/bin/env", "FOO=bar", str(tmp_path / "nope.sh")]})
    assert run(p).verdict == ORPHANED
    q = write_plist(agents_dir, "f.plist", {"Label": "x", "ProgramArguments": ["/usr/bin/env", "FOO=bar", script]})
    assert run(q).verdict == ALIVE


# ---- extras: clock, launchctl, codesign, index, summary -----------------

def test_age_uses_plist_mtime(agents_dir, tmp_path):
    exe = make_exe(tmp_path / "t")
    assert run(write_plist(agents_dir, "a.plist", {"Label": "x", "Program": exe}, age_days=37)).age_days == 37


def test_loaded_state_and_last_exit(agents_dir, tmp_path):
    p = write_plist(agents_dir, "a.plist", {"Label": "com.x.l", "ProgramArguments": [str(tmp_path / "gone")]})
    a = run(p, loaded={"com.x.l": 78})
    assert a.loaded is True and a.last_exit == 78
    assert run(p, loaded={"other": 0}).loaded is False
    d = classify(p, "system-daemon", AppIndex(), now=NOW, signer=None, loaded={"com.x.l": 0})
    assert d.loaded is None  # daemons are not visible without root


def test_codesign_only_for_existing_binaries(agents_dir, tmp_path):
    calls = []
    signer = lambda path: calls.append(path) or "TEAM123"
    exe = make_exe(tmp_path / "t")
    a = classify(write_plist(agents_dir, "a.plist", {"Label": "x", "Program": exe}), "user", AppIndex(), now=NOW, signer=signer)
    assert a.team == "TEAM123" and calls == [exe]
    calls.clear()
    b = classify(write_plist(agents_dir, "b.plist", {"Label": "x", "Program": str(tmp_path / "gone")}), "user", AppIndex(), now=NOW, signer=signer)
    assert b.team is None and calls == []


def test_build_app_index_reads_bundle_ids_and_helpers(tmp_path):
    import plistlib
    app = tmp_path / "Apps" / "Foo.app"
    (app / "Contents" / "Library" / "LaunchServices").mkdir(parents=True)
    (app / "Contents" / "Library" / "LaunchServices" / "com.foo.shipped").write_text("x")
    with open(app / "Contents" / "Info.plist", "wb") as f:
        plistlib.dump({"CFBundleIdentifier": "com.Foo.App", "SMPrivilegedExecutables": {"com.foo.helper": "anchor apple"}}, f)
    bad = tmp_path / "Apps" / "Bad.app" / "Contents"
    bad.mkdir(parents=True)
    (bad / "Info.plist").write_text("garbage")
    idx = S.build_app_index([str(tmp_path / "Apps")])
    assert idx.ids == {"com.foo.app"}
    assert idx.helpers == {"com.foo.helper", "com.foo.shipped"}


def test_scan_walks_folders_and_summarizes(agents_dir, tmp_path):
    exe = make_exe(tmp_path / "ok")
    write_plist(agents_dir, "ok.plist", {"Label": "ok", "Program": exe})
    write_plist(agents_dir, "ghost.plist", {"Label": "ghost", "Program": str(tmp_path / "gone")}, age_days=900)
    (agents_dir / "notes.txt").write_text("ignored")
    found = S.scan([("user", str(agents_dir)), ("system-daemon", str(tmp_path / "does-not-exist"))],
                   app_dirs=[], use_codesign=False, use_launchctl=False, now=NOW)
    assert [a.label for a in found] == ["ghost", "ok"]
    sm = S.summarize(found)
    assert sm["total"] == 2 and sm["ghosts"] == 1 and sm["oldest_ghost_days"] == 900
    assert sm["counts"][ORPHANED] == 1 and sm["counts"][ALIVE] == 1


def test_scan_never_modifies_the_folder(agents_dir, tmp_path):
    write_plist(agents_dir, "ghost.plist", {"Label": "ghost", "Program": str(tmp_path / "gone")})
    before = {n: (os.stat(agents_dir / n).st_mtime, (agents_dir / n).read_bytes()) for n in os.listdir(agents_dir)}
    S.scan([("user", str(agents_dir))], app_dirs=[], use_codesign=False, use_launchctl=False)
    after = {n: (os.stat(agents_dir / n).st_mtime, (agents_dir / n).read_bytes()) for n in os.listdir(agents_dir)}
    assert before == after


# ---- false-positive guards (found in review) -----------------------------

def test_inline_interpreter_code_is_not_a_missing_script(agents_dir, tmp_path):
    interp = make_exe(tmp_path / "bin" / "python3")
    a = run(write_plist(agents_dir, "a.plist", {"Label": "x", "ProgramArguments": [interp, "-c", "import time; time.sleep(9)", str(tmp_path / "nope")]}))
    assert a.verdict == ALIVE and a.target == interp


def test_java_classpath_is_not_a_script(agents_dir, tmp_path):
    java = make_exe(tmp_path / "bin" / "java")
    a = run(write_plist(agents_dir, "a.plist", {"Label": "x", "ProgramArguments": [java, "-cp", "/opt/a/lib/*:/opt/b", "Main"]}))
    assert a.verdict == ALIVE
    b = run(write_plist(agents_dir, "b.plist", {"Label": "x", "ProgramArguments": [java, "-jar", str(tmp_path / "gone.jar")]}))
    assert b.verdict == ORPHANED and b.target.endswith("gone.jar")


def test_pattern_like_script_argument_is_unknown(agents_dir, tmp_path):
    sh = make_exe(tmp_path / "bin" / "node")
    a = run(write_plist(agents_dir, "a.plist", {"Label": "x", "ProgramArguments": [sh, "/opt/*/server.js"]}))
    assert a.verdict == UNKNOWN and "pattern" in a.reason


def test_relative_working_directory_is_unknown(agents_dir):
    a = run(write_plist(agents_dir, "a.plist", {"Label": "x", "WorkingDirectory": "rel", "ProgramArguments": ["bin/tool"]}))
    assert a.verdict == UNKNOWN


def test_installed_associated_app_beats_helper_rule(agents_dir, tmp_path, monkeypatch):
    helpers = tmp_path / "PrivilegedHelperTools"
    monkeypatch.setattr(S, "HELPER_DIR", str(helpers) + "/")
    h = make_exe(helpers / "com.v.helper")
    p = write_plist(agents_dir, "a.plist", {"Label": "com.v.helper", "Program": h, "AssociatedBundleIdentifiers": ["com.v.app"]})
    assert run(p, AppIndex(ids={"com.v.app"}, helpers=set())).verdict == ALIVE
    assert run(p, AppIndex(ids={"com.other"}, helpers={"com.v.helper"})).verdict == HALF_GHOST


def test_thin_app_index_never_makes_half_ghosts(agents_dir, tmp_path):
    exe = make_exe(tmp_path / "support" / "helper")
    write_plist(agents_dir, "a.plist", {"Label": "x", "Program": exe, "AssociatedBundleIdentifiers": ["com.v.app"]})
    kw = dict(app_dirs=[str(tmp_path / "NoApps")], use_codesign=False, use_launchctl=False, now=NOW)
    assert S.scan([("user", str(agents_dir))], **kw)[0].verdict == ALIVE            # index unreadable: do not guess
    assert S.scan([("user", str(agents_dir))], min_apps=0, **kw)[0].verdict == HALF_GHOST


def test_app_index_finds_vendor_folders_and_nested_helpers(tmp_path):
    import plistlib

    def app(path, bid, **extra):
        (path / "Contents").mkdir(parents=True)
        with open(path / "Contents" / "Info.plist", "wb") as f:
            plistlib.dump({"CFBundleIdentifier": bid, **extra}, f)
    root = tmp_path / "Apps"
    app(root / "Vendor Suite" / "Tool.app", "com.vendor.tool")
    app(root / "Main.app" / "Contents" / "Helpers" / "Helper.app", "com.main.helper")
    idx = S.build_app_index([str(root)])
    assert {"com.vendor.tool", "com.main.helper"} <= idx.ids and idx.apps == 2
