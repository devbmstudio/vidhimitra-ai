"""One-time seed script: loads process_guides.json into both local file and Supabase."""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
env_path = Path(__file__).resolve().parent.parent / ".env"
if env_path.exists():
    load_dotenv(dotenv_path=env_path)

from config import supabase
from db import _save_process_guides_local

GUIDES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "process_guides.json")

def main():
    if not os.path.exists(GUIDES_FILE):
        print(f"Guides file not found: {GUIDES_FILE}")
        sys.exit(1)

    with open(GUIDES_FILE, encoding="utf-8") as f:
        guides = json.load(f)

    print(f"Loaded {len(guides)} process guides")

    # Save locally
    _save_process_guides_local(guides)
    print("Saved to local file")

    # Save to Supabase
    for g in guides:
        try:
            supabase.table("process_guides").upsert(g, on_conflict="id").execute()
            print(f"  Upserted: {g['id']} - {g['title']}")
        except Exception as e:
            print(f"  Supabase error for {g['id']}: {e}")

    print("\nDone! Guides are ready.")
    print("Run 'python -c \"from db import _load_process_guides_local; print(len(_load_process_guides_local()))\"' to verify")

if __name__ == "__main__":
    main()
