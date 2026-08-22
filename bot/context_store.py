"""
context_store.py — Thread-safe in-memory context storage.

Stores all 4 context types (category, merchant, customer, trigger) with
version management. Handles idempotency, atomic upgrades, and suppression.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any


class ContextStore:
    """
    Central in-memory store for all 4 context types.
    Key = (scope, context_id); value = {version, payload}.
    Thread-safe via RLock.
    """

    def __init__(self):
        self._lock = threading.RLock()
        # (scope, context_id) -> {version: int, payload: dict}
        self._store: dict[tuple[str, str], dict] = {}
        # suppression_key -> True  (dedup — never send the same suppression key twice)
        self._suppressed: set[str] = set()
        # conversation_id -> True  (conversations ended by "end" action)
        self._ended_conversations: set[str] = set()
        # merchant_id -> timestamp  (merchants who opted out — suppress for N days)
        self._opted_out_merchants: dict[str, datetime] = {}

    # ── Context CRUD ───────────────────────────────────────────────────────────

    def push(self, scope: str, context_id: str, version: int, payload: dict) -> dict:
        """
        Store or upgrade a context.
        Returns {"accepted": bool, ...} matching the API contract.
        """
        key = (scope, context_id)
        now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        with self._lock:
            existing = self._store.get(key)
            if existing is not None:
                if existing["version"] >= version:
                    return {
                        "accepted": False,
                        "reason": "stale_version",
                        "current_version": existing["version"],
                    }
            self._store[key] = {"version": version, "payload": payload}

        return {
            "accepted": True,
            "ack_id": f"ack_{context_id}_v{version}",
            "stored_at": now_iso,
        }

    def get(self, scope: str, context_id: str) -> dict | None:
        """Return payload dict or None."""
        with self._lock:
            entry = self._store.get((scope, context_id))
            return entry["payload"] if entry else None

    def get_category(self, slug: str) -> dict | None:
        return self.get("category", slug)

    def get_merchant(self, merchant_id: str) -> dict | None:
        return self.get("merchant", merchant_id)

    def get_customer(self, customer_id: str) -> dict | None:
        return self.get("customer", customer_id)

    def get_trigger(self, trigger_id: str) -> dict | None:
        return self.get("trigger", trigger_id)

    def count_by_scope(self) -> dict[str, int]:
        """For healthz endpoint."""
        counts: dict[str, int] = {
            "category": 0, "merchant": 0, "customer": 0, "trigger": 0
        }
        with self._lock:
            for (scope, _) in self._store:
                if scope in counts:
                    counts[scope] += 1
        return counts

    # ── Suppression ────────────────────────────────────────────────────────────

    def is_suppressed(self, suppression_key: str) -> bool:
        with self._lock:
            return suppression_key in self._suppressed

    def suppress(self, suppression_key: str) -> None:
        with self._lock:
            self._suppressed.add(suppression_key)

    # ── Conversation lifecycle ─────────────────────────────────────────────────

    def mark_conversation_ended(self, conversation_id: str) -> None:
        with self._lock:
            self._ended_conversations.add(conversation_id)

    def is_conversation_ended(self, conversation_id: str) -> bool:
        with self._lock:
            return conversation_id in self._ended_conversations

    def mark_merchant_opted_out(self, merchant_id: str) -> None:
        with self._lock:
            self._opted_out_merchants[merchant_id] = datetime.now(timezone.utc)

    def is_merchant_opted_out(self, merchant_id: str) -> bool:
        from datetime import timedelta
        from bot.config import OPT_OUT_SUPPRESS_DAYS
        with self._lock:
            ts = self._opted_out_merchants.get(merchant_id)
            if ts is None:
                return False
            return (datetime.now(timezone.utc) - ts).days < OPT_OUT_SUPPRESS_DAYS

    def teardown(self) -> None:
        """Wipe all state (called by optional POST /v1/teardown)."""
        with self._lock:
            self._store.clear()
            self._suppressed.clear()
            self._ended_conversations.clear()
            self._opted_out_merchants.clear()


# Singleton instance shared across the FastAPI app
store = ContextStore()
