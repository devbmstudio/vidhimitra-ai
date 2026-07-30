"""
LLM Structurer: Reads raw scraped data, uses Groq to extract structured
process_guides entries, and upserts them into Supabase.

Usage:
    python -m backend.scripts.generate_guides --all
    python -m backend.scripts.generate_guides --mahadbt-only
    python -m backend.scripts.generate_guides --aaple-only
    python -m backend.scripts.generate_guides --force   (re-generate existing)
"""

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import groq_client, supabase

RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"
SCHEMES_DIR = RAW_DIR / "schemes"
SERVICES_DIR = RAW_DIR / "services"

# Existing guides that were hand-crafted (gold standard) — skip by default
GOLD_STANDARD_IDS = {
    "ews_certificate", "income_certificate", "caste_certificate",
    "mahadbt_registration", "dpd_scholarship",
    "domicile_certificate", "birth_death_certificate",
    "nmms_scholarship", "caste_validity",
}

SYSTEM_PROMPT = """You are a data extraction assistant for Maharashtra government schemes.
Extract structured information from the raw text below and return ONLY valid JSON.
Use the exact schema shown. If a field has no data, use an empty string or empty list.
Do not make up information. Only extract what is present in the text.

Output JSON schema:
{
  "id": "unique_id_string",
  "title": "full title",
  "category": "certificate | scholarship | registration | license | noc",
  "state": "Maharashtra",
  "tags": ["tag1", "tag2"],
  "eligibility": "eligibility text",
  "documents_needed": ["doc1", "doc2"],
  "step_by_step": ["step1", "step2"],
  "where_to_apply": "location text",
  "portal": "portal url or empty",
  "common_problems": ["problem1", "problem2"],
  "validity": "validity text or empty",
  "fees": "fee text or empty"
}
"""


def _load_raw_files(directory: Path) -> list:
    """Load all raw JSON files from a directory."""
    results = []
    if not directory.exists():
        return results
    for f in sorted(directory.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            data["_source_file"] = str(f)
            results.append(data)
        except Exception as e:
            print(f"  [WARN] Could not read {f}: {e}")
    return results


def _call_groq(raw_text: str, max_retries: int = 2) -> dict:
    """Send raw text to Groq and return structured JSON."""
    prompt = f"Raw text:\n{raw_text[:3000]}\n\nExtract the structured data as JSON."
    for attempt in range(max_retries):
        try:
            resp = groq_client.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
                temperature=0.1,
                max_tokens=1500,
            )
            text = resp.choices[0].message.content.strip()
            text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            return json.loads(text)
        except Exception as e:
            print(f"    [GROQ] Attempt {attempt+1} failed: {e}")
            time.sleep(3)
    return None


def _validate_guide(data: dict) -> bool:
    """Validate guide has minimum required fields."""
    required = ["id", "title", "category"]
    for field in required:
        if not data.get(field):
            print(f"    [VALIDATE] Missing required field: {field}")
            return False
    if data["category"] not in ("certificate", "scholarship", "registration", "license", "noc"):
        print(f"    [VALIDATE] Invalid category: {data['category']}")
        return False
    return True


def _merge_with_metadata(raw: dict, parsed: dict) -> dict:
    """Merge raw metadata with LLM-parsed data (metadata takes precedence)."""
    guide = {
        "id": raw.get("id", parsed.get("id", "")).lower().replace(" ", "_").replace("-", "_"),
        "title": parsed.get("title", raw.get("title", "")),
        "category": raw.get("category", parsed.get("category", "certificate")),
        "state": "Maharashtra",
        "tags": parsed.get("tags", []),
        "eligibility": parsed.get("eligibility", ""),
        "documents_needed": parsed.get("documents_needed", []),
        "step_by_step": parsed.get("step_by_step", []),
        "where_to_apply": parsed.get("where_to_apply", ""),
        "portal": parsed.get("portal", raw.get("source", "")),
        "common_problems": parsed.get("common_problems", []),
        "validity": parsed.get("validity", ""),
        "fees": parsed.get("fees", ""),
    }
    if raw.get("department") and "department" not in guide["tags"]:
        guide["tags"].append(raw["department"].lower())
    if raw.get("source"):
        guide["tags"].append(raw["source"])
    if raw.get("category") and raw["category"] not in guide["tags"]:
        guide["tags"].append(raw["category"])
    return guide


def generate_guide(raw_item: dict, force: bool = False) -> dict:
    """Generate a structured guide from raw scraped data."""
    sid = raw_item.get("id", "unknown")
    raw_text = raw_item.get("raw_text", "")

    if sid in GOLD_STANDARD_IDS and not force:
        print(f"  [SKIP] {sid} (gold standard guide exists)")
        return None

    if not raw_text or len(raw_text.strip()) < 10:
        print(f"  [SKIP] {sid} (no raw text to process)")
        return None

    print(f"  [GROQ] Structuring '{sid}'...")
    parsed = _call_groq(raw_text)
    if not parsed:
        print(f"    [FAIL] Could not parse '{sid}'")
        return None

    guide = _merge_with_metadata(raw_item, parsed)
    if not _validate_guide(guide):
        print(f"    [FAIL] Validation failed for '{sid}'")
        return None

    guide["tags"] = list(set(guide["tags"]))
    return guide


def upsert_guide(guide: dict):
    """Upsert a guide into Supabase process_guides table."""
    try:
        supabase.table("process_guides").upsert(guide, on_conflict="id").execute()
        print(f"    [DB] Upserted '{guide['id']}'")
        return True
    except Exception as e:
        print(f"    [DB] Error upserting '{guide['id']}': {e}")
        return False


def generate_all(force: bool = False):
    """Generate and upsert all guides from raw data."""
    schemes = _load_raw_files(SCHEMES_DIR)
    services = _load_raw_files(SERVICES_DIR)
    all_items = schemes + services

    print(f"\n{'='*60}")
    print(f"  LLM Guide Generator")
    print(f"  Items to process: {len(all_items)} ({len(schemes)} schemes, {len(services)} services)")
    print(f"{'='*60}")

    generated = 0
    upserted = 0
    for item in all_items:
        sid = item.get("id", "unknown")
        print(f"\n  [{all_items.index(item)+1}/{len(all_items)}] {sid}")

        guide = generate_guide(item, force=force)
        if not guide:
            continue
        generated += 1

        if upsert_guide(guide):
            upserted += 1

        time.sleep(2)

    print(f"\nDone. Generated: {generated}, Upserted: {upserted}")
    return {"generated": generated, "upserted": upserted}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Generate process guides from raw data")
    parser.add_argument("--all", action="store_true", help="Process all raw data")
    parser.add_argument("--force", action="store_true", help="Re-generate even gold standard guides")
    args = parser.parse_args()

    if args.all:
        generate_all(force=args.force)
    else:
        print("Use --all to process all raw data. Or --force to overwrite existing guides.")
