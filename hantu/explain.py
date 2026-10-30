"""Gemma explains and ranks ghosts. It never decides what is a ghost: scan.py already did."""

from __future__ import annotations

import json
import os
import plistlib
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from .scan import Agent

API_URL = "https://openrouter.ai/api/v1/chat/completions"
# Gemma 4 31B first (open weights), the smaller Gemma 4 as fallback. Which one ran is recorded.
DEFAULT_MODELS = ["google/gemma-4-31b-it:free", "google/gemma-4-26b-a4b-it:free"]
BATCH = 8
BACKOFF = [5, 15, 30, 60]  # seconds; free Gemma endpoints are often rate-limited upstream
MAX_PLIST_CHARS = 1200

SYSTEM = (
    "You explain macOS launchd plists to a non-expert. You are given ghosts: launch agents that the "
    "program has ALREADY classified as ORPHANED (program missing) or HALF-GHOST (program present, owning "
    "app gone). Do not question that classification. For each ghost write ONE plain-English sentence "
    "(max 160 chars) saying what it probably belonged to and what it is still trying to do, and a risk "
    "score from 1 to 5 (5 = most worth the user's attention: runs at every login, restarts forever, "
    "runs as a privileged daemon, or a security/updater tool left unattended; 1 = harmless clutter) with a "
    "reason of max 120 chars. Never invent facts that are not in the plist. "
    'Reply with strict JSON only, no prose, exactly: {"items":[{"id":<int>,"explanation":"...","risk":<1-5>,'
    '"risk_reason":"..."}]}. One item per input id.'
)


@dataclass
class Explanation:
    explanation: str
    risk: int
    risk_reason: str


@dataclass
class ExplainResult:
    items: Dict[str, Explanation] = field(default_factory=dict)  # keyed by plist path
    model_used: List[str] = field(default_factory=list)
    problems: List[str] = field(default_factory=list)


class ExplainError(RuntimeError):
    pass


def load_key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY")
    if key:
        return key.strip()
    path = os.path.expanduser("~/.config/openrouter/credentials.json")
    try:
        with open(path) as f:
            data = json.load(f)
        keys = data.get("keys")
        if isinstance(keys, list) and keys:
            first = keys[0]
            return (first.get("key") if isinstance(first, dict) else first).strip()
        if data.get("api_key"):
            return str(data["api_key"]).strip()
    except (OSError, ValueError, AttributeError):
        pass
    raise ExplainError("no OpenRouter key: set OPENROUTER_API_KEY (or ~/.config/openrouter/credentials.json)")


def redact(text: str) -> str:
    """Strip the home directory / username before anything leaves the machine."""
    home = os.path.expanduser("~")
    text = text.replace(home, "~")
    return re.sub(r"/Users/[^/\s\"<]+", "~", text)


SENSITIVE_KEYS = ("UserName", "GroupName", "SessionCreate", "StandardInPath")


def _plist_text(a: Agent) -> str:
    """The plist as XML with env values masked and user/group dropped, home path stripped."""
    try:
        with open(a.path, "rb") as f:
            data = plistlib.load(f)
        if isinstance(data, dict):
            for k in SENSITIVE_KEYS:
                data.pop(k, None)
            env = data.get("EnvironmentVariables")
            if isinstance(env, dict):
                data["EnvironmentVariables"] = {k: "MASKED" for k in env}
            raw = plistlib.dumps(data).decode("utf-8", "replace")
        else:
            raw = ""
    except Exception:
        raw = ""
    raw = re.sub(r"<\?xml.*?\?>|<!DOCTYPE.*?>", "", raw, flags=re.S).strip()
    return redact(raw)[:MAX_PLIST_CHARS]


def build_messages(batch: List[Agent]) -> List[dict]:
    parts = []
    for i, a in enumerate(batch):
        parts.append(
            f"### id {i}\nclassification (by code): {a.verdict} — {a.reason}\n"
            f"age: {a.age_days} days since plist last modified\n"
            f"plist ({redact(os.path.basename(a.path))}):\n{_plist_text(a)}"
        )
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": "\n\n".join(parts)}]


