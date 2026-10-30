# Gemma run (redacted)

`hantu explain` on the author's Mac, 2026-10-31. Classification by code; text below by google/gemma-4-31b-it.
Ghosts explained: 5 of 5. Showing well-known vendors only, one per vendor, max 3.

- `com.xk72.charles.ProxyHelper` — HALF-GHOST, 1188 days old
  - Gemma (risk 3/5): This belonged to Charles Proxy and is trying to run a privileged network proxy helper.
  - why: Runs as a privileged daemon with root-level access.
- `com.adobe.ARMDC.Communicator` — HALF-GHOST, 695 days old
  - Gemma (risk 3/5): This belonged to Adobe software and is trying to run a privileged communication service.
  - why: Runs as a privileged daemon with root-level access.

## Ranking of all 5 (labels withheld except the examples above)

- Risk 4/5: 1 ghost. It is the oldest one (1549 days) and the only one with `RunAtLoad`. Gemma's reason: "Privileged daemon that runs at load and creates a system socket."
- Risk 3/5: 4 ghosts, all with the same reason: "Runs as a privileged daemon with root-level access."

## How this run was made

The free ids (`google/gemma-4-31b-it:free`, then `google/gemma-4-26b-a4b-it:free`) answered HTTP 429 (rate limited) every time they were tried that night. This run used `hantu explain --model google/gemma-4-31b-it`: the same open-weights model, through OpenRouter's paid listing. It took about 13 seconds and cost $0.00046 (the key's usage before and after the run).

The same night, OpenRouter's endpoint list for `google/gemma-4-31b-it` (`/api/v1/models/google/gemma-4-31b-it/endpoints`) showed 16 endpoints from 13 different providers serving these open weights.
