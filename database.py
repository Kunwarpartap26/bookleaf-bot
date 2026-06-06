import os
import json
import traceback
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# ── Supabase client (lazy init so app doesn't crash if keys are missing) ───────
_supabase_client = None

def get_supabase_client():
    """Return a cached Supabase client, or None if credentials are absent."""
    global _supabase_client
    if _supabase_client is not None:
        return _supabase_client
    try:
        from supabase import create_client, Client
        url = os.getenv("SUPABASE_URL", "").strip()
        key = os.getenv("SUPABASE_KEY", "").strip()
        if not url or not key:
            print("[DB] Supabase credentials not set — running in memory-only mode.")
            return None
        _supabase_client = create_client(url, key)
        print("[DB] Supabase client initialised.")
        return _supabase_client
    except Exception as e:
        print(f"[DB] Failed to initialise Supabase client: {e}")
        return None


# ── In-memory fallback store (populated by seed_mock_data) ────────────────────
_memory_authors: list[dict] = []
_memory_logs: list[dict] = []


# ── Schema helpers ─────────────────────────────────────────────────────────────
def _author_row(author: dict) -> dict:
    """Flatten a mock_data author dict into a DB-friendly row."""
    return {
        "email": author["email"],
        "name": author["name"],
        "book_title": author["book_title"],
        "final_submission_date": author["final_submission_date"],
        "book_live_date": author.get("book_live_date"),
        "royalty_status": author["royalty_status"],
        "isbn": author["isbn"],
        "add_on_services": json.dumps(author.get("add_on_services", [])),
        "whatsapp": author.get("whatsapp", ""),
        "instagram": author.get("instagram", ""),
        "dashboard_name": author.get("dashboard_name", ""),
    }


# ── Seed / setup ───────────────────────────────────────────────────────────────
def seed_mock_data(force: bool = False) -> dict:
    """
    Insert mock authors into Supabase (table: authors) AND the in-memory store.
    If Supabase is unavailable, only in-memory is populated.
    Pass force=True to re-insert even if rows already exist.
    """
    from mock_data import MOCK_AUTHORS

    global _memory_authors
    _memory_authors = [_author_row(a) for a in MOCK_AUTHORS]

    client = get_supabase_client()
    if client is None:
        return {"status": "memory_only", "count": len(_memory_authors)}

    try:
        # Check if data already exists
        existing = client.table("authors").select("email").execute()
        if existing.data and not force:
            print(f"[DB] Skipping seed — {len(existing.data)} authors already in DB.")
            return {"status": "skipped", "count": len(existing.data)}

        rows = _memory_authors
        result = client.table("authors").upsert(rows).execute()
        return {"status": "seeded", "count": len(rows)}
    except Exception as e:
        print(f"[DB] Seed error: {e}")
        return {"status": "error", "error": str(e)}


# ── Author queries ─────────────────────────────────────────────────────────────
def get_author_by_email(email: str) -> dict | None:
    """
    Fetch a single author row by email.
    Returns the author dict, None if not found, or raises on DB error.
    """
    if not email:
        return None

    email_lower = email.strip().lower()

    client = get_supabase_client()
    if client:
        try:
            result = (
                client.table("authors")
                .select("*")
                .ilike("email", email_lower)
                .execute()
            )
            if result.data:
                if len(result.data) > 1:
                    raise ValueError(
                        f"Multiple authors found for email '{email}'. "
                        "Please contact support."
                    )
                row = result.data[0]
                # Deserialise add_on_services if stored as JSON string
                if isinstance(row.get("add_on_services"), str):
                    row["add_on_services"] = json.loads(row["add_on_services"])
                return row
            # Fall through to memory store if Supabase found nothing
        except ValueError:
            raise
        except Exception as e:
            print(f"[DB] get_author_by_email Supabase error: {e} — falling back to memory")

    # In-memory fallback
    matches = [a for a in _memory_authors if a["email"].lower() == email_lower]
    if not matches:
        return None
    if len(matches) > 1:
        raise ValueError(f"Multiple records found for '{email}' in memory store.")
    row = matches[0].copy()
    if isinstance(row.get("add_on_services"), str):
        row["add_on_services"] = json.loads(row["add_on_services"])
    return row


def get_author_by_book_title(title: str) -> dict | None:
    """
    Case-insensitive partial match on book_title.
    Returns first match or None.
    """
    if not title:
        return None

    title_lower = title.strip().lower()

    client = get_supabase_client()
    if client:
        try:
            result = (
                client.table("authors")
                .select("*")
                .ilike("book_title", f"%{title_lower}%")
                .execute()
            )
            if result.data:
                row = result.data[0]
                if isinstance(row.get("add_on_services"), str):
                    row["add_on_services"] = json.loads(row["add_on_services"])
                return row
        except Exception as e:
            print(f"[DB] get_author_by_book_title error: {e} — falling back to memory")

    # In-memory fallback
    for a in _memory_authors:
        if title_lower in a["book_title"].lower():
            row = a.copy()
            if isinstance(row.get("add_on_services"), str):
                row["add_on_services"] = json.loads(row["add_on_services"])
            return row
    return None


# ── Query logging ──────────────────────────────────────────────────────────────
LOG_FILE = "query_logs.json"


def log_query(
    email: str,
    query: str,
    intent: str,
    response: str,
    confidence: float,
    escalated: bool,
    source: str = "db",
) -> None:
    """
    Write a query log entry to:
    1. Supabase 'query_logs' table (if available)
    2. Local JSON file (always)
    """
    entry = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "email": email,
        "query": query,
        "intent": intent,
        "response": response[:500],  # truncate for storage
        "confidence": round(confidence, 4),
        "escalated": escalated,
        "source": source,
    }

    # ── Supabase log ──
    client = get_supabase_client()
    if client:
        try:
            client.table("query_logs").insert(entry).execute()
        except Exception as e:
            print(f"[DB] Log insert failed: {e}")

    # ── In-memory log ──
    _memory_logs.append(entry)

    # ── Local JSON file (append mode) ──
    try:
        existing: list = []
        if os.path.exists(LOG_FILE):
            with open(LOG_FILE, "r", encoding="utf-8") as f:
                try:
                    existing = json.load(f)
                except json.JSONDecodeError:
                    existing = []
        existing.append(entry)
        with open(LOG_FILE, "w", encoding="utf-8") as f:
            json.dump(existing, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[DB] Local log write failed: {e}")


def get_recent_logs(limit: int = 50) -> list[dict]:
    """Return recent log entries from memory or Supabase."""
    client = get_supabase_client()
    if client:
        try:
            result = (
                client.table("query_logs")
                .select("*")
                .order("timestamp", desc=True)
                .limit(limit)
                .execute()
            )
            return result.data or []
        except Exception:
            pass
    return _memory_logs[-limit:]


# ── DB health check ────────────────────────────────────────────────────────────
def health_check() -> dict:
    """Quick ping to verify Supabase connectivity."""
    client = get_supabase_client()
    if client is None:
        return {"status": "memory_only", "supabase": False}
    try:
        client.table("authors").select("email").limit(1).execute()
        return {"status": "ok", "supabase": True}
    except Exception as e:
        return {"status": "error", "supabase": False, "error": str(e)}
