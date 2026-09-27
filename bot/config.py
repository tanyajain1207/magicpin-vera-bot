"""
config.py — Central configuration for Vera AI Bot.
All secrets come from environment variables. Never hardcode keys.
"""

import os

# ─── LLM Configuration ────────────────────────────────────────────────────────
GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
GEMINI_PRIMARY_MODEL: str = "gemini-2.5-flash"
GEMINI_FALLBACK_MODEL: str = "gemini-3.5-flash"
GEMINI_FALLBACK_MODEL_2: str = "gemini-3.5-flash-lite"
LLM_TEMPERATURE: float = 0.0       # deterministic output
LLM_MAX_TOKENS: int = 1024
LLM_TIMEOUT_SECONDS: int = 25      # must respond to judge in <30s

# ─── Team Metadata ─────────────────────────────────────────────────────────────
TEAM_NAME: str = "VeraPlus"
TEAM_MEMBERS: list[str] = ["Tanya Jain"]
CONTACT_EMAIL: str = os.getenv("CONTACT_EMAIL", "7440tanyajain@gmail.com")
BOT_VERSION: str = "2.0.0"
SUBMITTED_AT: str = "2026-08-22T08:00:00Z"
APPROACH: str = (
    "4-context LLM composer with trigger-kind dispatch (14+ prompt variants). "
    "Features: auto-reply state machine, intent transition router, "
    "language-adaptive Hindi-English code-mix, social proof + curious-ask injection, "
    "post-LLM validation with re-prompt loop. Model: Gemini 3.6 Flash @ temp=0."
)

# ─── Bot Behaviour ─────────────────────────────────────────────────────────────
MAX_ACTIONS_PER_TICK: int = 15        # stay under the judge's 20-action cap
MAX_CONVERSATION_TURNS: int = 5       # graceful exit after this many turns
AUTO_REPLY_WAIT_SECONDS: int = 14400  # 4h backoff after first auto-reply detection
AUTO_REPLY_LONG_WAIT_SECONDS: int = 86400  # 24h after second detection
OPT_OUT_SUPPRESS_DAYS: int = 30       # suppress merchant for 30 days after hard no
