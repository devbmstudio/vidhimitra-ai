"""Debug guide matching"""
import json
guides = json.load(open("backend/data/process_guides.json", "r"))

def match(query):
    q = query.lower().strip()
    for h in ["dr.", "shri", "smt", "mr", "mrs", "ms"]:
        q = q.replace(h + " ", "")
    q_words = set(q.split())
    q_tokens = q.split()
    q_bigrams = set()
    for i in range(len(q_tokens) - 1):
        q_bigrams.add(q_tokens[i] + " " + q_tokens[i + 1])
    query_acronyms = set()
    for w in query.split():
        if len(w) >= 3:
            query_acronyms.add(w.upper())

    signals = {
        "certificate": ["certificate", "certif", "dakhla", "pramaanpatra", "provisional", "cast", "income", "domicile", "birth", "death", "residence", "nationality"],
        "ews": ["ews", "economically weaker", "general", "ekm", "economic"],
        "income": ["income", "aay", "income proof", "financial", "salary", "tax", "itr", "annual", "8 lakh", "rupees"],
        "caste": ["caste", "jati", "sc", "st", "obc", "vjnt", "nt", "sbc", "non creamy", "ncl", "creamy layer"],
        "scholarship": ["scholarship", "scholar", "shishyavrutti", "bhatta", "yojna", "yojana", "scheme", "allowance", "maintenance", "stipend", "fee", "reimbursement", "freeship", "tuition"],
        "mahadbt": ["mahadbt", "dbt", "maharashtra scholarship", "aaple dbt", "aaple sarkar", "maha"],
        "dpd": ["dpd", "panjabrao", "deshmukh", "vasatigruh", "hostel", "maintenance allowance", "dr panjabrao", "dpd scholarship", "obc scholarship"],
    }
    topic_keywords = {
        "scholarship": ["scholarship", "scholar", "allowance", "stipend", "scheme", "yojana", "bhatta", "fellowship", "tuition", "fee"],
        "certificate": ["certificate", "certif", "cert", "dakhla", "pramaanpatra", "cast", "income certificate", "domicile", "birth certificate"],
    }

    scored = []
    for g in guides:
        score = 0
        title_lower = g["title"].lower()
        tags_lower = [t.lower() for t in g.get("tags", [])]
        guide_text = title_lower + " " + " ".join(tags_lower)
        q_parts = [w for w in q.split() if len(w) >= 3]

        title_matches = sum(1 for w in q_parts if w in title_lower)
        score += title_matches * 2

        title_bigrams = set()
        title_tokens = title_lower.split()
        for i in range(len(title_tokens) - 1):
            title_bigrams.add(title_tokens[i] + " " + title_tokens[i + 1])
        bigram_overlap = len(q_bigrams & title_bigrams)
        score += bigram_overlap * 5

        title_words = set(title_lower.split())
        overlap = len(q_words & title_words)
        score += overlap * 3

        for tag in tags_lower:
            if q == tag or tag == q:
                score += 5
            tag_words = set(tag.split())
            for qw in q_words:
                if qw in tag_words:
                    score += 2
                for tw in tag_words:
                    if len(qw) >= 4 and len(tw) >= 4 and (qw in tw or tw in qw):
                        score += 1

        for qw in q_parts:
            if qw in title_lower:
                score += 1

        for g_tag_upper in [t.upper() for t in tags_lower]:
            for qa in query_acronyms:
                if qa == g_tag_upper or qa in g_tag_upper or g_tag_upper in qa:
                    score += 5

        for sig_key, sig_words in signals.items():
            for sw in sig_words:
                if sw in q:
                    score += 1

        all_tag_words = set(w for t in tags_lower for w in t.split())
        strong_overlap = len(q_words & all_tag_words)
        if strong_overlap >= 2:
            score += 5

        # Domain boost
        guide_cat = g.get("category", "").lower()
        for topic, keywords in topic_keywords.items():
            has_topic = any(kw in q for kw in keywords)
            if has_topic and guide_cat == topic:
                score += 5

        scored.append((score, g["id"], g["title"]))

    scored.sort(key=lambda x: -x[0])
    return scored[0]

for q in [
    "Am I eligible for DPD scholarship if my family income is 9 lakh?",
    "What is the income limit for EWS?",
    "Tell me about Dr. Panjabrao Deshmukh scholarship allowance",
]:
    result = match(q)
    print(f"Q: {q[:60]}... -> {result[1]} ({result[2][:50]}) score={result[0]}")
