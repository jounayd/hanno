# Running a local reasoning model on Mac Mini M4 (16GB)

## Before you start — read this

16GB unified memory is genuinely tight for a 14B model. This guide sizes things to actually work on your hardware rather than following the headline "12–15 tok/s" number literally — that figure is the theoretical memory-bandwidth ceiling (base M4: 120GB/s ÷ ~9GB model ≈ 13 tok/s), not a measured result under real load. In practice, on a 16GB machine:

- Model weights (Q4_K_M, 14B) ≈ 9GB
- macOS + background apps ≈ 3–5GB
- What's left for the KV cache (grows with context length, and with a *reasoning* model's own internal "thinking" tokens) ≈ 2–4GB

That's workable for short-context use, but it doesn't take much (a long prompt, a long thinking trace, another app open) to start swapping to disk — which doesn't degrade gracefully, it can drop throughput by 10x or hang the machine. This guide keeps context short and gives you a lighter fallback model, and ends with a real benchmark step so you know your actual numbers before relying on it for anything.

**Scope note:** this gets a model running and usable locally (chat, API testing). Wiring it into `hanno mine` is now real — see the end of this guide — but do the standalone setup and benchmark below *first*, so you know your actual speed/memory numbers before pointing real mining runs at it.

## 1. Install Ollama

The simplest path on macOS — handles Metal/Apple Silicon acceleration automatically, no manual llama.cpp build.

```bash
brew install ollama
```

Start the Ollama server (runs in the background, listens on `localhost:11434`):

```bash
brew services start ollama
```

Verify it's running:

```bash
curl http://localhost:11434/api/version
```

## 2. Pull a right-sized model

**Primary pick — DeepSeek-R1-Distill-Qwen-14B, Q4_K_M quantization** (~9GB, matches the table you found):

```bash
ollama pull deepseek-r1:14b-qwen-distill-q4_K_M
```

This will take a while on first pull (~9GB download).

**Safer fallback — pull this too, now, while you're at it.** If the 14B model feels sluggish or the machine starts swapping, switch to this without a re-download later:

```bash
ollama pull deepseek-r1:7b-qwen-distill-q4_K_M
```

The 7B variant is ~4.5GB, leaves far more headroom for context, and real-world reports put it well over 2x faster on the same hardware — still a genuine reasoning model, just less capable on the hardest problems.

## 3. Configure a safe context window

Ollama defaults to a small context window (2048) unless told otherwise, which is actually *in your favor* here — don't raise it blindly. Create a custom model file that pins a safe, explicit limit:

```bash
cat > ~/Modelfile-hanno-local <<'EOF'
FROM deepseek-r1:14b-qwen-distill-q4_K_M
PARAMETER num_ctx 8192
PARAMETER temperature 0.7
EOF

ollama create deepseek-r1-14b-hanno -f ~/Modelfile-hanno-local
```

8192 tokens is a deliberate ceiling for a 16GB machine — enough for a real prompt plus a reasoning trace plus an answer, without inviting swap. If you confirm in step 5 that memory has real headroom to spare, you can recreate this with a larger `num_ctx`, but start conservative.

## 4. Basic smoke test

```bash
ollama run deepseek-r1-14b-hanno "Prove that the square root of 2 is irrational. Be rigorous."
```

Watch for:
- Does it complete without hanging or crashing?
- Does the response arrive in a reasonable time (should be well under a minute for a prompt this size)?
- `<think>...</think>` tags in the output are normal — that's the reasoning trace; the actual answer follows it.

## 5. Real benchmark — do this before trusting any speed number, including this guide's

```bash
ollama run --verbose deepseek-r1-14b-hanno "Write a proof that there are infinitely many prime numbers." 2>&1 | tail -5
```

