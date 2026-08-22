"""
smoke_test.py - Quick local test to verify the bot works before deploying.
Run: python smoke_test.py
"""

import os, sys, json, io

# Force UTF-8 output
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# Check API key
if not os.getenv("GEMINI_API_KEY"):
    print("ERROR: Set GEMINI_API_KEY first.")
    sys.exit(1)

print(">> Running Vera Bot smoke tests...\n")

# ---------- Test 1: Context store ----------
from bot.context_store import store

store.push("category", "dentists", 1, {
    "slug": "dentists",
    "voice": {"tone": "peer_clinical", "vocab_taboo": ["guaranteed"]},
    "offer_catalog": [{"id": "den_001", "title": "Dental Cleaning @ Rs.299", "status": "active"}],
    "peer_stats": {"avg_ctr": 0.030, "avg_rating": 4.4, "avg_review_count": 62},
    "digest": [{"id": "d_test", "kind": "research",
                "title": "3-month fluoride recall cuts caries 38%",
                "source": "JIDA Oct 2026, p.14", "trial_n": 2100,
                "patient_segment": "high_risk_adults",
                "summary": "Multi-center trial result"}],
    "seasonal_beats": [],
    "trend_signals": []
})
store.push("merchant", "m_test_001", 1, {
    "merchant_id": "m_test_001",
    "category_slug": "dentists",
    "identity": {
        "name": "Dr. Test Clinic", "city": "Delhi", "locality": "Saket",
        "languages": ["en", "hi"], "owner_first_name": "Test"
    },
    "performance": {"views": 1500, "calls": 10, "ctr": 0.020, "window_days": 30,
                    "delta_7d": {"views_pct": 0.05, "calls_pct": -0.1}},
    "offers": [{"id": "o1", "title": "Dental Cleaning @ Rs.299", "status": "active"}],
    "signals": ["ctr_below_peer_median", "stale_posts:22d"],
    "customer_aggregate": {"total_unique_ytd": 200, "lapsed_180d_plus": 40,
                           "retention_6mo_pct": 0.35, "high_risk_adult_count": 60},
    "conversation_history": [],
    "subscription": {"status": "active", "plan": "Pro", "days_remaining": 45},
    "review_themes": []
})
store.push("trigger", "trg_test_001", 1, {
    "id": "trg_test_001", "scope": "merchant", "kind": "research_digest",
    "source": "external", "merchant_id": "m_test_001", "customer_id": None,
    "payload": {"category": "dentists", "top_item_id": "d_test"},
    "urgency": 2, "suppression_key": "test:smoke:001",
    "expires_at": "2026-12-31T00:00:00Z"
})

counts = store.count_by_scope()
assert counts["category"] >= 1 and counts["merchant"] >= 1 and counts["trigger"] >= 1
print("PASS Test 1: Context store - counts:", counts)

# ---------- Test 2: Auto-reply detection ----------
from bot.conversation import detect_auto_reply, detect_opt_out, detect_intent_transition

assert detect_auto_reply("Thank you for contacting us! Our team will respond shortly.")
assert detect_auto_reply("Aapki jaankari ke liye bahut-bahut shukriya.")
assert not detect_auto_reply("Yes please send me the abstract")
print("PASS Test 2: Auto-reply detection")

assert detect_opt_out("Not interested. Stop messaging me.")
assert not detect_opt_out("Yes I want to proceed")
print("PASS Test 3: Opt-out detection")

assert detect_intent_transition("Ok lets do it")
assert detect_intent_transition("Yes please proceed")
assert not detect_intent_transition("What does this mean?")
print("PASS Test 4: Intent transition detection")

# ---------- Test 5: LLM composition (calls Gemini API) ----------
print("\n>> Test 5: LLM composition (calling Gemini API)...")
from bot.composer import compose

category = store.get_category("dentists")
merchant = store.get_merchant("m_test_001")
trigger = store.get_trigger("trg_test_001")

result = compose(category=category, merchant=merchant, trigger=trigger)

if result and result.get("body"):
    print("PASS Test 5: Composition successful!")
    print("\n--- COMPOSED MESSAGE ---")
    print(result["body"])
    print("\nCTA:", result.get("cta"))
    print("Send as:", result.get("send_as"))
    print("Rationale:", result.get("rationale", "")[:120] + "...")
    print("------------------------\n")
else:
    print("FAIL Test 5: Composition returned empty. Check API key.")
    sys.exit(1)

print(">> ALL TESTS PASSED! Bot is ready.\n")
print("Next steps:")
print("  1. python generate_submission.py   (generates submission.jsonl)")
print("  2. uvicorn bot.main:app --host 0.0.0.0 --port 8080")
print("  3. Deploy to Railway for public URL")
