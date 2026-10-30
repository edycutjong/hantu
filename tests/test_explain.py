import itertools
import json
import os
import urllib.error

import pytest

from hantu import explain as E
from hantu.scan import ORPHANED, Agent

from conftest import write_plist


def good(n, risk=3):
    return json.dumps({"items": [{"id": i, "explanation": f"thing {i}", "risk": risk, "risk_reason": "r"} for i in range(n)]})


def ghost(tmp_path, name="a", age=100, run_at_load=False):
    p = write_plist(tmp_path, f"{name}.plist", {"Label": name, "Program": f"{os.path.expanduser('~')}/gone/{name}"})
    return Agent(path=p, source="user", label=name, verdict=ORPHANED, reason="program missing", age_days=age, run_at_load=run_at_load)


# ---- validation: the model's JSON is untrusted --------------------------

def test_parse_valid_and_fenced():
    assert E.parse_response(good(2), 2)[1].explanation == "thing 1"
    fenced = "Sure!\n```json\n" + good(1) + "\n```"
    assert E.parse_response(fenced, 1)[0].risk == 3


@pytest.mark.parametrize("bad", ["", "no json here", "{}", '{"items": "x"}', '{"items": [{"id": 0, "risk": 3}]}'])
def test_parse_rejects_garbage(bad):
    with pytest.raises(ValueError):
        E.parse_response(bad, 1)


def test_parse_drops_invalid_items_keeps_valid():
    items = [
        {"id": 0, "explanation": "ok", "risk": 4, "risk_reason": "r"},
        {"id": 0, "explanation": "dup", "risk": 1, "risk_reason": "r"},    # duplicate id
        {"id": 5, "explanation": "out of range", "risk": 2},               # id not in batch
        {"id": 1, "explanation": "bad risk", "risk": 9},                   # risk out of 1..5
        {"id": 2, "explanation": "  ", "risk": 2},                         # blank text
        {"id": True, "explanation": "bool id", "risk": 2},                 # bool is not an id
        {"id": 3, "explanation": "str risk", "risk": "2", "risk_reason": 7},
        "junk",
    ]
    out = E.parse_response(json.dumps({"items": items}), 4)
    assert sorted(out) == [0, 3]
    assert out[0].explanation == "ok" and out[3].risk == 2 and out[3].risk_reason == ""


def test_parse_truncates_long_text():
    out = E.parse_response(json.dumps({"items": [{"id": 0, "explanation": "x" * 900, "risk": 1, "risk_reason": "y" * 900}]}), 1)
    assert len(out[0].explanation) == 200 and len(out[0].risk_reason) == 160


def test_model_cannot_change_the_verdict(tmp_path):
    a = ghost(tmp_path)
    reply = json.dumps({"items": [{"id": 0, "explanation": "this is fine", "risk": 1, "risk_reason": "r", "verdict": "ALIVE"}]})
    res = E.explain([a], key="k", post=lambda *x: reply)
    assert a.verdict == ORPHANED
    assert not hasattr(res.items[a.path], "verdict")


# ---- privacy ------------------------------------------------------------

def test_redact_strips_home_and_username():
    home = os.path.expanduser("~")
    assert home not in E.redact(f"{home}/Library/x and /Users/someoneelse/y")
    assert "someoneelse" not in E.redact("/Users/someoneelse/y")


def test_prompt_never_contains_home_path(tmp_path):
    msgs = E.build_messages([ghost(tmp_path)])
    text = json.dumps(msgs)
    assert os.path.expanduser("~") not in text
    assert "ORPHANED" in text and "classification (by code)" in text


# ---- transport: retry, backoff, fallback --------------------------------

def http_error(code):
    return urllib.error.HTTPError("u", code, "m", {}, None)


def test_retries_429_with_backoff_then_succeeds():
    calls, naps = [], []
    def post(model, *a):
        calls.append(model)
        if len(calls) < 3:
            raise http_error(429)
        return good(1)
    parsed, model = E.ask([], 1, "k", ["m1", "m2"], post=post, sleep=naps.append)
    assert model == "m1" and len(calls) == 3 and naps == [5, 15]


def test_falls_back_to_second_model_and_records_which():
    def post(model, *a):
        if model == "big":
            raise http_error(404)
        return good(1)
    parsed, model = E.ask([], 1, "k", ["big", "small"], post=post, sleep=lambda s: None)
    assert model == "small"


