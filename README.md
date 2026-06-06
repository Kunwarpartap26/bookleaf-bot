# BookLeaf Publishing — AI Author Support Bot

**Assignment submission for:** AI Automation Specialist role at BookLeaf Publishing  
**Built by:** [Your Name]  
**Stack:** Python · Flask · OpenAI GPT-4o-mini · Supabase · Vanilla HTML/CSS/JS

---

## What This Builds

Two complete systems submitted as one project:

1. **Customer Query Bot** — A chat interface where authors ask natural-language questions ("Is my book live?", "Where's my royalty?") and receive instant, data-driven answers pulled from a Supabase author database and a RAG knowledge base.

2. **Identity Unification System** — Fuzzy + LLM logic that links the same author across Email, WhatsApp, Instagram, and Dashboard name into a single unified profile with confidence scoring and a manual-review fallback.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Author's Browser                         │
│              templates/index.html (Vanilla JS)              │
└────────────────────────┬────────────────────────────────────┘
                         │  POST /chat  { email, query }
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                    Flask  app.py                             │
│                                                             │
│  1. classify_intent_with_llm()  ──► OpenAI gpt-4o-mini      │
│        └─► keyword fallback if OpenAI unavailable           │
│                                                             │
│  2. confidence < 0.8?  ──► escalation_response()           │
│                                                             │
│  3. DB-bound intent + email?                                │
│        └─► get_author_by_email()  ──► Supabase authors      │
│                 └─► in-memory fallback if DB down           │
│                                                             │
│  4. general_faq / no email?                                 │
│        └─► search_knowledge_base()  ──► OpenAI embeddings   │
│                 └─► keyword search fallback                 │
│                                                             │
│  5. log_query()  ──► Supabase query_logs + query_logs.json  │
└─────────────────────────────────────────────────────────────┘
                         │
          ┌──────────────┴───────────────┐
          ▼                              ▼
┌─────────────────┐           ┌──────────────────────┐
│  Supabase       │           │  OpenAI API           │
│  • authors      │           │  • gpt-4o-mini        │
│  • query_logs   │           │  • text-embed-3-small │
└─────────────────┘           └──────────────────────┘

Identity Unifier (standalone module):
┌─────────────────────────────────────────────────────────────┐
│  identity_unifier.py                                        │
│                                                             │
│  PlatformIdentity  ──► normalise_identifier()               │
│        └─► compute_match_score()  ──► rapidfuzz             │
│                 └─► _llm_match() if borderline              │
│                        └─► flag_for_manual_review() < 0.70  │
└─────────────────────────────────────────────────────────────┘
```

---

## Project Structure

```
bookleaf/
├── app.py                        # Flask app + chat endpoint
├── database.py                   # Supabase client + query helpers
├── knowledge_base.py             # RAG layer (embeddings + search)
├── identity_unifier.py           # Cross-platform identity matching
├── mock_data.py                  # 10 realistic mock authors
├── requirements.txt              # All dependencies pinned
├── knowledge_base.txt            # FAQ content (loaded as RAG)
├── kb_embeddings_cache.json      # Auto-generated embedding cache
├── query_logs.json               # Auto-generated local query log
├── .env                          # API keys (NOT committed)
├── .env.example                  # Template for .env
├── identity_unification_flowchart.md
└── templates/
    └── index.html                # Chat UI (pure HTML/CSS/JS)
```

---

## Prerequisites

- Python 3.11+ (tested on 3.11 and 3.12)
- An OpenAI API key ([platform.openai.com](https://platform.openai.com))
- A Supabase project ([supabase.com](https://supabase.com)) — **optional**; the app runs fully in-memory without it

---

## Setup Instructions

### 1. Clone or unzip the project

```bash
cd bookleaf
```

### 2. Create and activate a virtual environment

```bash
python -m venv venv

# macOS / Linux
source venv/bin/activate

# Windows
venv\Scripts\activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment variables

```bash
cp .env.example .env
```

Edit `.env` and fill in your keys:

