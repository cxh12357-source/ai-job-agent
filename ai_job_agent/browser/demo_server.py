from __future__ import annotations

import threading
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


DEMO_HTML_PATH = Path(__file__).with_name("demo_form.html")
DEMO_JOB_HTML_PATH = Path(__file__).with_name("demo_job.html")

CAPTCHA_HTML = b"""<!doctype html><html><body>
<h1>Verify you are human</h1><iframe src='/recaptcha/mock'></iframe>
</body></html>"""
LOGIN_HTML = b"""<!doctype html><html><body>
<h1>Sign in to apply</h1><label>Password <input type='password' name='password'></label>
</body></html>"""


class _DemoHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        path = self.path.split("?", 1)[0]
        if path == "/health":
            payload = b"ok"
            content_type = "text/plain; charset=utf-8"
            status = 200
        elif path in {"/", "/demo/apply"}:
            payload = DEMO_HTML_PATH.read_bytes()
            content_type = "text/html; charset=utf-8"
            status = 200
        elif path == "/demo/job":
            payload = DEMO_JOB_HTML_PATH.read_bytes()
            content_type = "text/html; charset=utf-8"
            status = 200
        elif path == "/demo/captcha":
            payload = CAPTCHA_HTML
            content_type = "text/html; charset=utf-8"
            status = 200
        elif path == "/demo/login":
            payload = LOGIN_HTML
            content_type = "text/html; charset=utf-8"
            status = 200
        elif path == "/recaptcha/mock":
            payload = b"<!doctype html><html><body>captcha mock</body></html>"
            content_type = "text/html; charset=utf-8"
            status = 200
        else:
            payload = b"not found"
            content_type = "text/plain; charset=utf-8"
            status = 404
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, _format: str, *_args: object) -> None:
        # Request paths can contain tokens on real systems.  The demo server does
        # not print them or copy them into test output.
        return


class DemoServer:
    """A loopback-only HTTP server for Playwright integration tests."""

    def __init__(self, *, host: str = "127.0.0.1", port: int = 0) -> None:
        if host not in {"127.0.0.1", "localhost"}:
            raise ValueError("demo server must bind to loopback")
        self.host = host
        self.port = int(port)
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def apply_url(self) -> str:
        if self._server is None:
            raise RuntimeError("demo server is not running")
        port = self._server.server_address[1]
        return f"http://{self.host}:{port}/demo/apply"

    @property
    def job_url(self) -> str:
        if self._server is None:
            raise RuntimeError("demo server is not running")
        port = self._server.server_address[1]
        return f"http://{self.host}:{port}/demo/job"

    @property
    def captcha_url(self) -> str:
        if self._server is None:
            raise RuntimeError("demo server is not running")
        port = self._server.server_address[1]
        return f"http://{self.host}:{port}/demo/captcha"

    @property
    def login_url(self) -> str:
        if self._server is None:
            raise RuntimeError("demo server is not running")
        port = self._server.server_address[1]
        return f"http://{self.host}:{port}/demo/login"

    def start(self) -> "DemoServer":
        if self._server is not None:
            return self
        self._server = ThreadingHTTPServer((self.host, self.port), _DemoHandler)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="ai-job-agent-demo",
            daemon=True,
        )
        self._thread.start()
        return self

    def close(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def __enter__(self) -> "DemoServer":
        return self.start()

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        self.close()


__all__ = ["DEMO_HTML_PATH", "DEMO_JOB_HTML_PATH", "DemoServer"]


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the loopback-only application demo")
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    with DemoServer(port=args.port) as server:
        print(f"Local demo: {server.apply_url}")
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
