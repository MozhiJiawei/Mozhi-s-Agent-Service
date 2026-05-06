from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import argparse
import json
from datetime import datetime, timezone


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            if self.path != "/health":
                self.send_response(404)
                self.end_headers()
                return

            body = {
                "service": "mozhi-agent-service-mock-desktop",
                "status": "ok",
                "server_time": datetime.now(timezone.utc).isoformat(),
            }
            payload = json.dumps(body).encode("utf-8")

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        except Exception as exc:
            print(f"handler error: {exc!r}", flush=True)
            raise

    def log_message(self, fmt, *args):
        print("%s - %s" % (self.address_string(), fmt % args), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18082)
    args = parser.parse_args()

    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()