def test_invalid_json_is_asked_again():
    replies = iter(["I cannot do that", good(1)])
    parsed, model = E.ask([], 1, "k", ["m"], post=lambda *a: next(replies), sleep=lambda s: None)
    assert parsed[0].explanation == "thing 0"


def test_all_models_failing_raises_explain_error():
    with pytest.raises(E.ExplainError, match="all models failed"):
        E.ask([], 1, "k", ["a", "b"], post=lambda *a: (_ for _ in ()).throw(http_error(503)), sleep=lambda s: None, retries=2)


def test_explain_batches_and_reports_model(tmp_path, monkeypatch):
    monkeypatch.setattr(E, "BATCH", 2)
    ghosts = [ghost(tmp_path, f"g{i}") for i in range(5)]
    sizes = []
    def post(model, messages, key, timeout):
        n = messages[1]["content"].count("### id ")
        sizes.append(n)
        return good(n)
    res = E.explain(ghosts, key="k", models=["google/gemma-4-31b-it:free"], post=post, sleep=lambda s: None)
    assert sizes == [2, 2, 1]
    assert len(res.items) == 5 and res.model_used == ["google/gemma-4-31b-it:free"] and not res.problems


def test_explain_survives_a_failed_batch(tmp_path, monkeypatch):
    monkeypatch.setattr(E, "BATCH", 1)
    ghosts = [ghost(tmp_path, f"g{i}") for i in range(2)]
    replies = itertools.chain([good(1)], itertools.repeat("nope"))
    res = E.explain(ghosts, key="k", models=["m"], post=lambda *a: next(replies), sleep=lambda s: None)
    assert len(res.items) == 1 and any("batch 1" in p for p in res.problems)


def test_load_key_prefers_env(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENROUTER_API_KEY", " sk-env ")
    assert E.load_key() == "sk-env"
    monkeypatch.delenv("OPENROUTER_API_KEY")
    monkeypatch.setenv("HOME", str(tmp_path))
    with pytest.raises(E.ExplainError):
        E.load_key()
    cfg = tmp_path / ".config" / "openrouter"
    cfg.mkdir(parents=True)
    (cfg / "credentials.json").write_text(json.dumps({"keys": [{"name": "a", "key": "sk-file"}]}))
    assert E.load_key() == "sk-file"


# ---- ranking ------------------------------------------------------------

def test_rank_highest_risk_first_unexplained_last(tmp_path):
    a, b, c, d = (ghost(tmp_path, n, age=age, run_at_load=r) for n, age, r in
                  [("a", 10, False), ("b", 10, False), ("c", 500, False), ("d", 10, True)])
    res = E.ExplainResult(items={a.path: E.Explanation("x", 2, ""), b.path: E.Explanation("x", 5, ""), d.path: E.Explanation("x", 2, "")})
    assert [g.label for g in E.rank([a, b, c, d], res)] == ["b", "d", "a", "c"]


def test_plist_text_masks_env_values_and_drops_user(tmp_path):
    p = write_plist(tmp_path, "s.plist", {"Label": "l", "UserName": "alice", "Program": "/x",
                                          "EnvironmentVariables": {"API_TOKEN": "hunter2", "PATH": "/usr/bin"}})
    a = Agent(path=p, source="user", label="l", verdict=ORPHANED)
    txt = E._plist_text(a)
    assert "hunter2" not in txt and "alice" not in txt and "/usr/bin" not in txt
    assert "API_TOKEN" in txt and "MASKED" in txt


def test_post_parses_openrouter_reply(monkeypatch):
    import io
    import urllib.request
    seen = {}

    class Resp(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake(req, timeout):
        seen["auth"] = req.get_header("Authorization")
        seen["body"] = json.loads(req.data)
        return Resp(json.dumps({"choices": [{"message": {"content": "hello"}}]}).encode())
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    assert E._post("google/gemma-4-31b-it:free", [{"role": "user", "content": "x"}], "sk-k", 5) == "hello"
    assert seen["auth"] == "Bearer sk-k" and seen["body"]["model"] == "google/gemma-4-31b-it:free"


def test_network_errors_back_off_and_fall_back():
    def post(model, *a):
        if model == "a":
            raise urllib.error.URLError("down")
        return good(1)
    parsed, model = E.ask([], 1, "k", ["a", "b"], post=post, sleep=lambda s: None, retries=2)
    assert model == "b"
