"""Local-only HTTP app: static UI, retrieval API, My home and safe manual serving.

Everything here binds to loopback and is meant for one household on one
computer. The write endpoints (saved appliances) and the optional paid answer
endpoint share the same guards, and the guards run before any body is read:

* the `Host` header must name a loopback host and the port actually being
  served, so a DNS-rebinding page cannot reach the port;
* the `Origin`, when present, must match the scheme/host/port really serving
  this request - not merely any localhost;
* `Sec-Fetch-Site`, when present, must be `same-origin` or `none`;
* the body must be `application/json` and within a fixed size, and every field
  is type-checked before use.
"""

from __future__ import annotations

import json
import re
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import config, generate, retrieve, __version__
from .generate import GenerationLimiter
from .household import HouseholdError, HouseholdStore
from .index import ManualIndex
from .registry import blocked_sources

DOC_ID_RE = re.compile(r"^[a-z0-9][a-z0-9\-]{0,63}$")
APPLIANCE_ID_RE = re.compile(r"^app_[a-f0-9]{12}$")
STATIC_WHITELIST = {"index.html", "app.js", "styles.css"}
MAX_BODY_BYTES = 32 * 1024
CHUNK_SIZE = 64 * 1024
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
ALLOWED_FETCH_SITES = {"same-origin", "none"}
DEFAULT_PORTS = {"http": 80, "https": 443}

HOUSEHOLD_ERROR_STATUS = {
    "unknown_appliance": 404,
    "unsupported_appliance": 409,
    "unsupported_model": 400,
    "invalid_name": 400,
    "invalid_model": 400,
    "invalid_category": 400,
    "invalid_id": 400,
    "too_many": 409,
    "storage_error": 500,
}


def split_host_port(value: str) -> tuple[str | None, str | None]:
    """Split a Host header into (host, port text). A None host means malformed.

    Handles the bracketed IPv6 form; a bare (unbracketed) IPv6 literal is
    rejected rather than guessed at.
    """
    text = (value or "").strip().lower()
    if not text:
        return None, None
    if text.startswith("["):
        host, separator, rest = text.partition("]")
        if not separator:
            return None, None
        rest = rest.strip()
        if rest and not rest.startswith(":"):
            return None, None
        return f"{host}]", (rest[1:] or None)
    if text.count(":") > 1:
        return None, None
    host, _, port = text.partition(":")
    return (host or None), (port or None)


def effective_port(port_text: str | None, scheme: str = "http") -> int | None:
    """Apply the scheme default. Returns None when the port text is unusable."""
    if port_text is None:
        return DEFAULT_PORTS.get(scheme)
    try:
        port = int(port_text)
    except (TypeError, ValueError):
        return None
    return port if 1 <= port <= 65535 else None


def normalise_host(host: str) -> str:
    return (host or "").strip().strip("[]").lower()


def _empty_result(question: str, status: str, note: str, **extra) -> dict:
    result = {
        "question": question,
        "status": status,
        "mode": "retrieved_evidence",
        "filters": {"category": None, "model": None, "doc_id": None},
        "evidence": [],
        "notes": [note],
        "clarification": None,
        "matched_models": [],
        "unknown_models": [],
        "model_dependent": False,
        "implied_category": None,
    }
    result.update(extra)
    return result


