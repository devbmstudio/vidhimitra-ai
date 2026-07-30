import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from config import supabase

# Lazy-loaded embedding model
_vector_model = None


def _get_vector_model():
    global _vector_model
    if _vector_model is None:
        from sentence_transformers import SentenceTransformer
        _vector_model = SentenceTransformer("all-MiniLM-L6-v2")
    return _vector_model

GUIDES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "process_guides.json")


def _load_process_guides_local():
    if not os.path.exists(GUIDES_FILE):
        return []
    with open(GUIDES_FILE, encoding="utf-8") as f:
        try:
            return json.load(f)
        except Exception:
            return []


def _save_process_guides_local(guides):
    os.makedirs(os.path.dirname(GUIDES_FILE), exist_ok=True)
    with open(GUIDES_FILE, "w", encoding="utf-8") as f:
        json.dump(guides, f, indent=2, ensure_ascii=False)


def search_process_guides(query, session_guide_id=None):
    """Match query against process guides by tags/title/ID. Returns best match or None.

    Session guide is returned ONLY for short follow-up queries (≤3 words) that don't
    explicitly name a different topic. Otherwise does a fresh scored search."""
    guides = _load_process_guides_local()
    if not guides:
        return None

    # Session follow-up detection: only for short queries that don't name new topics
    q = query.lower().strip()
    for h in ["dr.", "shri", "smt", "mr", "mrs", "ms"]:
        q = q.replace(h + " ", "")
    q_words = q.split()
    if session_guide_id and len(q_words) <= 3:
        session_guide = next((g for g in guides if g["id"] == session_guide_id), None)
        if session_guide:
            # Check query doesn't explicitly name a different guide
            other_mentioned = False
            for other in guides:
                if other["id"] == session_guide_id:
                    continue
                other_text = other["title"].lower() + " " + " ".join(other.get("tags", []))
                if any(w in other_text for w in q_words if len(w) >= 3):
                    other_mentioned = True
                    break
            if not other_mentioned:
                return session_guide

    # Strip punctuation so "EWS?" matches "ews" in guides
    q = re.sub(r'[^\w\s]', '', q)
    # Remove honorifics
    for h in ["dr.", "shri", "smt", "mr", "mrs", "ms"]:
        q = q.replace(h + " ", "")
    q_words = set(q.split())

    # Build bigrams from query (multi-word phrases)
    q_tokens = q.split()
    q_bigrams = set()
    for i in range(len(q_tokens) - 1):
        q_bigrams.add(q_tokens[i] + " " + q_tokens[i + 1])

    scored = []
    for g in guides:
        score = 0
        title_lower = g["title"].lower()
        tags_lower = [t.lower() for t in g.get("tags", [])]
        guide_text = (title_lower + " " + " ".join(tags_lower)).lower()

        # Direct title match (looser: any 3+ word substring of query in title)
        q_parts = [w for w in q.split() if len(w) >= 3]
        title_matches = sum(1 for w in q_parts if w in title_lower)
        score += title_matches * 2

        # Bigram overlap with title
        title_bigrams = set()
        title_tokens = title_lower.split()
        for i in range(len(title_tokens) - 1):
            title_bigrams.add(title_tokens[i] + " " + title_tokens[i + 1])
        bigram_overlap = len(q_bigrams & title_bigrams)
        score += bigram_overlap * 5

        # Word overlap with title
        title_words = set(title_lower.split())
        overlap = len(q_words & title_words)
        score += overlap * 3

        # Tag matches (whole-word + partial)
        for tag in tags_lower:
            if q == tag or tag == q:
                score += 5
            tag_words = set(tag.split())
            for qw in q_words:
                if qw in tag_words:
                    score += 2
                # Partial word match (e.g., "panjabrao" matches query containing "panjabrao")
                for tw in tag_words:
                    if len(qw) >= 4 and len(tw) >= 4 and (qw in tw or tw in qw):
                        score += 1

        # Direct substring match: if any 3+ char query word appears in title
        for qw in q_parts:
            if qw in title_lower:
                score += 1

        # Acronym bonus: if a 3+ char word matches a tag, boost it (check original for case)
        query_acronyms = set()
        for w in query.split():
            clean_w = re.sub(r'[^a-zA-Z0-9]', '', w)
            if len(clean_w) >= 3:
                query_acronyms.add(clean_w.upper())
        for g_tag_upper in [t.upper() for t in tags_lower]:
            for qa in query_acronyms:
                if qa == g_tag_upper or qa in g_tag_upper or g_tag_upper in qa:
                    score += 5

        # Keyword signals (expanded)
        signals = {
            "certificate": ["certificate", "certif", "dakhla", "pramaanpatra", "provisional", "cast", "income", "domicile", "birth", "death", "residence", "nationality"],
            "ews": ["ews", "economically weaker", "general", "ekm", "economic"],
            "income": ["income", "aay", "income proof", "financial", "salary", "tax", "itr", "annual", "8 lakh", "rupees"],
            "caste": ["caste", "jati", "sc", "st", "obc", "vjnt", "nt", "sbc", "non creamy", "ncl", "creamy layer"],
            "scholarship": ["scholarship", "scholar", "shishyavrutti", "bhatta", "yojna", "yojana", "scheme", "allowance", "maintenance", "stipend", "fee", "reimbursement", "freeship", "tuition"],
            "mahadbt": ["mahadbt", "dbt", "maharashtra scholarship", "aaple dbt", "aaple sarkar", "maha"],
            "dpd": ["dpd", "panjabrao", "deshmukh", "vasatigruh", "hostel", "maintenance allowance", "dr panjabrao", "dpd scholarship", "obc scholarship"],
        }
        for sig_key, sig_words in signals.items():
            for sw in sig_words:
                if sw in q:
                    score += 1

        # Strong signal: if any complete tag word appears in query, bonus
        all_tag_words = set(w for t in tags_lower for w in t.split())
        strong_overlap = len(q_words & all_tag_words)
        if strong_overlap >= 2:
            score += 5

        # Domain boost: match query topic to guide category
        topic_keywords = {
            "scholarship": ["scholarship", "scholar", "allowance", "stipend", "scheme", "yojana", "bhatta", "fellowship", "tuition", "fee"],
            "certificate": ["certificate", "certif", "cert", "dakhla", "pramaanpatra", "cast", "income certificate", "domicile", "birth certificate"],
        }
        guide_cat = g.get("category", "").lower()
        for topic, keywords in topic_keywords.items():
            has_topic = any(kw in q for kw in keywords)
            if has_topic and guide_cat == topic:
                score += 5

        # Guide-ID boost: if any word from guide ID appears in query
        gid = g.get("id", "")
        gid_parts = gid.replace("_", " ").split()
        for gid_word in gid_parts:
            if len(gid_word) >= 3 and gid_word in q:
                # Extra boost for short unique identifiers (acronyms like "dpd", "ews")
                boost = 6 if len(gid_word) <= 4 else 4
                score += boost

        if score >= 2:
            scored.append((score, g))

    if not scored:
        return None
    scored.sort(key=lambda x: -x[0])
    return scored[0][1]