```
OPENAI_API_KEY=sk-...
SUPABASE_URL=https://xxxx.supabase.co        # optional
SUPABASE_KEY=eyJhbGciOiJIUzI1NiIsInR5...     # optional (anon/service key)
```

> **No Supabase?** Leave `SUPABASE_URL` and `SUPABASE_KEY` blank.  
> The app will run entirely in memory using mock data. All features work.

### 5. (Optional) Create Supabase tables

If you want persistent storage, run this SQL in the Supabase SQL Editor:

```sql
-- Authors table
create table if not exists authors (
  id            bigserial primary key,
  email         text unique not null,
  name          text,
  book_title    text,
  final_submission_date text,
  book_live_date        text,
  royalty_status        text,
  isbn                  text,
  add_on_services       text,
  whatsapp              text,
  instagram             text,
  dashboard_name        text,
  created_at    timestamptz default now()
);

-- Query logs table
create table if not exists query_logs (
  id          bigserial primary key,
  timestamp   text,
  email       text,
  query       text,
  intent      text,
  response    text,
  confidence  numeric,
  escalated   boolean,
  source      text,
  created_at  timestamptz default now()
);
```

### 6. Run the app

```bash
python app.py
```

You should see:

```
═══════════════════════════════════════════════════════
  BookLeaf Author Query Bot — Starting Up
═══════════════════════════════════════════════════════
[DB]     Supabase client initialised.  (or: running in memory-only mode)
[Startup] DB seed: {'status': 'seeded', 'count': 10}
[Startup] Knowledge base: {'status': 'embedded', 'chunks': 42}
═══════════════════════════════════════════════════════
 * Running on http://0.0.0.0:5000
```

Open **http://localhost:5000** in your browser.

---

## How to Use

### Web Chat Interface

1. Open `http://localhost:5000`
2. Enter your registered author email (e.g. `priya.sharma@gmail.com`)
3. Type a question or click a quick-ask button
4. The bot responds with account data or knowledge-base answers

**Test emails from mock data:**

| Email | Book |
|---|---|
| `priya.sharma@gmail.com` | Echoes of the Yamuna |
| `arjun.mehta@hotmail.com` | The Silent Monsoon |
| `vikram.iyer@gmail.com` | Code and Karma (not yet live) |
| `suresh.kumar@yahoo.co.in` | Villages I Never Left |

### API (Postman / curl)

```bash
# Ask a data-bound question
curl -X POST http://localhost:5000/chat \
  -H "Content-Type: application/json" \
  -d '{"email": "priya.sharma@gmail.com", "query": "Is my book live?"}'

# Ask a general FAQ question (no email needed)
curl -X POST http://localhost:5000/chat \
  -H "Content-Type: application/json" \
  -d '{"email": "", "query": "When are royalties paid?"}'

# Health check
curl http://localhost:5000/health

# View recent logs
curl http://localhost:5000/logs?limit=10

# Re-seed mock data
curl -X POST http://localhost:5000/seed
```

### Run Identity Unifier Demo

```bash
python identity_unifier.py
```

---

## Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Chat UI |
| `POST` | `/chat` | Main query endpoint |
| `GET` | `/health` | DB + app health check |
| `GET` | `/logs?limit=N` | Recent query logs |
| `POST` | `/seed?force=true` | (Re-)seed mock author data |

### `/chat` Request / Response

**Request:**
```json
{
  "email": "priya.sharma@gmail.com",
  "query": "Is my book live yet?"
}
```

**Response:**
```json
{
  "response": "Hi Priya!  Your book \"Echoes of the Yamuna\" went live on 2025-01-10...",
  "confidence": 0.95,
  "escalated": false,
  "intent": "book_live_status",
  "source": "db"
}
```

---

## Confidence & Escalation Rules

| Confidence | Behaviour |
|---|---|
| ≥ 0.80 | Normal response from DB or knowledge base |
| < 0.80 | **Always escalated** — logged, human agent notified |
| KB < 0.30 | Falls through to final escalation |

Identity Unifier thresholds:

