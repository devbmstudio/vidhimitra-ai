import io
import json
import hashlib
import time
import sys
import os
import warnings
warnings.filterwarnings("ignore", category=RuntimeWarning, message=".*duckduckgo_search.*")
from datetime import datetime
from contextlib import asynccontextmanager

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI, HTTPException, UploadFile, File, BackgroundTasks, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from typing import Optional
from pydantic import BaseModel
import pdfplumber

from config import groq_client, supabase, SUPABASE_URL
from db import (
    search_documents,
    search_scholarships,
    get_scholarships_closing_soon,
    get_latest_documents,
    get_or_create_session,
    update_session,
    get_cached_response,
    set_cached_response,
    make_hash,
    search_process_guides,
    get_process_guide,
    vector_search_documents,
    hybrid_search_documents,
    detect_section_intent,
)
from classifier import smart_classify, groq_classify, extract_document_details


def ensure_tables():
    try:
        import psycopg2
        ref = SUPABASE_URL.split("//")[1].split(".")[0] if "//" in SUPABASE_URL else ""
        pw = os.environ.get("POSTGRES_PASSWORD", "")
        if pw and ref:
            conn = psycopg2.connect(
                host=f"db.{ref}.supabase.co",
                port=5432,
                dbname="postgres",
                user="postgres",
                password=pw,
                connect_timeout=5,
            )
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS document_insights (
                    id SERIAL PRIMARY KEY,
                    scheme_name TEXT,
                    doc_type TEXT,
                    amount TEXT,
                    application_deadline TEXT,
                    portal TEXT,
                    provider TEXT,
                    category TEXT,
                    education_level TEXT,
                    state TEXT,
                    description TEXT,
                    user_confirmed BOOLEAN DEFAULT false,
                    count INTEGER DEFAULT 1,
                    first_seen TIMESTAMPTZ DEFAULT NOW(),
                    last_seen TIMESTAMPTZ DEFAULT NOW(),
                    promoted BOOLEAN DEFAULT false,
                    UNIQUE(scheme_name, provider, amount)
                );
            """)
            conn.commit()
            conn.close()
            print("[DB] document_insights table ensured")
    except Exception as e:
        print(f"[DB] Could not auto-create document_insights: {e}")
        print("[DB] Run backend/migrations/002_create_document_insights.sql in Supabase SQL Editor")


@asynccontextmanager
async def lifespan(app: FastAPI):
    from greentunnel import start_greentunnel, stop_greentunnel
    ensure_tables()
    # Pre-warm vector model
    from db import _get_vector_model
    t0 = time.time()
    _get_vector_model()
    print(f"[App] Vector model loaded in {time.time()-t0:.1f}s")
    app.state.proxies = start_greentunnel()
    print("[App] VidhiMitra backend started")
    yield
    stop_greentunnel()
    print("[App] Shutdown")


FRONTEND_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend")

app = FastAPI(
    title="VidhiMitra API",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    message: str
    session_id: str = "default"


class ChatResponse(BaseModel):
    reply: str
    sources: list = []
    quick_chips: list = []
    session_id: str = "default"


class EligibilityRequest(BaseModel):
    session_id: str
    answers: dict


@app.get("/")
async def serve_index():
    idx = os.path.join(FRONTEND_DIR, "index.html")
    if os.path.isfile(idx):
        return FileResponse(idx)
    return JSONResponse({"error": "Frontend not found"}, status_code=404)

@app.get("/app.js")
async def serve_js():
    js = os.path.join(FRONTEND_DIR, "app.js")
    if os.path.isfile(js):
        return FileResponse(js, media_type="application/javascript")
    return JSONResponse({"error": "Not found"}, status_code=404)

@app.get("/favicon.ico")
async def favicon():
    from starlette.responses import Response
    return Response(status_code=204)

FUZZY_MAP = {
    "scholarship": ["scholarship", "scholorship", "scholor", "scholarships", "schorlarship"],
    "fellowship": ["fellowship", "felloship"],
    "stipend": ["stipend", "stipnd"],
    "eligible": ["eligible", "eligble", "eligiblity", "eligibility", "elgible"],
    "scholarship_short": ["scholar", "nsp", "national scholarship"],
    "scheme_names": ["yojna", "yojana", "scheme", "bhatta", "pragati", "saksham", "yashasvi"],
}

SCHOLARSHIP_TRIGGERS = FUZZY_MAP["scholarship"] + FUZZY_MAP["fellowship"] + FUZZY_MAP["stipend"] + FUZZY_MAP["eligible"] + FUZZY_MAP["scholarship_short"] + FUZZY_MAP["scheme_names"]

def detect_intent(message, session_data=None):
    import re
    ml = message.lower()

    if session_data and session_data.get("eligibility_started"):
        return "scholarship"

    for word in SCHOLARSHIP_TRIGGERS:
        if word in ml:
            return "scholarship"

    if re.search(r"\b(act|rule|notification|gazette|bill|law|circular|amendment|ordinance|statute|regulation)\b", ml):
        return "document"

    return "general"

QUICK_CHIPS_DEFAULT = [
    {"label": "Latest Acts", "action": "Show me the latest acts"},
    {"label": "Find Scholarships", "action": "Find scholarships for me"},
    {"label": "Closing Soon", "action": "Show scholarships closing soon"},
    {"label": "Search by Ministry", "action": "Search by ministry"},
]


def append_source_footer(reply, sources):
    if not sources:
        return reply
    footer = "\n\n---\n**Sources:**"
    for s in sources[:3]:
        title = s.get("title", "Link")
        url = s.get("url", "")
        if url:
            footer += f"\n• [{title}]({url})"
    return reply + footer


def _build_guide_context(guide):
    parts = [f"# {guide['title']}"]
    if guide.get("eligibility"):
        parts.append(f"\n## Eligibility\n{guide['eligibility']}")
    if guide.get("documents_needed"):
        parts.append(f"\n## Documents Needed\n" + "\n".join(f"- {d}" for d in guide["documents_needed"]))
    if guide.get("step_by_step"):
        parts.append(f"\n## Step by Step\n" + "\n".join(f"{s}" for s in guide["step_by_step"]))
    if guide.get("where_to_apply"):
        parts.append(f"\n## Where to Apply\n{guide['where_to_apply']}")
    if guide.get("portal"):
        parts.append(f"\n## Portal\n{guide['portal']}")
    if guide.get("common_problems"):
        parts.append(f"\n## Common Problems\n" + "\n".join(f"- {p}" for p in guide["common_problems"]))
    if guide.get("validity"):
        parts.append(f"\n## Validity\n{guide['validity']}")
    if guide.get("fees"):
        parts.append(f"\n## Fees\n{guide['fees']}")
    return "\n".join(parts)


def _format_guide_brief(guide):
    lines = [f"**{guide['title']}**"]
    if guide.get("eligibility"):
        lines.append(f"\n\n**Eligibility:** {guide['eligibility']}")
    if guide.get("documents_needed"):
        lines.append(f"\n\n**Documents needed:**")
        for d in guide["documents_needed"][:5]:
            lines.append(f"• {d}")
        if len(guide["documents_needed"]) > 5:
            lines.append(f"...and {len(guide['documents_needed'])-5} more")
    if guide.get("step_by_step"):
        lines.append(f"\n\n**Steps:**")
        for s in guide["step_by_step"][:3]:
            lines.append(f"{s}")
        if len(guide["step_by_step"]) > 3:
            lines.append(f"...and {len(guide['step_by_step'])-3} more steps")
    if guide.get("portal"):
        lines.append(f"\n\n**Portal:** {guide['portal']}")
    if guide.get("common_problems"):
        lines.append(f"\n\n**Common issues:**")
        for p in guide["common_problems"][:2]:
            lines.append(f"• {p}")
    return "\n".join(lines)


def call_groq(messages, model="llama-3.1-8b-instant", max_tokens=500):
    try:
        resp = groq_client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.3,
            max_tokens=max_tokens,
        )
        return resp.choices[0].message.content
    except Exception as e:
        print(f"[Groq] Error: {e}")
        return None


def live_search(query):
    try:
        from duckduckgo_search import DDGS
        results = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=5):
                results.append(f"**{r.get('title','')}**\n{r.get('body','')}\nSource: {r.get('href','')}")
        if not results:
            return None
        return call_groq([
            {"role": "system", "content": "You are VidhiMitra, a specialist in Maharashtra government schemes (Aaple Sarkar, MahaDBT). Answer using ONLY the web search results below. Be concise (2-4 sentences). If the results don't contain the answer, say 'I only have access to Aaple Sarkar and MahaDBT scholarship data. I cannot assist with that query.' Do not give general advice. Never make up details."},
            {"role": "user", "content": f"User asked: '{query}'\n\nWeb search results:\n\n" + "\n\n".join(results) + "\n\nAnswer the user's question based ONLY on these results."}
        ], max_tokens=600)
    except Exception as e:
        print(f"[LiveSearch] Error: {e}")
        return None


def format_generic_reply(db_results, result_type="document"):
    if not db_results or len(db_results) == 0:
        return "I couldn't find anything matching your query in my database. Try different keywords or check back later."

    if result_type == "document":
        lines = ["Here's what I found in the database:"]
        for i, doc in enumerate(db_results[:5], 1):
            title = doc.get("title", "Untitled")
            doc_type = doc.get("doc_type", "Document")
            date = doc.get("published_date", "")[:10] if doc.get("published_date") else ""
            link = doc.get("source_url", "")
            lines.append(f"\n{i}. **{title}**")
            lines.append(f"   Type: {doc_type} | Date: {date}")
            if link:
                lines.append(f"   [View Source]({link})")
        return "\n".join(lines)

    lines = ["Here are the scholarships I found:"]
    for i, s in enumerate(db_results[:5], 1):
        name = s.get("scheme_name", "Untitled")
        provider = s.get("provider", "")
        amount = s.get("amount", "")
        deadline = s.get("application_deadline", "")[:10] if s.get("application_deadline") else ""
        link = s.get("application_link", "")
        lines.append(f"\n{i}. **{name}** ({provider})")
        if amount:
            lines.append(f"   Amount: ₹{amount}")
        if deadline:
            lines.append(f"   Deadline: {deadline}")
        if link:
            lines.append(f"   [Apply Here]({link})")
    return "\n".join(lines)


@app.get("/health")
async def health():
    from greentunnel import is_running
    gt_ok = is_running()
    groq_ok = False
    db_ok = False
    try:
        r = supabase.table("documents").select("count", count="exact").limit(0).execute()
        db_ok = True
    except Exception:
        pass
    try:
        groq_client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=1,
        )
        groq_ok = True
    except Exception:
        pass

    return {
        "status": "ok",
        "greentunnel": gt_ok,
        "groq": groq_ok,
        "database": db_ok,
        "timestamp": datetime.now().isoformat(),
    }


@app.post("/run-scraper")
async def run_scraper(
    background_tasks: BackgroundTasks,
    request: Request,
):
    cron_secret = os.environ.get("CRON_SECRET_KEY", "")
    if not cron_secret:
        return {"status": "error", "detail": "CRON_SECRET_KEY not configured on server"}
    header_secret = request.headers.get("x-cron-secret", "")
    if header_secret != cron_secret:
        raise HTTPException(status_code=403, detail="Invalid secret")
    from scraper import run_all_scrapers
    background_tasks.add_task(run_all_scrapers)
    return {"status": "accepted", "detail": "Scraper started in background"}


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    msg = req.message.strip()
    session_id = req.session_id

    if not msg:
        return ChatResponse(reply="Please type a message!", session_id=session_id)

    cache_key = make_hash(f"{session_id}:{msg}")
    cached = get_cached_response(cache_key)
    if cached:
        return ChatResponse(session_id=session_id, **cached)

    session = get_or_create_session(session_id)
    session_data = session.get("data", {})
    if isinstance(session_data, str):
        try:
            session_data = json.loads(session_data)
        except Exception:
            session_data = {}

    in_eligibility = session_data.get("eligibility_started", False)
    reply = ""
    sources = []

    if in_eligibility:
        step = session_data.get("step", 0)
        answers = session_data.get("answers", {})
        ml = msg.lower()

        is_valid_eligibility = (
            (step == 0 and any(w in ml for w in ["school", "undergrad", "postgrad", "phd", "class", "10th", "12th", "bachelor", "master", "graduate", "diploma", "ug", "pg"])) or
            (step == 1 and any(w in ml for w in ["general", "obc", "sc", "st", "ebc", "minority", "ews", "nt", "sbc", "open"])) or
            (step == 2 and any(w in ml for w in ["andhra", "bihar", "delhi", "goa", "gujarat", "haryana", "karnataka", "kerala", "madhya", "maharashtra", "punjab", "rajasthan", "tamil", "telangana", "uttar", "west bengal", "jharkhand", "odisha", "assam", "chhattisgarh", "himachal", "other"])) or
            (step == 3 and (any(c.isdigit() for c in msg) or "lakh" in ml or "thousand" in ml or "rupee" in ml))
        )

        if not is_valid_eligibility:
            update_session(session_id, {"eligibility_started": False, "step": -1})
            in_eligibility = False

    intent = detect_intent(msg, session_data if in_eligibility else {})
    if in_eligibility:
        step = session_data.get("step", 0)
        answers = session_data.get("answers", {})

        if step == 0:
            answers["level"] = msg
            update_session(session_id, {"eligibility_started": True, "step": 1, "answers": answers})
            chips = [
                {"label": "General", "action": "General"},
                {"label": "OBC", "action": "OBC"},
                {"label": "SC", "action": "SC"},
                {"label": "ST", "action": "ST"},
                {"label": "EBC", "action": "EBC"},
            ]
            return ChatResponse(reply="Great! **What is your caste/category?**", quick_chips=chips, session_id=session_id)

        elif step == 1:
            answers["category"] = msg
            update_session(session_id, {"eligibility_started": True, "step": 2, "answers": answers})
            chips = [
                {"label": "Maharashtra", "action": "Maharashtra"},
                {"label": "Uttar Pradesh", "action": "Uttar Pradesh"},
                {"label": "Tamil Nadu", "action": "Tamil Nadu"},
                {"label": "Karnataka", "action": "Karnataka"},
                {"label": "Delhi", "action": "Delhi"},
                {"label": "Other", "action": "Other"},
            ]
            return ChatResponse(reply="Thanks! **Which state are you from?**", quick_chips=chips, session_id=session_id)

        elif step == 2:
            answers["state"] = msg
            update_session(session_id, {"eligibility_started": True, "step": 3, "answers": answers})
            chips = [
                {"label": "Below ₹1 Lakh", "action": "100000"},
                {"label": "₹1-2.5 Lakh", "action": "250000"},
                {"label": "₹2.5-5 Lakh", "action": "500000"},
                {"label": "₹5-8 Lakh", "action": "800000"},
                {"label": "Above ₹8 Lakh", "action": "999999999"},
            ]
            return ChatResponse(reply="Almost done! **What is your annual family income?**", quick_chips=chips, session_id=session_id)

        elif step == 3:
            answers["income"] = msg
            update_session(session_id, {"eligibility_started": False, "step": -1, "answers": answers})
            level = answers.get("level", "")
            category = answers.get("category", "")
            state_from_answers = answers.get("state", "")
            income = answers.get("income", "")
            db_results = search_scholarships(level=level, category=category)
            if db_results:
                sources = [
                    {"title": s.get("scheme_name", ""), "url": s.get("application_link", ""), "provider": s.get("provider", "")}
                    for s in db_results[:5]
                ]
                groq_reply = call_groq([
                    {"role": "system", "content": "You are VidhiMitra, a scholarship assistant. Rank and recommend these scholarships for the student's profile. Be encouraging and empathetic. Keep replies concise."},
                    {"role": "user", "content": f"Student profile: Level={level}, Category={category}, State={state_from_answers}, Income≈₹{income}\n\nAvailable scholarships:\n{json.dumps(db_results[:8], indent=2, default=str)}\n\nRecommend the top 3-5 with reasons."}
                ], max_tokens=600)
                reply = groq_reply if groq_reply else format_generic_reply(db_results, "scholarship")
            else:
                reply = "I couldn't find scholarships matching your exact profile in my database. Check back later or try different criteria."
        else:
            update_session(session_id, {"eligibility_started": False, "step": -1})
            reply = "Let me know if you'd like to search for scholarships again!"

    # Out-of-domain check — runs BEFORE guide matching
    if not in_eligibility:
        ood_keywords = [
            "driving license", "driving licence", "rto", "vehicle registration",
            "passport", "visa", "immigration",
            "gst", "company registration", "trademark", "patent",
            "property registration", "stamp duty",
            "marriage registration", "birth certificate", "death certificate",
            "income tax", "itr filing", "tax return",
            "labour law", "factory license", "shop act",
            "police complaint", "fir", "ncr",
        ]
        if any(kw in msg.lower() for kw in ood_keywords):
            reply = "I only have access to **Aaple Sarkar** and **MahaDBT** scholarship and certificate data for Maharashtra. I cannot assist with that query."
            return ChatResponse(reply=reply, quick_chips=QUICK_CHIPS_DEFAULT, session_id=session_id, sources=[])

    # Process guide matching — runs for ALL non-eligibility queries
    if not in_eligibility:
        guide_matched = False
        active_guide_id = session_data.get("active_guide_id")
        guide = search_process_guides(msg, active_guide_id)
        if not guide:
            if session_data.get("active_guide_id"):
                update_session(session_id, {"active_guide_id": None})
            guide = search_process_guides(msg, None)
        if guide:
            guide_matched = True
            update_session(session_id, {"active_guide_id": guide["id"]})
            sr = _build_guide_context(guide)
            groq_reply = call_groq([
                {"role": "system", "content": "You are VidhiMitra. Answer based on the document."},
                {"role": "user", "content": f"Document:\n{sr}\n\nQuestion: {msg}\n\nAnswer from the document. Be detailed."},
            ], max_tokens=800)
            if groq_reply:
                reply = groq_reply + "\n\n_📋 Process Guide_"
            else:
                reply = _format_guide_brief(guide) + "\n\n_📋 Process Guide_"
            return ChatResponse(reply=reply, quick_chips=QUICK_CHIPS_DEFAULT, session_id=session_id, sources=[])

    if intent == "document":
        vec_results = hybrid_search_documents(msg, top_k=5)
        if vec_results:
            sources = [
                {"title": r.get("document_title", ""), "url": r.get("document_source_url", ""), "type": "Vector"}
                for r in vec_results[:3]
            ]
            context = "\n\n".join(
                f"[{r.get('section_heading','general')}] {r.get('content','')}\n"
                f"(similarity: {r.get('similarity',0):.2f})"
                for r in vec_results
            )
            groq_reply = call_groq([
                {"role": "system", "content": "You are VidhiMitra, a specialist in Maharashtra government documents (Aaple Sarkar, MahaDBT). Answer using ONLY the retrieved sections below. Be concise. Always cite specific details from the context.\n\nRetrieved Sections:\n" + context},
                {"role": "user", "content": msg},
            ])
            reply = groq_reply if groq_reply else "I found relevant documents but couldn't summarize them. Try rephrasing your query."
            reply += "\n\n_🔍 Retrieved via semantic search._"
        else:
            reply = "I couldn't find anything in my database for that. Try different keywords, or I can look up recent documents."

    elif intent == "scholarship":
        msg_words = msg.split()
        ml = msg.lower()
        caps_count = sum(1 for w in msg_words if w and w[0].isupper())
        has_acronym = any(len(w) >= 3 and w.isupper() for w in msg_words)
        is_generic = not has_acronym and caps_count < 2 and "scheme" not in ml and "yojna" not in ml
        if not is_generic:
            msg_lower = msg.lower()
            all_sch = search_scholarships(limit=100)
            msg_word_set = set(msg_lower.split())
            query_acronyms = {w.upper() for w in msg_words if len(w) >= 3 and w.isupper()}
            db_match = [
                s for s in all_sch
                if len(msg_word_set & set(s.get("scheme_name","").lower().split())) >= 2
                or any(a in s.get("scheme_name","").upper() for a in query_acronyms)
            ]
            if db_match:
                reply = format_generic_reply(db_match, "scholarship")
                reply += "\n\n_✅ Found in VidhiMitra's database._"
                return ChatResponse(reply=reply, quick_chips=QUICK_CHIPS_DEFAULT, session_id=session_id, sources=[])
            live_reply = live_search(msg + " India government scheme")
            if live_reply:
                reply = live_reply + "\n\n_🔍 Retrieved live from web sources._"
                return ChatResponse(reply=reply, quick_chips=QUICK_CHIPS_DEFAULT, session_id=session_id, sources=[])
            reply = "I couldn't find any information about that in my database or from government web sources. Try checking the **National Scholarship Portal** (scholarships.gov.in) or the specific ministry website."
            return ChatResponse(reply=reply, quick_chips=QUICK_CHIPS_DEFAULT, session_id=session_id, sources=[])
        update_session(session_id, {"eligibility_started": True, "step": 0})
        reply = "Let me find the best scholarships for you! I'll ask a few quick questions.\n\n**What is your current education level?**"
        chips = [
            {"label": "School (9-12)", "action": "School"},
            {"label": "Undergraduate", "action": "Undergraduate"},
            {"label": "Postgraduate", "action": "Postgraduate"},
            {"label": "PhD", "action": "PhD"},
        ]
        return ChatResponse(reply=reply, quick_chips=chips, session_id=session_id)

    if not reply:
        # Out-of-domain topic check
        out_of_domain_keywords = [
            "driving license", "driving licence", "rto", "vehicle registration",
            "passport", "visa", "immigration",
            "gst", "company registration", "trademark", "patent",
            "property registration", "stamp duty",
            "marriage registration", "birth certificate", "death certificate",
            "income tax", "itr filing", "tax return",
            "labour law", "factory license", "shop act",
            "police complaint", "fir", "ncr",
        ]
        ml = msg.lower()
        is_out_of_domain = any(kw in ml for kw in out_of_domain_keywords)
        if is_out_of_domain:
            reply = "I only have access to **Aaple Sarkar** and **MahaDBT** scholarship and certificate data for Maharashtra. I cannot assist with that query."
            return ChatResponse(reply=reply, quick_chips=QUICK_CHIPS_DEFAULT, session_id=session_id, sources=[])

        # Try vector search first
        vec_results = hybrid_search_documents(msg, top_k=5) if len(msg) > 5 else []
        if vec_results:
            context = "\n\n".join(
                f"[{r.get('section_heading','general')}] {r.get('content','')}\n"
                f"(similarity: {r.get('similarity',0):.2f})"
                for r in vec_results
            )
            sources = [
                {"title": r.get("document_title", ""), "url": r.get("document_source_url", ""), "type": "Vector"}
                for r in vec_results[:3]
            ]
            groq_reply = call_groq([
                {"role": "system", "content": "You are VidhiMitra, an assistant for Maharashtra government processes. Answer using ONLY the retrieved sections below. Be concise.\n\nRetrieved Sections:\n" + context},
                {"role": "user", "content": msg},
            ])
            reply = groq_reply or "I found relevant information but couldn't summarize it."
            reply += "\n\n_🔍 Retrieved via semantic search._"
        else:
            # Fallback: keyword search on scholarships + live search
            db_results = search_documents(query=msg) if len(msg) > 5 else []
            if not db_results:
                msg_lower = msg.lower()
                all_scholarships = search_scholarships(limit=100) if len(msg) > 5 else []
                query_words = set(msg_lower.split())
                query_acronyms = {w.upper() for w in msg.split() if len(w) >= 3 and w.isupper()}
                db_results = [
                    s for s in all_scholarships
                    if len(query_words & set(s.get("scheme_name", "").lower().split())) >= 2
                    or any(a in s.get("scheme_name", "").upper() for a in query_acronyms)
                ]
            if db_results:
                sources = [
                    {"title": d.get("scheme_name", d.get("title", "")), "url": d.get("application_link", d.get("source_url", "")), "provider": d.get("provider", "")}
                    for d in db_results[:3]
                ]
                reply = format_generic_reply(db_results, "scholarship" if "scheme_name" in db_results[0] else "document")
                reply += "\n\n_✅ Found in VidhiMitra's database._"
            else:
                groq_reply = live_search(msg + " India government")
                reply = groq_reply or "I couldn't find specific information about that from my database or web search. Try different keywords or ask about a specific scheme or document."

    if not reply:
        reply = "I'm processing your request. Try being more specific about what document or scholarship you need."

    reply = append_source_footer(reply, sources)

    result_data = {"reply": reply, "sources": sources, "quick_chips": QUICK_CHIPS_DEFAULT}
    set_cached_response(cache_key, result_data)

    return ChatResponse(session_id=session_id, **result_data)


@app.get("/documents/search")
async def documents_search(q: str = "", doc_type: str = "", since: str = "", limit: int = 10):
    results = search_documents(query=q or None, doc_type=doc_type or None, since=since or None, limit=limit)
    return {"results": results, "count": len(results)}


@app.get("/scholarships/search")
async def scholarships_search(level: str = "", category: str = "", state: str = "", limit: int = 10):
    results = search_scholarships(level=level or None, category=category or None, state=state or None, limit=limit)
    return {"results": results, "count": len(results)}


@app.get("/scholarships/closing-soon")
async def closing_soon(days: int = 14, limit: int = 10):
    results = get_scholarships_closing_soon(days=days, limit=limit)
    return {"results": results, "count": len(results)}


@app.get("/documents/latest")
async def latest_documents(days: int = 30, limit: int = 10):
    results = get_latest_documents(days=days, limit=limit)
    return {"results": results, "count": len(results)}


@app.post("/scholarships/eligibility")
async def eligibility_flow(req: EligibilityRequest):
    session = get_or_create_session(req.session_id)
    session_data = session.get("data", {})
    if isinstance(session_data, str):
        try:
            session_data = json.loads(session_data)
        except Exception:
            session_data = {}

    existing = session_data.get("answers", {})
    existing.update(req.answers)
    session_data["answers"] = existing
    update_session(req.session_id, session_data)

    level = existing.get("level", "")
    category = existing.get("category", "")
    state = existing.get("state", "")
    results = search_scholarships(level=level, category=category, state=state)

    return {"eligible_scholarships": results[:10], "count": len(results)}


@app.post("/upload")
async def upload_pdf(file: UploadFile = File(...)):
    if not file.filename.endswith(".pdf"):
        raise HTTPException(400, "Only PDF files are supported")

    contents = await file.read()
    text = ""
    try:
        with pdfplumber.open(io.BytesIO(contents)) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n"
    except Exception as e:
        raise HTTPException(400, f"Failed to parse PDF: {str(e)}")

    if not text.strip():
        return {
            "type": "unknown",
            "error": "Could not extract text from this PDF. It may be scanned/image-based.",
        }

    # Extract rich details using LLM
    details = extract_document_details(text[:3000], file.filename)

    if not details:
        # Fallback to keyword classification
        classification = smart_classify(text[:3000], file.filename)
        return {
            "type": "document",
            "doc_type": classification.get("doc_type", "Document"),
            "ministry": classification.get("ministry", "Unknown"),
            "explanation": "Could not extract structured details from this document.",
            "details": classification,
            "can_help": False,
        }

    # Build plain-language explanation
    explanation = details.get("description", "")
    if not explanation:
        if details.get("is_scholarship"):
            scheme = details.get("scheme_name", "")
            explanation = f"This is a scholarship-related document"
            if scheme:
                explanation += f" for **{scheme}**"
        else:
            explanation = f"This is a **{details.get('doc_type', 'document')}** from Indian government sources."

    can_help = details.get("helpful_for_others", False) and bool(details.get("scheme_name"))

    # Convert values to strings (not lists) for insight storage
    def _to_str(v):
        if isinstance(v, list):
            return v[0] if v else None
        if v is not None:
            return str(v)
        return None

    amount = details.get("amount")
    if amount is not None:
        amount = str(int(amount)) if isinstance(amount, float) and amount == int(amount) else str(amount)

    return {
        "type": "document",
        "doc_type": details.get("doc_type", "Document"),
        "explanation": explanation,
        "details": {
            "scheme_name": _to_str(details.get("scheme_name")),
            "amount": amount,
            "application_deadline": _to_str(details.get("application_deadline")),
            "portal": _to_str(details.get("portal")),
            "provider": _to_str(details.get("provider")),
            "category": _to_str(details.get("category")),
            "education_level": _to_str(details.get("education_level")),
            "state": _to_str(details.get("state")),
            "doc_type": _to_str(details.get("doc_type")),
            "action_items": details.get("action_items", []),
            "description": _to_str(details.get("description")),
        },
        "can_help": can_help,
    }


INSIGHTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "insights.json")


class InsightData(BaseModel):
    scheme_name: Optional[str] = None
    doc_type: Optional[str] = None
    amount: Optional[str] = None
    application_deadline: Optional[str] = None
    portal: Optional[str] = None
    provider: Optional[str] = None
    category: Optional[str] = None
    education_level: Optional[str] = None
    state: Optional[str] = None
    description: Optional[str] = None


def _save_insight_local(data: InsightData):
    """Save insight to local JSON file (works without DB table)."""
    os.makedirs(os.path.dirname(INSIGHTS_FILE), exist_ok=True)
    insights = []
    if os.path.exists(INSIGHTS_FILE):
        with open(INSIGHTS_FILE, encoding="utf-8") as f:
            try:
                insights = json.load(f)
            except Exception:
                insights = []

    key = (data.scheme_name or "", data.provider or "", data.amount or "")
    found_idx = None
    for i, ins in enumerate(insights):
        if (ins.get("scheme_name", ""), ins.get("provider", ""), ins.get("amount", "")) == key:
            found_idx = i
            break

    now = datetime.now().isoformat()
    if found_idx is not None:
        insights[found_idx]["count"] = insights[found_idx].get("count", 1) + 1
        insights[found_idx]["last_seen"] = now
        insights[found_idx]["user_confirmed"] = True
        for field in ["application_deadline", "portal", "doc_type", "category", "education_level", "state", "description"]:
            val = getattr(data, field, None)
            if val:
                insights[found_idx][field] = val
        new_count = insights[found_idx]["count"]
        # Auto-promote if 3+ confirmations
        if new_count >= 3 and not insights[found_idx].get("promoted"):
            insights[found_idx]["promoted"] = True
    else:
        entry = {
            "scheme_name": data.scheme_name,
            "doc_type": data.doc_type,
            "amount": data.amount,
            "application_deadline": data.application_deadline,
            "portal": data.portal,
            "provider": data.provider,
            "category": data.category,
            "education_level": data.education_level,
            "state": data.state,
            "description": data.description,
            "user_confirmed": True,
            "count": 1,
            "first_seen": now,
            "last_seen": now,
            "promoted": False,
        }
        insights.append(entry)

    with open(INSIGHTS_FILE, "w", encoding="utf-8") as f:
        json.dump(insights, f, indent=2, ensure_ascii=False)

    return insights


def _try_save_insight_supabase(data: InsightData):
    """Try saving to Supabase table (fails silently if table doesn't exist)."""
    try:
        existing = supabase.table("document_insights").select("*").eq("scheme_name", data.scheme_name).eq("provider", data.provider or "").eq("amount", data.amount or "").execute()
        if existing.data and len(existing.data) > 0:
            supabase.table("document_insights").update({
                "count": existing.data[0]["count"] + 1,
                "last_seen": datetime.now().isoformat(),
                "application_deadline": data.application_deadline or existing.data[0].get("application_deadline"),
                "portal": data.portal or existing.data[0].get("portal"),
                "doc_type": data.doc_type or existing.data[0].get("doc_type"),
                "category": data.category or existing.data[0].get("category"),
                "education_level": data.education_level or existing.data[0].get("education_level"),
                "state": data.state or existing.data[0].get("state"),
                "description": data.description or existing.data[0].get("description"),
                "user_confirmed": True,
            }).eq("id", existing.data[0]["id"]).execute()
            new_count = existing.data[0]["count"] + 1
            if new_count >= 3 and not existing.data[0].get("promoted"):
                supabase.table("document_insights").update({"promoted": True}).eq("id", existing.data[0]["id"]).execute()
                _promote_to_schemes_supabase(data)
        else:
            supabase.table("document_insights").insert({
                "scheme_name": data.scheme_name,
                "doc_type": data.doc_type,
                "amount": data.amount,
                "application_deadline": data.application_deadline,
                "portal": data.portal,
                "provider": data.provider,
                "category": data.category,
                "education_level": data.education_level,
                "state": data.state,
                "description": data.description,
                "user_confirmed": True,
                "count": 1,
            }).execute()
        return True
    except Exception as e:
        print(f"[Insights] Supabase save failed: {e}")
        return False


def _promote_to_schemes_supabase(data: InsightData):
    try:
        existing = supabase.table("scholarships").select("*").eq("scheme_name", data.scheme_name).execute()
        if existing.data and len(existing.data) > 0:
            print(f"[Promote] Scheme '{data.scheme_name}' already exists")
            return

        category_list = [data.category] if data.category else ["General"]
        level_list = [data.education_level] if data.education_level else ["Undergraduate"]
        amount_val = None
        if data.amount:
            try:
                amount_val = float(data.amount)
            except ValueError:
                pass

        supabase.table("scholarships").insert({
            "scheme_name": data.scheme_name,
            "provider": data.provider or "Central",
            "amount": amount_val,
            "application_deadline": data.application_deadline,
            "application_link": data.portal or "",
            "education_level": level_list,
            "category": category_list,
            "state": data.state or "",
            "status": "Open",
        }).execute()
        print(f"[Promote] Promoted '{data.scheme_name}' to scholarships table")
    except Exception as e:
        print(f"[Promote] Error: {e}")


@app.post("/insights")
async def save_insight(data: InsightData):
    if not data.scheme_name and not data.doc_type:
        return {"status": "skipped", "reason": "No identifiable scheme or type"}

    # Try Supabase first, fallback to local JSON
    saved_to_db = _try_save_insight_supabase(data)
    insights = _save_insight_local(data)

    total = len(insights)
    return {"status": "saved", "total_insights": total, "db": saved_to_db}


@app.get("/insights/stats")
async def insight_stats():
    """Return insight collection stats."""
    if not os.path.exists(INSIGHTS_FILE):
        return {"count": 0, "promoted": 0, "schemes": []}

    with open(INSIGHTS_FILE, encoding="utf-8") as f:
        try:
            insights = json.load(f)
        except Exception:
            return {"count": 0, "promoted": 0, "schemes": []}

    promoted = [i for i in insights if i.get("promoted")]
    return {
        "count": len(insights),
        "promoted": len(promoted),
        "schemes": [
            {
                "scheme_name": i.get("scheme_name"),
                "confirms": i.get("count", 1),
                "provider": i.get("provider"),
                "amount": i.get("amount"),
            }
            for i in insights[:20]
        ],
    }
