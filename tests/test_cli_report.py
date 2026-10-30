import json
import os

import pytest

from hantu import cli, scan as S
from hantu.report import home_to_tilde, public_examples, render_markdown
from hantu.explain import Explanation, ExplainResult

from conftest import NOW, make_exe, write_plist


@pytest.fixture
def fixture_dir(tmp_path):
    d = tmp_path / "fx"
    exe = make_exe(tmp_path / "alive")
    write_plist(d, "ok.plist", {"Label": "com.ok.agent", "Program": exe})
    write_plist(d, "g1.plist", {"Label": "com.google.keystone.agent", "ProgramArguments": [str(tmp_path / "gone1")], "RunAtLoad": True}, age_days=800)
    write_plist(d, "g2.plist", {"Label": "com.google.other", "ProgramArguments": [str(tmp_path / "gone2")]}, age_days=300)
    write_plist(d, "g3.plist", {"Label": "net.secret.vendor", "ProgramArguments": [str(tmp_path / "gone3")]}, age_days=900)
    write_plist(d, "g4.plist", {"Label": "com.dropbox.updater", "ProgramArguments": [str(tmp_path / "gone4")]}, age_days=100)
    return d


def agents_of(fixture_dir):
    return S.scan([("user", str(fixture_dir))], app_dirs=[], use_codesign=False, use_launchctl=False, now=NOW)


