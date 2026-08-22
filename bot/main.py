"""
main.py — FastAPI application. All 5 required endpoints.

Endpoints:
  GET  /v1/healthz    — liveness probe
  GET  /v1/metadata   — bot identity
  POST /v1/context    — receive context push
  POST /v1/tick       — periodic wake-up; bot decides proactive sends
  POST /v1/reply      — receive merchant/customer reply; bot responds
  POST /v1/teardown   — optional: wipe all state (end of test)
"""

from __future__ import annotations

# Load .env if present (local development)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import logging
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from bot import config
from bot.composer import compose, compose_reply
from bot.context_store import store
from bot.conversation import registry, ConversationState
from bot.dispatcher import build_tick_action, make_conversation_id

# ─── App setup ─────────────────────────────────────────────────────────────────

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Vera AI Bot — magicpin Challenge",
    description="AI-powered merchant engagement assistant",
    version=config.BOT_VERSION,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

START_TIME = time.time()


# ─── Request / Response Models ─────────────────────────────────────────────────

class ContextBody(BaseModel):
    scope: str
    context_id: str
    version: int
    payload: dict[str, Any]
    delivered_at: str


class TickBody(BaseModel):
    now: str
    available_triggers: list[str] = []


class ReplyBody(BaseModel):
    conversation_id: str
    merchant_id: str | None = None
    customer_id: str | None = None
    from_role: str
    message: str
    received_at: str
    turn_number: int = 1


# ─── GET /v1/healthz ──────────────────────────────────────────────────────────

@app.get("/v1/healthz")
async def healthz():
    counts = store.count_by_scope()
    return {
        "status": "ok",
        "uptime_seconds": int(time.time() - START_TIME),
        "contexts_loaded": counts,
    }


# ─── GET /v1/metadata ─────────────────────────────────────────────────────────

@app.get("/v1/metadata")
async def metadata():
    return {
        "team_name": config.TEAM_NAME,
        "team_members": config.TEAM_MEMBERS,
        "model": config.GEMINI_PRIMARY_MODEL,
        "approach": config.APPROACH,
        "contact_email": config.CONTACT_EMAIL,
        "version": config.BOT_VERSION,
        "submitted_at": config.SUBMITTED_AT,
    }


# ─── POST /v1/context ─────────────────────────────────────────────────────────

@app.post("/v1/context")
async def push_context(body: ContextBody):
    valid_scopes = {"category", "merchant", "customer", "trigger"}
    if body.scope not in valid_scopes:
        return {
            "accepted": False,
            "reason": "invalid_scope",
            "details": f"scope must be one of {valid_scopes}",
        }
    result = store.push(body.scope, body.context_id, body.version, body.payload)
    logger.info(
        f"Context push: scope={body.scope} id={body.context_id} "
        f"v={body.version} accepted={result['accepted']}"
    )
    return result


# ─── POST /v1/tick ────────────────────────────────────────────────────────────

@app.post("/v1/tick")
async def tick(body: TickBody):
    actions = []
    tick_start = time.time()

    for trg_id in body.available_triggers:
        # Hard time budget: leave 5s buffer before the 30s judge timeout
        if time.time() - tick_start > 24:
            logger.warning("Tick time budget reached; stopping early.")
            break

        # Fetch trigger
        trg = store.get_trigger(trg_id)
        if not trg:
            logger.debug(f"Trigger {trg_id} not in store; skipping.")
            continue

        # Check suppression
        suppression_key = trg.get("suppression_key", "")
        if suppression_key and store.is_suppressed(suppression_key):
            logger.debug(f"Trigger {trg_id} suppressed; skipping.")
            continue

        # Fetch merchant
        merchant_id = trg.get("merchant_id")
        if not merchant_id:
            logger.debug(f"Trigger {trg_id} has no merchant_id; skipping.")
            continue

        if store.is_merchant_opted_out(merchant_id):
            logger.debug(f"Merchant {merchant_id} opted out; skipping.")
            continue

        merchant = store.get_merchant(merchant_id)
        if not merchant:
            logger.debug(f"Merchant {merchant_id} not in store; skipping.")
            continue

        # Check if this conversation is already ended
        conv_id = make_conversation_id(merchant_id, trg_id)
        if store.is_conversation_ended(conv_id):
            logger.debug(f"Conversation {conv_id} already ended; skipping.")
            continue

        # Fetch category
        category_slug = merchant.get("category_slug", "")
        category = store.get_category(category_slug)
        if not category:
            logger.debug(f"Category {category_slug} not in store; skipping.")
            continue

        # Fetch customer (optional)
        customer_id = trg.get("customer_id")
        customer = store.get_customer(customer_id) if customer_id else None

        # Get prior bodies for this conversation
        conv_state = registry.get(conv_id)
        prior_bodies = conv_state.bodies_sent if conv_state else []

        # Compose the message
        composed = compose(
            category=category,
            merchant=merchant,
            trigger=trg,
            customer=customer,
            prior_bodies=prior_bodies,
        )

        if not composed or not composed.get("body"):
            logger.error(f"Composition returned empty for trigger {trg_id}")
            continue

        # Build the action
        action = build_tick_action(
            merchant_id=merchant_id,
            trigger_id=trg_id,
            customer_id=customer_id,
            composed=composed,
            trigger=trg,
        )

        # Record in conversation state
        conv = registry.get_or_create(conv_id, merchant_id, customer_id, trg_id)
        conv.add_turn("vera", composed["body"])

        # Register suppression
        if suppression_key:
            store.suppress(suppression_key)

        actions.append(action)

        logger.info(
            f"Tick action: conv={conv_id} trigger={trg.get('kind')} "
            f"merchant={merchant.get('identity', {}).get('name', merchant_id)}"
        )

        if len(actions) >= config.MAX_ACTIONS_PER_TICK:
            break

    return {"actions": actions}


