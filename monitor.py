#!/usr/bin/env python3
"""
Passkey Hotel Booking Monitor — Midwest FurFest 2026
Monitors hotel availability and sends Telegram alerts on changes.
Designed to run as a GitHub Actions cron job (no PC needed).
"""

import requests
from bs4 import BeautifulSoup
import json
import os
import sys
import hashlib
import random
import re
from datetime import datetime, timezone

# ── Config (from GitHub Secrets / environment) ─────────────────
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# ── Passkey URLs ───────────────────────────────────────────────
BASE_URL = "https://book.passkey.com"
EVENT_PATH = "/event/51314789/owner/3293004"
SPLASH_URL = f"{BASE_URL}{EVENT_PATH}/home"
GROUP_URL = f"{BASE_URL}{EVENT_PATH}/home/group"
HOTELS_URL = f"{BASE_URL}{EVENT_PATH}/list/hotels"

ATTENDEE_GROUP_TYPE_ID = "221660598"
STATE_FILE = "hotel_state.json"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:132.0) Gecko/20100101 Firefox/132.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0",
]


def log(msg):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{ts}] {msg}")


def create_session():
    """Create a fresh HTTP session with realistic browser headers."""
    s = requests.Session()
    ua = random.choice(USER_AGENTS)
    s.headers.update({
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
    })
    return s


def extract_csrf(html):
    """Extract CSRF / XSRF token from page HTML."""
    # Try hidden input first
    soup = BeautifulSoup(html, "html.parser")
    token_input = soup.find("input", {"name": "_csrf"})
    if token_input and token_input.get("value"):
        return token_input["value"]
    # Try JS header setup
    m = re.search(r"'X-XSRF-TOKEN':\s*'([^']+)'", html)
    if m:
        return m.group(1)
    return None


def fetch_page_content(session):
    """
    Navigate the full Passkey booking flow and return the hotel list HTML.
    Flow: splash page → select attendee → (dates) → hotel list
    """
    # ── Step 1: Load splash page ───────────────────────────────
    log("Step 1: Loading splash page...")
    resp = session.get(SPLASH_URL, timeout=30)
    resp.raise_for_status()
    log(f"  → {resp.status_code} | URL: {resp.url}")

    csrf = extract_csrf(resp.text)
    if not csrf:
        log("  ⚠ No CSRF token found on splash page")
        # Try the hotels URL directly (might work without form flow)
        resp = session.get(HOTELS_URL, timeout=30)
        return resp.text, resp.url

    log(f"  → CSRF: {csrf[:12]}...")
    session.headers["X-XSRF-TOKEN"] = csrf

    # ── Step 2: Accept cookies (silently, don't fail if it errors) ─
    log("Step 2: Accepting cookies...")
    try:
        session.post(
            f"{BASE_URL}/cookieBanner/hideWithSession",
            timeout=10,
            headers={"X-XSRF-TOKEN": csrf},
        )
    except Exception:
        log("  → Cookie acceptance failed (non-critical, continuing)")

    # ── Step 3: Submit attendee type ───────────────────────────
    log("Step 3: Selecting 'Attendee' type...")
    form_data = {
        "_csrf": csrf,
        "groupTypeId": ATTENDEE_GROUP_TYPE_ID,
    }
    resp = session.post(
        GROUP_URL,
        data=form_data,
        timeout=30,
        allow_redirects=True,
        headers={
            "Referer": SPLASH_URL,
            "Origin": BASE_URL,
            "Content-Type": "application/x-www-form-urlencoded",
            "Sec-Fetch-Site": "same-origin",
        },
    )
    resp.raise_for_status()
    log(f"  → {resp.status_code} | URL: {resp.url}")

    current_html = resp.text
    current_url = resp.url

    # ── Step 4: Check if we're on hotels page already ──────────
    if "/list/hotels" in current_url:
        log("  → Already on hotel list page!")
        return current_html, current_url

    # If we're on a date selection or other intermediate page,
    # try to navigate to hotels directly
    log("Step 4: Navigating to hotel list...")
    resp = session.get(HOTELS_URL, timeout=30, allow_redirects=True)
    resp.raise_for_status()
    log(f"  → {resp.status_code} | URL: {resp.url}")

    return resp.text, resp.url


