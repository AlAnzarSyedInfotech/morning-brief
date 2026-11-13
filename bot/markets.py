"""Markets panel: open market currency rates, gold, PSX indices and top stocks.

Every figure keeps its source name, link and date so the page can show where
it came from. Any source that fails is skipped; the rest of the panel and the
paper still build.
"""

from __future__ import annotations

import io
import re
import sys
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser

import requests
from pypdf import PdfReader

HEADERS = {"User-Agent": "MorningBriefBot/1.0 (personal news digest)"}
MONTHS = ["january", "february", "march", "april", "may", "june", "july",
          "august", "september", "october", "november", "december"]


def _get(url: str, timeout: int = 25) -> requests.Response:
    response = requests.get(url, headers=HEADERS, timeout=timeout)
    response.raise_for_status()
    return response


def _num(text: str) -> float:
    return float(text.replace(",", "").replace("%", "").strip())


# ---------------------------------------------------------------- currencies

def ecap_urls(day: datetime) -> list[str]:
    """Known SBP addresses for ECAP's daily open market rates PDF."""
    month = MONTHS[day.month - 1]
    mon = day.strftime("%b")
    return [
        f"https://www.sbp.org.pk/assets/document/open-market-closing-exchange-rates-{day:%d}-{month}-{day:%Y}.pdf",
        f"https://www.sbp.org.pk/ecodata/rates/ECAP/{day:%Y}/{mon}/{day:%d}-{mon}-{day:%y}.pdf",
    ]


def parse_ecap(text: str, currencies: list[str]) -> list[dict]:
    rows = []
    for code in currencies:
        match = re.search(rf"\b{code}\s+([\d,]+\.\d+)\s+([\d,]+\.\d+)", text)
        if match:
            rows.append({"code": code, "buy": _num(match.group(1)), "sell": _num(match.group(2))})
    return rows


def open_market_rates(local_now: datetime, currencies: list[str]) -> dict | None:
    """Latest ECAP closing rates, looking back up to 7 days (weekends, holidays)."""
    for back in range(0, 8):
        day = local_now - timedelta(days=back)
        for url in ecap_urls(day):
            try:
                response = _get(url)
                if not response.content.startswith(b"%PDF"):
                    continue
                text = "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(response.content)).pages)
            except Exception:
                continue
            rows = parse_ecap(text, currencies)
            if rows:
                return {"rows": rows, "date": day.strftime("%d %b %Y"), "link": url,
                        "source": "ECAP open market closing rates (via State Bank of Pakistan)"}
    return None


# ---------------------------------------------------------------- gold

def international_gold() -> dict | None:
    """USD per troy ounce from free no-key sources, first one that works."""
    sources = [
        ("gold-api.com", "https://api.gold-api.com/price/XAU", lambda d: d.get("price")),
        ("xaus.com", "https://xaus.com/api/v1/spot", lambda d: d.get("spot_usd_oz")),
    ]
    for name, url, pick in sources:
        try:
            price = pick(_get(url).json())
            if price and 500 < float(price) < 20000:
                return {"price": float(price), "source": name, "link": url}
        except Exception as exc:
            print(f"[markets] gold {name}: {exc}", file=sys.stderr)
    return None


def pakistan_gold(entries: list[dict], now: datetime, max_age_hours: int = 48) -> dict | None:
    """24K per tola rate as reported in today's news (APGJSA rates).

    entries: dicts with title, summary, link, source, published (UTC).
    """
    best = None
    for e in entries:
        text = f"{e['title']} {e['summary']}"
        low = text.lower()
        if "gold" not in low or "tola" not in low:
            continue
        if now - e["published"] > timedelta(hours=max_age_hours):
            continue
        amounts = [_num(a) for a in re.findall(r"Rs\.?\s?([\d,]{6,9})", text)]
        amounts = [a for a in amounts if 100_000 <= a <= 3_000_000]
        if not amounts:
            continue
        candidate = {"price": max(amounts), "title": e["title"], "link": e["link"],
                     "source": e["source"], "published": e["published"]}
        if best is None or candidate["published"] > best["published"]:
            best = candidate
    return best


