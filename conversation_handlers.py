"""
conversation_handlers.py — Multi-turn handler (extra credit submission artifact).

This implements the optional `respond(state, merchant_message)` function that
the judge can call to test multi-turn conversation capability.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bot.conversation import ConversationState, detect_auto_reply, detect_opt_out, detect_intent_transition
from bot.composer import compose_reply
from bot.context_store import store


def respond(state: dict, merchant_message: str) -> dict:
    """
    Given the conversation so far + the merchant's latest message, produce the reply.

    Args:
        state: ConversationState dict with keys:
            - conversation_id: str
            - merchant_id: str
            - customer_id: str | None
            - trigger_id: str | None
            - history: list of {from, body, ts}
            - turn_count: int
            - auto_reply_count: int
            - bodies_sent: list[str]
            - intent_confirmed: bool

        merchant_message: The merchant's latest message text.

    Returns:
        dict with keys: action (send/wait/end), body?, cta?, wait_seconds?, rationale
    """
    from bot import config

    # ── 1. Opt-out / hostility ────────────────────────────────────────────────
    if detect_opt_out(merchant_message):
        return {
            "action": "end",
            "rationale": (
                "Merchant opted out or expressed hostility. "
                "Closing conversation; suppressing merchant for 30 days."
            ),
        }

    # ── 2. Auto-reply detection ────────────────────────────────────────────────
    if detect_auto_reply(merchant_message):
        auto_count = state.get("auto_reply_count", 0) + 1

        if auto_count == 1:
            return {
                "action": "send",
                "body": "Looks like an auto-reply 😊 When the owner sees this, just reply YES to continue.",
                "cta": "binary_yes_no",
                "rationale": "Detected auto-reply (1st time). One gentle flag to owner before backing off.",
            }
        elif auto_count == 2:
            return {
                "action": "wait",
                "wait_seconds": config.AUTO_REPLY_WAIT_SECONDS,
                "rationale": "Auto-reply twice → owner not at phone. Waiting 4 hours.",
            }
        else:
            return {
                "action": "end",
                "rationale": f"Auto-reply {auto_count} times. No real engagement. Closing.",
            }

    # ── 3. Intent transition ────────────────────────────────────────────────────
    if detect_intent_transition(merchant_message):
        # Flag for the LLM so it switches to action mode
        state = {**state, "intent_confirmed": True}

    # ── 4. LLM-based reply ────────────────────────────────────────────────────
    merchant_id = state.get("merchant_id", "")
    customer_id = state.get("customer_id")
    trigger_id = state.get("trigger_id")

    merchant = store.get_merchant(merchant_id) or {}
    category_slug = merchant.get("category_slug", "")
    category = store.get_category(category_slug) or {}
    trigger = store.get_trigger(trigger_id) if trigger_id else None
    customer = store.get_customer(customer_id) if customer_id else None

    conv_state_label = "ACTION_MODE" if state.get("intent_confirmed") else "ENGAGED"
    history = state.get("history", [])

    return compose_reply(
        category=category,
        merchant=merchant,
        trigger=trigger,
        customer=customer,
        conversation_history=history,
        merchant_message=merchant_message,
        conversation_state=conv_state_label,
    )
