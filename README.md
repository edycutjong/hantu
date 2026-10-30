# hantu 👻

**Find the launch agents of apps you already deleted.** A read-only macOS scanner. Code decides what is a ghost; an open-weights model (Gemma) explains it in plain English and ranks it.

Deleting an app does not delete everything it set up. Some installers leave a launchd plist behind that can still start a helper, sometimes as root, years later. `hantu` lists them.

## Install and run (30 seconds)

```bash
pipx install git+https://github.com/edycutjong/hantu
hantu                  # read-only scan of the three launchd folders (same as `hantu scan`)
hantu scan --ghosts    # only the ghosts
export OPENROUTER_API_KEY=...   # free key from openrouter.ai
hantu explain          # Gemma writes one line per ghost and ranks them 1-5
hantu report --md      # Markdown report (add --redact for a publication-safe one)
```

Python 3.9+, standard library only, macOS only.

## What it found on my Mac

One real run, 2026-10-31, on the author's own machine. Full redacted summary: [`evidence/scan-summary.md`](evidence/scan-summary.md).

| | |
|---|---|
| Launch plists scanned | 58 |
| Ghosts (HALF-GHOST) | **5** |
| Ghosts that are launch daemons running as root (no `UserName` in the plist) | 5 of 5 |
| Oldest ghost | **1549 days** since its plist was last modified |
| ORPHANED (program missing) | 0 |
| UNKNOWN (could not prove either way) | 9 |

I expected dangling binaries. There are none. What stayed behind are privileged helpers: no installed app claims them any more, but the root helper is still on disk with a launchd plist that can start it. All five targets sit in `/Library/PrivilegedHelperTools`, and no app that matches them by bundle id (Charles, AlDente, NiceHash, Adobe Acrobat) is installed: I checked by hand with Spotlight and `/Applications`. (A first version of the rule also flagged macFUSE, which installs its app inside a filesystem bundle, not in `/Applications`. That is now handled, and is why the rule is only a rule.)

## How it decides (code) and what the model does

| Verdict | Rule, in code |
|---|---|
| `ORPHANED` | the program launchd would start is missing (for `sh -c`, `python script.py` and similar, the script is what is checked) |
| `HALF-GHOST` | the program exists, but no installed app claims it: the plist's `AssociatedBundleIdentifiers` match no installed app, or (when it has none) a helper in `/Library/PrivilegedHelperTools` is neither declared (`SMPrivilegedExecutables`) nor shipped (`Contents/Library/LaunchServices`) by any installed app. `com.apple.*` ids always count as installed, and a program inside an existing `.app` is never flagged. If the list of installed apps cannot be read (fewer than 5 readable app `Info.plist` files), hantu makes no HALF-GHOST calls at all |
| `ALIVE` | the program exists and nothing shows its owner is gone (hantu looks for an owner only when the plist names one, or the program is in `/Library/PrivilegedHelperTools`) |
| `UNKNOWN` | malformed or empty plist, no program to launch, `BundleProgram`, a relative path with no absolute `WorkingDirectory`, a bare command not on launchd's PATH, a script argument that looks like a classpath or glob, an unreadable path, a target on a `/Volumes` disk that may just be unmounted. Never counted as a ghost |

Gemma (`google/gemma-4-31b-it:free` on OpenRouter, falling back to `gemma-4-26b-a4b-it:free`) is given the plist, the verdict, the reason string and the plist's age, and returns strict JSON: one sentence and a risk score from 1 to 5 per ghost. The reply is validated (ids, ranges, risk 1-5, non-empty text, truncated to a fixed length); invalid items are dropped, and the model has no field in which it could change a verdict.

`explain` sends data off your machine: for each ghost, the plist (at most 1200 characters), its file name, age, verdict and reason go to OpenRouter and the Gemma host. Before sending, `/Users/<name>` becomes `~`, `EnvironmentVariables` values are masked, and `UserName`, `GroupName`, `SessionCreate` and `StandardInPath` are dropped. Everything else, including `ProgramArguments`, is sent as written (re-serialised as XML), because labels and paths are the point. `scan` sends nothing anywhere. `report` sends nothing unless you add `--explain`; with `--redact` that sends only the at most three well-known-vendor examples.

## Safety

- Scanning is read-only. It reads plists, `codesign -dv` and `launchctl list`. It never writes.
- Nothing is changed unless you pass **both** `--clean` and `--yes`. `hantu scan --clean` alone prints the plan.
- `--clean` only touches `ORPHANED` agents in your own `~/Library/LaunchAgents`. It never touches `/Library` (that needs root), and it never touches HALF-GHOSTs.
- Files are moved to `~/.hantu/quarantine/<timestamp>/` with a `manifest.json`, not deleted. Undo is `mv` back. If the agent was loaded, `--yes` also runs `launchctl bootout` for it after the move; putting the plist back may not restart it until the next login or a `launchctl bootstrap`.
- The clean path is tested on fixture plists in temp directories only. It has not been run against a real machine.

## Tests

```bash
pip install pytest && pytest
```

79 tests over fixture plists: orphaned, half-ghost, alive, malformed, empty stub, relative paths, `~` expansion, interpreters, `/Volumes`; the Gemma reply validator, retry and fallback paths with a fake transport; the clean path; the CLI. The suite fails any test that tries to reach the network.

## Limits

- Login items (System Settings > General > Login Items) are not scanned: listing them needs privileges or a permission prompt.
- Launch daemons cannot be checked for "currently loaded" without root, so hantu does not say whether a ghost daemon is running right now.
- HALF-GHOST is a rule, not a proof. An app that lives outside the places hantu looks (`/Applications`, `~/Applications`, `/System/Applications` and its `Utilities` folder, each also one folder down, plus a few vendor locations), or a helper that its app installs without declaring it, will look like a ghost. Read the line before you remove anything. `--clean` never acts on HALF-GHOSTs for that reason.
- Free Gemma endpoints on OpenRouter are rate-limited; `explain` retries with backoff and falls back to the second model.

## License

MIT. Copyright (c) 2026 Edy Cu Tjong.
