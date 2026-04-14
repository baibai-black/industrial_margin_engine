from __future__ import annotations

import argparse
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .runtime import MarginRuntime


class MarginRequestHandler(BaseHTTPRequestHandler):
    runtime: MarginRuntime | None = None

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        try:
            payload = self._handle_get(parsed)
            self._write_json(HTTPStatus.OK, payload)
        except KeyError as exc:
            self._write_json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
        except Exception as exc:  # pragma: no cover - demo server safety net
            self._write_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        body = self._read_json_body()
        try:
            payload = self._handle_post(parsed, body)
            self._write_json(HTTPStatus.OK, payload)
        except KeyError as exc:
            self._write_json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
        except Exception as exc:  # pragma: no cover - demo server safety net
            self._write_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

    def _handle_get(self, parsed) -> dict[str, object]:
        runtime = self._require_runtime()
        path = parsed.path

        if path == "/health":
            return runtime.health()
        if path == "/accounts":
            return runtime.list_accounts()
        if path == "/snapshots":
            return runtime.list_snapshots()
        if path == "/tasks":
            return runtime.list_tasks()
        if path == "/underlyings":
            return runtime.list_underlyings()
        if path.startswith("/accounts/") and path.endswith("/snapshot"):
            account_id = path.split("/")[2]
            return runtime.get_snapshot(account_id)
        if path.startswith("/accounts/") and path.endswith("/portfolio"):
            account_id = path.split("/")[2]
            return runtime.get_portfolio(account_id)
        if path.startswith("/underlyings/") and path.endswith("/matrix"):
            symbol = path.split("/")[2]
            family = parse_qs(parsed.query).get("family", ["TIMS"])[0]
            return runtime.get_matrix(symbol, family)
        raise KeyError("Unknown path: %s" % path)

    def _handle_post(self, parsed, body: dict[str, object]) -> dict[str, object]:
        runtime = self._require_runtime()
        path = parsed.path
        if path == "/demo/reset":
            return runtime.reset_demo_data()
        if path == "/events":
            return runtime.emit_event(body)
        if path == "/accounts/upsert":
            return runtime.upsert_account(body)
        if path == "/positions/replace":
            return runtime.replace_positions(body)
        if path == "/underlyings/upsert":
            return runtime.upsert_underlying(body)
        raise KeyError("Unknown path: %s" % path)

    def _read_json_body(self) -> dict[str, object]:
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length == 0:
            return {}
        raw = self.rfile.read(content_length).decode("utf-8")
        if not raw.strip():
            return {}
        return json.loads(raw)

    def _write_json(self, status: HTTPStatus, payload: dict[str, object]) -> None:
        encoded = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _require_runtime(self) -> MarginRuntime:
        if self.runtime is None:
            raise RuntimeError("Server runtime is not initialized.")
        return self.runtime

    def log_message(self, format: str, *args) -> None:
        # Keep console output compact for demo usage.
        return


def create_server(host: str, port: int, runtime: MarginRuntime | None = None) -> ThreadingHTTPServer:
    runtime = runtime or MarginRuntime()
    runtime.reset_demo_data()
    MarginRequestHandler.runtime = runtime
    return ThreadingHTTPServer((host, port), MarginRequestHandler)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Industrial Margin Engine demo server.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8010)
    args = parser.parse_args(argv)

    server = create_server(args.host, args.port)
    print("Industrial Margin Engine demo server running at http://%s:%s" % (args.host, args.port))
    print("Loaded demo data. Try GET /health or GET /accounts/ACC10001/snapshot")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
