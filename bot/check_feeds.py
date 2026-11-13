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
            dates = [d for d in (entry_time(e) for e in entries) if d and d <= now]
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
    # Markets panel sources
    import markets
    from datetime import timedelta
    local = now + timedelta(hours=config["settings"]["utc_offset_hours"])
    checks = [
        ("Open market rates (ECAP via SBP)", lambda: markets.open_market_rates(local, ["USD", "EUR", "GBP", "AED", "SAR"]),
         lambda r: f"{len(r['rows'])} currencies, {r['date']}" if r else "none found. Tried: " + "; ".join(sorted(set(markets.ECAP_LOG)))[:300]),
        ("Pakistan gold (Aaj News page)", lambda: markets.pakistan_gold_page(local, max_age_days=30),
         lambda r: f"Rs {r['price']:,.0f}, {r['date']}" if r else "no per-tola headline found"),
        ("International gold", markets.international_gold, lambda r: f"{r['price']:,.2f} USD from {r['source']}" if r else "no source answered"),
        ("PSX index levels", lambda: markets.psx_index_levels(["KSE100", "KMI30"]), lambda r: ", ".join(i["name"] for i in r) or "none parsed"),
        ("PSX KSE-100 stocks", lambda: markets.psx_index_stocks("KSE100"), lambda r: f"{len(r)} stocks"),
        ("PSX KMI-30 stocks", lambda: markets.psx_index_stocks("KMI30"), lambda r: f"{len(r)} stocks"),
    ]
    for name, fn, describe in checks:
        try:
            result = fn()
            ok = "OK" if result else "FAILED"
            rows.append(f"| {name} (markets) | yes | {ok} | {describe(result)} | - |")
        except Exception as exc:
            rows.append(f"| {name} (markets) | yes | FAILED: {str(exc)[:80]} | - | - |")
    table = "\n".join(rows)
    print(table)
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        Path(summary_path).write_text("## Feed check\n\n" + table + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
