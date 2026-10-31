# local-llm

Local models (MLX) on this Mac, plus evals for Thinking Space's background tasks (summaries, digests).

## Always-on Qwen server

`mlx-community/Qwen3.8-27B-4bit` is served at `http://127.0.0.1:1234/v1` (OpenAI-compatible) whenever the Mac is on AC power. One model at a time, by design: the consumers are Thinking Space background jobs.

### How it works

- `scripts/com.anurag.local-qwen.plist` is a launchd agent (installed to `~/Library/LaunchAgents/`). It starts at login and launchd restarts it if it dies.
- It runs `scripts/qwen_supervisor.py`, which starts the engine (`scripts/qwen_engine.py`, a wrapper around `mlx_lm.server`) and owns its lifecycle:
  - **On AC:** starts the engine, proves it can generate with a 1-token canary, then health-checks it every 5s.
  - **On battery:** stops the engine.
  - **Any failure** ends the engine process and gets a recorded reason, then a restart with backoff (0/5/30/120s). 4 failures in 15 min open a circuit (state `failed`, macOS notification, retry after 15 min).
  - If something else already holds port 1234, it leaves it alone (state `external`).
- Models load from `Models/` (`HF_HOME`) with `HF_HUB_OFFLINE=1`; nothing is downloaded.

While the Mac sleeps nothing runs; the engine is back within seconds of waking on AC.

### Failure handling

`mlx_lm.server` generates on one background thread. When that thread dies the HTTP layer stays up and accepts requests that never finish (this is what hung Thinking Space on 2026-10-25 and 2026-10-30). `qwen_engine.py` makes every such state a process exit, within ~2s:

| Failure | Detected by | Reason recorded |
|---|---|---|
| Exception on the generation (or any background) thread | `threading.excepthook` | `thread_exception` (+ `error`: type, thread, message) |
| Generation thread gone | watchdog, every 2s | `generator_exited` |
| Requests in flight, no prefill/decode progress for 180s (sleep excluded) | watchdog | `stalled` |
| Process killed / crashed (incl. OOM) | supervisor sees the exit | `engine exited (killed by SIGKILL ...)` |
| Process frozen, `/health` unanswered or 503 for 3 checks | supervisor | `health check unanswered` / `engine reported unhealthy` |
| Never becomes ready after start | supervisor, 300s canary deadline | `startup_failed` |

The engine writes what was in flight (`X-Request-Id`, `X-Job` headers) into the failure record, so clients can tell which jobs died with it.

The handling is deliberately generic: it does not depend on why the engine failed. (The failures seen so far were mlx-lm's Metal buffer leak on long generations, [mlx-lm#1672](https://github.com/ml-explore/mlx-lm/issues/1672); we run unpatched mlx-lm.)

### Memory

`mlx_lm.server` on its own wires up to ~84% of RAM and caches freed GPU buffers without bound, which can make the whole Mac slow. Now:

- Engine: wired memory capped at 32 GB (`QWEN_WIRED_LIMIT_GB`), freed-buffer cache at 4 GB (`QWEN_CACHE_LIMIT_GB`).
- Prompt cache: 4 conversations, 4 GB total (`PROMPT_CACHE_SIZE`, `PROMPT_CACHE_BYTES` in the supervisor).
- The supervisor recycles the engine when idle if it holds more than 40 GB, and when macOS memory pressure is critical (after 30s if idle, 120s even if busy). While pressure stays critical the model stays unloaded (state `paused_memory_pressure`). Recycles are logged as events, not failures.

### Status for clients

`GET http://127.0.0.1:1233/status` (served by the supervisor, so it answers even while the engine is down):

```json
{"state": "ready", "reason": "canary passed", "since": "...", "ready": true, "retry_after_s": null,
 "engine": {"url": "http://127.0.0.1:1234/v1", "model": "...", "pid": 123, "health": {...}},
 "system": {"memory_pressure": "normal"},
 "failures_last_hour": 0, "last_failure": null}
```

States: `starting`, `ready`, `restarting`, `failed`, `paused_on_battery`, `paused_memory_pressure`, `external`. Use `ready`, not `/v1/models`, to decide whether to send work. `GET :1234/health` is the engine's own view (200 only if the generator is alive and not stalled).

### Commands

| | |
|---|---|
| `npm run qwen:status` | Supervisor state, engine health, last failure |
| `npm run qwen:events` | Recent state changes, failures, recycles (`~/Library/Logs/local-qwen-events.jsonl`) |
| `npm run qwen:logs` | Follow the log (`~/Library/Logs/local-qwen.log`, rotated at 10 MB x 3) |
| `npm run qwen:restart` | Restart the supervisor (and engine) |
| `npm run qwen:reload` | Reinstall the launchd job (after editing the plist) |
| `npm run qwen:install` / `qwen:uninstall` | Load / unload the job |

Changing the model or port: edit `MODEL` / `PORT` at the top of `scripts/qwen_supervisor.py`, then `npm run qwen:restart`.

Do not start a second server by hand (`npm run eval:serve:mlx`) on port 1234; the supervisor owns it.

### History

- Before 2026-10-16 the server was started by hand, so it died with the terminal session. The launchd job replaced that.
- 2026-10-31: the zsh supervisor (`/v1/models` health check) was replaced by `qwen_supervisor.py` + `qwen_engine.py` after the generator-death hangs above.

## Evals

See `evals/chain-digest/README.md`.
