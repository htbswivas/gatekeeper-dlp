#!/usr/bin/env python3
"""
DLP demo server — serves employee.html / manager.html and records team-level stats.

Run:    python3 server.py        (Windows: python server.py)
Open:   http://localhost:8000/employee.html
        http://localhost:8000/manager.html
Both pages must be opened through the same address so they share browser storage.

Stats:  Every scan / block / send / approve / edit / return is appended to dlp_stats.json
        in this folder. The file survives restarts. manager.html reads it for the Weekly Report.

Stored per event: type, timestamp, flag categories, counts and the sender's display name
         (for the Weekly Report's per-person coaching table). Email addresses and email text
         are dropped on the server even if a client sends them.

Uses only the Python standard library.
"""
import json
import os
import socket
import threading
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("PORT", 8000))
HERE = os.path.dirname(os.path.abspath(__file__))
STATS_FILE = os.path.join(HERE, "dlp_stats.json")
MAX_EVENTS = 20000

ALLOWED_TYPES = {"scanned", "blocked", "sent", "approved", "edited", "returned"}
lock = threading.Lock()


# ---------------------------------------------------------------- storage
def load_stats():
    try:
        with open(STATS_FILE, encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get("events"), list):
                return data
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return {"version": 1, "events": []}


def save_stats(data):
    tmp = STATS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STATS_FILE)  # atomic, so a crash never leaves a half-written file


def clean_event(raw):
    """Keep only anonymous team-level fields."""
    if not isinstance(raw, dict) or raw.get("type") not in ALLOWED_TYPES:
        return None
    ts = raw.get("ts")
    try:
        ts = datetime.fromisoformat(str(ts).replace("Z", "+00:00")).astimezone(timezone.utc).isoformat()
    except ValueError:
        ts = datetime.now(timezone.utc).isoformat()
    evt = {"type": raw["type"], "ts": ts}
    sender = raw.get("sender")
    if isinstance(sender, str) and sender.strip() and "@" not in sender:
        evt["sender"] = sender.strip()[:60]
    for key in ("flagged", "highRisk", "demo"):
        if key in raw:
            evt[key] = bool(raw[key])
    if "unverified" in raw:
        try:
            evt["unverified"] = max(0, min(50, int(raw["unverified"])))
        except (TypeError, ValueError):
            pass
    pats = raw.get("patterns")
    if isinstance(pats, list):
        evt["patterns"] = [str(p)[:80] for p in pats[:10] if isinstance(p, str) and "@" not in p]
    return evt


# ---------------------------------------------------------------- HTTP
class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=HERE, **kwargs)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length > 2_000_000:
            raise ValueError("payload too large")
        return json.loads(self.rfile.read(length) or b"null")

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", ""):
            self.path = "/employee.html"
        elif path in ("/dlp_stats.json", "/api/stats"):
            with lock:
                return self._json(200, load_stats())
        return super().do_GET()

    def do_POST(self):
        path = self.path.split("?")[0]
        if path == "/api/event":
            try:
                payload = self._read_json()
            except (ValueError, json.JSONDecodeError):
                return self._json(400, {"error": "invalid JSON"})
            items = payload if isinstance(payload, list) else [payload]
            events = [e for e in (clean_event(x) for x in items) if e]
            if not events:
                return self._json(400, {"error": "no valid events"})
            with lock:
                data = load_stats()
                data["events"].extend(events)
                data["events"] = data["events"][-MAX_EVENTS:]
                save_stats(data)
            return self._json(200, {"ok": True, "added": len(events)})
        if path == "/api/stats/reset":
            with lock:
                save_stats({"version": 1, "events": []})
            return self._json(200, {"ok": True})
        return self._json(404, {"error": "unknown endpoint"})

    def log_message(self, fmt, *args):
        if self.command == "GET" and ("dlp_stats" in self.path or "/api/" in self.path):
            return  # hide polling noise
        super().log_message(fmt, *args)


def lan_ips():
    ips = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    return sorted(ip for ip in ips if not ip.startswith("127."))


if __name__ == "__main__":
    if not os.path.exists(STATS_FILE):
        save_stats({"version": 1, "events": []})
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print("\nDLP demo server running.\n")
    print(f"  Employee:  http://localhost:{PORT}/employee.html")
    print(f"  Manager:   http://localhost:{PORT}/manager.html")
    print(f"  Stats file: {STATS_FILE} ({len(load_stats()['events'])} events)")
    for ip in lan_ips():
        print(f"  (on your network: http://{ip}:{PORT}/ — note pending approvals use browser storage, so use one browser)")
    print("\nPress Ctrl+C to stop.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