def test_cli_scan_json(fixture_dir, capsys):
    assert cli.main(["scan", "--json", "--dir", str(fixture_dir), "--no-codesign", "--app-dir", str(fixture_dir)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["summary"]["counts"]["ORPHANED"] == 4 and out["summary"]["total"] == 5


def test_cli_default_command_is_read_only_scan(fixture_dir, capsys):
    assert cli.main(["--dir", str(fixture_dir), "--no-codesign"]) == 0
    out = capsys.readouterr().out
    assert "4 orphaned" in out and "Nothing was changed." in out


def test_cli_yes_without_clean_is_refused(capsys):
    assert cli.main(["scan", "--yes"]) == 2


def test_cli_clean_dry_run_then_yes(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    user = tmp_path / "Library" / "LaunchAgents"
    write_plist(user, "ghost.plist", {"Label": "com.x.ghost", "ProgramArguments": [str(tmp_path / "gone")]})
    monkeypatch.setattr(S, "DEFAULT_DIRS", [("user", "~/Library/LaunchAgents")])
    assert cli.main(["scan", "--clean", "--no-codesign", "--no-launchctl"]) == 0
    assert "Dry run" in capsys.readouterr().out
    assert (user / "ghost.plist").exists()                              # dry run touched nothing
    assert cli.main(["scan", "--clean", "--yes", "--no-codesign", "--no-launchctl"]) == 0
    assert not (user / "ghost.plist").exists()
    moved = list((tmp_path / ".hantu" / "quarantine").rglob("ghost.plist"))
    assert len(moved) == 1


def test_cli_clean_on_custom_dir_plans_nothing(fixture_dir, capsys):
    assert cli.main(["scan", "--dir", str(fixture_dir), "--clean", "--yes", "--no-codesign"]) == 0
    assert "nothing to do" in capsys.readouterr().out
    assert len(os.listdir(fixture_dir)) == 5


def test_cli_explain_without_key_fails_cleanly(fixture_dir, monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "nohome"))
    assert cli.main(["explain", "--dir", str(fixture_dir), "--no-codesign"]) == 2
    assert "no OpenRouter key" in capsys.readouterr().err


def test_redacted_report_is_publication_safe(fixture_dir, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))          # fixture programs live "under home"
    md = render_markdown(agents_of(fixture_dir), redact=True)
    assert "`~/gone1`" in md
    assert "net.secret.vendor" not in md                  # not on the vendor allowlist
    assert str(tmp_path) not in md
    assert "Oldest ghost: **900 days**" in md
    assert "`com.google.keystone.agent`" in md
    assert md.count("- `com.") <= 3                       # at most 3 named examples
    assert "ORPHANED (program gone): **4**" in md
    assert "running as root" not in md                     # user-folder agents are not root daemons


def test_public_examples_one_per_vendor_oldest_first(fixture_dir):
    ex = public_examples(agents_of(fixture_dir), 3)
    assert [a.label for a in ex] == ["com.google.keystone.agent", "com.dropbox.updater"]


def test_full_report_lists_ghosts_with_gemma_line(fixture_dir):
    agents = agents_of(fixture_dir)
    g = next(a for a in agents if a.label == "com.google.other")
    res = ExplainResult(items={g.path: Explanation("An updater left behind.", 4, "runs at boot")}, model_used=["google/gemma-4-31b-it:free"])
    md = render_markdown(agents, explained=res)
    assert "Gemma (risk 4/5): An updater left behind." in md
    assert md.index("com.google.other") < md.index("net.secret.vendor")   # ranked above unexplained
    assert "classification by code" in md


def test_home_to_tilde():
    assert home_to_tilde(os.path.expanduser("~") + "/Library/x") == "~/Library/x"
    assert home_to_tilde("/Users/anyone/Library/x") == "~/Library/x"


def test_redacted_report_shows_gemma_only_for_public_examples(fixture_dir):
    agents = agents_of(fixture_dir)
    items = {a.path: Explanation(f"about {a.label}", 3, "") for a in agents if a.verdict == "ORPHANED"}
    md = render_markdown(agents, redact=True, explained=ExplainResult(items=items, model_used=["google/gemma-4-31b-it:free"]))
    assert "about com.google.keystone.agent" in md
    assert "net.secret.vendor" not in md
    assert "Explanations by google/gemma-4-31b-it:free" in md


def test_cli_explain_prints_ranked_ghosts(fixture_dir, monkeypatch, capsys):
    def fake_explain(ghosts, models=None, **kw):
        items = {g.path: Explanation(f"about {g.label}", 5 if g.label == "net.secret.vendor" else 2, "") for g in ghosts}
        return ExplainResult(items=items, model_used=["google/gemma-4-31b-it:free"])
    monkeypatch.setattr(cli, "explain", fake_explain)
    assert cli.main(["explain", "--dir", str(fixture_dir), "--no-codesign"]) == 0
    out = capsys.readouterr().out
    assert out.index("[5/5]") < out.index("[2/5]")
    assert "classification by code; explanation by google/gemma-4-31b-it:free" in out


def test_report_explain_redact_sends_only_public_examples(fixture_dir, monkeypatch, tmp_path, capsys):
    sent = []
    def fake_explain(ghosts, **kw):
        sent.extend(g.label for g in ghosts)
        return ExplainResult(items={}, model_used=["m"])
    monkeypatch.setattr(cli, "explain", fake_explain)
    assert cli.main(["report", "--redact", "--explain", "--dir", str(fixture_dir), "--no-codesign"]) == 0
    assert "net.secret.vendor" not in sent and sent


def test_scan_redact_withholds_all_but_public_examples(fixture_dir, capsys):
    assert cli.main(["scan", "--ghosts", "--redact", "--dir", str(fixture_dir), "--no-codesign"]) == 0
    out = capsys.readouterr().out
    assert "com.google.keystone.agent" in out and "com.dropbox.updater" in out
    assert "net.secret.vendor" not in out and "com.google.other" not in out   # one per vendor
    assert out.count("[label withheld]") == 2
    assert "gone3" not in out and "gone2" not in out                           # withheld rows show no path
    assert "4 orphaned" in out and "Nothing was changed." in out


def test_scan_redact_rewrites_home(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    d = tmp_path / "Library" / "LaunchAgents"
    write_plist(d, "g.plist", {"Label": "com.google.keystone.agent", "ProgramArguments": [str(tmp_path / "gone")]})
    assert cli.main(["scan", "--redact", "--dir", str(d), "--no-codesign"]) == 0
    out = capsys.readouterr().out
    assert "~/gone" in out and str(tmp_path) not in out


def test_redact_refuses_json_and_clean(fixture_dir, capsys):
    assert cli.main(["scan", "--redact", "--json", "--dir", str(fixture_dir)]) == 2
    assert cli.main(["scan", "--redact", "--clean", "--dir", str(fixture_dir)]) == 2
    assert cli.main(["explain", "--redact", "--json", "--dir", str(fixture_dir)]) == 2


def test_narrow_terminal_stacks_rows_and_folds_paths(fixture_dir, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_width", lambda: 52)
    assert cli.main(["scan", "--ghosts", "--dir", str(fixture_dir), "--no-codesign"]) == 0
    out = capsys.readouterr().out
    assert "  ->  " not in out and "   -> " in out
    long_lines = [l for l in out.splitlines() if len(l) > 52 and "/" not in l]
    assert not long_lines                                  # only an unbreakable path segment may overflow
    assert "Nothing was changed." in out


def test_wrap_breaks_after_slash():
    assert cli._wrap("-> /Library/PrivilegedHelperTools/com.xk72.charles.ProxyHelper", 40, "   ") == [
        "   -> /Library/PrivilegedHelperTools/", "   com.xk72.charles.ProxyHelper"]
    assert cli._wrap("-> /Library/PrivilegedHelperTools/com.xk72.charles.ProxyHelper", 40, "   ", "      ")[1] == \
        "      com.xk72.charles.ProxyHelper"


def test_explain_redact_sends_and_shows_only_public_examples(fixture_dir, monkeypatch, capsys):
    sent = []
    def fake_explain(ghosts, models=None, **kw):
        sent.extend(g.label for g in ghosts)
        return ExplainResult(items={g.path: Explanation(f"about {g.label}", 3, "") for g in ghosts}, model_used=["m"])
    monkeypatch.setattr(cli, "explain", fake_explain)
    assert cli.main(["explain", "--redact", "--dir", str(fixture_dir), "--no-codesign"]) == 0
    out = capsys.readouterr().out
    assert sorted(sent) == ["com.dropbox.updater", "com.google.keystone.agent"]
    assert "net.secret.vendor" not in out and "com.google.other" not in out
    assert "sent 2 of 4 ghosts" in out and "2 never left this Mac" in out
