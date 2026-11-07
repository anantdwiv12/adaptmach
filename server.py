"""AdaptMach server. Stdlib only: `python3 server.py` then open http://localhost:8000"""
import json
import os
import threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

HERE_ = os.path.dirname(os.path.abspath(__file__))
_env = os.path.join(HERE_, ".env")
if os.path.exists(_env):  # simple KEY=VALUE loader, no dependencies
    for _line in open(_env):
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

import agent

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = agent.Engine()
ENGINE.load_latest_brain()
PORT = int(os.environ.get("PORT", "8000"))


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path in ("/", "/index.html"):
            b = open(os.path.join(HERE, "static", "index.html"), "rb").read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
        elif u.path == "/api/state":
            self._json(ENGINE.snapshot(int(q.get("since", ["0"])[0])))
        elif u.path == "/api/runs":
            d = os.path.join(HERE, "runs")
            runs = sorted(os.listdir(d), reverse=True) if os.path.isdir(d) else []
            self._json([r[:-5] for r in runs if r.endswith(".json")])
        elif u.path == "/api/run":
            rid = os.path.basename(q.get("id", [""])[0])
            p = os.path.join(HERE, "runs", rid + ".json")
            if os.path.exists(p):
                self._json(json.load(open(p)))
            else:
                self._json({"error": "not found"}, 404)
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        u = urlparse(self.path)
        body = self._body()
        if ENGINE.running and u.path != "/api/reset":
            return self._json({"error": "busy: a run is in progress"}, 409)
        if u.path == "/api/learn":
            ENGINE.reset()
            cycles = int(body.get("cycles", 4))
            threading.Thread(target=ENGINE.learn, args=(cycles,), daemon=True).start()
            self._json({"ok": True})
        elif u.path == "/api/adapt":
            bid, instr = body.get("brief_id"), (body.get("instruction") or "").strip()
            if bid not in ENGINE.solved or not instr:
                return self._json({"error": "pick a solved machine and type a change"}, 400)
            threading.Thread(target=ENGINE.adapt, args=(bid, instr), daemon=True).start()
            self._json({"ok": True})
        elif u.path == "/api/imagine":
            text = (body.get("text") or "").strip()
            bid = body.get("brief_id") or None
            from briefs import ALL
            if bid and bid not in ALL:
                return self._json({"error": f"unknown machine '{bid}'"}, 400)
            if not text and not bid:
                return self._json({"error": "describe a machine first"}, 400)
            threading.Thread(target=ENGINE.imagine, args=(text, body.get("planet") or None, bid),
                             daemon=True).start()
            self._json({"ok": True})
        elif u.path == "/api/race":
            threading.Thread(target=ENGINE.race,
                             kwargs={"brief_id": body.get("brief_id") or None,
                                     "text": (body.get("text") or "").strip() or None,
                                     "planet": body.get("planet") or None},
                             daemon=True).start()
            self._json({"ok": True})
        elif u.path == "/api/reset":
            ENGINE.reset()
            self._json({"ok": True})
        else:
            self._json({"error": "not found"}, 404)


if __name__ == "__main__":
    print(f"\n  AdaptMach running -> http://localhost:{PORT}")
    print(f"  Brain: {ENGINE.brain.label}\n")
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
