"""Markets panel: open market currency rates, gold, PSX indices and top stocks.

Every figure keeps its source name, link and date so the page can show where
it came from. Any source that fails is skipped; the rest of the panel and the
paper still build.
"""

from __future__ import annotations

import io
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser

from html import unescape as html_unescape

import requests
from pypdf import PdfReader

HEADERS = {"User-Agent": "MorningBriefBot/1.0 (personal news digest)"}
# Some data sites drop connections from unknown clients, so data requests
# identify as a normal browser.
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/pdf,application/json;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
ECAP_LOG: list[str] = []
GOLD_LOG: list[str] = []  # what the last Aaj News gold page lookup found (shown by Check feeds)
TOLA_PER_OUNCE = 11.6638038 / 31.1034768  # 1 tola = 11.6638 g; 1 troy ounce = 31.1035 g  # what the last open-market lookup tried (shown by Check feeds)
MONTHS = ["january", "february", "march", "april", "may", "june", "july",
          "august", "september", "october", "november", "december"]


def _get(url: str, timeout: int = 30, tries: int = 3) -> requests.Response:
    """GET with browser headers and up to 3 tries (data sites can drop connections)."""
    last_exc: Exception | None = None
    for attempt in range(tries):
        try:
            response = requests.get(url, headers=BROWSER_HEADERS, timeout=timeout)
            response.raise_for_status()
            return response
        except requests.HTTPError:
            raise  # a 404 or 403 will not change on retry
        except Exception as exc:
            last_exc = exc
            time.sleep(3 * (attempt + 1))
    raise last_exc


def _num(text: str) -> float:
    return float(text.replace(",", "").replace("%", "").strip())


# ---------------------------------------------------------------- currencies

def ecap_urls(day: datetime) -> list[str]:
    """Known SBP addresses for ECAP's daily open market rates PDF."""
    month = MONTHS[day.month - 1]
    mon = day.strftime("%b")
    base = "https://www.sbp.org.pk/assets/document/open-market-closing-exchange-rates"
    return [
        f"{base}-{day:%d}-{month}-{day:%Y}.pdf",               # e.g. ...-04-september-2026.pdf
        f"{base}-{day:%d}-{month.capitalize()}-{day:%Y}.pdf",  # SBP sometimes capitalises the month
        f"https://www.sbp.org.pk/ecodata/rates/ECAP/{day:%Y}/{mon}/{day:%d}-{mon}-{day:%y}.pdf",  # older format
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
    ECAP_LOG.clear()
    for back in range(0, 8):
        day = local_now - timedelta(days=back)
        for url in ecap_urls(day):
            try:
                response = _get(url, tries=1)
                if not response.content.startswith(b"%PDF"):
                    ECAP_LOG.append(f"{day:%d %b}: not a PDF")
                    continue
                text = "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(response.content)).pages)
            except requests.HTTPError as exc:
                ECAP_LOG.append(f"{day:%d %b}: HTTP {exc.response.status_code}")
                continue
            except Exception as exc:
                ECAP_LOG.append(f"{day:%d %b}: {type(exc).__name__}")
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


def pakistan_gold(entries: list[dict], now: datetime, max_age_hours: int = 72) -> dict | None:
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


GOLD_PAGES = [
    "https://english.aaj.tv/trends/gold-prices-in-pakistan",
    "https://beta.english.aaj.tv/trends/gold-prices-in-pakistan",
]


def pakistan_gold_page(local_now: datetime, max_age_days: int = 3) -> dict | None:
    """Fallback: newest per-tola headline on Aaj News' gold prices topic page.

    The page lists headlines followed by "Published 25 Oct, 2025 03:11pm".
    """
    GOLD_LOG.clear()
    for url in GOLD_PAGES:
        host = url.split("/")[2]
        try:
            raw = _get(url).text
        except requests.HTTPError as exc:
            GOLD_LOG.append(f"{host}: HTTP {exc.response.status_code}")
            continue
        except Exception as exc:
            GOLD_LOG.append(f"{host}: {type(exc).__name__}")
            continue
        raw = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", raw)
        found = 0
        sample = ""
        # Look at each mention of "tola" in the page (newest stories come first)
        for match in re.finditer(r"tola", raw, flags=re.I):
            window = raw[max(0, match.start() - 400): match.start() + 600]
            text = re.sub(r"\s+", " ", html_unescape(re.sub(r"<[^>]+>", " ", window)))
            # prefer the new rate ("... to Rs 436,962"), else the first per-tola sized amount
            target = re.search(r"to Rs\.?\s?([\d,]{6,9})", text)
            amounts = ([_num(target.group(1))] if target else []) + [_num(a) for a in re.findall(r"Rs\.?\s?([\d,]{6,9})", text)]
            amounts = [a for a in amounts if 100_000 <= a <= 3_000_000]
            if not amounts:
                continue
            found += 1
            sample = sample or text.strip()[:120]
            published = None
            iso = re.search(r"(20\d\d-\d\d-\d\d)", window)
            human = re.search(r"(\d{1,2} [A-Z][a-z]{2},? \d{4})", text)
            try:
                if iso:
                    published = datetime.strptime(iso.group(1), "%Y-%m-%d")
                elif human:
                    published = datetime.strptime(human.group(1).replace(",", ""), "%d %b %Y")
            except ValueError:
                published = None
            if published is None and re.search(r"\b\d{1,2} (minutes?|hours?) ago", text):
                published = local_now.replace(tzinfo=None)
            if published is None:
                continue  # freshness cannot be confirmed
            if (local_now.replace(tzinfo=None) - published).days > max_age_days:
                GOLD_LOG.append(f"{host}: newest per-tola report dated {published:%d %b %Y}, too old")
                return None
            return {"price": amounts[0], "title": "",
                    "link": url, "source": "Aaj News", "published": published.replace(tzinfo=timezone.utc),
                    "date": published.strftime("%d %b %Y")}
        GOLD_LOG.append(f"{host}: page {len(raw)//1000} KB, {found} per-tola amounts but no readable date"
                        + (f"; first: {sample}" if sample else ""))
    return None


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

    panel["gold_intl"] = international_gold()
    if panel["gold_intl"] is None:
        panel["failed"].append("International gold price")

    panel["gold_pk"] = pakistan_gold(news_entries, now) or pakistan_gold_page(local_now)
    if panel["gold_pk"] is None and panel["gold_intl"] and panel.get("fx"):
        # Estimate: APGJSA rates follow the international price plus a premium
        # (reported as $20 per ounce since Sept 2026), converted to PKR per tola.
        usd = next((r for r in panel["fx"]["rows"] if r["code"] == "USD"), None)
        if usd:
            premium = cfg.get("gold_premium_usd", 20)
            panel["gold_pk"] = {"price": (panel["gold_intl"]["price"] + premium) * usd["sell"] * TOLA_PER_OUNCE,
                                "estimated": True, "premium": premium}
    if panel["gold_pk"] is None:
        panel["failed"].append("Pakistan gold rate")

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
