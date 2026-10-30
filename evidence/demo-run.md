# The demo clip, as recorded

`docs/demo.gif` (and `docs/demo.mp4`, 22.7 s) was recorded on 2026-10-31 at 06:53 WIB on the author's Mac with
[`docs/record_demo.py`](../docs/record_demo.py), from real runs of `hantu` 0.1.0 installed with `pip install git+https://github.com/edycutjong/hantu`
(pip freeze: `hantu @ ...@43c7583`).

- Commands run: `hantu scan --json` (builds the opening card; not shown, it has full labels),
  `hantu scan --ghosts --redact`, `hantu explain --redact --model google/gemma-4-31b-it`. All read-only. `--clean` was not run.
- Live timings: scan 1.5 s; explain 7.7 s (the scan inside it plus one Gemma call for the 2 public examples).
- One speed-up: the clip keeps 0.8 s of the explain wait and cuts the other 6.92 s. The clip says so on screen.
- Model: `google/gemma-4-31b-it` on OpenRouter (the free ids were rate-limited, as in [`gemma-run.md`](gemma-run.md)).
  Model output varies run to run; the two lines below are this take's.
- Redaction is the CLI's own `--redact` (tested): only Charles and one Adobe label are named; 3 ghosts are
  `[label withheld]` and their plists were not sent to the model. No frame was edited by hand.

## Everything the clip shows (colours stripped)

```text
  hantu  ·  ghosts in my Mac

  5 root helpers are still on disk.
  No installed app declares them.

  Oldest ghost: plist untouched for 1549 days.

  Live read-only scan of 58 launch plists.
$ hantu scan --ghosts --redact
HALF-GHOST  1549d  [label withheld]
HALF-GHOST  1188d  com.xk72.charles.ProxyHelper
   -> /Library/PrivilegedHelperTools/ (same name)
   privileged helper still on disk, but no installed app
   declares it (SMPrivilegedExecutables)
HALF-GHOST  1096d  [label withheld]
HALF-GHOST   695d  com.adobe.ARMDC.Communicator
   -> /Library/PrivilegedHelperTools/ (same name)
   privileged helper still on disk, but no installed app
   declares it (SMPrivilegedExecutables)
HALF-GHOST   695d  [label withheld]

58 plists: 0 orphaned, 5 half-ghost,
44 alive, 9 unknown.
Nothing was changed.
$ hantu explain --redact --model google/gemma-4-31b-it
[4/5] HALF-GHOST  com.xk72.charles.ProxyHelper  (1188d)
      This is a leftover helper tool for the Charles
      proxy app that provides privileged system access.
[4/5] HALF-GHOST  com.adobe.ARMDC.Communicator  (695d)
      This is a leftover Adobe communicator tool that
      provides privileged system access.

classification by code; risk [n/5] and explanation by
google/gemma-4-31b-it.
--redact: sent 2 of 5 ghosts (public vendor list); 3
never left this Mac.
# ran live in 7.7 s; clip trims the wait to 0.8 s

  hantu is read-only by default.
  Nothing moves unless you add --clean --yes.

  pipx install git+https://github.com/edycutjong/hantu

  github.com/edycutjong/hantu
  MIT · Python stdlib · Gemma 4 via OpenRouter
```
