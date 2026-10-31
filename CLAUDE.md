# local-llm

See `README.md` for the full picture.

- The Qwen server on `127.0.0.1:1234` is managed by launchd (`com.anurag.local-qwen`, supervisor `scripts/qwen_supervisor.py`, engine wrapper `scripts/qwen_engine.py`). It runs on AC power and stops on battery. Live state: `GET 127.0.0.1:1233/status` or `npm run qwen:status`.
- If asked to start or fix the local model: run `npm run qwen:status` first. Use `npm run qwen:restart` or `qwen:install`; never start `mlx_lm.server` from a Claude session, since it dies when the session ends.
- Models live in `Models/` (`HF_HOME`). Never commit `evals/chain-digest/private/` or `runs/` (personal vault content).
