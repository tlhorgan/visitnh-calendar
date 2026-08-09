from __future__ import annotations

import hashlib
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from dateutil import parser as dtparser
from icalendar import Calendar, Event
from playwright.sync_api import sync_playwright

BASE = "https://www.visitnh.gov"
CALENDAR_URL = f"{BASE}/things-to-do/events-calendar"
OUTPUT = Path("visitnh.ics")

# How far into the future to collect events.
MONTHS_AHEAD = 12

def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()

def extract_labeled_value(text: str, label: str, next_labels: list[str]) -> str:
    pattern = rf"\b{re.escape(label)}\b\s*(.*?)(?=" + "|".join(
        rf"\b{re.escape(x)}\b" for x in next_labels
    ) + r"|$)"
    m = re.search(pattern, text, re.I | re.S)
    return clean(m.group(1)) if m else ""

def parse_date_range(value: str):
    value = clean(value)
    if not value:
        return None, None

    # Examples:
    # Saturday, September 26, 2026
    # Friday, July 17, 2026 - Monday, July 20, 2026
    parts = re.split(r"\s+-\s+", value, maxsplit=1)
    try:
        start = dtparser.parse(parts[0], fuzzy=True).date()
        end = dtparser.parse(parts[1], fuzzy=True).date() if len(parts) > 1 else start
        return start, end
    except Exception:
        return None, None

def parse_time_range(value: str, start_date, end_date):
    value = clean(value)
    if not value or not start_date:
        return None, None

    # Common Visit NH formats: "10 am - 3 pm", "7:30 pm", "8:30 AM - 2:30 PM"
    parts = re.split(r"\s+-\s+", value, maxsplit=1)
    try:
        start_time = dtparser.parse(parts[0], fuzzy=True).time()
        start_dt = datetime.combine(start_date, start_time)
        if len(parts) > 1:
            end_time = dtparser.parse(parts[1], fuzzy=True).time()
            end_dt = datetime.combine(end_date or start_date, end_time)
            if end_dt <= start_dt and (end_date or start_date) == start_date:
                end_dt += timedelta(days=1)
        else:
            end_dt = start_dt + timedelta(hours=1)
        return start_dt, end_dt
    except Exception:
        return None, None

def collect_event_urls(page):
    urls = set()
    cutoff = datetime.now().date() + timedelta(days=31 * MONTHS_AHEAD)

    # Use date filters when possible. If Visit NH changes the form names, the
    # fallback still scans whatever events are currently rendered.
    page.goto(CALENDAR_URL, wait_until="networkidle", timeout=90000)
    page.wait_for_timeout(2500)

    # Scroll repeatedly so lazy-loaded results have a chance to appear.
    last_count = 0
    for _ in range(30):
        for href in page.locator('a[href*="/things-to-do/events-calendar/"]').evaluate_all(
            "(els) => els.map(e => e.href)"
        ):
            p = urlparse(href)
            if p.netloc.endswith("visitnh.gov") and p.path.rstrip("/") != "/things-to-do/events-calendar":
                urls.add(href.split("#")[0].split("?")[0])

        page.mouse.wheel(0, 5000)
        page.wait_for_timeout(700)

        # Click common "load more" controls if present.
        for label in ["Load More", "Show More", "More Events", "Next"]:
            btn = page.get_by_text(label, exact=False)
            if btn.count() and btn.first.is_visible():
                try:
                    btn.first.click(timeout=1500)
                    page.wait_for_timeout(1200)
                except Exception:
                    pass

        if len(urls) == last_count:
            # Don't quit immediately; some pages load slowly.
            pass
        last_count = len(urls)

    return sorted(urls)

def scrape_event(page, url):
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(500)
    html = page.content()
    soup = BeautifulSoup(html, "html.parser")

    h1 = soup.find("h1")
    title = clean(h1.get_text(" ", strip=True)) if h1 else clean(soup.title.string if soup.title else "")
    body = clean(soup.get_text(" ", strip=True))

    date_text = extract_labeled_value(body, "Date", ["Time", "Location", "Price"])
    time_text = extract_labeled_value(body, "Time", ["Location", "Price", "Directions", "Website"])
    location = extract_labeled_value(body, "Location", ["Price", "Directions", "Website", "Register"])

    start_date, end_date = parse_date_range(date_text)
    if not start_date:
        return None

    start_dt, end_dt = parse_time_range(time_text, start_date, end_date)

    desc = ""
    meta = soup.find("meta", attrs={"name": "description"})
    if meta and meta.get("content"):
        desc = clean(meta["content"])
    if not desc:
        og = soup.find("meta", attrs={"property": "og:description"})
        if og and og.get("content"):
            desc = clean(og["content"])

    return {
        "title": title or "Visit NH Event",
        "url": url,
        "location": location,
        "description": desc,
        "start_date": start_date,
        "end_date": end_date,
        "start_dt": start_dt,
        "end_dt": end_dt,
    }

def build_calendar(events):
    cal = Calendar()
    cal.add("prodid", "-//Visit NH Events Calendar//EN")
    cal.add("version", "2.0")
    cal.add("x-wr-calname", "Visit NH Events")
    cal.add("x-wr-timezone", "America/New_York")

    for item in events:
        ev = Event()
        ev.add("summary", item["title"])
        uid = hashlib.sha256(item["url"].encode()).hexdigest()[:24] + "@visitnh-calendar"
        ev.add("uid", uid)
        ev.add("dtstamp", datetime.utcnow())
        ev.add("url", item["url"])

        if item["location"]:
            ev.add("location", item["location"])
        description = item["description"]
        if description:
            description += "\n\n"
        description += f"Source: {item['url']}"
        ev.add("description", description)

        if item["start_dt"]:
            # Floating local time; Proton will interpret with the calendar timezone.
            ev.add("dtstart", item["start_dt"])
            ev.add("dtend", item["end_dt"])
        else:
            ev.add("dtstart", item["start_date"])
            # ICS all-day DTEND is exclusive.
            ev.add("dtend", item["end_date"] + timedelta(days=1))

        cal.add_component(ev)

    OUTPUT.write_bytes(cal.to_ical())

def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(
            viewport={"width": 1440, "height": 1200},
            user_agent="Mozilla/5.0 (compatible; VisitNHCalendar/1.0)"
        )

        urls = collect_event_urls(page)
        print(f"Found {len(urls)} event URLs")

        events = []
        for i, url in enumerate(urls, 1):
            try:
                event = scrape_event(page, url)
                if event:
                    events.append(event)
                    print(f"[{i}/{len(urls)}] {event['title']}")
            except Exception as exc:
                print(f"[{i}/{len(urls)}] ERROR {url}: {exc}")

        browser.close()

    events.sort(key=lambda x: (x["start_date"], x["title"]))
    build_calendar(events)
    print(f"Wrote {OUTPUT} with {len(events)} events")

if __name__ == "__main__":
    main()
