import re
import json
from config import groq_client

DOC_TYPE_KEYWORDS = {
    "Act": [r"\bact\b", r"\bact\s+no", r"\bact\s+\d{4}"],
    "Amendment": [r"\bamendment\b", r"\bamend\b", r"\bamended\b"],
    "Bill": [r"\bbill\b", r"\bdraft\s+bill\b"],
    "Rule": [r"\brule[s]?\b", r"\brule[s]?\s+\d{4}"],
    "Regulation": [r"\bregulation[s]?\b", r"\bregulate\b"],
    "Notification": [r"\bnotification\b", r"\bpublic\s+notice\b", r"\bgazette\s+notification\b"],
    "Office Memorandum": [r"\boffice\s+memorandum\b", r"\bom\s+no", r"\bom\s+\d"],
    "Circular": [r"\bcircular\b", r"\bcircular\s+no\b"],
    "Press Release": [r"\bpress\s+release\b", r"\bpress\s+note\b", r"\bpib\s+release\b"],
}

SCHOLARSHIP_PROVIDER_KEYWORDS = {
    "Central": [r"\bcentral\b", r"\bministry\s+of\b", r"\bnational\s+scholarship\b"],
    "State": [r"\bstate\b", r"\bstate\s+government\b"],
    "UGC": [r"\bugc\b", r"\buniversity\s+grants\s+commission\b"],
    "AICTE": [r"\baicte\b", r"\ball\s+india\s+council\b"],
    "ICCR": [r"\biccr\b", r"\bindian\s+council\s+for\s+cultural\b"],
}

EDUCATION_LEVEL_KEYWORDS = {
    "School": [r"\bschool\b", r"\bclass\s+(9|10|11|12|ix|x|xi|xii)\b", r"\bsecondary\b", r"\bmatric\b"],
    "Undergraduate": [r"\bunder\s*graduate\b", r"\bug\b", r"\bbachelor\b", r"\bgraduation\b", r"\bdegree\b"],
    "Postgraduate": [r"\bpost\s*graduate\b", r"\bpg\b", r"\bmaster\b", r"\bpost\s*grad\b"],
    "PhD": [r"\bphd\b", r"\bph\.d\.\b", r"\bdoctorate\b", r"\bdoctoral\b", r"\bm\.phil\b"],
}

CATEGORY_KEYWORDS = {
    "SC": [r"\bsc\b", r"\bscheduled\s+caste\b"],
    "ST": [r"\bst\b", r"\bscheduled\s+tribe\b"],
    "OBC": [r"\bobc\b", r"\bother\s+backward\s+class\b"],
    "EBC": [r"\bebc\b", r"\beconomically\s+backward\b"],
    "Girls": [r"\bgirl[s]?\b", r"\bfemale\b", r"\bwomen\b", r"\bwoman\b", r"\bmahila\b"],
    "Minority": [r"\bminority\b", r"\bminorities\b", r"\bmuslim\b", r"\bchristian\b", r"\bsikh\b", r"\bbuddhist\b", r"\bjain\b"],
    "Specially-abled": [r"\bdisabled?\b", r"\bdivyang\b", r"\bspecially[\s-]abled\b", r"\bhandicapped\b", r"\bwith\s+disabilities\b"],
    "General": [r"\bgeneral\b", r"\ball\s+candidate\b", r"\bopen\s+for\s+all\b"],
}


def _match_keywords(text, keyword_dict):
    results = []
    text_lower = text.lower()
    for label, patterns in keyword_dict.items():
        for p in patterns:
            if re.search(p, text_lower):
                results.append(label)
                break
    return results


def classify_document(title, content=""):
    combined = f"{title} {content}"
    doc_types = _match_keywords(combined, DOC_TYPE_KEYWORDS)
    doc_type = doc_types[0] if doc_types else "Document"

    ministry = None
    ministry_match = re.search(r"(ministry\s+of\s+[\w\s]+?)(?:\s*\d{4}|$|[.;])", combined, re.IGNORECASE)
    if ministry_match:
        ministry = ministry_match.group(1).strip().title()

    return {
        "doc_type": doc_type,
        "ministry": ministry,
    }


