"""
generate_submission.py — Generates submission.jsonl for the 30 canonical test pairs.

Run AFTER setting GEMINI_API_KEY in your environment:
    set GEMINI_API_KEY=your_key_here
    python generate_submission.py
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

# ── Ensure the project root is on the path
sys.path.insert(0, str(Path(__file__).parent))

from bot.composer import compose
from bot.context_store import store
from bot.dispatcher import build_tick_action

EXPANDED_DIR = Path("dataset/expanded")
OUTPUT_FILE = Path("submission.jsonl")


def load_all_contexts():
    """Load all expanded dataset files into the context store."""
    print("Loading contexts into store...")

    # Categories
    for f in (EXPANDED_DIR / "categories").glob("*.json"):
        with open(f, encoding="utf-8") as fp:
            payload = json.load(fp)
        store.push("category", payload["slug"], 1, payload)

    # Merchants
    for f in (EXPANDED_DIR / "merchants").glob("*.json"):
        with open(f, encoding="utf-8") as fp:
            payload = json.load(fp)
        store.push("merchant", payload["merchant_id"], 1, payload)

    # Customers
    for f in (EXPANDED_DIR / "customers").glob("*.json"):
        with open(f, encoding="utf-8") as fp:
            payload = json.load(fp)
        store.push("customer", payload["customer_id"], 1, payload)

    # Triggers
    for f in (EXPANDED_DIR / "triggers").glob("*.json"):
        with open(f, encoding="utf-8") as fp:
            payload = json.load(fp)
        store.push("trigger", payload["id"], 1, payload)

    counts = store.count_by_scope()
    print(f"Loaded: {counts}")


def generate_submission():
    # Load the canonical test pairs
    test_pairs_file = EXPANDED_DIR / "test_pairs.json"
    with open(test_pairs_file, encoding="utf-8") as fp:
        raw = json.load(fp)
    # Handle both {"pairs": [...]} and direct list formats
    test_pairs = raw.get("pairs", raw) if isinstance(raw, dict) else raw

    print(f"\nGenerating {len(test_pairs)} submissions...\n")
    results = []

    for i, pair in enumerate(test_pairs, 1):
        test_id = pair.get("test_id", f"T{i:02d}")
        merchant_id = pair["merchant_id"]
        trigger_id = pair["trigger_id"]
        customer_id = pair.get("customer_id")

        merchant = store.get_merchant(merchant_id)
        trigger = store.get_trigger(trigger_id)
        category_slug = (merchant or {}).get("category_slug", "")
        category = store.get_category(category_slug)
        customer = store.get_customer(customer_id) if customer_id else None

        if not (merchant and trigger and category):
            print(f"  [{test_id}] SKIP — missing context (merchant={bool(merchant)}, trigger={bool(trigger)}, category={bool(category)})")
            continue

        print(f"  [{test_id}] {merchant.get('identity', {}).get('name', merchant_id)} / {trigger.get('kind', '?')}...", end=" ", flush=True)
        t0 = time.time()

        composed = compose(
            category=category,
            merchant=merchant,
            trigger=trigger,
            customer=customer,
        )

        elapsed = time.time() - t0

        if not composed or not composed.get("body"):
            print(f"FAILED ({elapsed:.1f}s)")
            continue

        record = {
            "test_id": test_id,
            "merchant_id": merchant_id,
            "trigger_id": trigger_id,
            "customer_id": customer_id,
            "body": composed["body"],
            "cta": composed.get("cta", "open_ended"),
            "send_as": composed.get("send_as", "vera"),
            "suppression_key": composed.get("suppression_key", trigger.get("suppression_key", "")),
            "rationale": composed.get("rationale", ""),
        }
        results.append(record)
        print(f"OK ({elapsed:.1f}s)")

        # Respectful pause between calls — avoids RPM rate limit on free tier
        time.sleep(3)

    # Write JSONL
    with open(OUTPUT_FILE, "w", encoding="utf-8") as fp:
        for r in results:
            fp.write(json.dumps(r, ensure_ascii=False) + "\n")

    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    print(f"\n[OK] Wrote {len(results)} lines to {OUTPUT_FILE}")
    print("\nSample (first entry):")
    if results:
        print(json.dumps(results[0], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    if not os.getenv("GEMINI_API_KEY"):
        print("ERROR: Set GEMINI_API_KEY environment variable first.")
        print("  Windows: set GEMINI_API_KEY=your_key_here")
        sys.exit(1)
    load_all_contexts()
    generate_submission()
