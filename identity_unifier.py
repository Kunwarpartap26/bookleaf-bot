import os
import re
import json
from dataclasses import dataclass, field, asdict
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

# ── Data structures ─────────────────────────────────────────────────────────────
@dataclass
class PlatformIdentity:
    """A single platform-specific identity fragment."""
    platform: str           # "email" | "whatsapp" | "instagram" | "dashboard"
    value: str              # raw identifier value
    normalised: str = ""    # cleaned/normalised value

    def __post_init__(self):
        self.normalised = normalise_identifier(self.platform, self.value)


@dataclass
class UnifiedProfile:
    """A merged author profile across platforms."""
    author_id: str
    display_name: str
    identities: list[PlatformIdentity] = field(default_factory=list)
    confidence: float = 0.0
    match_reasons: list[str] = field(default_factory=list)
    needs_manual_review: bool = False
    review_reason: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["identities"] = [asdict(i) for i in self.identities]
        return d


# ── Normalisation ───────────────────────────────────────────────────────────────
def normalise_identifier(platform: str, value: str) -> str:
    """
    Clean and normalise an identifier based on its platform type.
    E.g., strip '+91', lowercase emails, remove '@' from Instagram handles.
    """
    v = value.strip()
    if platform == "email":
        return v.lower()
    elif platform == "whatsapp":
        # Remove country code +91, spaces, and dashes for Indian numbers
        digits = re.sub(r"[^\d]", "", v)
        if digits.startswith("91") and len(digits) == 12:
            digits = digits[2:]
        return digits[-10:] if len(digits) >= 10 else digits
    elif platform == "instagram":
        return v.lstrip("@").lower()
    elif platform == "dashboard":
        return v.lower().strip()
    return v.lower()


def extract_name_from_email(email: str) -> str:
    """Derive a likely first+last name from an email local part."""
    local = email.split("@")[0]
    # Replace separators with spaces
    name_part = re.sub(r"[._\-+]", " ", local)
    # Remove trailing digits (e.g., sara123 → sara)
    name_part = re.sub(r"\d+$", "", name_part)
    return name_part.strip().lower()


def extract_name_from_instagram(handle: str) -> str:
    """Strip digits and separators from an Instagram handle."""
    name_part = re.sub(r"[_\-.]", " ", handle)
    name_part = re.sub(r"\d+", "", name_part)
    return name_part.strip().lower()


# ── Fuzzy scoring ───────────────────────────────────────────────────────────────
def _fuzzy_score(a: str, b: str) -> float:
    """
    Return a 0–1 similarity score between two strings using rapidfuzz.
    Falls back to a simple character-overlap ratio if rapidfuzz is absent.
    """
    a, b = a.strip().lower(), b.strip().lower()
    if not a or not b:
        return 0.0
    try:
        from rapidfuzz import fuzz
        # Weighted combo: token_sort handles word-order differences (e.g., "Sara J." vs "J Sara")
        ratio = fuzz.ratio(a, b) / 100
        token_sort = fuzz.token_sort_ratio(a, b) / 100
        partial = fuzz.partial_ratio(a, b) / 100
        return round(max(ratio, token_sort) * 0.6 + partial * 0.4, 4)
    except ImportError:
        # Simple fallback
        shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
        matches = sum(1 for c in shorter if c in longer)
        return round(matches / max(len(longer), 1), 4)


# ── LLM-assisted matching ───────────────────────────────────────────────────────
def _llm_match(candidate_a: dict, candidate_b: dict) -> dict:
    """
    Use the OpenAI API to decide if two identity fragments belong to the same person.
    Returns {"same_person": bool, "confidence": float, "reasoning": str}.
    Falls back gracefully if OpenAI is unavailable.
    """
    try:
        from openai import OpenAI
        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        prompt = f"""You are an identity resolution assistant for a publishing company.

Given these two identity fragments, determine if they belong to the SAME author.

Identity A:
{json.dumps(candidate_a, indent=2)}

Identity B:
{json.dumps(candidate_b, indent=2)}

Respond ONLY with a JSON object (no markdown, no explanation outside the JSON):
{{
  "same_person": true/false,
  "confidence": 0.0-1.0,
  "reasoning": "one sentence"
}}"""
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=150,
        )
        raw = response.choices[0].message.content.strip()
        # Strip markdown fences if present
        raw = re.sub(r"```(?:json)?", "", raw).strip("` \n")
        result = json.loads(raw)
        return result
    except Exception as e:
        print(f"[ID] LLM match failed: {e} — skipping LLM assist")
        return {"same_person": None, "confidence": 0.0, "reasoning": "LLM unavailable"}