class ManualApp:
    """Holds the index, the local household store and the generation settings."""

    def __init__(
        self,
        index: ManualIndex | None = None,
        transport=None,
        household_path: Path | None = None,
        limiter: GenerationLimiter | None = None,
    ) -> None:
        self.index = index or ManualIndex.load()
        self.transport = transport
        self.generation_state = config.generation_state()
        self.version = __version__
        self.household = HouseholdStore(path=household_path, index=self.index)
        self.limiter = limiter or GenerationLimiter()

    # -- metadata -----------------------------------------------------
    def meta(self) -> dict:
        blockers = [
            {
                "brand": item.get("brand", ""),
                "category": item.get("category", ""),
                "title": item.get("title", ""),
                "reason": item.get("blocked_reason", ""),
            }
            for item in blocked_sources()
        ]
        return {
            "app": config.APP_NAME,
            "version": self.version,
            "generation": self.generation_state,
            "categories": [
                {"id": key, "label": config.CATEGORY_LABELS[key]} for key in config.CATEGORIES
            ],
            "documents": [
                {
                    "doc_id": doc.doc_id,
                    "title": doc.title,
                    "brand": doc.brand,
                    "category": doc.category,
                    "category_label": config.CATEGORY_LABELS.get(doc.category, doc.category),
                    "models": list(doc.models),
                    "pages": doc.pages,
                    "bytes": doc.bytes,
                    "sha256": doc.sha256,
                    "sha256_short": doc.sha256[:12],
                    "retrieved_at": doc.retrieved_at,
                    "source_page_url": doc.source_page_url,
                    "pdf_url": doc.pdf_url,
                    "url": doc.served_path,
                    "pages_without_text": self.index.pages_without_text(doc.doc_id),
                }
                for doc in self.index.docs
            ],
            "blocked_sources": blockers,
            "index_built_at": self.index.built_at,
            "warnings": self.index.warnings,
            "household": {
                "appliances": self.household.list(),
                "storage": {
                    "kind": "sqlite",
                    "location": "data/household.db on this computer",
                    "note": "Saved appliances stay on this machine; nothing is uploaded.",
                },
            },
            "limits": {
                "max_question_chars": config.MAX_QUESTION_CHARS,
                "top_k": config.TOP_K,
                "search": "input terms matched against extracted manual text",
            },
        }

    # -- My home ------------------------------------------------------
    def add_appliance(self, payload: dict) -> tuple[int, dict]:
        if not isinstance(payload, dict):
            return 400, {"error": "request body must be a JSON object"}
        try:
            record = self.household.add(
                payload.get("nickname"), payload.get("category"), payload.get("model")
            )
        except HouseholdError as exc:
            return HOUSEHOLD_ERROR_STATUS.get(exc.code, 400), {
                "error": exc.message,
                "code": exc.code,
            }
        return 201, {"appliance": record, "appliances": self.household.list()}

    def rename_appliance(self, payload: dict) -> tuple[int, dict]:
        if not isinstance(payload, dict):
            return 400, {"error": "request body must be a JSON object"}
        try:
            record = self.household.rename(payload.get("id"), payload.get("nickname"))
        except HouseholdError as exc:
            return HOUSEHOLD_ERROR_STATUS.get(exc.code, 400), {
                "error": exc.message,
                "code": exc.code,
            }
        return 200, {"appliance": record, "appliances": self.household.list()}

    def remove_appliance(self, payload: dict) -> tuple[int, dict]:
        if not isinstance(payload, dict):
            return 400, {"error": "request body must be a JSON object"}
        try:
            removed = self.household.remove(payload.get("id"))
        except HouseholdError as exc:
            return HOUSEHOLD_ERROR_STATUS.get(exc.code, 400), {
                "error": exc.message,
                "code": exc.code,
            }
        if not removed:
            return 404, {"error": "That saved appliance no longer exists.", "code": "unknown_appliance"}
        return 200, {"removed": True, "appliances": self.household.list()}

    # -- asking -------------------------------------------------------
    def ask(self, payload: dict) -> tuple[int, dict]:
        if not isinstance(payload, dict):
            return 400, {"error": "request body must be a JSON object"}
        question = payload.get("question")
        if not isinstance(question, str):
            return 400, {"error": "question must be a string"}
        category = payload.get("category") or None
        model = payload.get("model") or None
        doc_id = payload.get("doc_id") or None
        appliance_id = payload.get("appliance_id") or None
        if category is not None and category not in config.CATEGORIES:
            return 400, {"error": f"unknown category: {category}"}
        if model is not None and (not isinstance(model, str) or len(model) > 40):
            return 400, {"error": "model must be a string of at most 40 characters"}
        if doc_id is not None and (not isinstance(doc_id, str) or not DOC_ID_RE.match(doc_id)):
            return 400, {"error": "invalid manual id"}
        raw_generate = payload.get("generate", False)
        if not isinstance(raw_generate, bool):
            return 400, {"error": "generate must be true or false"}
        wants_generation = raw_generate

        appliance = None
        if appliance_id is not None:
            if not isinstance(appliance_id, str) or not APPLIANCE_ID_RE.match(appliance_id):
                return 400, {"error": "invalid appliance id"}
            try:
                category, model = self.household.resolve(appliance_id)
            except HouseholdError as exc:
                status = exc.code
                code = HOUSEHOLD_ERROR_STATUS.get(status, 400)
                body = _empty_result(
                    question,
                    status,
                    exc.message,
                    generation=self._generation_block(wants_generation),
                )
                return (200 if code < 500 else 500), body
            appliance = self.household.get(appliance_id)

        result = retrieve.analyse(
            self.index,
            question,
            category=category,
            model=model,
            doc_id=doc_id,
        )
        status_code = 200 if result["status"] not in {"bad_request"} else 400
        result["appliance"] = appliance
        result["answer"] = None
        result["generation"] = self._generation_block(wants_generation)
        if result["status"] == "ok" and wants_generation:
            result["generation"]["result"] = self._run_generation(
                question, result, model=model
            )
            generation_result = result["generation"]["result"]
            if generation_result["status"] == "generated":
                result["mode"] = "generated_answer"
                result["answer"] = generation_result["answer"]
            elif generation_result["status"] == "needs_clarification":
                result["status"] = "needs_clarification"
                result["clarification"] = {
                    "kind": "model",
                    "message": generation_result["detail"],
                    "options": retrieve.model_options(self.index, result["implied_category"]),
                }
                result["evidence"] = []
                result["notes"].append(generation_result["detail"])
            else:
                result["mode"] = "retrieved_evidence"
                if generation_result["detail"]:
                    result["notes"].append(generation_result["detail"])
        elif result["status"] == "ok":
            result["mode"] = "retrieved_evidence"
        return status_code, result

    def _generation_block(self, requested: bool) -> dict:
        return {
            "requested": requested,
            "status": self.generation_state["status"],
            "detail": self.generation_state["detail"],
            "result": None,
        }

    def _run_generation(self, question: str, result: dict, model: str | None) -> dict:
        if self.generation_state.get("status") != "enabled":
            return generate.generate_answer(
                question,
                result["evidence"],
                transport=self.transport,
                settings={"generation_state": self.generation_state},
            )
        refusal = self.limiter.try_acquire()
        if refusal is not None:
            stats = self.limiter.stats()
            detail = (
                f"The in-process generation limit was reached ({stats['used']}/"
                f"{stats['max_requests']} requests, {stats['max_concurrency']} at once), so "
                "no request was sent. These are local guards, not a billing cap."
                if refusal == "rate_limited"
                else "Another answer is being written right now; try again in a moment."
            )
            return {
                "status": refusal,
                "answer": None,
                "problems": [],
                "model": config.deepseek_model(),
                "detail": detail,
                "citations": [],
                "selected_model": model,
            }
        try:
            return generate.generate_answer(
                question,
                result["evidence"],
                transport=self.transport,
                settings={"generation_state": self.generation_state},
                selected_model=model,
                single_doc_required=bool(model) or retrieve.is_procedural(question),
            )
        finally:
            self.limiter.release()


