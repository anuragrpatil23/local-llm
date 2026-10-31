# Thinking Space chain-digest evaluation

This evaluates the production local-LLM use case: turn one AI-activity chain
into a useful title and numbered-bullet summary. The exporter reads the exact
`SYSTEM_PROMPT` from Thinking Space's `chainDigestContractBlock.ts`; it does
not maintain a benchmark-specific copy of that instruction.

The dataset exporter reads the local Thinking Space vault. It creates a
private sample of real chain transcripts with one case per represented project
and allocates the remaining cases proportionally to project activity. It pairs each input with
the digest currently stored in the vault. Those existing digests are a useful
baseline, **not** a correctness label. Final quality is decided in a blinded
review of candidates against the chain source.

## First run

```sh
npm run eval:export -- --vault "/Users/patila06/Library/Mobile Documents/iCloud~md~obsidian/Documents/Long-Term-Memory-iCloud" --limit 50
npm run eval:check

# Run a complete MLX server cycle: start the model server on an isolated port,
# evaluate through its OpenAI-compatible endpoint, then stop it.
npm run eval:mlx -- --model-path Models/hub/models--mlx-community--gemma-4-26b-a4b-it-4bit/snapshots/efbeee6e582ebfd06abc9d65e90839c4b5d2116b \
  --server-model mlx-community/gemma-4-26b-a4b-it-4bit --label gemma-4-26b
```

For models implemented by MLX-VLM, use the same server lifecycle with its
OpenAI-compatible server:

```sh
npm run eval:mlx -- --engine mlx-vlm --model-path /path/to/muse-mlx \
  --server-model mlx-community/Muse-Glimmer-30B-4bit --label muse-glimmer
```

Muse Glimmer's current checkpoint requires MLX-VLM 0.6.12 according to its
model card. Do not run this command until that version (or an upstream build
with `muse_glimmer` support) is available; the latest published runtime rejects
the architecture rather than silently falling back to another backend.

For Qwen’s long, sustained run, use the batched server lifecycle (10 cases per
server process) to avoid an MLX server memory build-up while retaining one
combined result file:

```sh
npm run eval:mlx-batched -- --model-path /path/to/qwen-mlx --server-model qwen-id --label qwen
```

The batched runner automatically uses Thinking Space's Qwen 3.x production
setting to disable hidden reasoning. Pass `--disable-reasoning false` when
evaluating a model family that should reason.

```sh
npm run eval:score -- --run evals/chain-digest/runs/<run-id>.json
npm run eval:review -- --run evals/chain-digest/runs/<run-id>.json
```

Open `evals/chain-digest/runs/<run-id>.review.html` and score the candidates
blind. The page can download the entered judgments as JSON.

## What is measured

Automated scoring is deliberately narrow: output contract compliance,
successful parsing, title/summary length limits, bullets, latency, and failures.
It does **not** pretend semantic quality can be measured from the old answer.
The blinded review asks whether a digest is accurate, covers the meaningful
work, is specific, and is useful as a timeline label.

Keep private source material under `private/` and `runs/`; both are ignored by
Git. Re-export after changing the production prompt or chain extraction rules.
