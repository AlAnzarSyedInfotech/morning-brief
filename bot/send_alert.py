"""Send the 10:30 email alert with the top 3 headlines and a link to the paper.

Settings come from environment variables (stored as GitHub secrets):
  SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, ALERT_TO, PAGE_URL
If any are missing, the alert is skipped and the paper is still published.
"""

from __future__ import annotations

import html
import json
import os
import smtplib
import sys
from email.message import EmailMessage
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REQUIRED = ["SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "ALERT_TO", "PAGE_URL"]


def markets_line(data: dict) -> str:
    parts = []
    usd = data.get("usd_open_market")
    if usd:
        parts.append(f"USD open market {usd['buy']:,.2f} / {usd['sell']:,.2f}")
    kmi = data.get("kmi30")
    if kmi:
        parts.append(f"KMI-30 {kmi['value']:,.2f} ({kmi['change_pct']:+.2f}%)")
    return "Markets: " + ("; ".join(parts) if parts else "see the paper")


def main() -> None:
    missing = [name for name in REQUIRED if not os.environ.get(name)]
    if missing:
        print("Alert skipped; missing settings: " + ", ".join(missing))
        return

    data = json.loads((ROOT / "docs" / "latest.json").read_text(encoding="utf-8"))
    page_url = os.environ["PAGE_URL"]

    lines_text = [f"- [{s['grade']}] {s['title']} ({s['source']})\n  {s['link']}" for s in data["top"]]
    text = (
        f"Your Morning Brief is ready: {data['date']}\n\n"
        f"Pricing and licensing: {data['pricing_confirmed']} confirmed, {data['pricing_reported']} reported\n"
        f"Events and certification: {data.get('events', 0)} events, {data.get('offers', 0)} voucher offers\n"
        f"{markets_line(data)}\n\n"
        "Top stories today:\n" + "\n".join(lines_text) +
        f"\n\nOpen today's paper: {page_url}\n"
    )

    items_html = "".join(
        f'<li style="margin-bottom:10px"><strong>[{html.escape(s["grade"])}]</strong> '
        f'<a href="{html.escape(s["link"])}">{html.escape(s["title"])}</a> '
        f'<span style="color:#4A4F58">({html.escape(s["source"])})</span></li>'
        for s in data["top"]
    )
    body_html = f"""
<div style="font-family:Segoe UI,Arial,sans-serif;color:#16181D;max-width:560px">
  <p style="color:#1F4E8C;font-weight:600;margin:0">{html.escape(data['date'])}</p>
  <h1 style="font-family:Georgia,serif;font-size:24px;margin:4px 0 16px">Your Morning Brief is ready</h1>
  <p style="background:#EFEDE6;padding:10px 12px;border-radius:6px">
    Pricing and licensing: {data['pricing_confirmed']} confirmed, {data['pricing_reported']} reported, not confirmed<br>
    Events and certification: {data.get('events', 0)} events, {data.get('offers', 0)} voucher offers<br>
    {html.escape(markets_line(data))}
  </p>
  <p style="font-weight:600;margin-bottom:6px">Top stories today</p>
  <ol style="padding-left:20px">{items_html}</ol>
  <p><a href="{html.escape(page_url)}" style="display:inline-block;background:#1F4E8C;color:#fff;padding:12px 20px;border-radius:6px;text-decoration:none;font-weight:600">Open today's paper</a></p>
  <p style="color:#4A4F58;font-size:12px">{data['stories']} stories from {data['sources_ok']} sources, last 25 hours</p>
</div>"""

    msg = EmailMessage()
    msg["Subject"] = f"The Morning Brief: {data['date']}"
    msg["From"] = os.environ["SMTP_USER"]
    msg["To"] = os.environ["ALERT_TO"]
    msg.set_content(text)
    msg.add_alternative(body_html, subtype="html")

    try:
        with smtplib.SMTP(os.environ["SMTP_HOST"], int(os.environ["SMTP_PORT"]), timeout=30) as server:
            server.starttls()
            server.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
            server.send_message(msg)
    except Exception as exc:
        print(f"Alert failed: {exc}", file=sys.stderr)
        sys.exit(1)
    print("Alert sent to " + os.environ["ALERT_TO"])


if __name__ == "__main__":
    main()
