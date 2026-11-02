#!/usr/bin/env python3
"""Acquire official appliance manuals for the Home Appliance Manual Assistant.

Development-time tool, network required. It reads the curated registry in
data/source_registry.json, resolves candidate PDF links from each manufacturer
support page (or uses a curated direct PDF link), downloads the file, and then
verifies that it is a real PDF whose text actually contains the claimed model
number. Only verified files are recorded in data/manuals_manifest.json.

Nothing in this script runs inside the app. It never edits the registry, and it
records every failure honestly in data/acquisition_report.json.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
REGISTRY_PATH = DATA_DIR / "source_registry.json"
MANIFEST_PATH = DATA_DIR / "manuals_manifest.json"
MANUALS_DIR = DATA_DIR / "manuals"
REPORT_PATH = DATA_DIR / "acquisition_report.json"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
MIN_PAGES = 4
MAX_CANDIDATES = 8
REQUEST_PAUSE = 1.0

# Entries that appeared in earlier report runs and were later replaced or
# dropped. They are kept in the report with an explanation instead of being
# silently deleted, so "what was attempted" stays auditable.
HISTORICAL_ATTEMPTS = {
    "bosch-shem63w55n": "superseded: the MCDOC02307510_SHEM63W55N-CA-EN.pdf candidate "
    "was only a 3-page quick-start sheet; replaced by the 88-page 9001238146_A.pdf manual "
    "for the same model family.",
    "amana-art104psm": "dropped: Amana shares the whirlpool.com platform, which returns "
    "HTTP 403 to non-browser clients; the whole Whirlpool family is recorded as disabled.",
    "lg-dle7000w": "dropped: LG builds its manual links with JavaScript and its direct PDF "
    "endpoints return HTTP 404; LG is recorded as disabled instead.",
}

PREFER = (
    "owners-manual",
    "ownersmanual",
    "owner-manual",
    "use-and-care",
    "useandcare",
    "use_and_care",
    "userguide",
    "user-guide",
    "user_guide",
    "use-care",
    "operating-instructions",
    "manual",
)
AVOID = (
    "installation",
    "install",
    "spec-sheet",
    "specification",
    "specsheet",
    "spec-sheet",
    "energy",
    "warranty",
    "dimension",
    "quick-spec",
    "parts-list",
    "label",
    "wiring",
    "template",
)


def normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def fetch(url: str, timeout: int = 60, referer: str | None = None) -> tuple[bytes, dict, bool]:
    """Return (body, headers, tls_verified). Falls back to unverified TLS and
    says so, instead of silently pretending the certificate was validated."""
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/pdf,*/*",
        "Accept-Language": "en-US,en;q=0.9",
    }
    if referer:
        headers["Referer"] = referer
    request = urllib.request.Request(
        url,
        headers=headers,
    )
    try:
        with urllib.request.urlopen(
            request, timeout=timeout, context=ssl.create_default_context()
        ) as response:
            return response.read(), dict(response.headers), True
    except ssl.SSLError:
        with urllib.request.urlopen(
            request, timeout=timeout, context=ssl._create_unverified_context()
        ) as response:
            return response.read(), dict(response.headers), False


def pdf_links(html: str, base_url: str) -> list[str]:
    found: list[str] = []
    for raw in re.findall(r"""["'(]([^"'()\s]+\.pdf(?:\?[^"'()\s]*)?)["')]""", html, re.I):
        found.append(urllib.parse.urljoin(base_url, raw))
    for raw in re.findall(r"https?://[^\s\"'<>]+\.pdf", html, re.I):
        found.append(raw)
    # Samsung ships manual links as JSON download endpoints inside the page.
    for raw in re.findall(r'"downloadUrl"\s*:\s*"([^"]+)"', html):
        url = raw.replace("\\/", "/")
        if "VPath=" in url:
            found.append(url)
    for raw in re.findall(r'href="([^"]*VPath=[^"]+)"', html, re.I):
        found.append(urllib.parse.urljoin(base_url, raw.replace("\\/", "/")))
    seen: dict[str, None] = {}
    for url in found:
        seen.setdefault(url, None)
    return list(seen)


