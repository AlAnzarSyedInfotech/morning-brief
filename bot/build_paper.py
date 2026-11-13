"""Build The Morning Brief.

Reads config/sources.yaml, collects the last day's stories from each enabled
feed, removes duplicates, sorts them into sections and writes:
  docs/index.html                today's paper (published by GitHub Pages)
  docs/archive/YYYY-MM-DD.html   a dated copy
  docs/latest.json               the top stories, used by the email alert
"""

from __future__ import annotations

import html
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path

import feedparser
import requests
import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape

from markets import build_markets

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "sources.yaml"
TEMPLATES = ROOT / "templates"
DOCS = ROOT / "docs"
STATE = DOCS / "state" / "pages.json"

GRADE_RANK = {"A": 0, "B": 1, "C": 2}
SECTIONS = ["microsoft", "industry", "global", "side"]
HEADERS = {"User-Agent": "MorningBriefBot/1.0 (personal news digest)"}


# ---------------------------------------------------------------- helpers

def clean_text(raw: str) -> str:
    """Strip HTML tags and extra whitespace from a feed summary."""
    text = re.sub(r"<[^>]+>", " ", raw or "")
    text = html.unescape(text)
    # arXiv summaries start with "arXiv:xxxx Announce Type: new Abstract: ..."
    if "Abstract:" in text:
        text = text.split("Abstract:", 1)[1]
    return re.sub(r"\s+", " ", text).strip()


def short_summary(text: str, max_chars: int = 280) -> str:
    """First two sentences, capped at max_chars."""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    summary = " ".join(sentences[:2]).strip()
    if len(summary) > max_chars:
        summary = summary[: max_chars].rsplit(" ", 1)[0] + "..."
    return summary


def keyword_match(text: str, keywords: list[str]) -> bool:
    """True if any keyword appears at the start of a word in text."""
    low = text.lower()
    for kw in keywords:
        # short words like "ai" must match whole words (not "aim" or "said")
        pattern = r"\b" + re.escape(kw.lower()) + (r"\b" if len(kw) <= 3 else "")
        if re.search(pattern, low):
            return True
    return False


def entry_time(entry) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        value = entry.get(key)
        if value:
            return datetime(*value[:6], tzinfo=timezone.utc)
    return None


