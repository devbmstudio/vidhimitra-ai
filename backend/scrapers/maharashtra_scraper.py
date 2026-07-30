"""
Maharashtra Scraper: Fetches raw data from MahaDBT API and Aaple Sarkar portal.
Caches results in backend/data/raw/ for downstream processing.

Usage:
    python -m backend.scrapers.maharashtra_scraper --all
    python -m backend.scrapers.maharashtra_scraper --mahadbt-only
    python -m backend.scrapers.maharashtra_scraper --aaple-only
"""

import json
import os
import sys
import time
import hashlib
from datetime import datetime
from pathlib import Path

import requests
import trafilatura

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"
SCHEMES_DIR = RAW_DIR / "schemes"
SERVICES_DIR = RAW_DIR / "services"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/html, */*",
    "Accept-Language": "en-IN,en;q=0.9,hi;q=0.8",
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)
SESSION.verify = False
SESSION.trust_env = False

# ---------------------------------------------------------------------------
# Known Aaple Sarkar services (service id → metadata)
# These are the high-priority citizen-facing services on the portal.
# ---------------------------------------------------------------------------
AAPLE_SARKAR_SERVICES = [
    {"id": "ews_certificate", "title": "EWS Certificate", "department": "Revenue", "category": "certificate"},
    {"id": "income_certificate", "title": "Income Certificate", "department": "Revenue", "category": "certificate"},
    {"id": "caste_certificate", "title": "Caste Certificate", "department": "Revenue", "category": "certificate"},
    {"id": "domicile_certificate", "title": "Domicile / Residence Certificate", "department": "Revenue", "category": "certificate"},
    {"id": "non_creamy_layer", "title": "Non-Creamy Layer Certificate", "department": "Revenue", "category": "certificate"},
    {"id": "caste_validity", "title": "Caste Validity Certificate", "department": "Revenue", "category": "certificate"},
    {"id": "senior_citizen", "title": "Senior Citizen Certificate", "department": "Revenue", "category": "certificate"},
    {"id": "landless_certificate", "title": "Landless Certificate", "department": "Revenue", "category": "certificate"},
    {"id": "bpl_certificate", "title": "BPL / Destitute Certificate", "department": "Rural Development", "category": "certificate"},
    {"id": "birth_certificate", "title": "Birth Certificate", "department": "Rural Development", "category": "certificate"},
    {"id": "death_certificate", "title": "Death Certificate", "department": "Rural Development", "category": "certificate"},
    {"id": "marriage_registration", "title": "Marriage Registration Certificate", "department": "Registration & Stamps", "category": "certificate"},
    {"id": "police_clearance", "title": "Police Clearance Certificate", "department": "Home / Police", "category": "certificate"},
    {"id": "character_certificate", "title": "Character / Conduct Certificate", "department": "Revenue", "category": "certificate"},
    {"id": "shop_establishment", "title": "Shop & Establishment Registration", "department": "Labour", "category": "registration"},
    {"id": "factory_registration", "title": "Factory Registration", "department": "Labour", "category": "registration"},
    {"id": "driving_license", "title": "Driving License Services", "department": "Transport", "category": "license"},
    {"id": "vehicle_registration", "title": "Vehicle Registration", "department": "Transport", "category": "registration"},
    {"id": "tenant_farmer", "title": "Farmer / Small Marginal Farmer Certificate", "department": "Revenue", "category": "certificate"},
    {"id": "fire_safety", "title": "Fire Safety NOC", "department": "Home / Police", "category": "noc"},
]

# ---------------------------------------------------------------------------
# Known MahaDBT schemes (scheme id → metadata)
# Based on the 72+ schemes documented on the portal.
# ---------------------------------------------------------------------------
MAHADBT_SCHEMES = [
    {"id": "postmatric_sc_st", "title": "Post-Matric Scholarship (SC/ST/OBC)", "department": "Social Justice", "category": "scholarship", "income_limit": "Rs. 2.5 lakh (SC) / Rs. 1 lakh (OBC)", "benefit": "Tuition fee + maintenance allowance Rs. 350-1200/month for 10 months", "eligibility_summary": "SC/ST/OBC students in post-matric courses, domicile of Maharashtra"},
    {"id": "tuition_fee_freeship", "title": "Post-Matric Tuition Fee Freeship", "department": "Social Justice", "category": "scholarship", "income_limit": "Rs. 2.5 lakh", "benefit": "Full tuition fee waiver for professional courses", "eligibility_summary": "SC students in professional degree/diploma courses in Maharashtra"},
    {"id": "raje_shahu_sc", "title": "Rajarshi Chhatrapati Shahu Maharaj Merit Scholarship (SC)", "department": "Social Justice", "category": "scholarship", "income_limit": "Rs. 2.5 lakh", "benefit": "Rs. 5,000-10,000 per year", "eligibility_summary": "SC students in 11th-12th with 60%+ marks"},
    {"id": "pwd_scholarship", "title": "Post-Matric Scholarship for Persons with Disability", "department": "Social Justice", "category": "scholarship", "income_limit": "Rs. 2.5 lakh", "benefit": "Maintenance allowance + tuition fee", "eligibility_summary": "Disabled students (40%+ disability) in post-matric courses"},
    {"id": "postmatric_st", "title": "Post-Matric Scholarship for ST Students", "department": "Tribal Development", "category": "scholarship", "income_limit": "No limit", "benefit": "Full tuition fee + hostel + maintenance allowance", "eligibility_summary": "ST students domiciled in Maharashtra, any post-matric course"},
    {"id": "eklavya_st", "title": "Eklavya Scholarship (ST)", "department": "Tribal Development", "category": "scholarship", "income_limit": "No limit", "benefit": "Rs. 12,000-36,000 per year", "eligibility_summary": "Meritorious ST students from Class 11 to PG level"},
    {"id": "raje_shahu_fee_ebc", "title": "Rajarshi Chhatrapati Shahu Maharaj Fee Reimbursement (EBC)", "department": "Higher Education", "category": "scholarship", "income_limit": "Rs. 8 lakh", "benefit": "Up to 100% tuition fee reimbursement (max Rs. 1.15 lakh)", "eligibility_summary": "EBC students in graduate/PG courses in Maharashtra (non-professional)"},
    {"id": "open_merit", "title": "State Govt Open Merit Scholarship", "department": "Higher Education", "category": "scholarship", "income_limit": "Open", "benefit": "Rs. 3,000-10,000 per year", "eligibility_summary": "Open category students with 60%+ marks in higher education"},
    {"id": "meritorious_junior", "title": "Assistance to Meritorious Students Scholarship (Junior)", "department": "Higher Education", "category": "scholarship", "income_limit": "Open", "benefit": "Rs. 3,000 per year", "eligibility_summary": "Meritorious students in 11th-12th with 70%+ marks"},
    {"id": "meritorious_senior", "title": "Assistance to Meritorious Students Scholarship (Senior)", "department": "Higher Education", "category": "scholarship", "income_limit": "Open", "benefit": "Rs. 5,000 per year", "eligibility_summary": "Meritorious undergraduate students with 70%+ marks"},
    {"id": "dpd_hostel_ebc", "title": "Dr. Panjabrao Deshmukh Hostel Allowance (EBC)", "department": "Higher Education", "category": "scholarship", "income_limit": "Rs. 8 lakh", "benefit": "Up to Rs. 32,000/year hostel maintenance", "eligibility_summary": "EBC students in hostels, graduate/PG courses"},
    {"id": "raje_shahu_fee_dte", "title": "Rajarshi Chhatrapati Shahu Maharaj Fee Reimbursement (DTE)", "department": "Technical Education", "category": "scholarship", "income_limit": "Rs. 8 lakh", "benefit": "100% tuition fee reimbursement for CAP admitted students", "eligibility_summary": "EBC students in diploma/degree/PG technical courses via CAP"},
    {"id": "dpd_hostel_dte", "title": "Dr. Panjabrao Deshmukh Hostel Allowance (DTE)", "department": "Technical Education", "category": "scholarship", "income_limit": "Rs. 8 lakh", "benefit": "Rs. 32,000 for 10 months", "eligibility_summary": "EBC/SEBC students in technical education hostels"},
    {"id": "postmatric_vjnt", "title": "Post-Matric Scholarship (VJNT)", "department": "VJNT/OBC/SBC Welfare", "category": "scholarship", "income_limit": "Rs. 1 lakh", "benefit": "Maintenance Rs. 250-750/month + tuition fee", "eligibility_summary": "VJNT students in post-matric courses, Maharashtra domicile"},
    {"id": "postmatric_obc", "title": "Post-Matric Scholarship (OBC)", "department": "VJNT/OBC/SBC Welfare", "category": "scholarship", "income_limit": "Rs. 1 lakh", "benefit": "Maintenance Rs. 425/month + tuition fee reimbursement", "eligibility_summary": "OBC students in post-matric courses, non-creamy layer"},
    {"id": "postmatric_sbc", "title": "Post-Matric Scholarship (SBC)", "department": "VJNT/OBC/SBC Welfare", "category": "scholarship", "income_limit": "Rs. 1 lakh", "benefit": "Maintenance Rs. 250-750/month + tuition fee", "eligibility_summary": "SBC students in post-matric courses, Maharashtra domicile"},
    {"id": "raje_shahu_fee_medical", "title": "Rajarshi Chhatrapati Shahu Maharaj Fee Reimbursement (Medical)", "department": "Medical Education", "category": "scholarship", "income_limit": "Rs. 8 lakh", "benefit": "50% fee reimbursement for EWS in medical courses", "eligibility_summary": "EWS students in MBBS/BDS/BAMS/BHMS courses through CAP"},
    {"id": "minority_scholarship", "title": "State Minority Scholarship", "department": "Minority Development", "category": "scholarship", "income_limit": "Rs. 1 lakh", "benefit": "Up to Rs. 5,000/year", "eligibility_summary": "Minority community (Muslim/Christian/Sikh/Buddhist/Jain/Parsi) graduate/PG students"},
    {"id": "minority_tech", "title": "Minority Scholarship for Technical Courses", "department": "Minority Development", "category": "scholarship", "income_limit": "Rs. 8 lakh", "benefit": "Up to Rs. 25,000/year for diploma/degree; Rs. 50,000/year for medical", "eligibility_summary": "Minority students in technical/medical courses in Maharashtra"},
    {"id": "prematric_sc_st", "title": "Pre-Matric Scholarship (SC/ST/OBC/Minority)", "department": "Social Justice", "category": "scholarship", "income_limit": "Rs. 2 lakh (SC/ST) / Rs. 1 lakh (OBC/Minority)", "benefit": "Rs. 100-600/month for 10 months", "eligibility_summary": "SC/ST/OBC/Minority students in Class 1-10, Maharashtra domicile"},
    {"id": "nmms_scholarship", "title": "National Means-cum-Merit Scholarship (NMMS)", "department": "School Education", "category": "scholarship", "income_limit": "Rs. 3.5 lakh", "benefit": "Rs. 12,000/year for Class 8-12", "eligibility_summary": "Class 8 students in govt/aided schools, 55%+ in Class 7"},
    {"id": "vocational_fee", "title": "Vocational Training Fee Reimbursement", "department": "Skill Development", "category": "scholarship", "income_limit": "Rs. 8 lakh", "benefit": "Fee reimbursement for vocational/skill courses", "eligibility_summary": "SEBC and Open category (EWS) students in vocational training"},
    {"id": "shahu_merit_vjnt", "title": "Rajarshi Chhatrapati Shahu Maharaj Merit (VJNT/SBC)", "department": "VJNT/OBC/SBC Welfare", "category": "scholarship", "income_limit": "Rs. 1 lakh", "benefit": "Rs. 5,000-10,000 per year", "eligibility_summary": "VJNT/SBC students in 11th-12th with 60%+ marks"},
    {"id": "mahadbt_registration", "title": "MahaDBT Portal Registration (Common)", "department": "General", "category": "scholarship", "income_limit": "Varies by scheme", "benefit": "Portal access for all scholarship applications", "eligibility_summary": "Maharashtra domicile, Aadhaar-linked bank account"},
    {"id": "dpd_scholarship", "title": "Dr. Panjabrao Deshmukh OBC Scholarship", "department": "VJNT/OBC/SBC Welfare", "category": "scholarship", "income_limit": "Rs. 8 lakh", "benefit": "Tuition fee + hostel maintenance Rs. 32,000/year", "eligibility_summary": "OBC students in post-matric courses with non-creamy layer certificate"},
]


def _fetch_json(url: str, params: dict = None, timeout: int = 30):
    """Fetch JSON from a URL with error handling."""
    try:
        r = SESSION.get(url, params=params, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"  [FETCH] Failed: {url} - {e}")
        return None


def _fetch_text(url: str, timeout: int = 30):
    """Fetch HTML/text from a URL."""
    try:
        r = SESSION.get(url, timeout=timeout)
        r.raise_for_status()
        return r.text
    except Exception as e:
        print(f"  [FETCH] Failed: {url} - {e}")
        return None


def _cache_path(subdir: Path, service_id: str, ext: str = "json") -> Path:
    """Return a deterministic cache file path for a service/scheme."""
    return subdir / f"{service_id}.{ext}"


def _read_cache(path: Path):
    """Read cached data if it exists and is recent (< 7 days old)."""
    if not path.exists():
        return None
    age = time.time() - path.stat().st_mtime
    if age > 7 * 86400:
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _write_cache(path: Path, data):
    """Write data to cache."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"  [CACHE] Wrote {path}")


# ---------------------------------------------------------------------------
# MahaDBT Scraper
# ---------------------------------------------------------------------------
MAHADBT_BASE = "https://mahadbt.maharashtra.gov.in"
SCHEME_API = f"{MAHADBT_BASE}/SchemeData/SchemeData"


def fetch_mahadbt_scheme_list():
    """Fetch all schemes from MahaDBT API, return list of scheme dicts."""
    print("[MahaDBT] Fetching scheme list...")
    data = _fetch_json(SCHEME_API, {"FinanceYear": datetime.now().year})
    if not data:
        print("[MahaDBT] API returned nothing, trying generic call...")
        data = _fetch_json(SCHEME_API)
    if not data:
        print("[MahaDBT] API unavailable. Using known scheme list.")
        return None
    print(f"[MahaDBT] Got {len(data) if isinstance(data, list) else 'non-list'} results")
    return data


def scrape_mahadbt_scheme(scheme: dict):
    """Fetch details for a single scheme from cached metadata or API."""
    sid = scheme["id"]
    cache = _cache_path(SCHEMES_DIR, sid)
    cached = _read_cache(cache)
    if cached:
        return cached

    print(f"[MahaDBT] Fetching details for '{scheme['title'] or sid}'...")
    result = {
        "id": sid,
        "title": scheme.get("title", sid),
        "department": scheme.get("department", ""),
        "category": scheme.get("category", "scholarship"),
        "raw_text": "",
        "source": "mahadbt",
        "scraped_at": datetime.now().isoformat(),
    }

    for base_url in (MAHADBT_BASE, "https://mahadbt2.maharashtra.gov.in"):
        for path in (f"/SchemeDetails/{sid}", f"/Home/ShowScheme?schemeid={sid}"):
            url = f"{base_url}{path}"
            html = _fetch_text(url)
            if html:
                text = trafilatura.extract(html, include_formatting=True, include_links=True)
                result["raw_text"] = text or ""
                break
        if result["raw_text"]:
            break

    if not result["raw_text"]:
        dept = scheme.get("department", "")
        inc = scheme.get("income_limit", "")
        benefit = scheme.get("benefit", "")
        elig = scheme.get("eligibility_summary", "")
        name = scheme.get("title", sid)
        if elig:
            result["raw_text"] = (
                f"Scheme: {name}. Department: {dept}. Eligibility: {elig}. "
                f"Income Limit: {inc}. Benefits: {benefit}. "
                f"This scholarship is available on MahaDBT portal (mahadbt.maharashtra.gov.in). "
                f"Applicants need Maharashtra domicile, Aadhaar card, income certificate, "
                f"caste certificate (if applicable), previous mark sheets, bank account details, "
                f"and passport-size photographs. Apply online at mahadbt.maharashtra.gov.in during the "
                f"application period (typically August-October each year)."
            )
        else:
            result["raw_text"] = f"Scheme: {name}. Department: {dept}. This is a Maharashtra scholarship available on MahaDBT portal (mahadbt.maharashtra.gov.in)."

    _write_cache(cache, result)
    time.sleep(1)
    return result


def scrape_all_mahadbt(force: bool = False):
    """Scrape all MahaDBT schemes."""
    print(f"\n{'='*60}")
    print("  MahaDBT Scheme Scraper")
    print(f"{'='*60}")

    api_data = fetch_mahadbt_scheme_list()
    if api_data and isinstance(api_data, list):
        schemes_to_scrape = []
        for item in api_data:
            sid = str(item.get("SchemeId", item.get("id", "")))
            if sid:
                schemes_to_scrape.append({"id": sid, "title": item.get("SchemeName", item.get("title", ""))})
    else:
        schemes_to_scrape = MAHADBT_SCHEMES

    results = []
    for scheme in schemes_to_scrape:
        cache = _cache_path(SCHEMES_DIR, scheme["id"])
        if cache.exists() and not force:
            print(f"  [SKIP] {scheme['id']} (cached)")
            results.append(json.loads(cache.read_text(encoding="utf-8")))
            continue
        if force and cache.exists():
            print(f"  [FORCE] Re-fetching {scheme['id']}...")
            cache.unlink()
        data = scrape_mahadbt_scheme(scheme)
        if data:
            results.append(data)
    print(f"[MahaDBT] Done. {len(results)} schemes fetched.")
    return results


# ---------------------------------------------------------------------------
# Aaple Sarkar Scraper
# ---------------------------------------------------------------------------
AAPLE_BASE = "https://aaplesarkar.mahaonline.gov.in"


def scrape_aaple_service(service: dict):
    """Scrape a single Aaple Sarkar service page."""
    sid = service["id"]
    cache = _cache_path(SERVICES_DIR, sid)
    cached = _read_cache(cache)
    if cached:
        return cached

    print(f"[Aaple] Fetching '{service['title']}'...")
    url = f"{AAPLE_BASE}/MRTPSService/{sid}"
    result = {
        "id": sid,
        "title": service["title"],
        "department": service["department"],
        "category": service["category"],
        "raw_text": "",
        "source": "aaplesarkar",
        "scraped_at": datetime.now().isoformat(),
    }

    html = _fetch_text(url)
    if html:
        if sid in ("driving_license", "vehicle_registration"):
            try:
                extracted = trafilatura.extract(html, include_formatting=True, include_links=True, include_comments=False)
                result["raw_text"] = extracted or ""
            except Exception:
                result["raw_text"] = trafilatura.extract(html, include_formatting=True, include_links=True) or ""
        else:
            try:
                extracted = trafilatura.extract(html, include_formatting=True, include_links=True)
                result["raw_text"] = extracted or ""
            except Exception:
                result["raw_text"] = ""

    if not result["raw_text"]:
        result["raw_text"] = f"Service: {service['title']}. Department: {service['department']}. Category: {service['category']}."

    _write_cache(cache, result)
    time.sleep(0.5)
    return result


def scrape_all_aaple(force: bool = False):
    """Scrape all Aaple Sarkar services."""
    print(f"\n{'='*60}")
    print("  Aaple Sarkar Service Scraper")
    print(f"{'='*60}")

    results = []
    for service in AAPLE_SARKAR_SERVICES:
        cache = _cache_path(SERVICES_DIR, service["id"])
        if cache.exists() and not force:
            print(f"  [SKIP] {service['id']} (cached)")
            results.append(json.loads(cache.read_text(encoding="utf-8")))
            continue
        data = scrape_aaple_service(service)
        if data:
            results.append(data)
    print(f"[Aaple] Done. {len(results)} services fetched.")
    return results


# ---------------------------------------------------------------------------
# Combined Runner
# ---------------------------------------------------------------------------
def run_all(force: bool = False):
    """Run both scrapers and return summary."""
    total = {"mahadbt": 0, "aaplesarkar": 0}
    total["mahadbt"] = len(scrape_all_mahadbt(force=force))
    total["aaplesarkar"] = len(scrape_all_aaple(force=force))
    return total


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Maharashtra Scraper")
    parser.add_argument("--all", action="store_true", help="Scrape all sources")
    parser.add_argument("--mahadbt-only", action="store_true", help="Scrape only MahaDBT")
    parser.add_argument("--aaple-only", action="store_true", help="Scrape only Aaple Sarkar")
    parser.add_argument("--force", action="store_true", help="Re-scrape even if cached")
    args = parser.parse_args()

    print(f"=== Maharashtra Scraper ===")
    print(f"Date: {datetime.now().isoformat()}")
    print(f"Cache dir: {RAW_DIR}")

    if args.mahadbt_only:
        scrape_all_mahadbt(force=args.force)
    elif args.aaple_only:
        scrape_all_aaple(force=args.force)
    else:
        run_all(force=args.force)

    print(f"\nDone. Raw data in {RAW_DIR}")
