"""
Ingestion pipeline for Maharashtra government documents.
Usage:
    python ingest.py path/to/document.pdf [--source_url URL] [--portal mahadbt|aaplesarkar|nsp]
    python ingest.py path/to/documents/ [--portal aaplesarkar]
"""
import argparse
import json
import os
import re
import sys
import uuid
from datetime import datetime
from pathlib import Path

import pdfplumber
import trafilatura
from langchain_text_splitters import RecursiveCharacterTextSplitter
from sentence_transformers import SentenceTransformer
from supabase import create_client

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
if not SUPABASE_URL or not SUPABASE_KEY:
    from dotenv import load_dotenv
    load_dotenv()
    SUPABASE_URL = os.getenv("SUPABASE_URL", "")
    SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")

_supabase = None
def get_supabase():
    global _supabase
    if _supabase is None:
        _supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _supabase

_model = None
def get_model():
    global _model
    if _model is None:
        _model = SentenceTransformer("all-MiniLM-L6-v2")
    return _model

SECTION_PATTERNS = [
    r"^#{1,3}\s*(.*)$",
    r"^【\d+†.*?】\s*$",
    r"^(Eligibility|Eligibility Criteria|Who Can Apply|Benefits):?\s*$",
    r"^(Documents Needed|Required Documents|Documents Required|What You Need):?\s*$",
    r"^(Step(?:-by-|- )?Step|How to Apply|Application Process|Procedure|Process):?",
    r"^(Where to Apply|How to Apply Online|Apply Online):?\s*$",
    r"^(Portal|Official Website|Official Portal):?\s*$",
    r"^(Common Problems|Common Issues|Troubleshooting|FAQ|FAQs):?\s*$",
    r"^(Validity|Valid For|Period of Validity):?\s*$",
    r"^(Fees|Fee|Application Fee|Processing Fee|Cost):?\s*$",
    r"^(Scheme Details|About the Scheme|Overview|Description):?\s*$",
    r"^(Amount|Scholarship Amount|Financial Assistance|Stipend|Maintenance Allowance):?\s*$",
    r"^(Deadline|Last Date|Application Deadline|Due Date):?\s*$",
    r"^(Contact|Helpdesk|Support|Helpline|Phone|Email):?\s*$",
    r"^(Important Notes|Notes|Terms and Conditions|Conditions):?\s*$",
]

SECTION_ALIASES = {
    "eligibility": ["eligibility", "eligibility criteria", "who can apply", "benefits"],
    "documents_needed": ["documents needed", "required documents", "documents required", "what you need"],
    "steps": ["step-by-step", "step by step", "how to apply", "application process", "procedure", "process"],
    "where_to_apply": ["where to apply", "how to apply online", "apply online"],
    "portal": ["portal", "official website", "official portal"],
    "common_problems": ["common problems", "common issues", "troubleshooting", "faq", "faqs"],
    "validity": ["validity", "valid for", "period of validity"],
    "fees": ["fees", "fee", "application fee", "processing fee", "cost"],
    "amount": ["amount", "scholarship amount", "financial assistance", "stipend", "maintenance allowance"],
    "deadline": ["deadline", "last date", "application deadline", "due date"],
    "scheme_details": ["scheme details", "about the scheme", "overview", "description"],
    "contact": ["contact", "helpdesk", "support", "helpline", "phone", "email"],
    "notes": ["important notes", "notes", "terms and conditions", "conditions"],
}

DETECT_PORTAL = re.compile(
    r"(mahadbt\.maharashtra\.gov\.in|aaplesarkar\.mahaonline\.gov\.in|"
    r"scholarships\.gov\.in|nsp\.gov\.in|ugc\.gov\.in|aicte\.india\.org|"
    r"india code|egazette|prsindia)",
    re.IGNORECASE,
)