def normalise_title(title: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", title.lower()).strip()


def same_story(a: str, b: str) -> bool:
    """Rough duplicate test on two normalised titles."""
    if SequenceMatcher(None, a, b).ratio() > 0.8:
        return True
    wa, wb = set(a.split()), set(b.split())
    if len(wa) >= 4 and len(wb) >= 4:
        overlap = len(wa & wb) / len(wa | wb)
        return overlap > 0.6
    return False


def hours_ago(published: datetime, now: datetime) -> str:
    hours = int((now - published).total_seconds() // 3600)
    if hours < 1:
        return "under 1 h ago"
    return f"{hours} h ago"


# ---------------------------------------------------------------- collect

def fetch_feed(feed: dict) -> list:
    response = requests.get(feed["url"], headers=HEADERS, timeout=25)
    response.raise_for_status()
    parsed = feedparser.parse(response.content)
    if parsed.bozo and not parsed.entries:
        raise ValueError(f"could not read feed: {parsed.bozo_exception}")
    return parsed.entries


RAW_ENTRIES: list[dict] = []  # every recent entry, unfiltered (used by the Markets panel)


def collect(config: dict, now: datetime) -> tuple[list[dict], dict]:
    settings = config["settings"]
    stories: list[dict] = []
    stats = {"checked": 0, "ok": 0, "failed": [], "no_date": 0}

    for feed in config["feeds"]:
        if not feed.get("enabled"):
            continue
        stats["checked"] += 1
        try:
            entries = fetch_feed(feed)
        except Exception as exc:  # one broken feed must not stop the paper
            stats["failed"].append(feed["name"])
            print(f"[skip] {feed['name']}: {exc}", file=sys.stderr)
            continue
        stats["ok"] += 1
        for entry in entries:
            published = entry_time(entry)
            lookback = timedelta(days=max(2, settings.get("events_lookback_days", 7)))
            if published and now - lookback <= published <= now + timedelta(hours=1):
                RAW_ENTRIES.append({"title": clean_text(entry.get("title", "")), "summary": clean_text(entry.get("summary", "")),
                                    "link": entry.get("link", ""), "source": feed["name"], "published": published,
                                    "grade": feed["grade"], "microsoft_official": bool(feed.get("microsoft_official")),
                                    "age": hours_ago(published, now)})

        if feed.get("markets_only"):
            continue  # read for the Markets panel only (entries saved above)
        cutoff = now - timedelta(hours=feed.get("window_hours", settings["window_hours"]))
        excludes = settings.get("exclude_keywords", []) + feed.get("exclude_keywords", [])
        taken = 0
        for entry in entries:
            if taken >= feed.get("max_items", 4):
                break
            title = clean_text(entry.get("title", ""))
            link = entry.get("link", "")
            if not title or not link:
                continue
            published = entry_time(entry)
            if published is None:
                stats["no_date"] += 1
                continue  # freshness cannot be confirmed
            if published < cutoff or published > now + timedelta(hours=1):
                continue
            summary = clean_text(entry.get("summary", ""))
            keywords = feed.get("include_keywords")
            if keywords and not keyword_match(f"{title} {summary}", keywords):
                continue
            if excludes and keyword_match(f"{title} {summary}", excludes):
                continue
            stories.append({
                "title": title,
                "link": link,
                "summary": short_summary(summary),
                "source": feed["name"],
                "grade": feed["grade"],
                "section": feed["section"],
                "kicker": feed["kicker"],
                "tag": feed.get("tag", ""),
                "microsoft_official": bool(feed.get("microsoft_official")),
                "lead_eligible": feed.get("lead_eligible", True),
                "published": published,
                "age": hours_ago(published, now),
                "also": [],
            })
            taken += 1
    return stories, stats


# ---------------------------------------------------------------- organise

def merge_duplicates(stories: list[dict]) -> tuple[list[dict], int]:
    """Group stories about the same thing; keep the best-graded one."""
    stories = sorted(stories, key=lambda s: (GRADE_RANK[s["grade"]], -s["published"].timestamp()))
    kept: list[dict] = []
    merged = 0
    for story in stories:
        key = normalise_title(story["title"])
        for existing in kept:
            if same_story(key, normalise_title(existing["title"])):
                existing["also"].append(story["source"])
                merged += 1
                break
        else:
            kept.append(story)
    # Grade C stories stay only if nothing better covered them; label them.
    for story in kept:
        if story["grade"] == "C" and not story["tag"]:
            story["tag"] = "Unconfirmed"
    return kept, merged


def pricing_desk(stories: list[dict], keywords: list[str]) -> tuple[list[dict], list[dict]]:
    confirmed, reported = [], []
    for story in stories:
        if not keyword_match(f"{story['title']} {story['summary']}", keywords):
            continue
        if story["microsoft_official"] and story["grade"] == "A":
            confirmed.append(story)
        elif keyword_match(story["title"], ["microsoft", "azure", "copilot", "fabric", "power bi", "dynamics"]):
            reported.append(story)
    return confirmed[:3], reported[:3]


def events_desk(entries: list[dict], settings: dict, now: datetime) -> tuple[list[dict], list[dict]]:
    """Events (Microsoft, fintech and AI) and Microsoft exam voucher offers
    announced in the last few days (events_lookback_days, default 7)."""
    cutoff = now - timedelta(days=settings.get("events_lookback_days", 7))
    excludes = settings.get("exclude_keywords", [])
    events, offers = [], []
    seen: list[str] = []
    for e in sorted(entries, key=lambda x: (GRADE_RANK[x["grade"]], -x["published"].timestamp())):
        if e["published"] < cutoff:
            continue
        text = f"{e['title']} {e['summary']}"
        if excludes and keyword_match(text, excludes):
            continue
        key = normalise_title(e["title"])
        if any(same_story(key, k) for k in seen):
            continue
        about_microsoft = e["microsoft_official"] or keyword_match(text, ["microsoft", "azure", "copilot", "fabric", "ignite"])
        if about_microsoft and keyword_match(text, settings["voucher_keywords"]):
            offers.append(e)
            seen.append(key)
        elif keyword_match(text, settings["event_keywords"]) and (about_microsoft or keyword_match(text, settings["event_topic_keywords"])):
            # every Microsoft event counts; other events need a fintech, banking, AI or data topic
            e = dict(e, label="Microsoft event" if about_microsoft else "Fintech and AI event")
            events.append(e)
            seen.append(key)
    by_newest = lambda x: -x["published"].timestamp()
    return sorted(events, key=by_newest)[:6], sorted(offers, key=by_newest)[:4]


def upcoming_events(config: dict, local_now: datetime) -> list[dict]:
    """Calendar entries from config that have not finished yet."""
    today = local_now.date()
    items = []
    for ev in config.get("upcoming_events", []) or []:
        try:
            start = datetime.strptime(str(ev["start"]), "%Y-%m-%d").date()
            end = datetime.strptime(str(ev.get("end", ev["start"])), "%Y-%m-%d").date()
        except (KeyError, ValueError):
            continue
        if end < today:
            continue
        days = (start - today).days
        if days > 0:
            when = "tomorrow" if days == 1 else f"in {days} days"
        else:
            when = "happening now"
        dates = start.strftime("%d %b").lstrip("0") if start == end else f"{start.strftime('%d %b').lstrip('0')} to {end.strftime('%d %b %Y').lstrip('0')}"
        items.append({"name": ev["name"], "link": ev.get("link", ""), "note": ev.get("note", ""),
                      "dates": dates, "when": when, "start": start})
    return sorted(items, key=lambda x: x["start"])


def page_lines(raw_html: str, keywords: list[str]) -> set[str]:
    """Visible text chunks from a page that contain a keyword."""
    raw_html = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", raw_html)
    chunks = re.split(r"<[^>]+>", raw_html)
    lines = set()
    for chunk in chunks:
        text = re.sub(r"\s+", " ", html.unescape(chunk)).strip()
        if 15 <= len(text) <= 200 and keyword_match(text, keywords):
            lines.add(text)
    return lines


def watch_pages(config: dict) -> tuple[list[dict], list[str]]:
    """Return new keyword lines on watched pages since the last run."""
    previous = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    current, findings, failed = {}, [], []
    for page in config.get("page_watches", []):
        if not page.get("enabled"):
            continue
        try:
            response = requests.get(page["url"], headers=HEADERS, timeout=25)
            response.raise_for_status()
            lines = page_lines(response.text, page["keywords"])
        except Exception as exc:
            failed.append(page["name"])
            print(f"[skip page] {page['name']}: {exc}", file=sys.stderr)
            if page["name"] in previous:
                current[page["name"]] = previous[page["name"]]  # keep old baseline
            continue
        current[page["name"]] = sorted(lines)
        if page["name"] in previous:  # first run only records a baseline
            for line in sorted(lines - set(previous[page["name"]]))[:5]:
                findings.append({"page": page["name"], "text": line, "link": page["url"]})
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(current, indent=1), encoding="utf-8")
    return findings, failed


def build_sections(stories: list[dict], limits: dict) -> dict:
    sections = {name: [] for name in SECTIONS}
    for story in stories:
        sections[story["section"]].append(story)
    for name in SECTIONS:
        sections[name].sort(key=lambda s: (GRADE_RANK[s["grade"]], -s["published"].timestamp()))
        sections[name] = sections[name][: limits.get(name, 8)]
    return sections


def pick_lead(sections: dict) -> dict | None:
    """Lead = newest grade-A Microsoft announcement (blogs, not small roadmap
    or update items); otherwise the best story from the next section."""
    for name in ("microsoft", "global", "industry"):
        pool = [s for s in sections[name] if s["lead_eligible"]] or sections[name]
        candidates = [s for s in pool if s["grade"] == "A"] or pool
        if candidates:
            lead = max(candidates, key=lambda s: s["published"])
            sections[name].remove(lead)
            return lead
    return None


# ---------------------------------------------------------------- render

def main() -> None:
    config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    settings = config["settings"]
    now = datetime.now(timezone.utc)
    local = now + timedelta(hours=settings["utc_offset_hours"])

    stories, stats = collect(config, now)
    stories, merged = merge_duplicates(stories)
    confirmed, reported = pricing_desk(stories, settings["pricing_keywords"])
    events, offers = events_desk(RAW_ENTRIES, settings, now)
    coming_up = upcoming_events(config, local)
    page_news, pages_failed = watch_pages(config)
    stats["failed"] += pages_failed
    markets = build_markets(config, now, local, RAW_ENTRIES)
    stats["failed"] += markets.get("failed", [])
    sections = build_sections(stories, settings["max_per_section"])
    lead = pick_lead(sections)

    env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html", "j2"]))
    page = env.get_template("paper.html.j2").render(
        title=settings["page_title"],
        date_text=local.strftime("%A, %d %B %Y").replace(" 0", " "),
        time_text=local.strftime("%H:%M"),
        city=settings["edition_city"],
        window_hours=settings["window_hours"],
        lead=lead,
        sections=sections,
        pricing_confirmed=confirmed,
        pricing_reported=reported,
        events=events,
        offers=offers,
        coming_up=coming_up,
        page_news=page_news,
        markets=markets,
        stats=stats,
        merged=merged,
        story_count=len(stories),
    )

    (DOCS / "archive").mkdir(parents=True, exist_ok=True)
    (DOCS / "index.html").write_text(page, encoding="utf-8")
    (DOCS / "archive" / f"{local:%Y-%m-%d}.html").write_text(page, encoding="utf-8")

    top = ([lead] if lead else []) + [s for name in ("microsoft", "global", "industry") for s in sections[name]]
    summary = {
        "date": local.strftime("%A, %d %B %Y").replace(" 0", " "),
        "top": [{"title": s["title"], "link": s["link"], "grade": s["grade"], "source": s["source"]} for s in top[:3]],
        "pricing_confirmed": len(confirmed),
        "pricing_reported": len(reported),
        "events": len(events) + len(page_news),
        "next_event": f"{coming_up[0]['name']} {coming_up[0]['when']}" if coming_up else "",
        "offers": len(offers),
        "usd_open_market": next((r for r in (markets.get("fx") or {}).get("rows", []) if r["code"] == "USD"), None),
        "kmi30": next((i for i in markets.get("indices", []) if i["name"] == "KMI30"), None),
        "stories": len(stories),
        "sources_ok": stats["ok"],
        "sources_failed": stats["failed"],
    }
    (DOCS / "latest.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"Built paper: {len(stories)} stories, {stats['ok']}/{stats['checked']} sources OK, {merged} duplicates merged")
    if stats["failed"]:
        print("Failed sources: " + ", ".join(stats["failed"]))


if __name__ == "__main__":
    main()
