"""
Automated benchmark evaluator for VidhiMitra RAG pipeline.
Starts the server, runs 12 benchmark queries, scores each response,
and prints a Pass/Fail summary table.
"""
import json
import subprocess
import sys
import time
import urllib.request
import urllib.error

PORT = 8090
BASE = f"http://localhost:{PORT}"

BENCHMARKS = [
    {
        "id": 1,
        "query": "What is the maximum family income limit for EWS certificate?",
        "category": "eligibility",
        "expected_badges": ["guide"],
        "must_contain": ["8 lakh", "Rs. 8"],
        "must_not_contain": ["only have access", "cannot assist"],
    },
    {
        "id": 2,
        "query": "Am I eligible for DPD scholarship if my family income is 9 lakh?",
        "category": "eligibility",
        "expected_badges": ["guide"],
        "must_contain": ["8 lakh", "Rs. 8"],
        "must_not_contain": [],
    },
    {
        "id": 3,
        "query": "What documents do I need for EWS certificate?",
        "category": "documents",
        "expected_badges": ["guide"],
        "must_contain": ["Aadhaar", "income", "ration"],
        "must_not_contain": [],
    },
    {
        "id": 4,
        "query": "What are the required documents for DPD scholarship?",
        "category": "documents",
        "expected_badges": ["guide"],
        "must_contain": ["caste", "income", "domicile"],
        "must_not_contain": [],
    },
    {
        "id": 5,
        "query": "How do I apply for EWS certificate online?",
        "category": "steps",
        "expected_badges": ["guide"],
        "must_contain": ["aaplesarkar", "mahaonline", "register"],
        "must_not_contain": [],
    },
    {
        "id": 6,
        "query": "What is the step by step process for MahaDBT registration?",
        "category": "steps",
        "expected_badges": ["guide"],
        "must_contain": ["registration", "profile", "mahadbt"],
        "must_not_contain": [],
    },
    {
        "id": 7,
        "query": "How do I get a driving license in Maharashtra?",
        "category": "out-of-domain",
        "expected_badges": ["refusal"],
        "must_contain": ["only have access", "cannot assist"],
        "must_not_contain": [],
    },
    {
        "id": 8,
        "query": "Tell me about passport application process",
        "category": "out-of-domain",
        "expected_badges": ["refusal"],
        "must_contain": ["only have access", "cannot assist"],
        "must_not_contain": [],
    },
    {
        "id": 9,
        "query": "What is the income limit for EWS?",
        "category": "semantic",
        "expected_badges": ["guide"],
        "must_contain": ["8 lakh", "Rs. 8"],
        "must_not_contain": [],
    },
    {
        "id": 10,
        "query": "Do I need to use Aaple Sarkar to apply for MahaDBT scholarship?",
        "category": "cross-portal",
        "expected_badges": ["guide"],
        "must_contain": ["mahadbt", "MahaDBT"],
        "must_not_contain": ["only have access"],
    },
    {
        "id": 11,
        "query": "What is the validity of caste certificate?",
        "category": "validity",
        "expected_badges": ["guide"],
        "must_contain": ["lifetime", "valid", "life"],
        "must_not_contain": [],
    },
    {
        "id": 12,
        "query": "What are common problems with MahaDBT application?",
        "category": "common_problems",
        "expected_badges": ["guide"],
        "must_contain": ["problem"],
        "must_not_contain": [],
    },
]


def check_badge(reply: str, badge: str) -> bool:
    badges = {
        "guide": "Process Guide",
        "refusal": "only have access",
        "vector": "semantic search",
        "db": "Found in",
        "live": "Retrieved live",
    }
    return badges.get(badge, badge) in reply


def run_query(session_id: str, query: str) -> dict:
    data = json.dumps({"session_id": session_id, "message": query}).encode("utf-8")
    req = urllib.request.Request(
        BASE + "/chat",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    resp = urllib.request.urlopen(req, timeout=15)
    return json.loads(resp.read().decode("utf-8"))


def evaluate():
    print("VidhiMitra Benchmark Evaluator")
    print("=" * 65)
    print(f"{'ID':<4} {'Category':<16} {'Pass/Fail':<10} {'Badges':<28} Detail")
    print("-" * 65)

    passed = 0
    failed = 0
    results = []

    for bm in BENCHMARKS:
        q = bm["query"]
        session_id = f"eval_{bm['id']}"

        try:
            resp = run_query(session_id, q)
            reply = resp.get("reply", "")
        except Exception as e:
            results.append((bm, False, f"Request failed: {e}", ""))
            failed += 1
            continue

        # Check badges
        badges_found = []
        for badge in bm["expected_badges"]:
            if check_badge(reply, badge):
                badges_found.append(badge)

        badge_ok = len(badges_found) == len(bm["expected_badges"])

        # Check must_contain
        content_ok = True
        missing = []
        for keyword in bm["must_contain"]:
            if keyword.lower() not in reply.lower():
                content_ok = False
                missing.append(keyword)

        # Check must_not_contain
        forbidden_found = []
        for keyword in bm["must_not_contain"]:
            if keyword.lower() in reply.lower():
                forbidden_found.append(keyword)

        forbidden_ok = len(forbidden_found) == 0

        all_ok = badge_ok and content_ok and forbidden_ok

        if all_ok:
            passed += 1
            status = "PASS"
        else:
            failed += 1
            status = "FAIL"

        # Build detail string
        detail_parts = []
        if not badge_ok:
            detail_parts.append(f"missing badges: {bm['expected_badges']}")
        if not content_ok:
            detail_parts.append(f"missing keywords: {missing}")
        if not forbidden_ok:
            detail_parts.append(f"forbidden: {forbidden_found}")
        detail = "; ".join(detail_parts) if detail_parts else ""

        badge_str = ",".join(badges_found) if badges_found else "none"
        print(f"{bm['id']:<4} {bm['category']:<16} {status:<10} {badge_str:<28} {detail}")
        results.append((bm, all_ok, detail, reply[:150]))

    print("-" * 65)
    total = passed + failed
    accuracy = (passed / total) * 100 if total > 0 else 0
    print(f"Total: {total}  |  Passed: {passed}  |  Failed: {failed}  |  Accuracy: {accuracy:.0f}%")

    # Write detailed results to file
    with open("eval_report.json", "w", encoding="utf-8") as f:
        json.dump(
            {
                "summary": {"total": total, "passed": passed, "failed": failed, "accuracy": accuracy},
                "results": [
                    {
                        "id": r[0]["id"],
                        "query": r[0]["query"],
                        "category": r[0]["category"],
                        "pass": r[1],
                        "detail": r[2],
                        "reply_preview": r[3],
                    }
                    for r in results
                ],
            },
            f,
            indent=2,
            ensure_ascii=False,
        )
    print(f"\nDetailed report written to eval_report.json")

    return accuracy


if __name__ == "__main__":
    # Start server
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", str(PORT)],
        cwd=sys.path[0] if sys.path[0] else ".",
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    print("Starting server (pre-warming model)...")
    time.sleep(18)

    # Health check with retry
    import urllib.error
    for attempt in range(5):
        try:
            urllib.request.urlopen(BASE + "/", timeout=5)
            print("Server ready.")
            break
        except Exception:
            print(f"  Waiting for server (attempt {attempt+1}/5)...")
            time.sleep(5)
    else:
        print("Server failed to start.")
        proc.terminate()
        proc.wait()
        sys.exit(1)

    try:
        evaluate()
    finally:
        proc.terminate()
        proc.wait()
        print("Server stopped.")