def classify_scholarship(title, description=""):
    combined = f"{title} {description}"
    provider = "Central"
    providers_found = _match_keywords(combined, SCHOLARSHIP_PROVIDER_KEYWORDS)
    if providers_found:
        provider = providers_found[0]

    levels = _match_keywords(combined, EDUCATION_LEVEL_KEYWORDS)
    if not levels:
        levels = ["Undergraduate"]

    categories = _match_keywords(combined, CATEGORY_KEYWORDS)
    if not categories:
        categories = ["General"]

    return {
        "provider": provider,
        "education_level": levels,
        "category": categories,
    }


def smart_classify(text, title="", mode="auto"):
    if mode == "document" or mode == "auto":
        result = classify_document(title, text)
        if mode == "document":
            return result

    if mode == "scholarship" or mode == "auto":
        s_result = classify_scholarship(title, text)
        if mode == "scholarship":
            return {"type": "scholarship", **s_result}
        is_scholarship = bool(re.search(r"\b(scholarship|fellowship|stipend|freeship)\b", text, re.IGNORECASE))
        if is_scholarship:
            return {"type": "scholarship", **s_result}

    is_document = not re.search(r"\b(scholarship|fellowship|stipend)\b", text, re.IGNORECASE)
    if is_document:
        return {"type": "document", **result}

    return {"type": "document", "doc_type": "Document", "ministry": None}


def groq_classify(text):
    try:
        resp = groq_client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Classify the following text as either 'document' or 'scholarship'. "
                        "If document: extract doc_type (Act/Amendment/Bill/Rule/Regulation/Notification/OM/Circular/PressRelease). "
                        "If scholarship: extract provider (Central/State/UGC/AICTE/ICCR), "
                        "education_level (School/Undergraduate/Postgraduate/PhD), "
                        "categories (SC/ST/OBC/EBC/Girls/Minority/Specially-abled/General). "
                        "Return JSON only."
                    ),
                },
                {"role": "user", "content": text[:2000]},
            ],
            temperature=0.1,
            max_tokens=300,
        )
        return resp.choices[0].message.content
    except Exception:
        return None


def extract_document_details(text, filename=""):
    """Use LLM to extract structured details from an Indian government document.
    Returns None if LLM fails to parse valid JSON."""
    if not text or len(text.strip()) < 20:
        return None
    try:
        prompt = f"""You are an expert at analyzing Indian government documents. Extract the following details from this document text. Be thorough but honest — if something is not found, return null.

Document filename: {filename}

Text:
{text[:3000]}

Return ONLY valid JSON with these fields:
{{
  "doc_type": "one of: ScholarshipSanctionLetter, ScholarshipNotification, Act, Bill, Notification, OfficeMemorandum, Circular, PressRelease, SchemeGuidelines, FeeReimbursement, ApplicationForm, AwardLetter, Other",
  "is_scholarship": true/false,
  "scheme_name": "full official scheme name or null",
  "provider": "Central/State/UGC/AICTE/ICCR/Corporate/Other or null",
  "amount": "numeric amount in rupees (e.g. 12000) or null",
  "application_deadline": "date in YYYY-MM-DD format or null",
  "portal": "portal URL or name (e.g. mahadbt.maharashtra.gov.in) or null",
  "category": "SC/ST/OBC/EBC/General/Minority/Girls/Specially-abled or null",
  "education_level": "School/Undergraduate/Postgraduate/PhD/All or null",
  "state": "state name if state-specific or null",
  "description": "one-line plain language summary of what this document is (max 100 chars)",
  "action_items": ["list of 2-3 things the person needs to do next, in simple language"],
  "helpful_for_others": true/false
}}"""
        resp = groq_client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {"role": "system", "content": "You extract structured data from Indian government documents. Return ONLY valid JSON."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=500,
        )
        content = resp.choices[0].message.content.strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[1]
            if content.endswith("```"):
                content = content[:-3]
        return json.loads(content)
    except Exception as e:
        print(f"[ExtractDetails] Error: {e}")
        return None