def get_process_guide(guide_id):
    guides = _load_process_guides_local()
    for g in guides:
        if g["id"] == guide_id:
            return g
    return None


def upsert_process_guides(guides_list):
    _save_process_guides_local(guides_list)
    for g in guides_list:
        try:
            supabase.table("process_guides").upsert(g, on_conflict="id").execute()
        except Exception as e:
            print(f"[ProcessGuides] Supabase upsert failed: {e}")


def search_documents(query=None, doc_type=None, since=None, limit=10):
    q = supabase.table("documents").select("*").limit(limit).order("published_date", desc=True)

    if query:
        q = q.ilike("title", f"%{query}%")
    if doc_type:
        q = q.eq("doc_type", doc_type)
    if since:
        q = q.gte("published_date", since)

    resp = q.execute()
    return resp.data if resp.data else []


def search_scholarships(level=None, category=None, state=None, income=None, limit=10):
    q = supabase.table("scholarships").select("*").limit(limit)

    if level:
        q = q.contains("education_level", [level])
    if category:
        q = q.contains("category", [category])
    if state:
        q = q.ilike("provider", f"%{state}%")

    resp = q.execute()
    return resp.data if resp.data else []


def get_scholarships_closing_soon(days=14, limit=10):
    deadline = datetime.now().date() + timedelta(days=days)
    resp = (
        supabase.table("scholarships")
        .select("*")
        .lte("application_deadline", deadline.isoformat())
        .gte("application_deadline", datetime.now().date().isoformat())
        .neq("status", "Closed")
        .limit(limit)
        .order("application_deadline")
        .execute()
    )
    return resp.data if resp.data else []


def get_latest_documents(days=30, limit=10):
    since = datetime.now().date() - timedelta(days=days)
    resp = (
        supabase.table("documents")
        .select("*")
        .gte("published_date", since.isoformat())
        .limit(limit)
        .order("published_date", desc=True)
        .execute()
    )
    return resp.data if resp.data else []


def upsert_documents(docs_list):
    for doc in docs_list:
        supabase.table("documents").upsert(doc, on_conflict="source_url").execute()


