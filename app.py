#!/usr/bin/env python3
"""Run the Home Appliance Manual Assistant on this computer.

    python app.py                 # http://127.0.0.1:8099
    python app.py --rebuild-index # rebuild the search index first
"""

from __future__ import annotations

import argparse
import sys
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from homemanual import config  # noqa: E402
from homemanual.server import build_app, serve  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=config.APP_NAME)
    parser.add_argument("--host", default="127.0.0.1", help="loopback host (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8099, help="port (default 8099)")
    parser.add_argument("--rebuild-index", action="store_true", help="rebuild the index from data/manuals")
    parser.add_argument("--open", action="store_true", help="open the page in your browser")
    args = parser.parse_args(argv)

    app = build_app(rebuild=args.rebuild_index)
    if not app.index.docs:
        print(
            "No manuals found. Run: python scripts/acquire_manuals.py "
            "(needs network) and then python scripts/build_index.py",
            file=sys.stderr,
        )
        return 2
    httpd = serve(host=args.host, port=args.port, index=app.index)
    url = f"http://{args.host}:{args.port}/"
    print(f"{config.APP_NAME} {app.version}")
    print(f"  manuals : {len(app.index.docs)} documents, {len(app.index.chunks)} passages")
    print(f"  search  : retrieved manual evidence (offline, no model calls)")
    print(f"  generate: {app.generation_state['status']} - {app.generation_state['detail']}")
    print(f"  my home : {len(app.household.list())} saved appliance(s) in {app.household.path}")
    for warning in app.index.warnings:
        print(f"  warning : {warning}")
    print(f"  serving : {url}")
    if args.open:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