def parse_response(text: str, n: int) -> Dict[int, Explanation]:
    """Strict-ish JSON parse + validation. Returns only valid, in-range, de-duplicated items."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in reply")
    data = json.loads(text[start : end + 1])
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise ValueError("missing 'items' list")
    out: Dict[int, Explanation] = {}
    for it in items:
        if not isinstance(it, dict):
            continue
        i, exp, risk, why = it.get("id"), it.get("explanation"), it.get("risk"), it.get("risk_reason")
        if isinstance(i, bool) or not isinstance(i, int) or not 0 <= i < n or i in out:
            continue
        if isinstance(risk, str) and risk.strip().isdigit():
            risk = int(risk)
        if isinstance(risk, bool) or not isinstance(risk, int) or not 1 <= risk <= 5:
            continue
        if not isinstance(exp, str) or not exp.strip():
            continue
        why = why.strip() if isinstance(why, str) else ""
        out[i] = Explanation(exp.strip()[:200], risk, why[:160])
    if not out:
        raise ValueError("no valid items")
    return out


def _post(model: str, messages: List[dict], key: str, timeout: int) -> str:
    body = json.dumps({"model": model, "messages": messages, "temperature": 0.2, "max_tokens": 3500}).encode()
    req = urllib.request.Request(API_URL, data=body, method="POST")
    req.add_header("Authorization", f"Bearer {key}")
    req.add_header("Content-Type", "application/json")
    req.add_header("X-Title", "hantu")
    req.add_header("HTTP-Referer", "https://github.com/edycutjong/hantu")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        payload = json.loads(r.read().decode())
    return (payload["choices"][0]["message"].get("content") or "")


def ask(
    messages: List[dict],
    n: int,
    key: str,
    models: List[str],
    *,
    post: Callable[[str, List[dict], str, int], str] = _post,
    sleep: Callable[[float], None] = time.sleep,
    retries: int = 4,
    timeout: int = 120,
):
    """Try each model in order; back off on 429/5xx; re-ask on invalid JSON. Returns (parsed, model)."""
    last = "no attempt"
    for model in models:
        for attempt in range(retries):
            try:
                return parse_response(post(model, messages, key, timeout), n), model
            except urllib.error.HTTPError as e:
                last = f"{model}: HTTP {e.code}"
                if e.code in (429, 500, 502, 503, 504):
                    sleep(BACKOFF[min(attempt, len(BACKOFF) - 1)])
                    continue
                break  # 4xx other than 429: this model id will not work, try the next one
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last = f"{model}: {type(e).__name__}"
                sleep(BACKOFF[min(attempt, len(BACKOFF) - 1)])
            except (ValueError, KeyError, IndexError, TypeError) as e:
                last = f"{model}: invalid reply ({e})"
    raise ExplainError(f"all models failed; last: {last}")


def explain(
    ghosts: List[Agent],
    *,
    key: Optional[str] = None,
    models: Optional[List[str]] = None,
    post: Callable[[str, List[dict], str, int], str] = _post,
    sleep: Callable[[float], None] = time.sleep,
) -> ExplainResult:
    key = key or load_key()
    models = models or DEFAULT_MODELS
    res = ExplainResult()
    for off in range(0, len(ghosts), BATCH):
        batch = ghosts[off : off + BATCH]
        try:
            parsed, model = ask(build_messages(batch), len(batch), key, models, post=post, sleep=sleep)
        except ExplainError as e:
            res.problems.append(f"batch {off // BATCH}: {e}")
            continue
        if model not in res.model_used:
            res.model_used.append(model)
        for i, ex in parsed.items():
            res.items[batch[i].path] = ex
        missing = len(batch) - len(parsed)
        if missing:
            res.problems.append(f"batch {off // BATCH}: {missing} item(s) dropped by validation")
    return res


def rank(ghosts: List[Agent], res: ExplainResult) -> List[Agent]:
    """Highest model risk first; unexplained ghosts last. Ties: runs at load, then older."""
    def key(a: Agent):
        ex = res.items.get(a.path)
        return (-(ex.risk if ex else 0), not a.run_at_load, -a.age_days)
    return sorted(ghosts, key=key)