def parse_hotels(html):
    """
    Extract hotel data from page HTML.
    Tries multiple CSS selector strategies since we may not know the exact structure.
    """
    soup = BeautifulSoup(html, "html.parser")
    hotels = []

    # ── Strategy 1: Look for hotel card-like containers ────────
    selectors = [
        ".hotel-card", ".property-card", ".hotel-item",
        "[class*='hotel-list'] > div", "[class*='property-list'] > div",
        ".hotel-result", ".property-result",
        "[data-hotel]", "[data-property]",
        ".hotel", ".property",
    ]

    hotel_elements = []
    for sel in selectors:
        found = soup.select(sel)
        if found:
            log(f"  → Found {len(found)} elements with selector '{sel}'")
            hotel_elements = found
            break

    for el in hotel_elements:
        hotel = {}
        # Name
        for name_sel in ["h2", "h3", "h4", ".hotel-name", ".property-name",
                         "[class*='name']", "[class*='title']"]:
            name_el = el.select_one(name_sel)
            if name_el:
                text = name_el.get_text(strip=True)
                if text and len(text) > 2:
                    hotel["name"] = text
                    break

        # Price
        for price_sel in [".price", ".rate", "[class*='price']", "[class*='rate']",
                          "[class*='cost']"]:
            price_el = el.select_one(price_sel)
            if price_el:
                hotel["price"] = price_el.get_text(strip=True)
                break

        # Availability
        for avail_sel in ["[class*='avail']", "[class*='sold']", "[class*='status']",
                          "[class*='inventory']", ".availability", ".sold-out"]:
            avail_el = el.select_one(avail_sel)
            if avail_el:
                hotel["availability"] = avail_el.get_text(strip=True)
                break

        # Distance / location
        for loc_sel in ["[class*='distance']", "[class*='location']", "[class*='address']"]:
            loc_el = el.select_one(loc_sel)
            if loc_el:
                hotel["location"] = loc_el.get_text(strip=True)
                break

        if hotel.get("name"):
            hotels.append(hotel)

    # ── Strategy 2: Look for text patterns if no cards found ───
    if not hotels:
        # Look for any text that looks like hotel names with prices
        text = soup.get_text()
        # Common convention hotel patterns
        hotel_patterns = re.findall(
            r'((?:Hyatt|Marriott|Hilton|Sheraton|Westin|Renaissance|DoubleTree|'
            r'Hampton|Holiday Inn|Crowne Plaza|Embassy|Radisson|Best Western|'
            r'Comfort|Fairfield|SpringHill|Courtyard|Residence Inn|Donald E|'
            r'Convention|Stephens|O\'Hare|Rosemont)[^<\n]{0,80})',
            text, re.IGNORECASE
        )
        for match in hotel_patterns:
            name = match.strip()
            if name and len(name) > 3:
                hotels.append({"name": name, "source": "text-match"})

    return hotels


def compute_content_hash(html):
    """
    Hash the meaningful page content, stripping out dynamic tokens/sessions
    so that only real content changes trigger notifications.
    """
    soup = BeautifulSoup(html, "html.parser")

    # Remove elements that change every page load
    for tag in soup.find_all(["script", "style", "noscript", "link", "meta"]):
        tag.decompose()
    for inp in soup.find_all("input", {"type": "hidden"}):
        inp.decompose()

    text = soup.get_text(separator=" ", strip=True)

    # Strip dynamic values
    text = re.sub(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', '', text)
    text = re.sub(r'\b\d{10,}\b', '', text)  # timestamps
    text = re.sub(r'\s+', ' ', text).strip()

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_state():
    """Load previous state from JSON file."""
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state):
    """Save state to JSON file."""
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def detect_changes(old_state, new_hash, new_hotels):
    """Compare old and new state, return dict of changes."""
    changes = {
        "hash_changed": False,
        "new_hotels": [],
        "removed_hotels": [],
        "changed_hotels": [],
    }

    old_hash = old_state.get("content_hash", "")
    if old_hash and old_hash != new_hash:
        changes["hash_changed"] = True

    old_hotels = old_state.get("hotels", [])
    old_names = {h["name"] for h in old_hotels if h.get("name")}
    new_names = {h["name"] for h in new_hotels if h.get("name")}

    # New hotels
    changes["new_hotels"] = [
        h for h in new_hotels if h.get("name") and h["name"] not in old_names
    ]

    # Removed hotels
    changes["removed_hotels"] = [
        h for h in old_hotels if h.get("name") and h["name"] not in new_names
    ]

    # Changed hotels (price or availability)
    old_by_name = {h["name"]: h for h in old_hotels if h.get("name")}
    for h in new_hotels:
        name = h.get("name")
        if name and name in old_by_name:
            old_h = old_by_name[name]
            diffs = {}
            if h.get("availability") != old_h.get("availability"):
                diffs["availability"] = (old_h.get("availability", "?"), h.get("availability", "?"))
            if h.get("price") != old_h.get("price"):
                diffs["price"] = (old_h.get("price", "?"), h.get("price", "?"))
            if diffs:
                changes["changed_hotels"].append({"name": name, "diffs": diffs})

    return changes


def has_meaningful_changes(changes):
    """Return True if any real changes were detected."""
    return (
        changes["hash_changed"]
        or changes["new_hotels"]
        or changes["removed_hotels"]
        or changes["changed_hotels"]
    )