def score_candidate(url: str, models: list[str]) -> int:
    low = url.lower()
    score = 0
    if any(normalise(model) in normalise(low) for model in models):
        score += 6
    if any(token in low for token in PREFER):
        score += 3
    if any(token in low for token in AVOID):
        score -= 3
    # Language hints: prefer English-only or multi-language with English.
    vpath = urllib.parse.unquote(low)
    if re.search(r"[_-](en|eng)[_-]", vpath):
        score += 4
    if re.search(r"[_-](mes|spa|sp|fr|cfr|bpt|ptb|ara|kor|chi)[_-]", vpath):
        score -= 2
    if "cdcttype=em" in low:  # Samsung quick guides
        score -= 2
    if "cdcttype=um" in low:  # Samsung user manuals
        score += 3
    if "en" in Path(urllib.parse.urlparse(low).path).stem.split("_")[-1:]:
        score += 1
    return score


CATEGORY_KEYWORDS = {
    "washer": ("washer", "washing machine"),
    "dryer": ("dryer", "drying"),
    "dishwasher": ("dishwasher",),
    "refrigerator": ("refrigerator", "fridge"),
    "microwave": ("microwave",),
}
BRAND_TOKENS = {
    "Whirlpool": ("whirlpool",),
    "Maytag": ("maytag",),
    "Amana": ("amana",),
    "KitchenAid": ("kitchenaid",),
    "LG": ("lg electronics", "lg "),
    "Bosch": ("bosch",),
    "Panasonic": ("panasonic",),
    "GE Appliances": ("ge appliances", "general electric", "ge®"),
    "Samsung": ("samsung",),
    "Frigidaire": ("frigidaire",),
}
def verify_pdf(
    data: bytes,
    models: list[str],
    brand: str = "",
    category: str = "",
    model_check: str = "pdf_text",
) -> dict:
    """Return verification info. Raises ValueError with a reason on failure."""
    if not data.startswith(b"%PDF"):
        raise ValueError("not_a_pdf")
    import io

    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        pages = len(reader.pages)
    except Exception as exc:  # pragma: no cover - depends on third-party file
        raise ValueError(f"unreadable_pdf:{type(exc).__name__}") from exc
    if pages < MIN_PAGES:
        raise ValueError(f"too_few_pages:{pages}")

    def page_text(index: int) -> str:
        try:
            return reader.pages[index].extract_text() or ""
        except Exception:
            return ""

    # Cover and table-of-contents pages are the usual place for the model list,
    # so scan the front matter first and only then fall back to the whole file.
    haystack = "".join(page_text(index) for index in range(min(pages, 12)))
    scanned = min(pages, 12)
    haystack_norm = normalise(haystack)
    # Exact curated aliases only: no trimming, no prefixes. A model is either
    # printed in the PDF text or it is not, and the manifest says which.
    alias_map = {normalise(model): model for model in models if normalise(model)}
    matched = sorted({alias_map[alias] for alias in alias_map if alias in haystack_norm})
    if not matched and model_check == "pdf_text":
        haystack = "".join(page_text(index) for index in range(pages))
        scanned = pages
        haystack_norm = normalise(haystack)
        matched = sorted({alias_map[alias] for alias in alias_map if alias in haystack_norm})
    if not matched and model_check == "pdf_text":
        raise ValueError("model_not_found_in_text")

    brand_tokens = BRAND_TOKENS.get(brand, (normalise(brand),))
    brand_present = any(normalise(token) in haystack_norm for token in brand_tokens if token)
    category_present = any(
        normalise(word) in haystack_norm for word in CATEGORY_KEYWORDS.get(category, ())
    )
    if not (brand_present or category_present):
        raise ValueError("no_brand_or_category_evidence")
    if model_check == "manufacturer_page" and not (brand_present and category_present):
        raise ValueError("manufacturer_page_link_needs_brand_and_category")
    return {
        "pages": pages,
        "models_matched": matched,
        "text_chars": len(haystack),
        "pages_scanned": scanned,
        "brand_present": brand_present,
        "category_present": category_present,
        "model_check": model_check,
    }