def make_handler(app: ManualApp):
    static_dir = config.STATIC_DIR.resolve()
    manuals_dir = config.MANUALS_DIR.resolve()

    class Handler(BaseHTTPRequestHandler):
        server_version = f"HomeManualAssistant/{app.version}"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args) -> None:  # keep logs short, never log bodies
            sys.stderr.write(f"{self.address_string()} - {fmt % args}\n")

        # -- plumbing -------------------------------------------------
        def _send_json(self, payload: dict, status: int = 200, head_only: bool = False) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if not head_only:
                self.wfile.write(body)

        def _send_bytes(
            self,
            body: bytes,
            content_type: str,
            status: int = 200,
            extra: dict | None = None,
            head_only: bool = False,
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            if not head_only:
                self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 - http.server API
            self._route("GET")

        def do_HEAD(self) -> None:  # noqa: N802
            self._route("HEAD")

        def do_POST(self) -> None:  # noqa: N802
            self._route("POST")

        def _route(self, method: str) -> None:
            parsed = urllib.parse.urlparse(self.path)
            path = urllib.parse.unquote(parsed.path)
            head_only = method == "HEAD"
            try:
                # DNS-rebinding guard: only our own loopback host names are served.
                if not self._host_allowed():
                    self._send_json(
                        {"error": "unexpected Host header"}, status=403, head_only=head_only
                    )
                    return
                if method == "POST":
                    self._handle_post(path)
                    return
                if path in ("/", "/index.html"):
                    self._serve_static("index.html", head_only)
                    return
                if path.startswith("/static/"):
                    self._serve_static(path[len("/static/") :], head_only)
                    return
                if path == "/api/meta":
                    self._send_json(app.meta(), head_only=head_only)
                    return
                if path == "/api/health":
                    self._send_json(
                        {
                            "status": "ok",
                            "documents": len(app.index.docs),
                            "chunks": len(app.index.chunks),
                            "generation": app.generation_state["status"],
                            "appliances": len(app.household.list()),
                        },
                        head_only=head_only,
                    )
                    return
                if path.startswith("/manuals/"):
                    self._serve_manual(path[len("/manuals/") :], head_only)
                    return
                if path == "/favicon.ico":
                    self._send_bytes(b"", "image/x-icon", status=204, head_only=head_only)
                    return
                self._send_json({"error": "not found", "path": path}, status=404, head_only=head_only)
            except BrokenPipeError:  # client closed the connection
                return

        # -- guards ---------------------------------------------------
        def _host_allowed(self) -> bool:
            """The Host header must name this loopback host at the serving port.

            A missing port means the scheme default (80 for us), so a bare
            `127.0.0.1` is only accepted when the app is actually serving on 80.
            """
            host, port_text = split_host_port(self.headers.get("Host") or "")
            if not host:
                return False
            if host not in LOOPBACK_HOSTS | {"[::1]"}:
                return False
            port = effective_port(port_text)
            return port is not None and port == self.server.server_address[1]

        def _origin_allowed(self) -> bool:
            """Compare a present Origin with the origin this request really used.

            Not "some loopback": the scheme must be the plain HTTP this server
            speaks, the host must be the same host the request was addressed to,
            and the effective port (with scheme defaults applied) must be the
            port actually being served. So `http://localhost:8099` against a
            `127.0.0.1:8099` request, `https://127.0.0.1:8099`, and a
            port-less `http://127.0.0.1` are all refused.
            """
            raw = self.headers.get("Origin")
            if raw is None:
                return True
            origin = raw.strip()
            if not origin or origin.lower() == "null":
                return False
            try:
                parsed = urllib.parse.urlparse(origin)
                origin_port = parsed.port
            except ValueError:  # malformed or out-of-range port
                return False
            if parsed.scheme != "http":
                return False
            if parsed.username or parsed.password:
                return False
            if not parsed.hostname:
                return False
            request_host, _ = split_host_port(self.headers.get("Host") or "")
            if not request_host:
                return False
            if normalise_host(parsed.hostname) != normalise_host(request_host):
                return False
            port = effective_port(str(origin_port) if origin_port is not None else None)
            return port is not None and port == self.server.server_address[1]

        def _fetch_site_allowed(self) -> bool:
            site = (self.headers.get("Sec-Fetch-Site") or "").strip().lower()
            return (not site) or site in ALLOWED_FETCH_SITES

        def _write_guards(self) -> bool:
            """Everything that can write or spend money runs behind these checks."""
            content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if content_type != "application/json":
                self._send_json({"error": "Content-Type must be application/json"}, status=415)
                return False
            if not self._origin_allowed():
                self._send_json({"error": "cross-origin request refused"}, status=403)
                return False
            if not self._fetch_site_allowed():
                self._send_json({"error": "cross-site request refused"}, status=403)
                return False
            return True

        def _read_json_body(self) -> tuple[dict | None, bool]:
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                self._send_json({"error": "invalid Content-Length"}, status=400)
                return None, False
            if length <= 0 or length > MAX_BODY_BYTES:
                self._send_json(
                    {"error": f"request body must be between 1 and {MAX_BODY_BYTES} bytes"},
                    status=413 if length > MAX_BODY_BYTES else 400,
                )
                return None, False
            raw = self.rfile.read(length)
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._send_json({"error": "request body must be valid JSON"}, status=400)
                return None, False
            if not isinstance(payload, dict):
                self._send_json({"error": "request body must be a JSON object"}, status=400)
                return None, False
            return payload, True

        def _handle_post(self, path: str) -> None:
            routes = {
                "/api/ask": app.ask,
                "/api/appliances/add": app.add_appliance,
                "/api/appliances/rename": app.rename_appliance,
                "/api/appliances/remove": app.remove_appliance,
            }
            handler = routes.get(path)
            if handler is None:
                self._send_json({"error": "not found", "path": path}, status=404)
                return
            if not self._write_guards():
                return
            payload, ok = self._read_json_body()
            if not ok:
                return
            status, body = handler(payload)
            self._send_json(body, status=status)

        # -- static and manuals --------------------------------------
        def _serve_static(self, name: str, head_only: bool) -> None:
            if name not in STATIC_WHITELIST:
                self._send_json({"error": "not found"}, status=404, head_only=head_only)
                return
            target = (static_dir / name).resolve()
            if not target.is_file() or not target.is_relative_to(static_dir):
                self._send_json({"error": "not found"}, status=404, head_only=head_only)
                return
            content_type = {
                ".html": "text/html; charset=utf-8",
                ".js": "text/javascript; charset=utf-8",
                ".css": "text/css; charset=utf-8",
            }.get(target.suffix, "application/octet-stream")
            self._send_bytes(target.read_bytes(), content_type, head_only=head_only)

        def _serve_manual(self, doc_id: str, head_only: bool) -> None:
            if not DOC_ID_RE.match(doc_id):
                self._send_json({"error": "invalid manual id"}, status=400, head_only=head_only)
                return
            doc = app.index.doc(doc_id)
            if doc is None:
                self._send_json({"error": "unknown manual"}, status=404, head_only=head_only)
                return
            target = doc.local_path.resolve()
            # Defence in depth: the served file must live inside data/manuals.
            if not target.is_relative_to(manuals_dir):
                self._send_json({"error": "forbidden"}, status=403, head_only=head_only)
                return
            if not target.is_file():
                self._send_json({"error": "manual file missing on disk"}, status=404, head_only=head_only)
                return
            size = target.stat().st_size
            start, end = 0, size - 1
            status = 200
            extra = {
                "Accept-Ranges": "bytes",
                "Content-Disposition": f'inline; filename="{doc_id}.pdf"',
                "Cache-Control": "private, max-age=300",
            }
            range_header = self.headers.get("Range")
            if range_header:
                match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip())
                if match:
                    raw_start, raw_end = match.groups()
                    if raw_start:
                        start = int(raw_start)
                        end = int(raw_end) if raw_end else size - 1
                    elif raw_end:
                        start = max(0, size - int(raw_end))
                    if start > end or start >= size:
                        self.send_response(416)
                        self.send_header("Content-Range", f"bytes */{size}")
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    end = min(end, size - 1)
                    status = 206
                    extra["Content-Range"] = f"bytes {start}-{end}/{size}"
                else:
                    self._send_json({"error": "unsupported Range header"}, status=416, head_only=head_only)
                    return
            length = end - start + 1
            self.send_response(status)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Length", str(length))
            self.send_header("X-Content-Type-Options", "nosniff")
            for key, value in extra.items():
                self.send_header(key, value)
            self.end_headers()
            if head_only:
                return
            with target.open("rb") as handle:
                handle.seek(start)
                remaining = length
                while remaining > 0:
                    block = handle.read(min(CHUNK_SIZE, remaining))
                    if not block:
                        break
                    self.wfile.write(block)
                    remaining -= len(block)

    return Handler


def serve(
    host: str = "127.0.0.1",
    port: int = 8099,
    index: ManualIndex | None = None,
    transport=None,
    household_path: Path | None = None,
) -> ThreadingHTTPServer:
    if host not in LOOPBACK_HOSTS:
        raise ValueError(f"refusing to bind {host!r}: the assistant is local-only")
    app = ManualApp(index=index, transport=transport, household_path=household_path)
    httpd = ThreadingHTTPServer((host, port), make_handler(app))
    httpd.daemon_threads = True
    return httpd


def build_app(
    index_path: Path | None = None,
    rebuild: bool = False,
    household_path: Path | None = None,
) -> ManualApp:
    return ManualApp(
        index=ManualIndex.load(index_path=index_path, rebuild=rebuild),
        household_path=household_path,
    )