| Confidence | Behaviour |
|---|---|
| ≥ 0.70 | Auto-matched, profile merged |
| < 0.70 | Flagged for manual review |

---

## How Each File Works

| File | Role |
|---|---|
| `app.py` | Flask routes, intent classification, response builder, orchestration |
| `database.py` | Supabase client, in-memory fallback, author queries, query logging |
| `knowledge_base.py` | Chunk KB text, generate/cache OpenAI embeddings, cosine similarity search |
| `identity_unifier.py` | Normalise identifiers, fuzzy match via rapidfuzz, LLM assist, manual-review queue |
| `mock_data.py` | 10 realistic Indian author profiles used to seed the DB |
| `templates/index.html` | Pure HTML/CSS/JS chat UI — zero npm/node dependencies |
| `knowledge_base.txt` | FAQ source document (publishing timelines, royalties, dashboard, add-ons) |

---

## Error Handling Matrix

| Scenario | Handling |
|---|---|
| OpenAI API key missing / rate limit | Falls back to keyword-based intent classification |
| Supabase down / credentials missing | Falls back to in-memory mock data store |
| Author email not found in DB | Returns a clear "account not found" message |
| Multiple accounts for same email | Raises a descriptive error, escalates to support |
| KB embeddings unavailable | Falls back to keyword overlap search |
| Confidence < 0.8 for any reason | Always escalates — no exceptions |
| Invalid JSON request body | Returns HTTP 400 with error message |

---

## Self-Rating

| Skill | Rating | Context |
|---|---|---|
| **Zapier / Make / N8N** | 3/10 | Strong Python automation background; actively learning N8N for workflow orchestration |
| **LangChain / OpenAI integrations** | 7/10 | Built TETRA (on-device Gemma 3 270M Android AI) and DocuFlow (live AI document processing at docuflow-ai-roan.vercel.app) |
| **System design & troubleshooting** | 8/10 | Architected TETRA, ATOM (95% NLP accuracy offline voice assistant), and a real-time Flask webhook pipeline handling 500+ events with zero data loss |

---

## Background

- **TETRA** — Fully offline Android AI companion running Gemma 3 270M on-device; no internet required, full NLP pipeline in Java/Kotlin
- **ATOM** — Offline voice-controlled Android assistant with 95% NLP accuracy, custom intent engine
- **Webhook pipeline** — Real-time Flask system handling 500+ events/day with zero data loss, built with SNS + CloudWatch
- **DocuFlow** — Live AI document processing app: [docuflow-ai-roan.vercel.app](https://docuflow-ai-roan.vercel.app)
- Daily tooling: Claude Code, Cursor, AWS (EC2, CloudWatch, SNS, IAM — certified)

---

## .env.example

```
# OpenAI — required for LLM classification and embeddings
OPENAI_API_KEY=sk-your-openai-key-here

# Supabase — optional (app runs in-memory without these)
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your-supabase-anon-or-service-key
```

---

## Running Tests (manual smoke test)

```bash
# 1. Start the server
python app.py

# 2. In a new terminal, test all intents:
python - <<'EOF'
import urllib.request, json

BASE = "http://localhost:5000"

tests = [
    ("priya.sharma@gmail.com", "Is my book live?"),
    ("arjun.mehta@hotmail.com", "When will I get my royalty?"),
    ("vikram.iyer@gmail.com", "Where is my author copy?"),
    ("fatima.khan@gmail.com", "What add-on services am I enrolled in?"),
    ("suresh.kumar@yahoo.co.in", "What is my ISBN?"),
    ("", "How do I log in to the author dashboard?"),
    ("nobody@test.com", "Is my book live?"),
]

for email, query in tests:
    body = json.dumps({"email": email, "query": query}).encode()
    req = urllib.request.Request(f"{BASE}/chat", data=body,
                                  headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        data = json.loads(r.read())
    esc = " ESC" if data["escalated"] else ""
    print(f"{esc} [{data['intent']}] {query[:40]:<42} conf={data['confidence']:.0%}")
EOF
```

---

*Built with care for the BookLeaf Publishing AI Automation Specialist assignment.*
