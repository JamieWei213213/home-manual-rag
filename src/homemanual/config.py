"""Paths, limits and environment configuration for the assistant."""

from __future__ import annotations

import os
import re
import urllib.parse
from pathlib import Path

APP_NAME = "Home Appliance Manual Assistant"

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
MANUALS_DIR = DATA_DIR / "manuals"
MANIFEST_PATH = DATA_DIR / "manuals_manifest.json"
REGISTRY_PATH = DATA_DIR / "source_registry.json"
ACQUISITION_REPORT_PATH = DATA_DIR / "acquisition_report.json"
INDEX_PATH = DATA_DIR / "index" / "chunks.json"
STATIC_DIR = Path(__file__).resolve().parent / "static"

CATEGORIES = ("washer", "dryer", "dishwasher", "refrigerator", "microwave")
CATEGORY_LABELS = {
    "washer": "Washer",
    "dryer": "Dryer",
    "dishwasher": "Dishwasher",
    "refrigerator": "Refrigerator",
    "microwave": "Microwave",
}

# Retrieval tuning.
TOP_K = 6
CHUNK_WORDS = 170
CHUNK_OVERLAP_WORDS = 40
MIN_CHUNK_WORDS = 12
MIN_EVIDENCE_SCORE = 1.0
MAX_QUESTION_CHARS = 500

# Optional DeepSeek answer generation. Disabled unless explicitly switched on.
GENERATION_FLAG_ENV = "HOME_MANUAL_ENABLE_GENERATION"
DEEPSEEK_API_KEY_ENV = "DEEPSEEK_API_KEY"
DEEPSEEK_MODEL_ENV = "DEEPSEEK_MODEL"
DEEPSEEK_BASE_URL_ENV = "DEEPSEEK_BASE_URL"
DEFAULT_DEEPSEEK_MODEL = "deepseek-chat"
DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
GENERATION_TIMEOUT_SECONDS = 45
GENERATION_MAX_TOKENS = 700
GENERATION_TEMPERATURE = 0.0

# In-process guards around the optional paid endpoint. These bound what this
# process will send; they are not a durable billing cap and do not survive a
# restart.
GENERATION_MAX_REQUESTS_ENV = "HOME_MANUAL_GENERATION_MAX_REQUESTS"
GENERATION_MAX_CONCURRENCY_ENV = "HOME_MANUAL_GENERATION_MAX_CONCURRENCY"
GENERATION_MAX_CONTEXT_CHARS_ENV = "HOME_MANUAL_GENERATION_MAX_CONTEXT_CHARS"
DEFAULT_GENERATION_MAX_REQUESTS = 60
DEFAULT_GENERATION_MAX_CONCURRENCY = 2
DEFAULT_GENERATION_MAX_CONTEXT_CHARS = 12000

# Local "My home" appliance records live in a single SQLite file on this
# computer. Nothing is uploaded anywhere.
HOUSEHOLD_DB_ENV = "HOME_MANUAL_HOUSEHOLD_DB"
DEFAULT_HOUSEHOLD_DB = DATA_DIR / "household.db"


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def generation_flag_set() -> bool:
    return _truthy(os.environ.get(GENERATION_FLAG_ENV))


def deepseek_api_key() -> str | None:
    key = os.environ.get(DEEPSEEK_API_KEY_ENV)
    return key.strip() if key and key.strip() else None


def deepseek_model() -> str:
    return (os.environ.get(DEEPSEEK_MODEL_ENV) or DEFAULT_DEEPSEEK_MODEL).strip()


def deepseek_base_url() -> str:
    return (os.environ.get(DEEPSEEK_BASE_URL_ENV) or DEFAULT_DEEPSEEK_BASE_URL).rstrip("/")


def household_db_path() -> Path:
    override = os.environ.get(HOUSEHOLD_DB_ENV)
    return Path(override) if override else DEFAULT_HOUSEHOLD_DB


def _positive_int(value: str | None, default: int) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def generation_max_requests() -> int:
    return _positive_int(os.environ.get(GENERATION_MAX_REQUESTS_ENV), DEFAULT_GENERATION_MAX_REQUESTS)


