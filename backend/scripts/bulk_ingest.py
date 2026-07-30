"""
Bulk Vector Ingestor: Scans data/raw/ for PDFs/HTML/TXT files, extracts text,
chunks with section detection, embeds with all-MiniLM-L6-v2, and upserts to
Supabase documents + document_chunks tables.

Usage:
    python -m backend.scripts.bulk_ingest --all
    python -m backend.scripts.bulk_ingest --dir data/raw/pdfs
    python -m backend.scripts.bulk_ingest --force   (re-ingest even if hash matches)
    python -m backend.scripts.bulk_ingest --list    (just list what would be ingested)
"""

import hashlib
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ingest import extract_text, detect_doc_type, detect_title, detect_portal_from_text
from ingest import split_with_sections, get_model, get_supabase

RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"
SUPPORTED = (".pdf", ".htm", ".html", ".txt")

BATCH_SIZE = 50


def scan_directory(directory: Path) -> list:
    """Scan a directory for supported files and return metadata."""
    files = sorted(
        p for p in directory.rglob("*")
        if p.suffix.lower() in SUPPORTED and not p.name.startswith(".")
    )
    results = []
    for f in files:
        stat = f.stat()
        results.append({
            "path": str(f),
            "name": f.name,
            "stem": f.stem,
            "suffix": f.suffix.lower(),
            "size_kb": round(stat.st_size / 1024, 1),
            "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        })
    return results


def content_hash(text: str) -> str:
    """SHA-256 hash of content for idempotency."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]


def check_exists(hash_val: str) -> bool:
    """Check if a document with this content hash already exists in Supabase."""
    try:
        resp = get_supabase().table("documents").select("id").eq("content_hash", hash_val).limit(1).execute()
        return len(resp.data) > 0
    except Exception:
        return False


def ingest_file(filepath: str, force: bool = False) -> dict:
    """Extract, chunk, embed, and upsert a single file."""
    path = Path(filepath)
    print(f"\n  [INGEST] {path.name} ({round(path.stat().st_size/1024, 1)} KB)")

    text = extract_text(str(path))
    if not text or len(text.strip()) < 20:
        print(f"    [SKIP] Empty or too short")
        return {"status": "skipped", "reason": "empty"}

    c_hash = content_hash(text)
    if not force and check_exists(c_hash):
        print(f"    [SKIP] Already ingested (hash: {c_hash[:12]}...)")
        return {"status": "skipped", "reason": "already ingested"}

    title = detect_title(str(path), text)
    doc_type = detect_doc_type(str(path))
    portal = detect_portal_from_text(text)

    chunks = split_with_sections(text, portal=portal)
    if not chunks:
        print(f"    [SKIP] No chunks produced")
        return {"status": "skipped", "reason": "no chunks"}

    print(f"    Chunks: {len(chunks)} | Type: {doc_type} | Portal: {portal}")

    model = get_model()
    doc_id = str(hashlib.md5(filepath.encode()).hexdigest()[:16])

    doc_payload = {
        "id": doc_id,
        "title": title,
        "content": text[:2000],
        "doc_type": doc_type,
        "portal": portal,
        "source_url": "",
        "content_hash": c_hash,
        "published_date": datetime.now().date().isoformat(),
    }

    chunk_rows = []
    for i, chunk in enumerate(chunks):
        chunk_text = chunk.get("content", "")
        if not chunk_text.strip():
            continue

        chunk_id = f"{doc_id}_chunk_{i:04d}"
        section = chunk.get("section", "general")
        heading = chunk.get("heading", "")

        embedding = model.encode(chunk_text, normalize_embeddings=True).tolist()

        chunk_rows.append({
            "id": chunk_id,
            "document_id": doc_id,
            "content": chunk_text,
            "section_heading": section if not heading else f"{section}: {heading}",
            "heading": heading,
            "chunk_index": i,
            "embedding": embedding,
            "portal": portal,
        })

    try:
        sb = get_supabase()
        sb.table("documents").upsert(doc_payload, on_conflict="id").execute()

        for i in range(0, len(chunk_rows), BATCH_SIZE):
            batch = chunk_rows[i:i + BATCH_SIZE]
            sb.table("document_chunks").upsert(batch, on_conflict="id").execute()

        print(f"    [DB] Upserted 1 document + {len(chunk_rows)} chunks")
        return {"status": "ingested", "chunks": len(chunk_rows), "doc_id": doc_id}

    except Exception as e:
        print(f"    [DB] Error: {e}")
        return {"status": "error", "error": str(e)}


def ingest_all(directories: list = None, force: bool = False, dry_run: bool = False):
    """Scan and ingest all supported files in given directories."""
    if not directories:
        directories = [RAW_DIR]

    all_files = []
    for d in directories:
        dir_path = Path(d)
        if not dir_path.exists():
            print(f"[WARN] Directory not found: {d}")
            continue
        files = scan_directory(dir_path)
        all_files.extend(files)
        print(f"  {dir_path.name}: {len(files)} files")

    print(f"\nTotal files found: {len(all_files)}")
    if not all_files:
        print("Nothing to ingest.")
        return {"total": 0, "ingested": 0}

    if dry_run:
        print("\nDry run — files that would be ingested:")
        for f in all_files:
            print(f"  {f['path']} ({f['size_kb']} KB)")
        return {"total": len(all_files), "dry_run": True}

    results = {"total": len(all_files), "ingested": 0, "skipped": 0, "errors": 0}
    for i, f in enumerate(all_files):
        print(f"\n[{i+1}/{len(all_files)}]", end="")
        r = ingest_file(f["path"], force=force)
        if r["status"] == "ingested":
            results["ingested"] += 1
        elif r["status"] == "error":
            results["errors"] += 1
        else:
            results["skipped"] += 1
        time.sleep(0.5)

    print(f"\n{'='*50}")
    print(f"  Total: {results['total']}")
    print(f"  Ingested: {results['ingested']}")
    print(f"  Skipped: {results['skipped']}")
    print(f"  Errors: {results['errors']}")
    return results


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Bulk vector ingestion pipeline")
    parser.add_argument("--all", action="store_true", help="Ingest all files in data/raw/")
    parser.add_argument("--dir", help="Specific directory to scan")
    parser.add_argument("--force", action="store_true", help="Re-ingest even if hash matches")
    parser.add_argument("--list", action="store_true", help="List files that would be ingested (dry run)")
    args = parser.parse_args()

    print(f"=== Bulk Vector Ingestor ===")
    print(f"Date: {datetime.now().isoformat()}")
    print(f"Model: all-MiniLM-L6-v2 (384-dim)")
    print(f"Chunk: 512 / overlap 128")

    dirs = [args.dir] if args.dir else None

    if args.list:
        ingest_all(directories=dirs, dry_run=True)
    else:
        ingest_all(directories=dirs, force=args.force)