`--verbose` prints real `eval rate` (tokens/sec) and `prompt eval rate` at the end. **This is the number that matters, not the table.** While it's running, open Activity Monitor → Memory tab in another window and watch:
- **Swap Used**: should stay at or near 0. If it climbs into the hundreds of MB or more during generation, you're memory-constrained — switch to the 7B fallback.
- **Memory Pressure**: should stay green. Yellow/red during generation is the same signal.

Run this 2-3 times with different prompts (short and long) to get a realistic range, not a single lucky number.

## 6. If 14B is unstable: switch to the fallback

```bash
cat > ~/Modelfile-hanno-local-7b <<'EOF'
FROM deepseek-r1:7b-qwen-distill-q4_K_M
PARAMETER num_ctx 8192
PARAMETER temperature 0.7
EOF

ollama create deepseek-r1-7b-hanno -f ~/Modelfile-hanno-local-7b
ollama run --verbose deepseek-r1-7b-hanno "test prompt" 2>&1 | tail -5
```

Re-run the same benchmark and memory check from step 5. This should have comfortable headroom on 16GB — if it's still swapping, something else on the machine is eating memory; check Activity Monitor for other heavy processes before troubleshooting the model setup itself.

## What's genuinely useful about this even before any Hanno integration

Once running, Ollama exposes a local OpenAI-compatible API (`http://localhost:11434/v1/chat/completions`) — useful for testing prompts, exploring how the model handles Hanno-style mathematical conjecture prompts by hand, or general local experimentation, all at zero marginal API cost. That's a real, immediate win given where your Anthropic credits are right now, independent of whether it ever becomes the Excavator's model.

## Pointing Hanno at this model

Hanno now speaks any OpenAI-compatible backend, not just Anthropic's API (`hanno/excavator/client_factory.py`, `hanno/excavator/openai_client.py`). To use what you just set up:

```bash
export HANNO_EXCAVATOR__PROVIDER=openai_compatible
export HANNO_EXCAVATOR__BASE_URL=http://localhost:11434/v1
export HANNO_EXCAVATOR__MODEL=deepseek-r1-14b-hanno
export HANNO_EXCAVATOR__REQUEST_TIMEOUT_SECONDS=1800
```

**Also raise the request timeout.** Hanno's default per-call timeout is 600 seconds, sized for hosted APIs. At real local speeds (single digits to ~10 tok/s), `max_tokens_per_candidate` (8000 by default) or `deep_reasoning_max_tokens` (16000) can take 800-1600+ seconds to actually finish — well past 600s — so long responses get killed mid-generation and logged as a plain "failed" candidate with no other explanation. This isn't a capability problem, it just looks like one until the timeout is raised. Set it generously (1800-3600s) via the env var above or the **Request timeout** field in `hanno ui`'s Settings tab, right under Model.

(or the equivalent fields in `hanno ui`'s Settings tab — a **Provider** dropdown and **Base URL** field sit next to Model; no API key is needed for a local Ollama server, since it doesn't check auth). Then run something small first:

```bash
hanno mine --seed <SEED_ID> --candidates 2
```

**Before trusting real mining runs to it**, look honestly at the actual output: run `hanno trace <seed_id> --json trace.json` and check `parse_status` on the resulting candidates. Hanno already tracks structured-JSON-output reliability as a real, measured concern even with Claude Sonnet — small/local models are generally noticeably worse at strict schema-following, and a high `parse_status: failed` rate is the single most likely way this underperforms, not raw tokens/sec.

**Skills (the `run_code`/`tree_lab`/etc. tools) do attempt to work on this provider** — Hanno sends real OpenAI-style function-calling requests, and if a candidate's model calls a skill mid-generation, the result gets fed back and a real `tool_invocations` row gets recorded, same as with Claude. Whether it actually happens depends on whether the specific model you're running understands tool-calling at all: some Ollama-served models do (check the model's own docs — this isn't something Hanno controls), others don't. Either way is handled without crashing: a model that ignores the tools and just answers directly just won't self-verify that round; a server/model that outright rejects the request for including `tools` gets silently retried without them for the rest of that generation, logged once, not per call.
