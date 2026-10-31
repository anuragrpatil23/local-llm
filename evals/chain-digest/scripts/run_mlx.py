#!/usr/bin/env python3
"""Run Thinking Space chain-digest cases directly with MLX.

The input messages match the production OpenAI-compatible provider exactly:
one system message (exported from the current TypeScript contract) followed by
one user message. The model is loaded once for the whole dataset.
"""
import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

from mlx_lm import generate, load


def normalize_output(raw):
    lines = re.sub(r"^```(?:markdown|md)?\s*", "", raw, flags=re.I)
    lines = re.sub(r"```\s*$", "", lines).split("\n")
    first = next((index for index, line in enumerate(lines) if line.strip()), -1)
    if first < 0:
        return {"title": "", "summary": "", "format": {"hasTitlePrefix": False, "hasBlankLine": False}}
    line = lines[first].strip()
    prefix = bool(re.match(r"^title\s*[:\-—]", line, flags=re.I))
    title = re.sub(r"^title\s*[:\-—]\s*", "", line, flags=re.I).strip()
    title = re.sub(r"[.!?;]+$", "", title).strip()
    return {
        "title": title,
        "summary": "\n".join(lines[first + 1:]).strip(),
        "format": {"hasTitlePrefix": prefix, "hasBlankLine": first + 1 < len(lines) and not lines[first + 1].strip()},
    }


parser = argparse.ArgumentParser()
parser.add_argument("--model-path", required=True)
parser.add_argument("--label", required=True)
parser.add_argument("--dataset", default="evals/chain-digest/private/dataset.json")
parser.add_argument("--append")
parser.add_argument("--limit", type=int)
parser.add_argument("--max-tokens", type=int, default=900)
parser.add_argument("--temperature", type=float, default=0.2)
args = parser.parse_args()

with open(args.dataset, encoding="utf-8") as handle:
    dataset = json.load(handle)
if args.append:
    with open(args.append, encoding="utf-8") as handle:
        run = json.load(handle)
else:
    run = {"schemaVersion": 1, "useCase": dataset["useCase"], "dataset": args.dataset, "createdAt": datetime.now(timezone.utc).isoformat(), "candidates": []}

print(f"Loading {args.label} with MLX...", flush=True)
model, tokenizer = load(args.model_path)
candidate = {"label": args.label, "model": args.model_path, "runtime": "mlx_lm", "temperature": args.temperature, "maxTokens": args.max_tokens, "cases": []}
cases = dataset["cases"][:args.limit] if args.limit else dataset["cases"]
for index, item in enumerate(cases, 1):
    started = time.perf_counter()
    try:
        messages = [{"role": "system", "content": dataset["systemPrompt"]}, {"role": "user", "content": item["prompt"]}]
        prompt = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        raw = generate(model, tokenizer, prompt=prompt, max_tokens=args.max_tokens, temp=args.temperature, verbose=False)
        row = {"id": item["id"], "latencyMs": round((time.perf_counter() - started) * 1000), "raw": raw, "normalized": normalize_output(raw), "usage": None}
        candidate["cases"].append(row)
        print(f"{index}/{len(cases)} {item['id']} {row['latencyMs']}ms", flush=True)
    except Exception as error:  # preserve a failed case for format / reliability scoring
        candidate["cases"].append({"id": item["id"], "latencyMs": round((time.perf_counter() - started) * 1000), "error": str(error)})
        print(f"{index}/{len(cases)} {item['id']} ERROR: {error}", file=sys.stderr, flush=True)

run["candidates"].append(candidate)
output = args.append or f"evals/chain-digest/runs/{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H-%M-%S-%fZ')}.json"
os.makedirs(os.path.dirname(output), exist_ok=True)
with open(output, "w", encoding="utf-8") as handle:
    json.dump(run, handle, indent=2)
    handle.write("\n")
print(f"Saved {output}", flush=True)
