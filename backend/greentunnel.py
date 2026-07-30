import subprocess
import time
import sys
import httpx
from config import GREEN_TUNNEL_ENABLED, GREEN_TUNNEL_PORT

GT_HOST = "127.0.0.1"
GT_URL = f"http://{GT_HOST}:{GREEN_TUNNEL_PORT}"
PROXIES = {
    "http://": GT_URL,
    "https://": GT_URL,
}

_process = None


def start_greentunnel():
    if not GREEN_TUNNEL_ENABLED:
        print("[GT] GreenTunnel disabled via env var")
        return PROXIES if GREEN_TUNNEL_ENABLED else {}

    global _process
    try:
        _process = subprocess.Popen(
            ["npx", "green-tunnel", "--port", str(GREEN_TUNNEL_PORT), "--no-system-proxy", "--silent"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print(f"[GT] Starting GreenTunnel on {GT_URL}...")

        for i in range(30):
            try:
                r = httpx.get(f"{GT_URL}/_health", timeout=2)
                if r.status_code < 500:
                    print(f"[GT] Ready after {i+1}s")
                    return PROXIES
            except Exception:
                pass
            time.sleep(1)

        print("[GT] WARNING: GreenTunnel did not respond in time, continuing without proxy")
        return {}
    except FileNotFoundError:
        print("[GT] WARNING: npx/green-tunnel not found, continuing without proxy")
        return {}
    except Exception as e:
        print(f"[GT] WARNING: Failed to start GreenTunnel: {e}")
        return {}


def stop_greentunnel():
    global _process
    if _process:
        _process.terminate()
        try:
            _process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _process.kill()
        _process = None
        print("[GT] Stopped")


def is_running():
    if not _process:
        return False
    return _process.poll() is None