# ── Core matching logic ─────────────────────────────────────────────────────────
def compute_match_score(
    known_author: dict,
    incoming: PlatformIdentity,
) -> tuple[float, list[str]]:
    """
    Compare a known author record against an incoming identity.

    Returns (confidence_score, reasons_list).
    Confidence is computed as a weighted average of individual signal scores.
    """
    reasons: list[str] = []
    signals: list[tuple[float, float]] = []  # (score, weight)

    # ── Direct email match ──
    if incoming.platform == "email":
        if known_author.get("email", "").lower() == incoming.normalised:
            signals.append((1.0, 1.0))
            reasons.append("Exact email match")
        else:
            # Fuzzy on email local part vs name
            email_name = extract_name_from_email(incoming.normalised)
            author_name = known_author.get("name", "").lower()
            score = _fuzzy_score(email_name, author_name)
            if score > 0.5:
                signals.append((score, 0.6))
                reasons.append(f"Email local-part ≈ author name (score={score:.2f})")

    # ── WhatsApp match ──
    elif incoming.platform == "whatsapp":
        known_wa = normalise_identifier("whatsapp", known_author.get("whatsapp", ""))
        if known_wa and known_wa == incoming.normalised:
            signals.append((1.0, 0.95))
            reasons.append("Exact WhatsApp number match")
        elif known_wa:
            # Last 8 digits match (partial number)
            if known_wa[-8:] == incoming.normalised[-8:]:
                signals.append((0.85, 0.7))
                reasons.append("WhatsApp last-8-digits match")

    # ── Instagram match ──
    elif incoming.platform == "instagram":
        known_ig = normalise_identifier("instagram", known_author.get("instagram", ""))
        if known_ig and known_ig == incoming.normalised:
            signals.append((1.0, 0.9))
            reasons.append("Exact Instagram handle match")
        elif known_ig:
            # Fuzzy on handle vs name
            handle_name = extract_name_from_instagram(incoming.normalised)
            author_name = known_author.get("name", "").lower()
            score = _fuzzy_score(handle_name, author_name)
            if score > 0.5:
                signals.append((score, 0.55))
                reasons.append(f"Instagram handle ≈ author name (score={score:.2f})")

    # ── Dashboard name match ──
    elif incoming.platform == "dashboard":
        known_dash = normalise_identifier("dashboard", known_author.get("dashboard_name", ""))
        if known_dash and known_dash == incoming.normalised:
            signals.append((1.0, 0.85))
            reasons.append("Exact dashboard name match")
        elif known_dash:
            score = _fuzzy_score(known_dash, incoming.normalised)
            if score > 0.5:
                signals.append((score, 0.7))
                reasons.append(f"Dashboard name fuzzy match (score={score:.2f})")

    if not signals:
        return (0.0, ["No matching signals found"])

    # Weighted average
    total_weight = sum(w for _, w in signals)
    weighted_sum = sum(s * w for s, w in signals)
    confidence = weighted_sum / total_weight if total_weight > 0 else 0.0
    return (round(confidence, 4), reasons)


# ── Public API ──────────────────────────────────────────────────────────────────
def find_best_match(
    incoming: PlatformIdentity,
    author_db: list[dict],
    use_llm: bool = True,
) -> dict:
    """
    Search the author DB for the best match for a given incoming identity.

    Returns:
        {
            "matched_author": dict | None,
            "confidence": float,
            "reasons": list[str],
            "needs_manual_review": bool,
            "review_reason": str
        }
    """
    if not author_db:
        return {
            "matched_author": None, "confidence": 0.0,
            "reasons": ["Empty author DB"], "needs_manual_review": True,
            "review_reason": "No authors to match against"
        }

    scores: list[tuple[float, dict, list[str]]] = []
    for author in author_db:
        conf, reasons = compute_match_score(author, incoming)
        if conf > 0:
            scores.append((conf, author, reasons))

    if not scores:
        return {
            "matched_author": None, "confidence": 0.0,
            "reasons": ["No positive-scoring matches"], "needs_manual_review": True,
            "review_reason": "Zero-confidence for all authors"
        }

    scores.sort(key=lambda x: x[0], reverse=True)
    best_conf, best_author, best_reasons = scores[0]

    # If there's a close second candidate and confidence is borderline, use LLM
    if use_llm and len(scores) >= 2:
        second_conf = scores[1][0]
        if best_conf < 0.85 and (best_conf - second_conf) < 0.15:
            llm_result = _llm_match(
                {"name": best_author.get("name"), "email": best_author.get("email")},
                {"platform": incoming.platform, "value": incoming.value}
            )
            if llm_result.get("same_person") is True:
                best_conf = max(best_conf, llm_result.get("confidence", best_conf))
                best_reasons.append(f"LLM confirmed: {llm_result.get('reasoning', '')}")
            elif llm_result.get("same_person") is False:
                best_conf = min(best_conf, 0.65)
                best_reasons.append(f"LLM rejected: {llm_result.get('reasoning', '')}")

    needs_review = best_conf < 0.70
    return {
        "matched_author": best_author if best_conf > 0.3 else None,
        "confidence": best_conf,
        "reasons": best_reasons,
        "needs_manual_review": needs_review,
        "review_reason": "Confidence below 70% threshold" if needs_review else ""
    }


