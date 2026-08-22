"""
composer.py — Core LLM composition engine using Google Gemini.

Calls Gemini with the appropriate prompt variant, validates the output,
and re-prompts once if validation fails.
"""

from __future__ import annotations

import json
import logging
import re
import time

import google.generativeai as genai

from bot.config import (
    GEMINI_API_KEY,
    GEMINI_PRIMARY_MODEL,
    GEMINI_FALLBACK_MODEL,
    LLM_TEMPERATURE,
)
from bot.dispatcher import get_prompt_fn, build_tick_action
from bot.prompt_variants import reply_composer_prompt

logger = logging.getLogger(__name__)

# ─── Gemini client setup ───────────────────────────────────────────────────────

def _get_model(model_name: str):
    genai.configure(api_key=GEMINI_API_KEY)
    return genai.GenerativeModel(
        model_name=model_name,
        generation_config=genai.types.GenerationConfig(
            temperature=LLM_TEMPERATURE,
            max_output_tokens=2048,  # generous budget for full JSON response
        ),
    )


def _repair_truncated_json(raw: str) -> dict:
    """
    Attempt to repair truncated JSON by extracting known fields with regex.
    Used when the model response is cut off before the closing brace.
    """
    result = {}

    # Extract "body" field
    body_match = re.search(r'"body"\s*:\s*"((?:[^"\\]|\\.)*)["\\]?', raw)
    if body_match:
        try:
            result["body"] = json.loads('"' + body_match.group(1) + '"')
        except Exception:
            result["body"] = body_match.group(1)

    # Extract "cta" field
    cta_match = re.search(r'"cta"\s*:\s*"([^"]+)"', raw)
    if cta_match:
        result["cta"] = cta_match.group(1)

    # Extract "send_as" field
    send_match = re.search(r'"send_as"\s*:\s*"([^"]+)"', raw)
    if send_match:
        result["send_as"] = send_match.group(1)

    # Extract "suppression_key" field
    supp_match = re.search(r'"suppression_key"\s*:\s*"([^"]*)"', raw)
    if supp_match:
        result["suppression_key"] = supp_match.group(1)

    # Extract "rationale" field
    rat_match = re.search(r'"rationale"\s*:\s*"((?:[^"\\]|\\.)*)"?', raw)
    if rat_match:
        try:
            result["rationale"] = json.loads('"' + rat_match.group(1) + '"')
        except Exception:
            result["rationale"] = rat_match.group(1)

    # Extract "template_name"
    tmpl_match = re.search(r'"template_name"\s*:\s*"([^"]*)"', raw)
    if tmpl_match:
        result["template_name"] = tmpl_match.group(1)

    # Set defaults for missing required fields
    if "cta" not in result:
        result["cta"] = "open_ended"
    if "send_as" not in result:
        result["send_as"] = "vera"
    if "template_params" not in result:
        result["template_params"] = []

    if result.get("body"):
        return result

    raise ValueError(f"Could not repair truncated JSON — no body found in: {raw[:300]}")


def _extract_json(raw: str) -> dict:
    """
    Robustly extract JSON from LLM output.
    Handles: markdown fences, preamble text, truncated responses.
    """
    if not raw or not raw.strip():
        raise ValueError("Empty response from LLM")

    # Strip markdown fences
    cleaned = re.sub(r"^```(?:json)?\s*\n?", "", raw.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"\n?```\s*$", "", cleaned).strip()

    # Try direct parse first
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Try to find a complete JSON object
    match = re.search(r"\{[\s\S]*\}", cleaned)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    # Try from first { to last }
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(cleaned[start:end+1])
        except json.JSONDecodeError:
            pass

    # Try closing the JSON if it's truncated (add missing closing brace)
    if start != -1:
        # Count open braces to figure out how many closing braces we need
        candidate = cleaned[start:]
        opens = candidate.count("{")
        closes = candidate.count("}")
        missing = opens - closes
        if missing > 0:
            patched = candidate + "}" * missing
            try:
                return json.loads(patched)
            except json.JSONDecodeError:
                pass

    # Last resort: field-by-field regex extraction
    return _repair_truncated_json(cleaned)


def _call_llm(prompt: str, model_name: str = GEMINI_PRIMARY_MODEL) -> dict:
    """Call Gemini and parse the JSON response. Retries up to 3 times on empty response."""
    model = _get_model(model_name)

    last_err = None
    for attempt in range(3):
        try:
            response = model.generate_content(prompt)
            if not response.text or not response.text.strip():
                raise ValueError(f"Empty response from model {model_name} (attempt {attempt+1})")
            return _extract_json(response.text)

        except Exception as e:
            last_err = e
            if attempt < 2:
                # Try to parse the recommended retry_delay from a 429 error message
                api_wait = 0
                err_str = str(e)
                delay_match = re.search(r"retry_delay\s*\{\s*seconds:\s*(\d+)", err_str)
                if delay_match:
                    api_wait = int(delay_match.group(1))

                wait = max((attempt + 1) * 2, api_wait)
                logger.warning(f"LLM attempt {attempt+1} failed: {str(e)[:120]}. Retrying in {wait}s...")
                time.sleep(wait)

    raise last_err


def _call_llm_with_fallback(prompt: str) -> dict:
    """Try primary → fallback → fallback2 → raise."""
    from bot.config import GEMINI_FALLBACK_MODEL_2

    for model_name in (GEMINI_PRIMARY_MODEL, GEMINI_FALLBACK_MODEL, GEMINI_FALLBACK_MODEL_2):
        try:
            return _call_llm(prompt, model_name)
        except Exception as e:
            logger.warning(f"Model {model_name} failed: {str(e)[:120]}. Trying next model.")

    raise ValueError("All LLM models exhausted")


