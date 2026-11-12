"""Test every feed in config/sources.yaml (enabled and candidates).

Prints a table and, when run in GitHub Actions, writes it to the job summary
so you can see which feeds work before switching any on.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import yaml

import requests

from build_paper import CONFIG, HEADERS, entry_time, fetch_feed, page_lines


def main() -> None:
    config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    now = datetime.now(timezone.utc)
    rows = ["| Feed | On? | Result | Items | Newest item |", "|---|---|---|---|---|"]
    for feed in config["feeds"]:
        on = "yes" if feed.get("enabled") else "no"
        try:
            entries = fetch_feed(feed)
            dates = [d for d in (entry_time(e) for e in entries) if d]
            newest = f"{int((now - max(dates)).total_seconds() // 3600)} h ago" if dates else "no dates"
            rows.append(f"| {feed['name']} | {on} | OK | {len(entries)} | {newest} |")
        except Exception as exc:
            rows.append(f"| {feed['name']} | {on} | FAILED: {str(exc)[:80]} | 0 | - |")
    for page in config.get("page_watches", []):
        on = "yes" if page.get("enabled") else "no"
        try:
            response = requests.get(page["url"], headers=HEADERS, timeout=25)
            response.raise_for_status()
            found = len(page_lines(response.text, page["keywords"]))
            result = "OK" if found else "OK, but no keyword lines found (page may need JavaScript)"
            rows.append(f"| {page['name']} (page watch) | {on} | {result} | {found} lines | - |")
        except Exception as exc:
            rows.append(f"| {page['name']} (page watch) | {on} | FAILED: {str(exc)[:80]} | 0 | - |")
    table = "\n".join(rows)
    print(table)
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        Path(summary_path).write_text("## Feed check\n\n" + table + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
