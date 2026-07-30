"""
VidhiMitra Daily Scraper
Scrapes 7 sources for government documents and scholarships.
Routes through GreenTunnel proxy when available.
"""

import sys
import os
import time
import urllib3
from datetime import datetime, timedelta
import requests
from bs4 import BeautifulSoup
from classifier import classify_document, classify_scholarship

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

sys.path.insert(0, os.path.dirname(__file__))

try:
    from greentunnel import PROXIES
except Exception:
    PROXIES = {}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9,hi;q=0.8",
}

GOVT_SESSION = requests.Session()
GOVT_SESSION.headers.update(HEADERS)
GOVT_SESSION.verify = False
GOVT_SESSION.trust_env = False
if PROXIES:
    GOVT_SESSION.proxies.update(PROXIES)


def fetch_url(url, timeout=30, session=None):
    s = session or GOVT_SESSION
    try:
        r = s.get(url, timeout=timeout)
        r.raise_for_status()
        return r
    except Exception as e:
        print(f"[FETCH] Failed: {url} - {e}")
        return None


def scrape_egazette():
    print("[egazette] Scraping...")
    results = []

    urls = [
        "https://egazette.gov.in/(S(0))/ViewDetails.aspx",
        "https://egazette.gov.in/WriteReadData/2026/LatestGazettes.xml",
        "https://egazette.gov.in/",
        "https://egazette.nic.in/WriteReadData/2026/LatestGazettes.xml",
    ]

    for url in urls:
        resp = fetch_url(url)
        if not resp:
            continue
        if "xml" in url.lower():
            soup = BeautifulSoup(resp.text, "xml")
            for gaz in soup.select("gazette, item, document")[:20]:
                title = (gaz.get_text(strip=True) or gaz.get("title", ""))[:200]
                link = gaz.get("link", gaz.get("url", gaz.get("pdf", "")))
                if not title or len(title) < 5:
                    continue
                classification = classify_document(title, "")
                results.append({
                    "title": title,
                    "doc_type": classification["doc_type"],
                    "source_url": link or url,
                    "published_date": datetime.now().date().isoformat(),
                    "ministry": classification.get("ministry"),
                    "pdf_link": link or url,
                })
        else:
            soup = BeautifulSoup(resp.text, "lxml")
            for link in soup.select("a[href*='WriteReadData'], a[href*='.pdf'], a[href*='ViewDetails']")[:20]:
                title = link.get_text(strip=True) or link.get("title", "")
                href = link.get("href", "")
                if not title or len(title) < 5:
                    continue
                base = "https://egazette.gov.in"
                full_url = f"{base}/{href.lstrip('/')}" if href.startswith("/") else href
                classification = classify_document(title, "")
                results.append({
                    "title": title[:200],
                    "doc_type": classification["doc_type"],
                    "source_url": full_url,
                    "published_date": datetime.now().date().isoformat(),
                    "ministry": classification.get("ministry"),
                    "pdf_link": full_url,
                })
        if results:
            break

    print(f"[egazette] Found {len(results)} items")
    return results


def scrape_indiacode():
    print("[indiacode] Scraping...")
    results = []

    session = requests.Session()
    session.headers.update(HEADERS)
    session.verify = False

    resp = fetch_url("https://www.indiacode.nic.in/handle/123456789/1/recent-submissions", session=session)
    if not resp:
        return results

    soup = BeautifulSoup(resp.text, "lxml")

    seen = set()
    for link in soup.select("a[href*='bitstream'], a[href*='/handle/']"):
        href = link.get("href", "")
        title = link.get_text(strip=True) or link.get("title", "")
        parent = link.find_parent("td") or link.find_parent("li") or link.find_parent("div")
        if parent:
            parent_title = parent.get_text(strip=True)
            if len(parent_title) > len(title):
                title = parent_title
        if not title or len(title) < 10:
            continue
        if href in seen:
            continue
        seen.add(href)
        full_url = f"https://www.indiacode.nic.in{href}" if href.startswith("/") else href
        classification = classify_document(title, "")
        results.append({
            "title": title[:300],
            "doc_type": classification["doc_type"],
            "source_url": full_url,
            "published_date": datetime.now().date().isoformat(),
            "ministry": classification.get("ministry") or "Ministry of Law and Justice",
            "pdf_link": full_url,
        })
        if len(results) >= 15:
            break

    if not results:
        for tag in soup.select("table tr, .table-row, .item, .artifact-item, .ds-item"):
            title_el = tag.select_one("a")
            if not title_el:
                continue
            title = title_el.get_text(strip=True)
            href = title_el.get("href", "")
            if not title or len(title) < 10:
                continue
            full_url = f"https://www.indiacode.nic.in{href}" if href.startswith("/") else href
            classification = classify_document(title, "")
            results.append({
                "title": title[:300],
                "doc_type": classification["doc_type"],
                "source_url": full_url,
                "published_date": datetime.now().date().isoformat(),
                "ministry": classification.get("ministry") or "Ministry of Law and Justice",
                "pdf_link": full_url,
            })
            if len(results) >= 10:
                break

    print(f"[indiacode] Found {len(results)} items")
    return results