# ─── Validation ───────────────────────────────────────────────────────────────

_URL_PATTERN = re.compile(r"https?://\S+")
_VALID_CTAS = {"open_ended", "binary_yes_no", "binary_confirm_cancel", "none", "multi_choice_slot"}
_VALID_SEND_AS = {"vera", "merchant_on_behalf"}


def _validate(composed: dict, prior_bodies: list[str]) -> list[str]:
    """Return list of issues; empty = valid."""
    issues = []
    body = composed.get("body", "")
    if not body or not body.strip():
        issues.append("Empty body")
    if _URL_PATTERN.search(body):
        issues.append("URL in body — will be rejected by Meta")
    if body in prior_bodies:
        issues.append("Duplicate body — anti-repetition violation")
    if composed.get("cta") not in _VALID_CTAS:
        # Auto-correct instead of penalising
        composed["cta"] = "open_ended"
    if composed.get("send_as") not in _VALID_SEND_AS:
        composed["send_as"] = "vera"
    return issues


# ─── Main compose function ─────────────────────────────────────────────────────

def compose(
    category: dict,
    merchant: dict,
    trigger: dict,
    customer: dict | None = None,
    prior_bodies: list[str] | None = None,
) -> dict | None:
    """
    Compose a message using the 4-context framework.
    Returns the full composed dict or None if composition fails.
    """
    prior_bodies = prior_bodies or []
    kind = trigger.get("kind", "generic")
    prompt_fn = get_prompt_fn(kind)

    t0 = time.time()
    try:
        prompt = prompt_fn(category, merchant, trigger, customer)
        composed = _call_llm_with_fallback(prompt)

        issues = _validate(composed, prior_bodies)
        if issues:
            logger.warning(f"Validation issues for {kind}: {issues}. Re-prompting...")
            fix_instruction = (
                f"\n\nYour previous response had these issues: {issues}. "
                "Please fix them and return corrected JSON only."
            )
            composed = _call_llm_with_fallback(prompt + fix_instruction)
            _validate(composed, prior_bodies)  # Accept result even if issues remain

        elapsed = time.time() - t0
        logger.info(f"Composed for {kind} in {elapsed:.1f}s — body length: {len(composed.get('body',''))}")
        return composed

    except Exception as e:
        logger.error(f"Main compose failed for kind={kind}: {e}. Trying minimal prompt.")
        # Minimal fallback — short, punchy, uses key facts only
        try:
            merchant_name = merchant.get("identity", {}).get("name", "")
            owner = merchant.get("identity", {}).get("owner_first_name", merchant_name.split()[0] if merchant_name else "")
            city = merchant.get("identity", {}).get("city", "")
            languages = merchant.get("identity", {}).get("languages", ["en"])
            is_hindi = "hi" in languages

            active_offers = [o["title"] for o in merchant.get("offers", []) if o.get("status") == "active"]
            offer_str = active_offers[0] if active_offers else ""
            peer_ctr = category.get("peer_stats", {}).get("avg_ctr", 0.030)
            merchant_ctr = merchant.get("performance", {}).get("ctr", 0)

            # Build a minimal but specific message
            if is_hindi:
                body = f"{owner} ji, aapka magicpin profile check kiya — CTR {merchant_ctr:.1%} hai, peer median {peer_ctr:.1%} hai. "
                if offer_str:
                    body += f"Active offer '{offer_str}' hai. "
                body += "Kya main ek Google post draft karun jo visibility badhaye? Reply YES."
            else:
                body = f"Hi {owner}, checked your magicpin profile — CTR is {merchant_ctr:.1%} vs peer median {peer_ctr:.1%}. "
                if offer_str:
                    body += f"Your active offer '{offer_str}' is live. "
                body += "Want me to draft a Google post to boost visibility? Reply YES."

            minimal = {
                "body": body,
                "cta": "binary_yes_no",
                "send_as": "vera",
                "template_name": f"vera_{kind}_v1",
                "template_params": [owner, city, offer_str],
                "suppression_key": trigger.get("suppression_key", ""),
                "rationale": f"Minimal fallback compose for {kind} — used peer CTR and active offer anchor.",
            }
            logger.info(f"Minimal fallback compose succeeded for {kind}")
            return minimal
        except Exception as e2:
            logger.error(f"Minimal fallback also failed: {e2}")
            return None


def compose_reply(
    category: dict,
    merchant: dict,
    trigger: dict | None,
    customer: dict | None,
    conversation_history: list[dict],
    merchant_message: str,
    conversation_state: str,
) -> dict:
    """
    Compose the next action in an ongoing conversation.
    Returns {action, body?, cta?, wait_seconds?, rationale}.
    """
    prompt = reply_composer_prompt(
        category=category,
        merchant=merchant,
        trigger=trigger,
        customer=customer,
        conversation_history=conversation_history,
        merchant_message=merchant_message,
        conversation_state=conversation_state,
    )

    try:
        result = _call_llm_with_fallback(prompt)
        action = result.get("action", "end")
        if action not in ("send", "wait", "end"):
            result["action"] = "end"
            result["rationale"] = "Unknown action returned; defaulting to end."
        return result
    except Exception as e:
        logger.error(f"Reply composition failed: {e}")
        return {"action": "end", "rationale": f"Composition error: {str(e)[:100]}"}