def upsert_scholarships(scholarships_list):
    for s in scholarships_list:
        supabase.table("scholarships").upsert(s, on_conflict="scheme_name,provider").execute()


def get_or_create_session(session_id):
    resp = supabase.table("user_sessions").select("*").eq("id", session_id).execute()
    if resp.data and len(resp.data) > 0:
        return resp.data[0]
    supabase.table("user_sessions").insert({"id": session_id, "data": {}}).execute()
    return {"id": session_id, "data": {}}


def update_session(session_id, data):
    now = datetime.now().isoformat()
    supabase.table("user_sessions").update({"data": data, "updated_at": now}).eq("id", session_id).execute()


def get_cached_response(prompt_hash):
    resp = (
        supabase.table("response_cache")
        .select("*")
        .eq("query_hash", prompt_hash)
        .execute()
    )
    if resp.data and len(resp.data) > 0:
        entry = resp.data[0]
        created = datetime.fromisoformat(entry["created_at"].replace("Z", "+00:00"))
        if datetime.now(timezone.utc) - created < timedelta(hours=24):
            return entry["response"]
    return None


def set_cached_response(prompt_hash, response_data):
    supabase.table("response_cache").upsert(
        {"query_hash": prompt_hash, "response": response_data, "created_at": datetime.now().isoformat()},
        on_conflict="query_hash",
    ).execute()


def make_hash(prompt):
    return hashlib.sha256(prompt.encode()).hexdigest()[:16]


# --- Vector search ------------------------------------------------------------------

SECTION_KEYWORDS = {
    "eligibility": ["eligible", "eligibility", "am i eligible", "who can apply", "qualify", "requirements", "criteria", "conditions"],
    "documents_needed": ["documents needed", "documents required", "what documents", "required documents", "need to submit", "upload", "attach", "paperwork"],
    "steps": ["how to apply", "step by step", "application process", "procedure", "steps", "process", "guide", "apply online", "how do i get", "how to get"],
    "amount": ["amount", "scholarship amount", "how much", "stipend", "financial assistance", "maintenance allowance", "money", "rupees"],
    "deadline": ["deadline", "last date", "due date", "closing date", "when", "apply before", "closing soon"],
    "portal": ["portal", "website", "apply online", "login", "register", "website link", "online portal"],
    "fees": ["fee", "fees", "application fee", "processing fee", "cost", "how much does it cost", "free"],
    "validity": ["valid", "validity", "valid for", "how long", "period", "renew", "renewal"],
    "common_problems": ["problem", "issue", "troubleshoot", "error", "common problem", "rejected", "stuck", "not working"],
}


def detect_section_intent(query: str) -> list:
    """Detect which section_heading filters to apply based on query keywords.
    Returns prioritized list of (section, boost) tuples."""
    q = query.lower()
    matches = []
    for section, keywords in SECTION_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in q)
        if score > 0:
            matches.append((section, score))
    matches.sort(key=lambda x: -x[1])
    return matches[:2]


def vector_search_documents(query: str, top_k: int = 5, threshold: float = 0.3,
                            portal: str = None, section_heading: str = None) -> list:
    """Embed query and search document_chunks via Supabase RPC."""
    model = _get_vector_model()
    emb = model.encode([query])[0].tolist()

    params = {
        "query_embedding": emb,
        "match_threshold": threshold,
        "match_count": top_k,
        "filter_portal": portal,
        "filter_section": section_heading,
    }
    try:
        resp = supabase.rpc("match_documents", params).execute()
        return resp.data if resp.data else []
    except Exception as e:
        print(f"[VectorSearch] RPC error: {e}")
        return []


def hybrid_search_documents(query: str, top_k: int = 5) -> list:
    """Vector search with section-aware boosting.
    Detects section intent, runs primary search + boosted section search, merges & deduplicates."""
    section_hints = detect_section_intent(query)
    all_results = []
    seen_ids = set()

    # Primary: unfiltered vector search with higher recall (threshold 0.2)
    primary = vector_search_documents(query, top_k=top_k, threshold=0.2)
    for r in primary:
        rid = r.get("id")
        if rid and rid not in seen_ids:
            seen_ids.add(rid)
            r["_match_type"] = "vector"
            all_results.append(r)

    # Boosted: if section detected, run targeted search with lower threshold
    for section, _ in section_hints:
        boosted = vector_search_documents(query, top_k=top_k, threshold=0.15,
                                          section_heading=section)
        for r in boosted:
            rid = r.get("id")
            if rid and rid not in seen_ids:
                seen_ids.add(rid)
                r["_match_type"] = f"section:{section}"
                # Insert at front for priority
                all_results.insert(0, r)

    return all_results[:top_k]