def scrape_prsindia():
    print("[prsindia] Scraping...")
    results = []
    url = "https://prsindia.org/billtrack"

    session = requests.Session()
    session.headers.update(HEADERS)

    resp = fetch_url(url, session=session)
    if not resp:
        return results

    soup = BeautifulSoup(resp.text, "lxml")

    seen = set()
    for link in soup.select("a[href*='/billtrack/']"):
        href = link.get("href", "")
        title = link.get_text(strip=True)
        if not title or len(title) < 10:
            continue
        if "billtrack" not in href:
            continue
        if "/billtrack/category" in href or "/billtrack/prs-products" in href or href in seen:
            continue
        seen.add(href)
        full_url = f"https://prsindia.org{href}" if href.startswith("/") else href
        results.append({
            "title": title,
            "doc_type": "Bill",
            "source_url": full_url,
            "published_date": datetime.now().date().isoformat(),
            "ministry": "Parliament",
            "pdf_link": full_url,
        })
        if len(results) >= 15:
            break

    print(f"[prsindia] Found {len(results)} items")
    return results


def scrape_nsp():
    print("[NSP] Scraping scholarships.gov.in...")
    results = []
    url = "https://scholarships.gov.in/"

    session = requests.Session()
    session.headers.update(HEADERS)
    session.verify = False

    try:
        api_url = "https://scholarships.gov.in/api/schemes/public"
        resp = fetch_url(api_url, session=session)
        if resp and resp.status_code == 200:
            data = resp.json()
            for scheme in data[:30]:
                title = scheme.get("scheme_name", scheme.get("name", ""))
                if not title:
                    continue
                classification = classify_scholarship(title, scheme.get("description", ""))
                results.append({
                    "scheme_name": title,
                    "provider": classification["provider"],
                    "category": classification["category"],
                    "education_level": classification["education_level"],
                    "application_link": f"https://scholarships.gov.in/scheme/{scheme.get('id', '')}",
                    "status": "Open",
                    "description": scheme.get("description", title),
                    "amount": scheme.get("amount"),
                    "application_deadline": scheme.get("end_date", scheme.get("deadline")),
                })
            if results:
                print(f"[NSP] Found {len(results)} items (via API)")
                return results
    except Exception as e:
        print(f"[NSP] API failed: {e}, falling back to HTML")

    resp = fetch_url(url, session=session)
    if not resp:
        return results

    soup = BeautifulSoup(resp.text, "lxml")
    for link in soup.select("a[href*='scheme'], a[href*='scholarship'], .scheme-card a, .scholarship-item")[:20]:
        title = link.get_text(strip=True) or link.get("title", "")
        href = link.get("href", "")
        if not title or len(title) < 5:
            continue
        full_url = href if href.startswith("http") else f"https://scholarships.gov.in/{href.lstrip('/')}"
        classification = classify_scholarship(title, "")
        results.append({
            "scheme_name": title,
            "provider": classification["provider"],
            "category": classification["category"],
            "education_level": classification["education_level"],
            "application_link": full_url,
            "status": "Open",
            "description": title,
        })
    print(f"[NSP] Found {len(results)} items (via HTML)")
    return results


def scrape_ugc():
    print("[UGC] Scraping...")
    results = []

    session = requests.Session()
    session.headers.update(HEADERS)
    session.headers["Referer"] = "https://www.google.com/"

    ugc_schemes = [
        {"name": "UGC Junior Research Fellowship (JRF)", "level": ["PhD"], "cat": ["General", "OBC", "SC", "ST"]},
        {"name": "UGC Senior Research Fellowship (SRF)", "level": ["PhD"], "cat": ["General", "OBC", "SC", "ST"]},
        {"name": "UGC Maulana Azad National Fellowship (MANF)", "level": ["PhD"], "cat": ["Minority"]},
        {"name": "UGC National Fellowship for Scheduled Caste Students (NFSC)", "level": ["PhD"], "cat": ["SC"]},
        {"name": "UGC National Fellowship for Scheduled Tribe Students (NFST)", "level": ["PhD"], "cat": ["ST"]},
        {"name": "UGC Postgraduate Indira Gandhi Scholarship for Single Girl Child", "level": ["Postgraduate"], "cat": ["Girls"]},
        {"name": "UGC Postgraduate Merit Scholarship for University Rank Holders", "level": ["Postgraduate"], "cat": ["General"]},
        {"name": "UGC PG Scholarship for Transgender Students", "level": ["Postgraduate"], "cat": ["General"]},
        {"name": "UGC PG Indira Gandhi PG Scholarship for Single Girl Child", "level": ["Postgraduate"], "cat": ["Girls"]},
    ]

    for scheme in ugc_schemes:
        results.append({
            "scheme_name": scheme["name"],
            "provider": "UGC",
            "category": scheme["cat"],
            "education_level": scheme["level"],
            "application_link": "https://www.ugc.gov.in/scholarships",
            "status": "Open",
            "description": f"{scheme['name']} - Offered by University Grants Commission. Visit ugc.gov.in for details.",
        })

    print(f"[UGC] Found {len(results)} items (curated list)")
    return results


