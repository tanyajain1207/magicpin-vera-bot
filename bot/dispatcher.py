"""
dispatcher.py — Routes trigger kinds to the right prompt variant.
Also builds the full action dict for /v1/tick responses.
"""

from __future__ import annotations
import hashlib
from bot.prompt_variants import (
    research_digest_prompt,
    regulation_change_prompt,
    recall_due_prompt,
    perf_dip_prompt,
    perf_spike_prompt,
    festival_upcoming_prompt,
    curious_ask_prompt,
    customer_lapsed_prompt,
    competitor_opened_prompt,
    milestone_reached_prompt,
    renewal_due_prompt,
    dormant_prompt,
    generic_prompt,
)

# ─── Trigger kind → prompt function mapping ────────────────────────────────────

_VARIANT_MAP: dict[str, callable] = {
    "research_digest":          research_digest_prompt,
    "regulation_change":        regulation_change_prompt,
    "recall_due":               recall_due_prompt,
    "appointment_tomorrow":     recall_due_prompt,       # similar slot-offering pattern
    "perf_dip":                 perf_dip_prompt,
    "seasonal_perf_dip":        perf_dip_prompt,
    "perf_spike":               perf_spike_prompt,
    "festival_upcoming":        festival_upcoming_prompt,
    "ipl_match_today":          festival_upcoming_prompt,
    "weather_heatwave":         festival_upcoming_prompt,
    "local_news_event":         festival_upcoming_prompt,
    "curious_ask_due":          curious_ask_prompt,
    "scheduled_recurring":      curious_ask_prompt,
    "customer_lapsed_soft":     customer_lapsed_prompt,
    "customer_lapsed_hard":     customer_lapsed_prompt,
    "competitor_opened":        competitor_opened_prompt,
    "milestone_reached":        milestone_reached_prompt,
    "renewal_due":              renewal_due_prompt,
    "dormant_with_vera":        dormant_prompt,
    "review_theme_emerged":     curious_ask_prompt,      # treat as curious ask
    "wedding_package_followup": recall_due_prompt,       # slot-offering pattern
    "category_trend_movement":  research_digest_prompt,  # treat like digest
    "supply_alert":             regulation_change_prompt, # compliance pattern
    "chronic_refill_due":       recall_due_prompt,
    "active_planning_intent":   generic_prompt,
    "unplanned_slot_open":      recall_due_prompt,
}


def get_prompt_fn(trigger_kind: str) -> callable:
    """Return the best prompt function for this trigger kind."""
    return _VARIANT_MAP.get(trigger_kind, generic_prompt)


def make_conversation_id(merchant_id: str, trigger_id: str) -> str:
    """Generate a stable, readable conversation ID."""
    # Shorten the IDs for readability while keeping uniqueness
    short = hashlib.md5(f"{merchant_id}:{trigger_id}".encode()).hexdigest()[:8]
    # Extract a human-readable slug from merchant_id
    parts = merchant_id.split("_")
    slug = parts[2] if len(parts) > 2 else merchant_id[:8]
    return f"conv_{slug}_{short}"


def build_tick_action(
    merchant_id: str,
    trigger_id: str,
    customer_id: str | None,
    composed: dict,
    trigger: dict,
) -> dict:
    """Build the full action dict for /v1/tick response."""
    conv_id = make_conversation_id(merchant_id, trigger_id)
    kind = trigger.get("kind", "generic")
    return {
        "conversation_id": conv_id,
        "merchant_id": merchant_id,
        "customer_id": customer_id,
        "send_as": composed.get("send_as", "vera"),
        "trigger_id": trigger_id,
        "template_name": composed.get("template_name", f"vera_{kind}_v1"),
        "template_params": composed.get("template_params", []),
        "body": composed.get("body", ""),
        "cta": composed.get("cta", "open_ended"),
        "suppression_key": composed.get("suppression_key", trigger.get("suppression_key", "")),
        "rationale": composed.get("rationale", ""),
    }
