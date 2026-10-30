import json
import os

from hantu import clean as C
from hantu.scan import ALIVE, HALF_GHOST, ORPHANED, AppIndex, classify, scan

from conftest import NOW, make_exe, write_plist


def agents_for(user_dir, tmp_path):
    exe = make_exe(tmp_path / "alive")
    write_plist(user_dir, "ghost.plist", {"Label": "com.x.ghost", "ProgramArguments": [str(tmp_path / "gone")]})
    write_plist(user_dir, "alive.plist", {"Label": "com.x.alive", "Program": exe})
    write_plist(user_dir, "half.plist", {"Label": "com.x.half", "Program": exe, "AssociatedBundleIdentifiers": ["com.x.noapp"]})
    return scan([("user", str(user_dir))], app_dirs=[], use_codesign=False, use_launchctl=False, now=NOW, min_apps=0)


def test_plan_only_orphaned_user_agents(tmp_path):
    user = tmp_path / "Library" / "LaunchAgents"
    agents = agents_for(user, tmp_path)
    assert {a.label: a.verdict for a in agents} == {"com.x.ghost": ORPHANED, "com.x.alive": ALIVE, "com.x.half": HALF_GHOST}
    acts = C.plan(agents, str(user), str(tmp_path / "q"))
    assert [a.label for a in acts] == ["com.x.ghost"]


def test_plan_ignores_system_sources_and_foreign_folders(tmp_path):
    user = tmp_path / "Library" / "LaunchAgents"
    agents = agents_for(user, tmp_path)
    for a in agents:
        a.source = "system-agent"
    assert C.plan(agents, str(user), "q") == []
    for a in agents:
        a.source = "user"
    assert C.plan(agents, str(tmp_path / "somewhere-else"), "q") == []


def test_plan_is_pure_nothing_touched(tmp_path):
    user = tmp_path / "Library" / "LaunchAgents"
    agents = agents_for(user, tmp_path)
    before = sorted(os.listdir(user))
    C.plan(agents, str(user), str(tmp_path / "q"))
    assert sorted(os.listdir(user)) == before and not (tmp_path / "q").exists()


def test_apply_moves_to_quarantine_never_deletes(tmp_path):
    user = tmp_path / "Library" / "LaunchAgents"
    agents = agents_for(user, tmp_path)
    q = tmp_path / "q"
    calls = []
    done = C.apply(C.plan(agents, str(user), str(q)), str(q), runner=lambda cmd: calls.append(list(cmd)) or 0)
    assert done[0].done
    assert not (user / "ghost.plist").exists()
    assert (q / "ghost.plist").exists()                       # recoverable
    assert (user / "alive.plist").exists() and (user / "half.plist").exists()
    assert calls == []                                        # never loaded -> no launchctl call
    manifest = json.loads((q / "manifest.json").read_text())
    assert manifest[0]["label"] == "com.x.ghost" and manifest[0]["done"] is True


def test_apply_boots_out_loaded_agents_first(tmp_path):
    user = tmp_path / "Library" / "LaunchAgents"
    p = write_plist(user, "g.plist", {"Label": "com.x.loaded", "ProgramArguments": [str(tmp_path / "gone")]})
    ag = classify(p, "user", AppIndex(), now=NOW, signer=None, loaded={"com.x.loaded": 78})
    q = tmp_path / "q"
    calls = []
    C.apply(C.plan([ag], str(user), str(q)), str(q), runner=lambda cmd: calls.append(list(cmd)) or 0)
    assert calls == [["launchctl", "bootout", f"gui/{os.getuid()}/com.x.loaded"]]
    assert (q / "g.plist").exists()


def test_apply_survives_launchctl_failure(tmp_path):
    user = tmp_path / "Library" / "LaunchAgents"
    p = write_plist(user, "g.plist", {"Label": "com.x.l", "ProgramArguments": [str(tmp_path / "gone")]})
    ag = classify(p, "user", AppIndex(), now=NOW, signer=None, loaded={"com.x.l": 1})
    q = tmp_path / "q"
    def boom(cmd):
        raise OSError("no launchctl")
    done = C.apply(C.plan([ag], str(user), str(q)), str(q), runner=boom)
    assert done[0].done and "bootout failed" in done[0].note


def test_apply_empty_plan_creates_nothing(tmp_path):
    q = tmp_path / "q"
    assert C.apply([], str(q)) == [] and not q.exists()


def test_failed_move_does_not_boot_out(tmp_path, monkeypatch):
    user = tmp_path / "Library" / "LaunchAgents"
    p = write_plist(user, "g.plist", {"Label": "com.x.l", "ProgramArguments": [str(tmp_path / "gone")]})
    ag = classify(p, "user", AppIndex(), now=NOW, signer=None, loaded={"com.x.l": 1})
    q = tmp_path / "q"
    calls = []
    monkeypatch.setattr(C.shutil, "move", lambda *a: (_ for _ in ()).throw(OSError("denied")))
    done = C.apply(C.plan([ag], str(user), str(q)), str(q), runner=lambda c: calls.append(c) or 0)
    assert not done[0].done and "move failed" in done[0].note and calls == []