# ---------------------------------------------------------------- PSX

class _TableParser(HTMLParser):
    """Collects the text of every table row."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._row is not None and self._cell is not None:
            self._row.append(re.sub(r"\s+", " ", "".join(self._cell)).strip())
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def parse_constituents(html_text: str) -> list[dict]:
    parser = _TableParser()
    parser.feed(html_text)
    header = None
    stocks = []
    for row in parser.rows:
        upper = [c.upper() for c in row]
        if "SYMBOL" in upper and "CHANGE (%)" in upper:
            header = upper
            continue
        if header is None or len(row) != len(header):
            continue
        cell = dict(zip(header, row))
        try:
            stocks.append({
                "symbol": cell["SYMBOL"].split()[0],
                "name": cell.get("NAME", ""),
                "price": _num(cell["CURRENT"]),
                "change_pct": _num(cell["CHANGE (%)"]),
                "market_cap_m": _num(cell["MARKET CAP (M)"]) if cell.get("MARKET CAP (M)") else 0.0,
            })
        except (KeyError, ValueError):
            continue
    return stocks


def psx_index_levels(names: list[str]) -> list[dict]:
    text = re.sub(r"<[^>]+>", " ", _get("https://dps.psx.com.pk/").text)
    text = re.sub(r"\s+", " ", text)
    levels = []
    for name in names:
        match = re.search(rf"\b{name}\s+([\d,]+\.\d+)\s+(-?[\d,]+\.\d+)\s+\((-?[\d.]+)%\)", text)
        if match:
            levels.append({"name": name, "value": _num(match.group(1)),
                           "change": _num(match.group(2)), "change_pct": _num(match.group(3))})
    return levels


def psx_index_stocks(index: str) -> list[dict]:
    return parse_constituents(_get(f"https://dps.psx.com.pk/indices/{index}").text)


# ---------------------------------------------------------------- panel

def build_markets(config: dict, now: datetime, local_now: datetime, news_entries: list[dict]) -> dict:
    cfg = config.get("markets", {})
    panel: dict = {"enabled": cfg.get("enabled", True), "failed": []}
    if not panel["enabled"]:
        return panel

    try:
        panel["fx"] = open_market_rates(local_now, cfg.get("currencies", ["USD", "EUR", "GBP", "AED", "SAR"]))
        if panel["fx"] is None:
            panel["failed"].append("Open market currency rates")
    except Exception as exc:
        panel["fx"] = None
        panel["failed"].append("Open market currency rates")
        print(f"[markets] fx: {exc}", file=sys.stderr)

    panel["gold_pk"] = pakistan_gold(news_entries, now)
    panel["gold_intl"] = international_gold()
    if panel["gold_intl"] is None:
        panel["failed"].append("International gold price")

    try:
        panel["indices"] = psx_index_levels(cfg.get("indices", ["KSE100", "KMI30"]))
    except Exception as exc:
        panel["indices"] = []
        panel["failed"].append("PSX index levels")
        print(f"[markets] indices: {exc}", file=sys.stderr)

    top_n = cfg.get("top_by_size", 10)
    movers = {"KSE100": cfg.get("kse100_movers", 10), "KMI30": cfg.get("kmi30_movers", 5)}
    for index, key in (("KSE100", "kse"), ("KMI30", "kmi")):
        movers_n = movers[index]
        try:
            stocks = psx_index_stocks(index)
        except Exception as exc:
            stocks = []
            print(f"[markets] {index}: {exc}", file=sys.stderr)
        if not stocks:
            panel["failed"].append(f"{index} stocks")
        panel[f"{key}_top"] = sorted(stocks, key=lambda s: -s["market_cap_m"])[:top_n]
        panel[f"{key}_gainers"] = [s for s in sorted(stocks, key=lambda s: -s["change_pct"]) if s["change_pct"] > 0][:movers_n]
        panel[f"{key}_losers"] = [s for s in sorted(stocks, key=lambda s: s["change_pct"]) if s["change_pct"] < 0][:movers_n]
    return panel
