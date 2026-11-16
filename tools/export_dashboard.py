"""Export the dashboard to a standalone HTML snapshot.

Run: python tools/export_dashboard.py --data-dir data/demo --out docs/dashboard.html
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import load_settings
from app.store import Store
from app.web import collect_facets, render_page


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--out", default="docs/dashboard.html")
    parser.add_argument("--limit", type=int, default=60)
    args = parser.parse_args()

    settings = load_settings({"data_dir": args.data_dir})
    with Store(settings.db_path) as store:
        stats = store.stats()
        rows = store.recent(limit=args.limit)

    sources, topics = collect_facets(rows)
    page = render_page(
        rows,
        stats,
        sources=sources,
        topics=topics,
        filters={},
        interactive=False,
        title="Agent tổng hợp tin tức — bản xem trước",
    )

    target = Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(page, encoding="utf-8")
    print(f"wrote {target} ({target.stat().st_size} bytes, {len(rows)} articles)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
