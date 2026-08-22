"""
conversation.py — Conversation state machine + auto-reply detection.

Tracks per-conversation state, detects auto-replies and opt-outs,
and routes intent transitions (qualification → action mode).
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone


# ─── Auto-reply detection patterns ────────────────────────────────────────────

_AUTO_REPLY_PATTERNS = [
    re.compile(r"thank you for contacting", re.IGNORECASE),
    re.compile(r"our team will (respond|get back to you|reach out)", re.IGNORECASE),
    re.compile(r"this is an (automated|automatic) (message|reply|response)", re.IGNORECASE),
    re.compile(r"aapki jaankari ke liye bahut.?bahut shukriya", re.IGNORECASE),
    re.compile(r"main aapki (yeh|yahi) (sabhi|sari) baatein.*team tak pahuncha", re.IGNORECASE),
    re.compile(r"we will (contact|call|reach) you (soon|shortly|back)", re.IGNORECASE),
    re.compile(r"business hours.*we('ll| will) respond", re.IGNORECASE),
    re.compile(r"currently (unavailable|busy|away|closed)", re.IGNORECASE),
]

_OPT_OUT_PATTERNS = [
    re.compile(r"\b(stop|unsubscribe|remove|opt.?out|do not (contact|message|call))\b", re.IGNORECASE),
    re.compile(r"\b(not interested|not relevant|nahi chahiye|mat bhejo|band karo)\b", re.IGNORECASE),
    re.compile(r"\b(leave me alone|stop messaging|stop (bothering|spamming))\b", re.IGNORECASE),
    re.compile(r"\b(bakwaas|useless|time waste|irritating|annoying)\b", re.IGNORECASE),
]

_INTENT_TRANSITION_PATTERNS = [
    re.compile(r"\b(yes|haan|ha|ok|okay|sure|let('?s| us) do (it|this))\b", re.IGNORECASE),
    re.compile(r"\b(go ahead|proceed|confirm|confirmed|agreed|done)\b", re.IGNORECASE),
    re.compile(r"\b(what('?s| is) next|kaise kare[ng]?|aage kya|please (proceed|do it|start))\b", re.IGNORECASE),
    re.compile(r"\b(send (it|me|the)|share (it|the)|draft (it|karo)|please (send|draft|share))\b", re.IGNORECASE),
]


def detect_auto_reply(message: str) -> bool:
    """Return True if the message looks like a WA Business auto-reply."""
    return any(p.search(message) for p in _AUTO_REPLY_PATTERNS)


def detect_opt_out(message: str) -> bool:
    """Return True if the merchant is explicitly opting out or expressing hostility."""
    return any(p.search(message) for p in _OPT_OUT_PATTERNS)


def detect_intent_transition(message: str) -> bool:
    """Return True if the merchant is signalling they want to proceed with an action."""
    return any(p.search(message) for p in _INTENT_TRANSITION_PATTERNS)


# ─── Conversation State ────────────────────────────────────────────────────────

@dataclass
class ConversationState:
    conversation_id: str
    merchant_id: str
    customer_id: str | None
    trigger_id: str | None

    # Turn tracking
    history: list[dict] = field(default_factory=list)  # [{from, body, ts}]
    turn_count: int = 0
    auto_reply_count: int = 0
    bodies_sent: list[str] = field(default_factory=list)

    # State flags
    ended: bool = False
    waiting_until: datetime | None = None
    opted_out: bool = False
    intent_confirmed: bool = False  # merchant said yes/go ahead

    def add_turn(self, from_role: str, body: str) -> None:
        ts = datetime.now(timezone.utc).isoformat()
        self.history.append({"from": from_role, "body": body, "ts": ts})
        self.turn_count += 1
        if from_role == "vera":
            self.bodies_sent.append(body)

    def classify_merchant_message(self, message: str) -> str:
        """
        Returns one of: 'opt_out', 'auto_reply', 'intent_transition', 'normal'
        """
        if detect_opt_out(message):
            return "opt_out"
        if detect_auto_reply(message):
            return "auto_reply"
        if detect_intent_transition(message):
            return "intent_transition"
        return "normal"

    def is_waiting(self) -> bool:
        if self.waiting_until is None:
            return False
        return datetime.now(timezone.utc) < self.waiting_until

    def get_state_label(self) -> str:
        if self.ended or self.opted_out:
            return "CLOSED"
        if self.is_waiting():
            return "WAITING_FOR_OWNER"
        if self.intent_confirmed:
            return "ACTION_MODE"
        if self.auto_reply_count > 0:
            return f"AUTO_REPLY_DETECTED_{self.auto_reply_count}x"
        if self.turn_count == 0:
            return "NEW"
        return "ENGAGED"


# ─── Global conversation registry ─────────────────────────────────────────────

class ConversationRegistry:
    def __init__(self):
        self._lock = threading.RLock()
        self._conversations: dict[str, ConversationState] = {}

    def get_or_create(
        self,
        conversation_id: str,
        merchant_id: str,
        customer_id: str | None = None,
        trigger_id: str | None = None,
    ) -> ConversationState:
        with self._lock:
            if conversation_id not in self._conversations:
                self._conversations[conversation_id] = ConversationState(
                    conversation_id=conversation_id,
                    merchant_id=merchant_id,
                    customer_id=customer_id,
                    trigger_id=trigger_id,
                )
            return self._conversations[conversation_id]

    def get(self, conversation_id: str) -> ConversationState | None:
        with self._lock:
            return self._conversations.get(conversation_id)

    def mark_ended(self, conversation_id: str) -> None:
        with self._lock:
            conv = self._conversations.get(conversation_id)
            if conv:
                conv.ended = True

    def clear(self) -> None:
        with self._lock:
            self._conversations.clear()


# Singleton
registry = ConversationRegistry()
