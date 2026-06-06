import os
import re
import json
from datetime import datetime
from flask import Flask, request, jsonify, render_template
from dotenv import load_dotenv

load_dotenv()

# ── App init ───────────────────────────────────────────────────────────────────
app = Flask(__name__)

# ── Lazy imports (so app boots even without all packages installed) ─────────────
def _get_openai_client():
    from openai import OpenAI
    return OpenAI(api_key=os.getenv("OPENAI_API_KEY"), base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"))


# ══════════════════════════════════════════════════════════════════════════════
# INTENT CLASSIFICATION
# ══════════════════════════════════════════════════════════════════════════════

INTENTS = [
    "book_live_status",       # "Is my book live?", "when does my book go live?"
    "royalty_status",         # "When do I get paid?", "what is my royalty?"
    "author_copy",            # "Where is my author copy?", "author copy delivery"
    "add_on_status",          # "What add-ons do I have?", "is my PR package active?"
    "isbn_query",             # "What is my ISBN?"
    "submission_date",        # "When did I submit my manuscript?"
    "general_faq",            # anything covered by the knowledge base
    "unknown",                # unrecognised intent
]

INTENT_PROMPT = """You are an intent classifier for BookLeaf Publishing's author support bot.

Classify the author's query into EXACTLY ONE of these intents:
- book_live_status    → questions about whether the book is published/live
- royalty_status      → questions about royalty payments, earnings, payment dates
- author_copy         → questions about physical author copies, shipment, delivery
- add_on_status       → questions about purchased add-on services (PR, Bestseller, Award, Editorial)
- isbn_query          → questions about ISBN number
- submission_date     → questions about manuscript submission or final submission date
- general_faq         → general questions about BookLeaf process, timelines, dashboard, contact info
- unknown             → cannot be classified with confidence

Also return a confidence score (0.0 to 1.0) for your classification.

Respond ONLY with this JSON (no markdown, no extra text):
{"intent": "<intent_name>", "confidence": <float>}

Author query: "{query}"
"""

KEYWORD_INTENT_MAP = {
    "book_live_status":  ["live", "published", "publish", "available", "launch", "release", "go live", "went live"],
    "royalty_status":    ["royalty", "royalties", "paid", "payment", "money", "earn", "revenue", "payout", "quarter"],
    "author_copy":       ["author copy", "copies", "shipment", "delivery", "dispatch", "physical", "print"],
    "add_on_status":     ["add-on", "addon", "add on", "bestseller", "pr campaign", "award", "editorial", "package"],
    "isbn_query":        ["isbn", "book number", "book code"],
    "submission_date":   ["submit", "submission", "manuscript", "final submission", "when did i submit"],
    "general_faq":       ["dashboard", "login", "access", "contact", "support", "timeline", "process", "how long"],
}


def classify_intent_with_llm(query: str) -> dict:
    """
    Use OpenAI to classify the query intent.
    Returns {"intent": str, "confidence": float, "method": "llm"}.
    """
    try:
        client = _get_openai_client()
        prompt = INTENT_PROMPT.replace("{query}", query)
        response = client.chat.completions.create(
            model=os.getenv("MODEL_NAME", "meta-llama/llama-3.1-8b-instruct:free"),
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            timeout=5,
            max_tokens=80,
        )
        raw = response.choices[0].message.content.strip()
        raw = re.sub(r"```(?:json)?", "", raw).strip("` \n")
        result = json.loads(raw)
        return {
            "intent": result.get("intent", "unknown"),
            "confidence": float(result.get("confidence", 0.5)),
            "method": "llm",
        }
    except Exception as e:
        print(f"[App] LLM classification failed: {e} — using keyword fallback")
        return classify_intent_with_keywords(query)


def classify_intent_with_keywords(query: str) -> dict:
    """
    Keyword-based intent classification fallback.
    Returns {"intent": str, "confidence": float, "method": "keyword"}.
    """
    q = query.lower()
    best_intent = "unknown"
    best_score = 0

    for intent, keywords in KEYWORD_INTENT_MAP.items():
        score = sum(1 for kw in keywords if kw in q)
        if score > best_score:
            best_score = score
            best_intent = intent

    # Confidence: 1 keyword hit → 0.65, 2+ → 0.75 (keyword is inherently less certain)
    confidence = 0.0 if best_score == 0 else (0.80 if best_score == 1 else 0.90)
    return {"intent": best_intent, "confidence": confidence, "method": "keyword"}


# ══════════════════════════════════════════════════════════════════════════════
# RESPONSE BUILDER
# ══════════════════════════════════════════════════════════════════════════════

def build_author_response(intent: str, author: dict) -> str:
    """
    Format a structured, friendly response based on the intent and author data.
    """
    name = author.get("name", "Author")
    first_name = name.split()[0] if name else "there"
    add_ons = author.get("add_on_services") or []
    if isinstance(add_ons, str):
        try:
            add_ons = json.loads(add_ons)
        except Exception:
            add_ons = [add_ons]
    add_ons_str = ", ".join(add_ons) if add_ons else "None"

    if intent == "book_live_status":
        live_date = author.get("book_live_date")
        if live_date:
            return (
                f"Hi {first_name}!  Your book **\"{author.get('book_title')}\"** "
                f"went live on **{live_date}**. It's available on Amazon, Flipkart, "
                f"Google Play Books, and the BookLeaf store. "
                f"You can verify the links by logging into your Author Dashboard."
            )
        else:
            sub_date = author.get("final_submission_date", "recently")
            return (
                f"Hi {first_name}! Your book **\"{author.get('book_title')}\"** is not yet live. "
                f"Your final submission was received on **{sub_date}**. "
                f"Standard publishing takes 45–60 days from submission. "
                f"We'll email you once your book is live! "
            )

    elif intent == "royalty_status":
        royalty = author.get("royalty_status", "Status unavailable")
        return (
            f"Hi {first_name}!  Here's your royalty status for "
            f"**\"{author.get('book_title')}\"**:\n\n"
            f"**Status:** {royalty}\n\n"
            f"Royalties are paid quarterly (Q1→Apr 30, Q2→Jul 31, Q3→Oct 31, Q4→Jan 31). "
            f"You can view your full sales report on the Author Dashboard."
        )

    elif intent == "author_copy":
        live_date = author.get("book_live_date")
        if live_date:
            return (
                f"Hi {first_name}!  Your book **\"{author.get('book_title')}\"** "
                f"is live (since {live_date}), so you can order author copies anytime.\n\n"
                f"**Your Add-ons:** {add_ons_str}\n\n"
                f"To order copies: Dashboard → My Books → Order Author Copies. "
                f"Dispatch takes 7–10 business days via Speed Post/Delhivery."
            )
        else:
            return (
                f"Hi {first_name}! Author copies can be ordered once your book is live. "
                f"Your book **\"{author.get('book_title')}\"** is currently in the publishing pipeline. "
                f"We'll notify you by email when it goes live and copies become available for order. 📬"
            )

    elif intent == "add_on_status":
        return (
            f"Hi {first_name}!  Here are the add-on services on your account "
            f"for **\"{author.get('book_title')}\"**:\n\n"
            f"**Active Add-ons:** {add_ons_str}\n\n"
            f"For detailed status (Active / Pending / Completed) on each add-on, "
            f"visit: Dashboard → My Add-ons. "
            f"Questions? Email support@bookleaf.in."
        )

    elif intent == "isbn_query":
        isbn = author.get("isbn", "Not yet assigned")
        return (
            f"Hi {first_name}!  The ISBN for **\"{author.get('book_title')}\"** is:\n\n"
            f"**ISBN:** {isbn}\n\n"
            f"Your ISBN certificate is available for download under "
            f"Dashboard → Documents."
        )

    elif intent == "submission_date":
        sub_date = author.get("final_submission_date", "Not on record")
        return (
            f"Hi {first_name}!  Your final manuscript submission date for "
            f"**\"{author.get('book_title')}\"** is recorded as:\n\n"
            f"**Submission Date:** {sub_date}\n\n"
            f"If this looks incorrect, please email editorial@bookleaf.in."
        )

    else:
        # Generic summary for unknown DB intents
        live_date = author.get("book_live_date") or "Pending"
        royalty = author.get("royalty_status", "N/A")
        return (
            f"Hi {first_name}! Here's a summary for your account "
            f"(**\"{author.get('book_title')}\"**):\n\n"
            f" **Book Live:** {live_date}\n"
            f" **Royalty:** {royalty}\n"
            f" **Add-ons:** {add_ons_str}\n\n"
            f"For more details, visit your Author Dashboard or email support@bookleaf.in."
        )


def escalation_response(query: str, confidence: float) -> str:
    """Return a human-escalation message for low-confidence situations."""
    return (
        f" I wasn't confident enough to automatically answer your query "
        f"(confidence: {confidence:.0%}). "
        f"Your message has been logged and will be reviewed by a BookLeaf support agent.\n\n"
        f"**What happens next:**\n"
        f"Our team will respond to your registered email within 1 business day (Mon–Sat, 10 AM–6 PM IST).\n\n"
        f"For urgent queries: WhatsApp +91 98100 00000 or email support@bookleaf.in."
    )


# ══════════════════════════════════════════════════════════════════════════════
# FLASK ROUTES
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/")
def index():
    """Serve the chat UI."""
    return render_template("index.html")


@app.route("/health", methods=["GET"])
def health():
    """Health check endpoint."""
    from database import health_check
    db_status = health_check()
    return jsonify({
        "status": "ok",
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "database": db_status,
    })


@app.route("/chat", methods=["POST"])
def chat():
    """
    Main chat endpoint.
    Accepts JSON: {"email": "author@email.com", "query": "Is my book live?"}
    Returns JSON: {"response": str, "confidence": float, "escalated": bool, "intent": str, "source": str}
    """
    # ── Parse request ──
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Invalid JSON body"}), 400

    email = (data.get("email") or "").strip().lower()
    query = (data.get("query") or "").strip()

    if not query:
        return jsonify({"error": "Query is required"}), 400

    # ── Classify intent ──
    classification = classify_intent_with_keywords(query)
    if classification["intent"] == "unknown" or classification["confidence"] == 0.0:
        classification = classify_intent_with_llm(query)
    intent = classification["intent"]
    confidence = classification["confidence"]
    method = classification["method"]

    print(f"[App] Query: '{query}' | Intent: {intent} | Conf: {confidence:.2f} | Method: {method}")

    # ── Escalate if confidence < 0.8 ──
    if confidence < 0.60:
        response_text = escalation_response(query, confidence)
        _log(email, query, intent, response_text, confidence, escalated=True, source="escalation")
        return jsonify({
            "response": response_text,
            "confidence": confidence,
            "escalated": True,
            "intent": intent,
            "source": "escalation",
        })

    # ── DB lookup if email provided and intent is data-specific ──
    db_intents = {
        "book_live_status", "royalty_status", "author_copy",
        "add_on_status", "isbn_query", "submission_date",
    }

    if email and intent in db_intents:
        author = _fetch_author(email)

        if isinstance(author, str):
            # Error string returned
            _log(email, query, intent, author, confidence, escalated=False, source="db_error")
            return jsonify({
                "response": author,
                "confidence": confidence,
                "escalated": False,
                "intent": intent,
                "source": "db_error",
            })

        if author:
            response_text = build_author_response(intent, author)
            _log(email, query, intent, response_text, confidence, escalated=False, source="db")
            return jsonify({
                "response": response_text,
                "confidence": confidence,
                "escalated": False,
                "intent": intent,
                "source": "db",
            })
        else:
            # No match — fall through to KB or escalate
            no_match_msg = (
                f"I couldn't find an account matching **{email}**. "
                f"Please double-check your registered email address. "
                f"If you registered with a different email, try that — "
                f"or contact support@bookleaf.in for help locating your account."
            )
            _log(email, query, intent, no_match_msg, confidence, escalated=False, source="no_match")
            return jsonify({
                "response": no_match_msg,
                "confidence": confidence,
                "escalated": False,
                "intent": intent,
                "source": "no_match",
            })

    # ── Knowledge base fallback (general FAQ or no email provided) ──
    kb_result = _search_kb(query)
    kb_answer = kb_result.get("answer", "")
    kb_confidence = kb_result.get("confidence", 0.0)

    if kb_answer and kb_confidence > 0.4:
        response_text = (
            f"📖 **BookLeaf Knowledge Base:**\n\n{kb_answer}\n\n"
            f"*For account-specific queries, please include your registered email.*"
        )
        final_confidence = min(confidence, kb_confidence)
        _log(email, query, intent, response_text, final_confidence, escalated=False, source="kb")
        return jsonify({
            "response": response_text,
            "confidence": final_confidence,
            "escalated": False,
            "intent": intent,
            "source": "kb",
        })

    # ── Last resort escalation ──
    response_text = escalation_response(query, confidence)
    _log(email, query, intent, response_text, confidence, escalated=True, source="escalation_final")
    return jsonify({
        "response": response_text,
        "confidence": confidence,
        "escalated": True,
        "intent": intent,
        "source": "escalation",
    })


@app.route("/logs", methods=["GET"])
def get_logs():
    """Return recent query logs (for debugging/admin)."""
    from database import get_recent_logs
    limit = int(request.args.get("limit", 20))
    logs = get_recent_logs(limit)
    return jsonify({"logs": logs, "count": len(logs)})


@app.route("/seed", methods=["POST"])
def seed():
    """Seed mock author data into the DB (call once during setup)."""
    from database import seed_mock_data
    result = seed_mock_data(force=request.args.get("force", "").lower() == "true")
    return jsonify(result)


# ══════════════════════════════════════════════════════════════════════════════
# INTERNAL HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _fetch_author(email: str) -> dict | str | None:
    """
    Fetch author from DB. Returns:
    - dict: author found
    - None: not found
    - str: error message for the user
    """
    try:
        from database import get_author_by_email
        return get_author_by_email(email)
    except ValueError as e:
        return f" {str(e)}"
    except Exception as e:
        print(f"[App] DB fetch error: {e}")
        return (
            "Our database is temporarily unavailable. "
            "Please try again in a few minutes, or contact support@bookleaf.in. 🔧"
        )


def _search_kb(query: str) -> dict:
    """Search the knowledge base. Returns empty result on failure."""
    try:
        from knowledge_base import search_knowledge_base
        return search_knowledge_base(query)
    except Exception as e:
        print(f"[App] KB search error: {e}")
        return {"answer": "", "confidence": 0.0, "method": "error"}


def _log(email, query, intent, response, confidence, escalated, source="db"):
    """Log query to DB and local file. Fails silently."""
    try:
        from database import log_query
        log_query(
            email=email,
            query=query,
            intent=intent,
            response=response,
            confidence=confidence,
            escalated=escalated,
            source=source,
        )
    except Exception as e:
        print(f"[App] Logging failed: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# STARTUP
# ══════════════════════════════════════════════════════════════════════════════

def initialise_app():
    """Run startup tasks: seed DB + init knowledge base."""
    print("\n" + "═" * 55)
    print("  BookLeaf Author Query Bot — Starting Up")
    print("═" * 55)

    # Seed mock data
    try:
        from database import seed_mock_data
        seed_result = seed_mock_data()
        print(f"[Startup] DB seed: {seed_result}")
    except Exception as e:
        print(f"[Startup] DB seed skipped: {e}")

    # Init knowledge base
    try:
        from knowledge_base import init_knowledge_base
        kb_result = init_knowledge_base()
        print(f"[Startup] Knowledge base: {kb_result}")
    except Exception as e:
        print(f"[Startup] KB init skipped: {e}")

    print("═" * 55 + "\n")


if __name__ == "__main__":
    initialise_app()
    app.run(debug=True, host="0.0.0.0", port=5000)