def format_message(changes, hotels, is_first_run, page_url):
    """Build a Telegram-friendly HTML message."""
    book_url = HOTELS_URL

    if is_first_run:
        msg = "🏨 <b>Hotel Monitor Started — MFF 2026</b>\n\n"
        if hotels:
            msg += f"Currently tracking <b>{len(hotels)}</b> hotel(s):\n"
            for h in hotels:
                msg += f"  • {h.get('name', '?')}"
                if h.get("price"):
                    msg += f" — {h['price']}"
                if h.get("availability"):
                    msg += f" ({h['availability']})"
                msg += "\n"
        else:
            msg += "No hotels listed yet — monitoring for when they appear.\n"
        msg += f"\nChecking every 5 min. You'll be notified of any changes."
        msg += f"\n\n🔗 <a href='{book_url}'>Booking page</a>"
        return msg

    msg = "🚨 <b>HOTEL UPDATE — MFF 2026</b>\n\n"

    for h in changes.get("new_hotels", []):
        msg += f"🆕 <b>NEW HOTEL:</b> {h.get('name', '?')}"
        if h.get("price"):
            msg += f" — {h['price']}"
        if h.get("availability"):
            msg += f" ({h['availability']})"
        msg += "\n"

    for h in changes.get("removed_hotels", []):
        msg += f"❌ <b>REMOVED:</b> {h.get('name', '?')}\n"

    for c in changes.get("changed_hotels", []):
        msg += f"🔄 <b>CHANGED:</b> {c['name']}\n"
        for field, (old_val, new_val) in c.get("diffs", {}).items():
            label = field.capitalize()
            msg += f"   {label}: {old_val} → <b>{new_val}</b>\n"

    # If hash changed but we couldn't determine specific changes
    if (changes.get("hash_changed")
            and not changes.get("new_hotels")
            and not changes.get("removed_hotels")
            and not changes.get("changed_hotels")):
        msg += "📝 The booking page content has changed!\n"
        msg += "(Exact changes couldn't be determined — check the page)\n"

    msg += f"\n🔗 <a href='{book_url}'>GO BOOK NOW!</a>"
    return msg


def send_telegram(message):
    """Send message via Telegram Bot API."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        resp = requests.post(url, json=payload, timeout=15)
        if resp.ok:
            log("✅ Telegram notification sent!")
        else:
            log(f"❌ Telegram error: {resp.status_code} — {resp.text}")
        return resp.ok
    except Exception as e:
        log(f"❌ Telegram send failed: {e}")
        return False


def main():
    log("=" * 50)
    log("Passkey Hotel Monitor — MFF 2026")
    log("=" * 50)

    # ── Validate config ────────────────────────────────────────
    if not TELEGRAM_BOT_TOKEN:
        log("ERROR: TELEGRAM_BOT_TOKEN not set!")
        sys.exit(1)
    if not TELEGRAM_CHAT_ID:
        log("ERROR: TELEGRAM_CHAT_ID not set!")
        sys.exit(1)

    # ── Load previous state ────────────────────────────────────
    old_state = load_state()
    is_first_run = not old_state.get("content_hash")
    log(f"First run: {is_first_run}")

    # ── Fetch the booking page ─────────────────────────────────
    session = create_session()
    try:
        html, final_url = fetch_page_content(session)
    except requests.exceptions.RequestException as e:
        log(f"ERROR: Failed to fetch page — {e}")
        # Only alert on errors if this isn't the very first run
        if not is_first_run:
            send_telegram(f"⚠️ <b>Monitor Error</b>\n\nCouldn't reach the booking page:\n<code>{e}</code>\n\nWill retry in 5 minutes.")
        sys.exit(1)

    if not html:
        log("ERROR: Empty page received")
        sys.exit(1)

    log(f"Final URL: {final_url}")
    log(f"Page size: {len(html)} bytes")

    # ── Analyze page ───────────────────────────────────────────
    content_hash = compute_content_hash(html)
    hotels = parse_hotels(html)

    log(f"Content hash: {content_hash[:16]}...")
    log(f"Hotels found: {len(hotels)}")
    for h in hotels:
        log(f"  → {h}")

    # ── Save a debug snapshot of the page text ─────────────────
    soup = BeautifulSoup(html, "html.parser")
    page_text = soup.get_text(separator="\n", strip=True)

    new_state = {
        "content_hash": content_hash,
        "hotels": hotels,
        "last_check": datetime.now(timezone.utc).isoformat(),
        "final_url": final_url,
        "page_preview": page_text[:3000],
    }

    # ── Compare and notify ─────────────────────────────────────
    if is_first_run:
        log("First run — saving baseline and sending startup message")
        msg = format_message(None, hotels, True, final_url)
        send_telegram(msg)
        save_state(new_state)
    else:
        changes = detect_changes(old_state, content_hash, hotels)
        if has_meaningful_changes(changes):
            log("🚨 CHANGES DETECTED!")
            log(f"  Hash changed: {changes['hash_changed']}")
            log(f"  New hotels: {changes['new_hotels']}")
            log(f"  Removed: {changes['removed_hotels']}")
            log(f"  Changed: {changes['changed_hotels']}")
            msg = format_message(changes, hotels, False, final_url)
            send_telegram(msg)
            save_state(new_state)
        else:
            log("No changes detected ✓")
            # Update timestamp only
            old_state["last_check"] = new_state["last_check"]
            save_state(old_state)

    log("Done!")


if __name__ == "__main__":
    main()