def generation_max_concurrency() -> int:
    return _positive_int(
        os.environ.get(GENERATION_MAX_CONCURRENCY_ENV), DEFAULT_GENERATION_MAX_CONCURRENCY
    )


def generation_max_context_chars() -> int:
    return _positive_int(
        os.environ.get(GENERATION_MAX_CONTEXT_CHARS_ENV), DEFAULT_GENERATION_MAX_CONTEXT_CHARS
    )


HOST_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.\-]*[A-Za-z0-9])?$")


def provider_url_ok(url: str) -> tuple[bool, str]:
    """The paid transport must be plain HTTPS to a bare, well-formed host.

    Refused so a key can never be sent over cleartext, to a mistyped host, or to
    an endpoint that carries credentials, a query or a fragment in the URL.
    This is a shape check on a user-configured endpoint, not an allowlist: any
    well-formed HTTPS host is accepted.
    """
    raw = (url or "").strip()
    if not raw:
        return False, "the provider URL is empty"
    try:
        parsed = urllib.parse.urlparse(raw)
        parsed.port  # invalid ports raise here rather than later
    except ValueError:
        return False, "the provider URL has a malformed port"
    if parsed.scheme != "https":
        return False, "the provider URL must use https"
    host = parsed.hostname
    if not host:
        return False, "the provider URL must name a host"
    if parsed.username or parsed.password:
        return False, "the provider URL must not embed credentials"
    if parsed.query or parsed.fragment:
        return False, "the provider URL must not carry a query or fragment"
    if not (HOST_RE.match(host) or re.fullmatch(r"[0-9A-Fa-f:.]+", host)):
        return False, "the provider URL host is malformed"
    return True, ""


def provider_endpoint_label(url: str) -> str:
    """Display-safe endpoint: scheme and authority only.

    Nothing after the host is echoed back to the UI, so a path, query or
    embedded credential can never show up in `/api/meta` or in a log line.
    """
    try:
        parsed = urllib.parse.urlparse((url or "").strip())
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        return "(unparsable provider URL)"
    if not host:
        return "(unparsable provider URL)"
    label = f"{parsed.scheme or '?'}://{host}"
    if port is not None and port not in (80, 443):
        label += f":{port}"
    return label


def generation_state() -> dict:
    """Never returns the key itself - only whether one is configured."""
    flag = generation_flag_set()
    key_present = deepseek_api_key() is not None
    base_url = deepseek_base_url()
    url_ok, url_problem = provider_url_ok(base_url)
    if not flag:
        status = "disabled"
        detail = (
            f"Set {GENERATION_FLAG_ENV}=1 (and {DEEPSEEK_API_KEY_ENV}) to opt in to "
            "DeepSeek answer writing. Retrieval works without it."
        )
    elif not url_ok:
        status = "invalid_config"
        detail = (
            f"Refusing the configured {DEEPSEEK_BASE_URL_ENV} "
            f"({provider_endpoint_label(base_url)}): {url_problem}."
        )
    elif not key_present:
        status = "missing_key"
        detail = f"{GENERATION_FLAG_ENV} is set but {DEEPSEEK_API_KEY_ENV} is empty."
    else:
        status = "enabled"
        detail = (
            f"DeepSeek generation enabled with model {deepseek_model()} via "
            f"{provider_endpoint_label(base_url)}. "
            "The key stays in this process; it is never written to the project or to logs."
        )
    return {
        "status": status,
        "flag_env": GENERATION_FLAG_ENV,
        "api_key_env": DEEPSEEK_API_KEY_ENV,
        "provider_url_valid": url_ok,
        "detail": detail,
        "limits": {
            "max_output_tokens": GENERATION_MAX_TOKENS,
            "max_context_chars": generation_max_context_chars(),
            "max_requests_per_process": generation_max_requests(),
            "max_concurrency": generation_max_concurrency(),
            "note": "In-process guards only; they are not a durable billing cap.",
        },
    }