# ─── POST /v1/reply ───────────────────────────────────────────────────────────

@app.post("/v1/reply")
async def reply(body: ReplyBody):
    conv_id = body.conversation_id
    merchant_id = body.merchant_id
    message = body.message.strip()

    # Fetch or create conversation state
    conv = registry.get_or_create(
        conv_id,
        merchant_id=merchant_id or "",
        customer_id=body.customer_id,
    )

    # If conversation already ended, just confirm gracefully
    if conv.ended or store.is_conversation_ended(conv_id):
        return {
            "action": "end",
            "rationale": "Conversation was already closed; no further messages.",
        }

    # Record the incoming message
    conv.add_turn(body.from_role, message)

    # ── Phase 1: Rule-based classification ────────────────────────────────────
    classification = conv.classify_merchant_message(message)

    if classification == "opt_out":
        conv.ended = True
        conv.opted_out = True
        store.mark_conversation_ended(conv_id)
        if merchant_id:
            store.mark_merchant_opted_out(merchant_id)
        logger.info(f"conv={conv_id}: opt-out detected. Closing.")
        return {
            "action": "end",
            "rationale": (
                "Merchant signalled opt-out or expressed frustration. "
                "Closing conversation and suppressing this merchant for 30 days."
            ),
        }

    if classification == "auto_reply":
        conv.auto_reply_count += 1
        logger.info(f"conv={conv_id}: auto-reply #{conv.auto_reply_count}")

        if conv.auto_reply_count == 1:
            # First auto-reply: acknowledge + one gentle flag for the owner
            conv.add_turn("vera", "Looks like an auto-reply 😊 When the owner sees this, just reply YES to continue.")
            return {
                "action": "send",
                "body": "Looks like an auto-reply 😊 When the owner sees this, just reply YES to continue.",
                "cta": "binary_yes_no",
                "rationale": (
                    "Detected WhatsApp Business auto-reply (first occurrence). "
                    "Sending one gentle flag to the owner before backing off."
                ),
            }
        elif conv.auto_reply_count == 2:
            # Second auto-reply: back off 4h
            from datetime import timedelta
            conv.waiting_until = datetime.now(timezone.utc) + timedelta(
                seconds=config.AUTO_REPLY_WAIT_SECONDS
            )
            logger.info(f"conv={conv_id}: auto-reply x2 → wait 4h")
            return {
                "action": "wait",
                "wait_seconds": config.AUTO_REPLY_WAIT_SECONDS,
                "rationale": (
                    "Same auto-reply twice in a row → owner not at phone. "
                    "Waiting 4 hours before retry."
                ),
            }
        else:
            # Third+ auto-reply: close the conversation
            conv.ended = True
            store.mark_conversation_ended(conv_id)
            logger.info(f"conv={conv_id}: auto-reply x3+ → end")
            return {
                "action": "end",
                "rationale": (
                    f"Auto-reply detected {conv.auto_reply_count} times in a row. "
                    "No real engagement signal. Closing conversation."
                ),
            }

    if classification == "intent_transition":
        conv.intent_confirmed = True
        logger.info(f"conv={conv_id}: intent transition detected → ACTION MODE")

    # ── Phase 2: LLM reply composition ────────────────────────────────────────
    # Fetch latest context (may have been updated mid-test)
    merchant = store.get_merchant(merchant_id) if merchant_id else {}
    category_slug = (merchant or {}).get("category_slug", "")
    category = store.get_category(category_slug) or {}
    trigger_id = conv.trigger_id
    trigger = store.get_trigger(trigger_id) if trigger_id else None
    customer_id = body.customer_id or conv.customer_id
    customer = store.get_customer(customer_id) if customer_id else None

    result = compose_reply(
        category=category,
        merchant=merchant or {},
        trigger=trigger,
        customer=customer,
        conversation_history=conv.history,
        merchant_message=message,
        conversation_state=conv.get_state_label(),
    )

    action = result.get("action", "end")

    # Record what we sent
    if action == "send":
        body_text = result.get("body", "")
        conv.add_turn("vera", body_text)
    elif action == "end":
        conv.ended = True
        store.mark_conversation_ended(conv_id)

    logger.info(f"conv={conv_id}: reply action={action}")
    return result


# ─── POST /v1/teardown (optional) ─────────────────────────────────────────────

@app.post("/v1/teardown")
async def teardown():
    store.teardown()
    registry.clear()
    logger.info("Teardown complete — all state wiped.")
    return {"status": "wiped"}


# ─── Root ─────────────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return {
        "service": "Vera AI Bot",
        "version": config.BOT_VERSION,
        "team": config.TEAM_NAME,
        "healthz": "/v1/healthz",
        "metadata": "/v1/metadata",
    }