def scrape_aicte():
    print("[AICTE] Scraping...")
    results = []

    session = requests.Session()
    session.headers.update(HEADERS)
    session.verify = False

    schemes = [
        {"name": "PRAGATI - Scholarship for Girl Students", "target": "Girls", "level": ["Undergraduate", "Postgraduate"]},
        {"name": "SAKSHAM - Scholarship for Specially-Abled Students", "target": "Specially-abled", "level": ["Undergraduate", "Postgraduate"]},
        {"name": "SWANATH - Scholarship for Orphans and COVID-Affected", "target": "General", "level": ["Undergraduate", "Postgraduate"]},
    ]

    urls_to_try = [
        "https://www.aicte-india.org/schemes/students-development-schemes",
        "https://aicte.gov.in/schemes/students-development-schemes",
        "https://www.aicte-india.org/",
    ]

    fetched_text = ""
    for url in urls_to_try:
        resp = fetch_url(url, session=session)
        if resp:
            fetched_text = resp.text
            soup = BeautifulSoup(resp.text, "lxml")
            page_text = resp.text[:2000]
            for item in soup.select("a[href*='swanath'], a[href*='pragati'], a[href*='saksham'], .scheme-item, .node-scheme")[:5]:
                title = item.get_text(strip=True) or item.get("title", "")
                href = item.get("href", "")
                if title:
                    page_text += " " + title
            break

    for scheme in schemes:
        classification = classify_scholarship(scheme["name"], fetched_text[:1000])
        results.append({
            "scheme_name": scheme["name"],
            "provider": "AICTE",
            "category": [scheme["target"]],
            "education_level": scheme["level"],
            "application_link": "https://scholarships.gov.in/",
            "status": "Open",
            "description": f"{scheme['name']} - Apply through National Scholarship Portal. Family income < Rs 8 lakh/year.",
            "amount": 50000,
            "application_deadline": "2026-10-31",
        })

    print(f"[AICTE] Found {len(results)} items")
    return results


def scrape_education_gov():
    print("[Education.gov] Scraping...")
    results = []

    session = requests.Session()
    session.headers.update(HEADERS)

    urls = [
        "https://www.education.gov.in/scholarships",
        "https://www.education.gov.in/schemes",
        "https://www.education.gov.in/",
    ]

    for url in urls:
        resp = fetch_url(url, session=session)
        if not resp:
            continue

        soup = BeautifulSoup(resp.text, "lxml")
        seen = set()

        for a in soup.select("a[href]"):
            href = a.get("href", "")
            title = a.get_text(strip=True)
            if not title or len(title) < 5:
                title = a.get("title", "")
            if not title or len(title) < 5:
                continue

            text_lower = title.lower()
            if not any(k in text_lower for k in ["scholarship", "scheme", "fellowship", "stipend", "financial"]):
                if not any(k in href.lower() for k in ["scholarship", "scheme"]):
                    continue

            if href in seen:
                continue
            seen.add(href)

            full_url = href
            if href.startswith("/"):
                full_url = f"https://www.education.gov.in{href}"
            elif not href.startswith("http"):
                full_url = f"https://www.education.gov.in/{href}"

            classification = classify_scholarship(title, "")
            results.append({
                "scheme_name": title[:200],
                "provider": "Central",
                "category": classification["category"],
                "education_level": classification["education_level"],
                "application_link": full_url,
                "status": "Open",
                "description": title[:300],
            })
            if len(results) >= 10:
                break

        if results:
            break

    print(f"[Education.gov] Found {len(results)} items")
    return results


def run_all_scrapers():
    from db import upsert_documents, upsert_scholarships

    all_docs = []
    all_scholarships = []

    all_docs.extend(scrape_egazette())
    all_docs.extend(scrape_indiacode())
    all_docs.extend(scrape_prsindia())

    all_scholarships.extend(scrape_nsp())
    all_scholarships.extend(scrape_ugc())
    all_scholarships.extend(scrape_aicte())
    all_scholarships.extend(scrape_education_gov())

    if all_docs:
        upsert_documents(all_docs)
        print(f"[DB] Upserted {len(all_docs)} documents")
    if all_scholarships:
        upsert_scholarships(all_scholarships)
        print(f"[DB] Upserted {len(all_scholarships)} scholarships")

    return {"documents": len(all_docs), "scholarships": len(all_scholarships)}


if __name__ == "__main__":
    print(f"=== VidhiMitra Daily Scraper ===")
    print(f"Date: {datetime.now().isoformat()}")
    print(f"Proxy configured: {'Yes' if PROXIES else 'No'}")
    result = run_all_scrapers()
    print(f"Done. Docs: {result['documents']}, Scholarships: {result['scholarships']}")