STANDARD_SECTION_ORDER = [
    "scheme_details",
    "eligibility",
    "documents_needed",
    "steps",
    "where_to_apply",
    "portal",
    "amount",
    "deadline",
    "fees",
    "validity",
    "common_problems",
    "contact",
    "notes",
]


def _canonical_section(raw_header: str) -> str:
    raw_lower = re.sub(r"[#*_]", "", raw_header).strip().lower()
    for canonical, aliases in SECTION_ALIASES.items():
        for alias in aliases:
            if raw_lower == alias or raw_lower.startswith(alias) or alias.startswith(raw_lower):
                return canonical
    return raw_lower.replace(" ", "_").replace("-", "_")


def extract_text_pdf(path: str) -> str:
    """Extract text from PDF using pdfplumber."""
    text_parts = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            t = page.extract_text()
            if t:
                text_parts.append(t)
    return "\n\n".join(text_parts)


def extract_text_html(path: str) -> str:
    """Extract clean text from HTML using trafilatura."""
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        html = f.read()
    result = trafilatura.extract(html, include_formatting=True, include_links=True)
    return result or ""


def extract_text(path: str) -> str:
    ext = Path(path).suffix.lower()
    if ext == ".pdf":
        return extract_text_pdf(path)
    elif ext in (".htm", ".html"):
        return extract_text_html(path)
    else:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()


def detect_doc_type(path: str) -> str:
    ext = Path(path).suffix.lower()
    mapping = {".pdf": "PDF", ".htm": "HTML", ".html": "HTML", ".json": "JSON", ".txt": "Text"}
    return mapping.get(ext, "Unknown")


def detect_title(path: str, text: str) -> str:
    stem = Path(path).stem.replace("_", " ").replace("-", " ").title()
    first_line = text.strip().split("\n")[0][:120] if text.strip() else stem
    if len(first_line) < 5 or len(first_line) > 200:
        return stem
    return first_line


def detect_portal_from_text(text: str, hint: str = None) -> str:
    if hint:
        return hint
    m = DETECT_PORTAL.search(text)
    return m.group(1).lower() if m else "unknown"


def split_with_sections(text: str, portal: str = "unknown") -> list:
    """Split text into tagged chunks, preserving section boundaries."""
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=512,
        chunk_overlap=128,
        length_function=len,
        separators=["\n## ", "\n### ", "\n\n", "\n", ". ", " "],
        keep_separator=True,
    )

    lines = text.split("\n")
    sections = []
    current_section = "general"
    current_lines = []

    for line in lines:
        header_match = None
        for pat in SECTION_PATTERNS:
            m = re.match(pat, line, re.IGNORECASE)
            if m:
                header_match = m
                break

        if header_match:
            if current_lines:
                sections.append((current_section, current_lines))
            raw = header_match.group(1) if header_match.groups() else line
            current_section = _canonical_section(raw)
            current_lines = []
        else:
            current_lines.append(line)

    if current_lines:
        sections.append((current_section, current_lines))

    chunks = []
    chunk_index = 0
    for section_name, section_lines in sections:
        section_text = "\n".join(section_lines).strip()
        if len(section_text) < 10:
            continue

        docs = splitter.create_documents([section_text])
        for i, doc in enumerate(docs):
            content = doc.page_content.strip()
            if len(content) < 10:
                continue
            chunks.append({
                "content": content,
                "section_heading": section_name,
                "portal": portal,
                "chunk_index": chunk_index,
            })
            chunk_index += 1

    return chunks


def upsert_document_record(title: str, source_url: str, doc_type: str, portal: str) -> str:
    """Upsert into documents table, return document_id."""
    doc_id = str(uuid.uuid4())
    record = {
        "id": doc_id,
        "title": title,
        "doc_type": doc_type,
        "source_url": source_url or "",
        "portal": portal,
        "published_date": datetime.now().isoformat(),
    }
    try:
        result = get_supabase().table("documents").upsert(
            record, on_conflict="source_url"
        ).execute()
        if result.data:
            return result.data[0].get("id", doc_id)
    except Exception as e:
        print(f"[WARN] Document upsert failed: {e}, using generated ID")
    return doc_id


