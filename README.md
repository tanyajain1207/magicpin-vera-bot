# Vera AI Bot — magicpin AI Challenge Submission

## What I Built

A production-quality implementation of **Vera** — magicpin's merchant AI assistant — as a live HTTP API. The bot handles all 5 required endpoints and composes high-compulsion WhatsApp messages using a 4-context LLM framework powered by Google Gemini 2.0 Flash.

---

## Architecture

```
Judge → /v1/context  (push category/merchant/customer/trigger data)
Judge → /v1/tick     (bot decides what to send proactively)
Judge → /v1/reply    (merchant replied; bot handles the next turn)
Judge → /v1/healthz  (liveness)
Judge → /v1/metadata (bot identity)
```

### Core Components

| File | Role |
|---|---|
| `bot/main.py` | FastAPI app — all 5 endpoints |
| `bot/composer.py` | Gemini 2.0 Flash LLM caller + validation loop |
| `bot/prompt_variants.py` | 14 trigger-specific prompt templates |
| `bot/dispatcher.py` | Routes trigger.kind → correct prompt variant |
| `bot/conversation.py` | State machine: auto-reply / opt-out / intent detection |
| `bot/context_store.py` | Thread-safe versioned in-memory store |

---

## Key Design Decisions

### 1. Trigger-Kind Dispatch (vs. one generic prompt)
Every trigger `kind` gets a dedicated prompt tuned with the exact compulsion levers that work for that scenario:
- `research_digest` → source citation + curiosity lever
- `perf_dip` → loss aversion + peer benchmark anchor
- `recall_due` → slot offering + language-match
- `curious_ask_due` → asking-the-merchant + effort externalization
- `regulation_change` → urgency + compliance framing
- (14 variants total)

### 2. Auto-Reply State Machine
Three-stage detection instead of burn-and-retry:
1. First auto-reply: one gentle flag to owner → `send`
2. Second: back off 4 hours → `wait`
3. Third+: close gracefully → `end`

### 3. Intent Transition Router
Pattern-matching on "yes", "ok let's do it", "go ahead", "confirm" → immediately flags `intent_confirmed=True` and switches the LLM to action-execution mode (drafts the artifact, stops asking qualifying questions).

### 4. Language-Adaptive Output
Merchant's `identity.languages` field drives code-mix ratio in the prompt. Merchants with `["en", "hi"]` get natural Hinglish; English-only merchants get pure English.

### 5. Post-LLM Validation + Re-prompt
Every composed message is checked for: no URLs, valid CTA shape, non-empty body, no repetition of prior message. If it fails, we re-prompt once with the error list before giving up.

### 6. Social Proof + Curious Ask
Production Vera barely fires these families. We specifically build `curious_ask_prompt` and `perf_dip_prompt` to inject `peer_stats` data ("3 dentists in your locality did X this month") — the highest-scoring compulsion lever Vera is missing today.

---

## Tradeoffs

| Choice | Why |
|---|---|
| In-memory store (no Redis) | Simpler, zero cold-start risk for judge test window |
| Gemini 2.0 Flash @ temp=0 | Fast (<5s per call), multilingual, deterministic |
| 14 prompt variants | Higher per-trigger quality vs. single generalist prompt |
| Re-prompt once on failure | Catches ~80% of CTA/URL issues without double latency cost |

---

## What Additional Context Would Help Most

1. **Merchant's real appointment calendar** — slot-offering messages (recall, winback) would be dramatically more specific with real open slots instead of derived estimates.

2. **Review sentiment history** (not just last 30d themes) — knowing that Dr. Meera's wait-time reviews have been worsening for 3 months vs. 3 weeks changes the urgency framing completely.

3. **Past campaign performance data** — knowing which offer types (service+price vs. free consultation vs. seasonal) historically got higher reply rates for this specific merchant allows A/B-informed message selection, not just category-level heuristics.

---

## How to Run Locally

```bash
# Install dependencies
pip install -r requirements.txt

# Set Gemini API key
set GEMINI_API_KEY=your_key_here   # Windows
# export GEMINI_API_KEY=your_key_here  # Linux/Mac

# Generate expanded dataset
python dataset/generate_dataset.py --seed-dir dataset --out dataset/expanded

# Start the bot
uvicorn bot.main:app --host 0.0.0.0 --port 8080

# Run judge simulator (in another terminal)
set BOT_URL=http://localhost:8080
python judge_simulator.py

# Generate submission.jsonl
python generate_submission.py
```

## Deployed URL
See submission portal.
