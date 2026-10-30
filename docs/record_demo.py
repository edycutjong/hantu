"""Record docs/demo.gif from REAL runs of hantu on the author's Mac. Read-only commands only.

    asciinema rec -f asciicast-v2 --headless --window-size 56x20 -c "python3 docs/record_demo.py drive" raw.cast
    python3 docs/record_demo.py trim raw.cast demo.cast      # cuts only the Gemma wait, which is labelled on screen
    agg --font-size 32 --line-height 1.35 --theme github-dark --last-frame-duration 4.5 demo.cast docs/demo.gif

`drive` prints its own "$ " prompt, types each command at a fixed pace, then runs it for real. The opening card is
built from a live `hantu scan --json` run made at that moment; nothing on screen is typed in by hand.
Set HANTU (default: `hantu` on PATH) and HANTU_MODEL (default: google/gemma-4-31b-it).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

HANTU = os.environ.get("HANTU", "hantu")
MODEL = os.environ.get("HANTU_MODEL", "google/gemma-4-31b-it")
WAIT = "\x1b]0;hantu-wait\x07"   # invisible marker: the gap after it is the model call
KEEP = 0.8                       # seconds of the model wait left in the clip
B, Y, D, R = "\x1b[1m", "\x1b[1;33m", "\x1b[1;32m", "\x1b[0m"
C = "\x1b[38;2;121;192;255m"      # light blue, ~9:1 on the dark theme (ANSI 36 renders ~3.7:1 there)
SHOW, HIDE = "\x1b[?25h", "\x1b[?25l"


def out(s: str) -> None:
    sys.stdout.write(s)
    sys.stdout.flush()


def clear() -> None:
    out("\x1b[2J\x1b[H")


def type_cmd(shown: str) -> None:
    out(f"{SHOW}{D}${R} ")
    time.sleep(0.35)
    for ch in shown:
        out(B + ch + R)
        time.sleep(0.035)
    time.sleep(0.25)
    out("\r\n" + HIDE)


def run(shown: str, argv: list, marker: bool = False) -> float:
    type_cmd(shown)
    if marker:
        out(WAIT)
    t = time.time()
    subprocess.run(argv, check=False)
    return time.time() - t


def card_numbers() -> dict:
    data = json.loads(subprocess.run([HANTU, "scan", "--json"], capture_output=True, text=True, check=True).stdout)
    ghosts = [a for a in data["agents"] if a["verdict"] in ("ORPHANED", "HALF-GHOST")]
    return {"total": data["summary"]["total"], "ghosts": len(ghosts),
            "root": sum(1 for a in ghosts if a["runs_as_root"]),
            "oldest": max((a["age_days"] for a in ghosts), default=0)}


def drive() -> None:
    log = {"started": time.strftime("%Y-%m-%d %H:%M:%S %z"), "model": MODEL}
    n = card_numbers()
    log["card"] = n
    out(HIDE)
    clear()
    helpers = f"{n['root']} root helpers" if n["root"] == n["ghosts"] else f"{n['ghosts']} helpers"
    out(f"\r\n" * 5 + f"  {B}hantu{R}  ·  ghosts in my Mac\r\n\r\n"
        f"  {Y}{helpers}{R} are still on disk.\r\n"
        f"  No installed app claims them.\r\n\r\n"
        f"  Oldest plist: {Y}{n['oldest']} days{R}.\r\n\r\n"
        f"  {C}{n['total']} launch plists scanned, read-only.{R}\r\n")
    time.sleep(3.0)

    clear()
    log["scan_s"] = round(run("hantu scan --ghosts --redact", [HANTU, "scan", "--ghosts", "--redact"]), 2)
    time.sleep(4.2)

    clear()
    shown = f"hantu explain --redact --model {MODEL}"
    secs = run(shown, [HANTU, "explain", "--redact", "--model", MODEL], marker=True)
    log["explain_s"] = round(secs, 2)
    out(f"{C}# ran live in {secs:.1f} s; clip trims the wait to {KEEP} s{R}\r\n")
    time.sleep(4.8)

    clear()
    out(f"\r\n" * 6 + f"  {B}hantu{R} is read-only by default.\r\n"
        f"  Nothing moves unless you add {B}--clean --yes{R}.\r\n\r\n"
        f"  {D}${R} {B}pipx install git+https://github.com/edycutjong/hantu{R}\r\n\r\n"
        f"  {Y}github.com/edycutjong/hantu{R}\r\n"
        f"  {C}MIT · Python stdlib · Gemma 4 via OpenRouter{R}\r\n")
    time.sleep(4.5)
    with open(os.environ.get("HANTU_LOG", os.devnull), "a") as f:
        f.write(json.dumps(log) + "\n")


def trim(src: str, dst: str, keep: float = KEEP) -> None:
    with open(src) as f:
        header, *events = [json.loads(l) for l in f if l.strip()]
    shift, cut_next, out_ev = max(events[0][0] - 0.05, 0.0), False, []  # drop pre-roll (the card's own scan)
    prev = events[0][0]
    for t, kind, data in events:
        if cut_next:  # first event after the marker: pull it (and everything after) forward
            gap = t - prev
            if gap > keep:
                shift += gap - keep
                header.setdefault("hantu_trim", []).append({"at": round(prev, 2), "cut_s": round(gap - keep, 2)})
            cut_next = False
        if WAIT in data:
            data = data.replace(WAIT, "")
            cut_next = True
        prev = t
        if data or kind != "o":
            out_ev.append([round(t - shift, 6), kind, data])
    with open(dst, "w") as f:
        f.write(json.dumps(header) + "\n")
        for e in out_ev:
            f.write(json.dumps(e) + "\n")
    print(json.dumps(header.get("hantu_trim", [])))


if __name__ == "__main__":
    if sys.argv[1:2] == ["drive"]:
        drive()
    elif sys.argv[1:2] == ["trim"] and len(sys.argv) == 4:
        trim(sys.argv[2], sys.argv[3])
    else:
        sys.exit(__doc__)