def merge_identities(
    author_record: dict,
    new_identity: PlatformIdentity,
) -> dict:
    """
    Merge a new platform identity into an existing author record.
    Adds the new identity channel if it doesn't already exist.
    """
    platform = new_identity.platform
    key_map = {
        "email": "email",
        "whatsapp": "whatsapp",
        "instagram": "instagram",
        "dashboard": "dashboard_name"
    }
    field_key = key_map.get(platform)
    if field_key and not author_record.get(field_key):
        author_record[field_key] = new_identity.value
        author_record["_merged_from"] = author_record.get("_merged_from", [])
        author_record["_merged_from"].append(platform)
    return author_record


def flag_for_manual_review(
    incoming: PlatformIdentity,
    match_result: dict,
    reviewer_queue: list | None = None,
) -> dict:
    """
    Add a low-confidence match to the manual review queue.
    If reviewer_queue is provided, appends the entry in-place.
    Returns the review entry dict.
    """
    entry = {
        "incoming_platform": incoming.platform,
        "incoming_value": incoming.value,
        "best_candidate": (match_result.get("matched_author") or {}).get("name", "Unknown"),
        "best_candidate_email": (match_result.get("matched_author") or {}).get("email", ""),
        "confidence": match_result.get("confidence", 0.0),
        "reasons": match_result.get("reasons", []),
        "review_reason": match_result.get("review_reason", "Low confidence"),
        "status": "pending_review"
    }
    if reviewer_queue is not None:
        reviewer_queue.append(entry)
    return entry


# ── Demo runner ─────────────────────────────────────────────────────────────────
def run_demo():
    """
    Run a quick demo of the identity unification system against mock data.
    Prints results to stdout.
    """
    from mock_data import MOCK_AUTHORS

    test_cases = [
        PlatformIdentity(platform="email",     value="priya.sharma@gmail.com"),
        PlatformIdentity(platform="whatsapp",  value="+91 9123456789"),
        PlatformIdentity(platform="instagram", value="@kavithanairpoetry"),
        PlatformIdentity(platform="dashboard", value="R. Desai"),
        PlatformIdentity(platform="instagram", value="@unknownwriter99"),  # should flag
        PlatformIdentity(platform="whatsapp",  value="+91 9000000000"),    # no match
    ]

    print("\n" + "═" * 60)
    print("  BookLeaf Identity Unifier — Demo Run")
    print("═" * 60)

    review_queue: list = []

    for identity in test_cases:
        result = find_best_match(identity, MOCK_AUTHORS, use_llm=False)
        author = result.get("matched_author")
        conf = result["confidence"]
        status = " MATCHED" if author and conf >= 0.70 else "  REVIEW"

        print(f"\n{status}  {identity.platform.upper()} → '{identity.value}'")
        if author:
            print(f"  Best match  : {author.get('name')} ({author.get('email')})")
        print(f"  Confidence  : {conf:.0%}")
        print(f"  Reasons     : {', '.join(result['reasons'])}")

        if result["needs_manual_review"]:
            entry = flag_for_manual_review(identity, result, review_queue)
            print(f"  Flagged     : {entry['review_reason']}")

    print(f"\n📋 Manual Review Queue: {len(review_queue)} item(s) flagged")
    for item in review_queue:
        print(f"   • [{item['incoming_platform']}] {item['incoming_value']} "
              f"(confidence={item['confidence']:.0%})")
    print()


if __name__ == "__main__":
    run_demo()