def load_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # pragma: no cover - older consoles
        pass
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", action="append", default=[], help="source id(s) to process")
    parser.add_argument("--list", action="store_true", help="list registry ids and exit")
    parser.add_argument("--dry-run", action="store_true", help="resolve links without downloading")
    parser.add_argument("--retries", type=int, default=2, help="download attempts per candidate")
    parser.add_argument(
        "--probe-url",
        action="append",
        default=[],
        help="fetch a support page and print every manual link found (dev-time discovery)",
    )
    parser.add_argument("--probe-referer", default=None, help="Referer header for --probe-url")
    parser.add_argument(
        "--inspect-url",
        action="append",
        default=[],
        help="download a candidate PDF and print its page count, cover text and model tokens",
    )
    args = parser.parse_args()

    if args.inspect_url:
        import io

        from pypdf import PdfReader

        for url in args.inspect_url:
            print(f"\n=== {url}")
            try:
                body, headers, tls_ok = fetch(url)
            except Exception as exc:
                print(f"  FETCH FAILED: {type(exc).__name__}: {exc}")
                continue
            print(f"  bytes={len(body)} magic={body[:5]!r} content_type={headers.get('Content-Type','')}")
            try:
                reader = PdfReader(io.BytesIO(body), strict=False)
            except Exception as exc:
                print(f"  NOT A READABLE PDF: {type(exc).__name__}: {exc}")
                continue
            print(f"  pages={len(reader.pages)}")
            text = ""
            for index in range(min(len(reader.pages), 3)):
                try:
                    text += (reader.pages[index].extract_text() or "") + "\n"
                except Exception as exc:
                    text += f"\n[extract error {type(exc).__name__}]\n"
            flat = normalise(text)
            print(f"  first_3_pages_chars={len(text)}")
            print("  --- cover text ---")
            for line in (text[:900].splitlines()):
                if line.strip():
                    print(f"    {line.strip()}")
            counts: dict[str, int] = {}
            for index in range(len(reader.pages)):
                try:
                    page_text = reader.pages[index].extract_text() or ""
                except Exception:
                    continue
                for match in re.findall(r"[A-Z0-9][A-Z0-9\-/]{5,24}", page_text.upper()):
                    if (
                        sum(char.isdigit() for char in match) >= 1
                        and sum(char.isalpha() for char in match) >= 2
                    ):
                        counts[match] = counts.get(match, 0) + 1
            ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
            print(f"  model-like tokens (whole document, top 25): {ordered[:25]}")
            print(f"  text_empty={not flat}")
            time.sleep(REQUEST_PAUSE)
        return 0

    if args.probe_url:
        exit_code = 0
        for url in args.probe_url:
            print(f"\n=== {url}")
            try:
                body, headers, tls_ok = fetch(url, referer=args.probe_referer)
            except Exception as exc:
                print(f"  FETCH FAILED: {type(exc).__name__}: {exc}")
                exit_code = 1
                continue
            print(f"  status=ok bytes={len(body)} tls_verified={tls_ok} "
                  f"content_type={headers.get('Content-Type', '')}")
            text = body.decode("utf-8", "replace")
            links = pdf_links(text, url)
            if not links:
                print("  no manual links found")
            for link in links[:25]:
                print(f"  - {link}")
            if len(links) > 25:
                print(f"  ... {len(links) - 25} more link(s) not shown")
            time.sleep(REQUEST_PAUSE)
        return exit_code

    registry = load_json(REGISTRY_PATH, {"sources": []})
    sources = registry.get("sources", [])
    if args.list:
        for source in sources:
            print(f"{source['id']:<32} {source['category']:<13} {source['brand']}")
        return 0

    selected = [s for s in sources if not args.only or s["id"] in args.only]
    if not selected:
        print("no matching sources", file=sys.stderr)
        return 2

    MANUALS_DIR.mkdir(parents=True, exist_ok=True)
    manifest = load_json(MANIFEST_PATH, {"manifest_version": 1, "documents": []})
    existing = {doc["doc_id"]: doc for doc in manifest.get("documents", [])}
    previous = load_json(REPORT_PATH, {"results": []})
    report_by_id = {item["doc_id"]: item for item in previous.get("results", [])}
    report: list[dict] = []

    for source in selected:
        doc_id = source["id"]
        models = source.get("models", [])
        entry: dict = {"doc_id": doc_id, "category": source["category"], "brand": source["brand"]}
        if source.get("disabled"):
            entry["status"] = "blocked"
            entry["reason"] = source.get("blocked_reason", "disabled in registry")
            report.append(entry)
            print(f"[skip] {doc_id}: {entry['reason'][:90]}")
            continue
        candidates: list[str] = []
        try:
            if source.get("pdf"):
                candidates = [source["pdf"]]
            else:
                html_bytes, headers, tls_ok = fetch(source["page"])
                html = html_bytes.decode("utf-8", "replace")
                candidates = sorted(
                    pdf_links(html, source["page"]),
                    key=lambda url: score_candidate(url, models),
                    reverse=True,
                )[:MAX_CANDIDATES]
                entry["candidate_count"] = len(candidates)
        except Exception as exc:
            entry["status"] = "unresolved"
            entry["reason"] = f"page_fetch_failed:{type(exc).__name__}:{exc}"
            report.append(entry)
            print(f"[page] {doc_id}: FAILED {entry['reason']}")
            continue

        if args.dry_run:
            entry["status"] = "candidates"
            entry["candidates"] = candidates
            report.append(entry)
            print(f"[dry-run] {doc_id}: {len(candidates)} candidate(s)")
            for url in candidates:
                print(f"    {url}")
            continue

        if not candidates:
            entry["status"] = "unresolved"
            entry["reason"] = "no_pdf_links_found"
            report.append(entry)
            print(f"[link] {doc_id}: no PDF links found")
            continue

        attempts: list[dict] = []
        verified = False
        for url in candidates:
            for attempt in range(1, args.retries + 1):
                try:
                    data, headers, tls_ok = fetch(url, referer=source.get("page"))
                except Exception as exc:
                    attempts.append({"url": url, "result": f"download_failed:{type(exc).__name__}"})
                    time.sleep(REQUEST_PAUSE)
                    continue
                try:
                    info = verify_pdf(
                        data,
                        models,
                        source.get("brand", ""),
                        source.get("category", ""),
                        source.get("model_check", "pdf_text"),
                    )
                except ValueError as exc:
                    attempts.append({"url": url, "result": f"rejected:{exc}"})
                    break
                target = MANUALS_DIR / f"{doc_id}.pdf"
                target.write_bytes(data)
                digest = hashlib.sha256(data).hexdigest()
                entry.update(
                    {
                        "status": "verified",
                        "title": source["title"],
                        "models": models,
                        "doc_type": source.get("doc_type", "owner_manual"),
                        "source_page_url": source.get("page"),
                        "pdf_url": url,
                        "local_path": str(target.relative_to(ROOT)).replace("\\", "/"),
                        "sha256": digest,
                        "bytes": len(data),
                        "pages": info["pages"],
                        "models_matched_in_text": info["models_matched"],
                        "pages_scanned_for_model": info["pages_scanned"],
                        "model_check": info["model_check"],
                        "content_type": headers.get("Content-Type", ""),
                        "tls_verified": tls_ok,
                        "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    }
                )
                if source.get("model_note"):
                    entry["model_note"] = source["model_note"]
                verified = True
                print(
                    f"[ok]   {doc_id}: {info['pages']} pages, {len(data)} bytes, "
                    f"models={info['models_matched']}"
                )
                break
            if verified:
                break
            time.sleep(REQUEST_PAUSE)

        if not verified:
            entry["status"] = "unresolved"
            entry["reason"] = "no_candidate_verified"
            entry["attempts"] = attempts
            print(f"[fail] {doc_id}: no candidate verified")
        entry["attempts"] = entry.get("attempts", attempts)
        report.append(entry)
        if verified:
            existing[doc_id] = {
                key: value
                for key, value in entry.items()
                if key not in {"attempts", "candidate_count", "status"}
            }
            existing[doc_id]["doc_id"] = doc_id
        time.sleep(REQUEST_PAUSE)

    if not args.dry_run:
        for item in report:
            report_by_id[item["doc_id"]] = item
        registry_ids = {source["id"] for source in sources}
        historical = [
            {
                "doc_id": doc_id,
                "status": item.get("status", "unknown"),
                "note": HISTORICAL_ATTEMPTS.get(
                    doc_id,
                    "no longer in data/source_registry.json; kept here for audit",
                ),
            }
            for doc_id, item in sorted(report_by_id.items())
            if doc_id not in registry_ids
        ]
        manifest = {
            "manifest_version": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "documents": sorted(existing.values(), key=lambda doc: (doc["category"], doc["doc_id"])),
        }
        MANIFEST_PATH.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        REPORT_PATH.write_text(
            json.dumps(
                {
                    "results": sorted(
                        report_by_id.values(), key=lambda item: (item.get("category", ""), item["doc_id"])
                    ),
                    "historical_attempts": historical,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    verified_count = sum(1 for item in report if item.get("status") == "verified")
    print(f"\n{verified_count}/{len(selected)} verified this run; manifest has {len(existing)} document(s).")
    return 0 if verified_count else 1


if __name__ == "__main__":
    raise SystemExit(main())