def batch_upsert_chunks(doc_id: str, chunks: list, embeddings: list, batch_size: int = 50):
    """Upsert chunks into document_chunks in batches."""
    total = len(chunks)
    for start in range(0, total, batch_size):
        batch = chunks[start : start + batch_size]
        emb_batch = embeddings[start : start + batch_size]
        records = []
        for i, (chunk, emb) in enumerate(zip(batch, emb_batch)):
            records.append({
                "id": str(uuid.uuid4()),
                "document_id": doc_id,
                "content": chunk["content"],
                "section_heading": chunk["section_heading"],
                "portal": chunk["portal"],
                "chunk_index": chunk["chunk_index"],
                "embedding": emb.tolist(),
            })
        try:
            get_supabase().table("document_chunks").upsert(
                records, on_conflict="id"
            ).execute()
            print(f"  Uploaded batch {start // batch_size + 1}/{(total - 1) // batch_size + 1} "
                  f"({len(records)} chunks)")
        except Exception as e:
            print(f"  [ERROR] Batch {start // batch_size + 1} failed: {e}")


def ingest_file(filepath: str, source_url: str = None, portal: str = None):
    """Ingest a single file into the vector pipeline."""
    print(f"\n📄 Ingesting: {filepath}")
    text = extract_text(filepath)
    if not text.strip():
        print(f"  [SKIP] Empty text extracted")
        return

    doc_type = detect_doc_type(filepath)
    title = detect_title(filepath, text)
    portal = detect_portal_from_text(text, portal)

    print(f"  Title: {title}")
    print(f"  Type: {doc_type}  Portal: {portal}")
    print(f"  Text length: {len(text)} chars")

    doc_id = upsert_document_record(title, source_url, doc_type, portal)
    print(f"  Document ID: {doc_id}")

    chunks = split_with_sections(text, portal)
    if not chunks:
        print(f"  [SKIP] No chunks generated")
        return
    print(f"  Chunks: {len(chunks)}")

    chunk_texts = [c["content"] for c in chunks]
    print(f"  Generating embeddings ({len(chunk_texts)} chunks)...")
    embeddings = get_model().encode(chunk_texts, show_progress_bar=True)
    print(f"  Embeddings generated: {embeddings.shape[0]} x {embeddings.shape[1]}")

    batch_upsert_chunks(doc_id, chunks, embeddings)
    print(f"  ✅ Done: {len(chunks)} chunks ingested")


def ingest_directory(dirpath: str, portal: str = None):
    """Ingest all compatible files in a directory."""
    supported = (".pdf", ".htm", ".html", ".txt")
    files = sorted(
        p for p in Path(dirpath).rglob("*")
        if p.suffix.lower() in supported and not p.name.startswith(".")
    )
    print(f"\n📁 Scanning: {dirpath} ({len(files)} files found)")
    for f in files:
        ingest_file(str(f), portal=portal)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest Maharashtra gov docs into vector DB")
    parser.add_argument("path", help="File or directory to ingest")
    parser.add_argument("--source-url", help="Original source URL")
    parser.add_argument("--portal", choices=["mahadbt", "aaplesarkar", "nsp", "ugc", "aicte"],
                        help="Portal name")
    args = parser.parse_args()

    path = args.path
    if not os.path.exists(path):
        print(f"Error: {path} not found")
        sys.exit(1)

    print(f"🚀 Document Ingestion Pipeline")
    print(f"   Model: all-MiniLM-L6-v2 (384-dim)")
    print(f"   Chunk: 512 / overlap 128")
    if os.path.isdir(path):
        ingest_directory(path, portal=args.portal)
    else:
        ingest_file(path, source_url=args.source_url, portal=args.portal)
