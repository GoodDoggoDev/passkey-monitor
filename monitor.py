#!/usr/bin/env python3
"""
Passkey Hotel Booking Monitor — Midwest FurFest 2026
Monitors hotel availability and sends Telegram alerts on changes.
Designed to run as a GitHub Actions cron job (no PC needed).

Flow:
  1. Load splash page → select "Attendee" → submit form
  2. Land on date/search page → submit search with event dates
  3. Parse hotel list → compare with previous state → notify via Telegram
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
from urllib.parse import urljoin, urlparse, parse_qs, urlencode

# ── Config (from GitHub Secrets / environment) ─────────────────
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# ── Passkey URLs ───────────────────────────────────────────────
BASE_URL = "https://book.passkey.com"
EVENT_PATH = "/event/51314789/owner/3293004"
SPLASH_URL = f"{BASE_URL}{EVENT_PATH}/home"
GROUP_URL = f"{BASE_URL}{EVENT_PATH}/home/group"
LANDING_URL = f"{BASE_URL}{EVENT_PATH}/landing"
HOTELS_URL = f"{BASE_URL}{EVENT_PATH}/list/hotels"

ATTENDEE_GROUP_TYPE_ID = "221660598"

# MFF 2026 event dates
CHECKIN_DATE = "2026-12-03"
CHECKOUT_DATE = "2026-12-07"

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
    })
    return s


def extract_csrf(html):
    """Extract CSRF / XSRF token from page HTML."""
    soup = BeautifulSoup(html, "html.parser")
    token_input = soup.find("input", {"name": "_csrf"})
    if token_input and token_input.get("value"):
        return token_input["value"]
    m = re.search(r"'X-XSRF-TOKEN':\s*'([^']+)'", html)
    if m:
        return m.group(1)
    return None


def detect_page_type(html, url):
    """Figure out which page we're on based on content and URL."""
    lower_html = html.lower()
    lower_url = url.lower()

    if "/list/hotels" in lower_url:
        return "hotel_list"
    if "hotels for" in lower_html and ("rooms available" in lower_html or "sold out" in lower_html):
        return "hotel_list"
    if "select attendee type" in lower_html or "make reservation" in lower_html:
        return "splash"
    if "check in" in lower_html and "checkout" in lower_html and "search" in lower_html:
        return "date_search"
    if "/landing" in lower_url:
        return "date_search"

    return "unknown"


def find_search_form(html):
    """
    Find the date search form on the landing/date page and extract its
    action URL and any hidden fields.
    """
    soup = BeautifulSoup(html, "html.parser")
    info = {"action": None, "method": "GET", "fields": {}}

    # Look for forms with search-related fields
    for form in soup.find_all("form"):
        form_html = str(form).lower()
        # Look for forms that have date inputs or search buttons
        if any(kw in form_html for kw in ["checkin", "check-in", "check_in", "arrival", "search"]):
            info["action"] = form.get("action", "")
            info["method"] = (form.get("method", "GET")).upper()
            # Collect hidden fields
            for hidden in form.find_all("input", {"type": "hidden"}):
                name = hidden.get("name")
                value = hidden.get("value", "")
                if name:
                    info["fields"][name] = value
            # Collect field names for date inputs
            for inp in form.find_all("input"):
                name = inp.get("name", "")
                if name:
                    info["fields"].setdefault(name, inp.get("value", ""))
            return info

    return info


def fetch_hotel_page(session):
    """
    Navigate the full Passkey booking flow:
      Splash → Attendee selection → Date search → Hotel list
    Returns the hotel list HTML and final URL.
    """

    # ═══════════════════════════════════════════════════════════
    # STEP 1: Load splash page
    # ═══════════════════════════════════════════════════════════
    log("STEP 1: Loading splash page...")
    resp = session.get(SPLASH_URL, timeout=30)
    resp.raise_for_status()
    log(f"  Status: {resp.status_code} | URL: {resp.url}")

    page_type = detect_page_type(resp.text, resp.url)
    log(f"  Page type: {page_type}")

    # If we somehow landed on the hotel list already, return it
    if page_type == "hotel_list":
        log("  → Already on hotel list!")
        return resp.text, resp.url

    # Extract CSRF token
    csrf = extract_csrf(resp.text)
    if csrf:
        log(f"  CSRF token: {csrf[:12]}...")
        session.headers["X-XSRF-TOKEN"] = csrf
    else:
        log("  ⚠ No CSRF token found")

    # ═══════════════════════════════════════════════════════════
    # STEP 2: Accept cookies (non-critical)
    # ═══════════════════════════════════════════════════════════
    log("STEP 2: Accepting cookies...")
    try:
        session.post(f"{BASE_URL}/cookieBanner/hideWithSession", timeout=10)
        log("  → Done")
    except Exception:
        log("  → Failed (non-critical)")

    # ═══════════════════════════════════════════════════════════
    # STEP 3: Submit attendee type form ("Attendee")
    # ═══════════════════════════════════════════════════════════
    log("STEP 3: Selecting 'Attendee' and submitting...")
    form_data = {
        "groupTypeId": ATTENDEE_GROUP_TYPE_ID,
    }
    if csrf:
        form_data["_csrf"] = csrf

    resp = session.post(
        GROUP_URL,
        data=form_data,
        timeout=30,
        allow_redirects=True,
        headers={
            "Referer": SPLASH_URL,
            "Origin": BASE_URL,
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    resp.raise_for_status()
    log(f"  Status: {resp.status_code} | URL: {resp.url}")

    page_type = detect_page_type(resp.text, resp.url)
    log(f"  Page type: {page_type}")

    if page_type == "hotel_list":
        log("  → Got hotel list after attendee selection!")
        return resp.text, resp.url

    # Save intermediate page for debugging
    save_debug_page(resp.text, "after_attendee_select")

    # ═══════════════════════════════════════════════════════════
    # STEP 4: We should be on the date/search page now
    #         Submit search with MFF event dates
    # ═══════════════════════════════════════════════════════════
    log("STEP 4: Submitting date search...")
    log(f"  Check-in: {CHECKIN_DATE} | Checkout: {CHECKOUT_DATE}")

    # Update CSRF if the new page has a different token
    new_csrf = extract_csrf(resp.text)
    if new_csrf:
        csrf = new_csrf
        session.headers["X-XSRF-TOKEN"] = csrf

    current_url = resp.url
    current_html = resp.text

    # Try to find and submit the search form
    form_info = find_search_form(current_html)
    log(f"  Search form info: action={form_info.get('action')}, method={form_info.get('method')}, fields={list(form_info.get('fields', {}).keys())}")

    # Build search parameters - try common Passkey field names
    search_params = {}
    if form_info.get("fields"):
        search_params.update(form_info["fields"])

    # Set date values using common parameter name patterns
    date_param_names = {
        "checkin": CHECKIN_DATE,
        "checkout": CHECKOUT_DATE,
        "checkIn": CHECKIN_DATE,
        "checkOut": CHECKOUT_DATE,
        "checkInDate": CHECKIN_DATE,
        "checkOutDate": CHECKOUT_DATE,
        "arrivalDate": CHECKIN_DATE,
        "departureDate": CHECKOUT_DATE,
        "arrival": CHECKIN_DATE,
        "departure": CHECKOUT_DATE,
        "startDate": CHECKIN_DATE,
        "endDate": CHECKOUT_DATE,
    }

    # If we found form fields, match them with date values
    found_date_fields = False
    for field_name in search_params:
        lower_name = field_name.lower()
        for param_name, param_value in date_param_names.items():
            if param_name.lower() == lower_name:
                search_params[field_name] = param_value
                found_date_fields = True

    # If no date fields found in form, add common ones
    if not found_date_fields:
        search_params["checkInDate"] = CHECKIN_DATE
        search_params["checkOutDate"] = CHECKOUT_DATE

    # Add room/guest defaults
    room_params = {"rooms": "1", "numRooms": "1", "guestsPerRoom": "1", "numGuests": "1"}
    for key, val in room_params.items():
        if key not in search_params:
            search_params[key] = val

    if csrf:
        search_params["_csrf"] = csrf

    # Determine where to submit the search
    search_url = HOTELS_URL  # Default target
    if form_info.get("action"):
        search_url = urljoin(current_url, form_info["action"])

    log(f"  Submitting search to: {search_url}")
    log(f"  Parameters: {search_params}")

    # Try POST first (most common for Passkey search forms)
    resp = session.post(
        search_url,
        data=search_params,
        timeout=30,
        allow_redirects=True,
        headers={
            "Referer": current_url,
            "Origin": BASE_URL,
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    resp.raise_for_status()
    log(f"  Status: {resp.status_code} | URL: {resp.url}")

    page_type = detect_page_type(resp.text, resp.url)
    log(f"  Page type: {page_type}")

    if page_type == "hotel_list":
        log("  → Got hotel list!")
        return resp.text, resp.url

    # If POST didn't work, try GET with params
    log("  POST didn't reach hotel list, trying GET...")
    resp = session.get(
        HOTELS_URL,
        params={
            "checkInDate": CHECKIN_DATE,
            "checkOutDate": CHECKOUT_DATE,
            "rooms": "1",
            "guestsPerRoom": "1",
        },
        timeout=30,
        allow_redirects=True,
    )
    resp.raise_for_status()
    log(f"  Status: {resp.status_code} | URL: {resp.url}")

    page_type = detect_page_type(resp.text, resp.url)
    log(f"  Page type: {page_type}")

    if page_type == "hotel_list":
        log("  → Got hotel list!")
        return resp.text, resp.url

    # ═══════════════════════════════════════════════════════════
    # STEP 5: Fallback — try direct navigation to hotel list
    # ═══════════════════════════════════════════════════════════
    log("STEP 5: Fallback — trying direct hotel list URL...")
    resp = session.get(HOTELS_URL, timeout=30, allow_redirects=True)
    resp.raise_for_status()
    log(f"  Status: {resp.status_code} | URL: {resp.url}")

    save_debug_page(resp.text, "final_page")

    return resp.text, resp.url


def save_debug_page(html, label):
    """Save a page snapshot for debugging (visible in GitHub Actions logs)."""
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(separator="\n", strip=True)
    # Print first 80 lines for debugging
    lines = [l for l in text.split("\n") if l.strip()][:80]
    log(f"  ── DEBUG [{label}] page text (first 80 lines) ──")
    for line in lines:
        log(f"  | {line[:120]}")
    log(f"  ── END DEBUG [{label}] ──")


def parse_hotels(html):
    """
    Parse hotel cards from the hotel list page.

    Based on the actual page structure observed:
    - Each hotel is in a card with availability badge, name, distance, price
    - Badges say "Rooms available" or "Sold out"
    - Names are in links/headings
    - Prices show as "XXX.XX USD"
    """
    soup = BeautifulSoup(html, "html.parser")
    hotels = []

    # ── Known non-hotel strings to filter out ──────────────────
    BLACKLIST = {
        "contact information", "session warning", "session warning!",
        "manage existing reservation", "start over", "start over?",
        "multiple tabs warning", "manage cookie preferences",
        "strictly necessary", "marketing", "cookie preferences",
        "accept all cookies", "accept only essential cookies",
        "customize preferences", "show details", "hide details",
        "save preferences", "supported browsers", "privacy policy",
        "close button", "cancel", "next", "ok", "continue",
        "now accepting reservations!", "midwest furfest 2026",
        "search", "update search", "more filters",
        "hotels for midwest furfest 2026",
    }

    def is_blacklisted(name):
        return name.lower().strip() in BLACKLIST or len(name) < 4

    # ── Strategy 1: Find hotel cards by common selectors ───────
    card_selectors = [
        # Common Passkey hotel card selectors
        "[class*='hotel-card']",
        "[class*='hotelCard']",
        "[class*='property-card']",
        "[class*='propertyCard']",
        "[class*='hotel-result']",
        "[class*='hotel-item']",
        "[class*='hotelItem']",
        "[data-hotel-id]",
        "[data-property-id]",
        # Grid item patterns
        "[class*='hotel'] [class*='card']",
        "[class*='search-result']",
        "[class*='result-card']",
    ]

    hotel_elements = []
    for sel in card_selectors:
        try:
            found = soup.select(sel)
            if found and len(found) <= 30:  # Sanity check
                log(f"  Selector '{sel}' matched {len(found)} elements")
                hotel_elements = found
                break
        except Exception:
            continue

    for el in hotel_elements:
        hotel = extract_hotel_from_element(el, BLACKLIST)
        if hotel and hotel.get("name") and not is_blacklisted(hotel["name"]):
            hotels.append(hotel)

    if hotels:
        return hotels

    # ── Strategy 2: Find cards by looking for price patterns ───
    # Look for elements containing price text like "163.00 USD"
    log("  Strategy 1 found nothing, trying price-pattern strategy...")
    price_pattern = re.compile(r'\d{2,4}\.\d{2}\s*USD', re.IGNORECASE)

    # Find all elements with price text and walk up to find the card container
    for el in soup.find_all(string=price_pattern):
        # Walk up the tree to find a reasonable container
        container = el.parent
        for _ in range(8):
            if container is None or container.name in ["body", "html", None]:
                break
            # Check if this container has enough content to be a hotel card
            text = container.get_text(strip=True)
            if len(text) > 50 and price_pattern.search(text):
                # Check if there are sibling containers (indicating a list)
                siblings = container.find_parent().find_all(container.name, recursive=False) if container.find_parent() else []
                if len(siblings) >= 2:
                    # Found a list of cards!
                    log(f"  Found {len(siblings)} card-like siblings using <{container.name}> tag")
                    for sib in siblings:
                        hotel = extract_hotel_from_element(sib, BLACKLIST)
                        if hotel and hotel.get("name") and not is_blacklisted(hotel["name"]):
                            hotels.append(hotel)
                    if hotels:
                        return hotels
            container = container.parent

    # ── Strategy 3: Text-based extraction ──────────────────────
    log("  Strategy 2 found nothing, trying text extraction...")
    page_text = soup.get_text()

    # Look for hotel brand names near prices
    hotel_brands = (
        r'Holiday Inn|Hilton|Hyatt|Marriott|Sheraton|Westin|Renaissance|'
        r'DoubleTree|Hampton|Crowne Plaza|Embassy Suites|Radisson|'
        r'Best Western|Comfort|Fairfield|SpringHill|Courtyard|'
        r'Residence Inn|Staybridge|Spark by|Homewood|La Quinta|'
        r'Wyndham|InterContinental|Kimpton|W Hotel|Aloft|'
        r'Four Points|AC Hotel|Moxy|Tru by|Home2|Candlewood'
    )
    brand_matches = re.finditer(
        rf'({hotel_brands})[^\n<]{{0,80}}',
        page_text, re.IGNORECASE
    )
    for match in brand_matches:
        name = match.group(0).strip()
        # Clean up the name (stop at common delimiters)
        name = re.split(r'(?:Rooms starting|View hotel|View rates|\d{2,3}\.\d{2})', name)[0].strip()
        if name and len(name) > 5 and not is_blacklisted(name):
            hotel = {"name": name, "source": "text-match"}
            # Try to find a nearby price
            price_match = re.search(
                r'(\d{2,4}\.\d{2})\s*USD',
                page_text[match.start():match.start() + 300]
            )
            if price_match:
                hotel["price"] = f"{price_match.group(1)} USD"
            hotels.append(hotel)

    # Deduplicate by name
    seen = set()
    unique_hotels = []
    for h in hotels:
        name = h.get("name", "").lower()
        if name not in seen:
            seen.add(name)
            unique_hotels.append(h)

    return unique_hotels


def extract_hotel_from_element(el, blacklist):
    """Extract hotel info from a single card element."""
    hotel = {}

    # ── Name: try headings, then links, then any prominent text ──
    for sel in ["h2", "h3", "h4", "h5",
                "a[class*='name']", "a[class*='title']",
                "[class*='hotel-name']", "[class*='hotelName']",
                "[class*='property-name']", "[class*='propertyName']",
                "a"]:
        name_el = el.select_one(sel)
        if name_el:
            text = name_el.get_text(strip=True)
            if text and len(text) > 3 and text.lower() not in blacklist:
                hotel["name"] = text
                break

    # ── Availability badge ───────────────────────────────────────
    for sel in ["[class*='badge']", "[class*='avail']", "[class*='status']",
                "[class*='sold']", "[class*='inventory']", "[class*='tag']",
                "[class*='label']"]:
        badge_el = el.select_one(sel)
        if badge_el:
            text = badge_el.get_text(strip=True)
            if text and any(kw in text.lower() for kw in ["room", "avail", "sold", "wait", "full"]):
                hotel["status"] = text
                break

    # Also check for "Rooms available" or "Sold out" anywhere in the card text
    if "status" not in hotel:
        card_text = el.get_text(strip=True).lower()
        if "rooms available" in card_text:
            hotel["status"] = "Rooms available"
        elif "sold out" in card_text:
            hotel["status"] = "Sold out"
        elif "waitlist" in card_text:
            hotel["status"] = "Waitlist"

    # ── Price ────────────────────────────────────────────────────
    price_match = re.search(r'(\d{2,4}\.\d{2})\s*USD', el.get_text())
    if price_match:
        hotel["price"] = f"{price_match.group(1)} USD"

    # ── Distance ─────────────────────────────────────────────────
    dist_match = re.search(r'([\d.]+)\s*miles?\s*(?:to\s*event)?', el.get_text(), re.IGNORECASE)
    if dist_match:
        hotel["distance"] = f"{dist_match.group(1)} miles"

    return hotel


def compute_content_hash(html):
    """
    Hash page content after removing dynamic elements (tokens, sessions, etc.)
    so only real content changes trigger alerts.
    """
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup.find_all(["script", "style", "noscript", "link", "meta"]):
        tag.decompose()
    for inp in soup.find_all("input", {"type": "hidden"}):
        inp.decompose()

    text = soup.get_text(separator=" ", strip=True)
    text = re.sub(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', '', text)
    text = re.sub(r'\b\d{10,}\b', '', text)
    text = re.sub(r'\s+', ' ', text).strip()

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def detect_changes(old_state, new_hash, new_hotels):
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
    old_names = {h["name"].lower() for h in old_hotels if h.get("name")}
    new_names = {h["name"].lower() for h in new_hotels if h.get("name")}

    changes["new_hotels"] = [
        h for h in new_hotels if h.get("name") and h["name"].lower() not in old_names
    ]
    changes["removed_hotels"] = [
        h for h in old_hotels if h.get("name") and h["name"].lower() not in new_names
    ]

    old_by_name = {h["name"].lower(): h for h in old_hotels if h.get("name")}
    for h in new_hotels:
        name = h.get("name", "").lower()
        if name in old_by_name:
            old_h = old_by_name[name]
            diffs = {}
            if h.get("status") != old_h.get("status"):
                diffs["status"] = (old_h.get("status", "?"), h.get("status", "?"))
            if h.get("price") != old_h.get("price"):
                diffs["price"] = (old_h.get("price", "?"), h.get("price", "?"))
            if diffs:
                changes["changed_hotels"].append({"name": h["name"], "diffs": diffs})

    return changes


def has_meaningful_changes(changes):
    return (
        changes["new_hotels"]
        or changes["removed_hotels"]
        or changes["changed_hotels"]
        or changes["hash_changed"]
    )


def format_message(changes, hotels, is_first_run):
    book_url = HOTELS_URL

    if is_first_run:
        msg = "🏨 <b>Hotel Monitor Started — MFF 2026</b>\n\n"
        if hotels:
            msg += f"Currently tracking <b>{len(hotels)}</b> hotel(s):\n\n"
            for h in hotels:
                status = h.get("status", "")
                status_icon = "🟢" if "avail" in status.lower() else "🔴" if "sold" in status.lower() else "⚪"
                msg += f"{status_icon} <b>{h.get('name', '?')}</b>\n"
                if h.get("price"):
                    msg += f"   💰 {h['price']}\n"
                if h.get("distance"):
                    msg += f"   📍 {h['distance']}\n"
                if status:
                    msg += f"   {status}\n"
                msg += "\n"
        else:
            msg += "No hotels listed yet — monitoring for when they appear.\n\n"
        msg += f"Checking every 5 min. You'll be notified of any changes."
        msg += f"\n\n🔗 <a href='{book_url}'>Booking page</a>"
        return msg

    msg = "🚨 <b>HOTEL UPDATE — MFF 2026</b>\n\n"

    for h in changes.get("new_hotels", []):
        msg += f"🆕 <b>NEW HOTEL:</b> {h.get('name', '?')}\n"
        if h.get("price"):
            msg += f"   💰 {h['price']}\n"
        if h.get("status"):
            msg += f"   {h['status']}\n"
        msg += "\n"

    for h in changes.get("removed_hotels", []):
        msg += f"❌ <b>REMOVED:</b> {h.get('name', '?')}\n\n"

    for c in changes.get("changed_hotels", []):
        msg += f"🔄 <b>CHANGED:</b> {c['name']}\n"
        for field, (old_val, new_val) in c.get("diffs", {}).items():
            label = field.capitalize()
            if "sold" in new_val.lower() if isinstance(new_val, str) else False:
                msg += f"   {label}: {old_val} → 🔴 <b>{new_val}</b>\n"
            elif "avail" in new_val.lower() if isinstance(new_val, str) else False:
                msg += f"   {label}: {old_val} → 🟢 <b>{new_val}</b>\n"
            else:
                msg += f"   {label}: {old_val} → <b>{new_val}</b>\n"
        msg += "\n"

    if (changes.get("hash_changed")
            and not changes.get("new_hotels")
            and not changes.get("removed_hotels")
            and not changes.get("changed_hotels")):
        msg += "📝 The booking page content has changed!\n"
        msg += "(Exact changes couldn't be determined — check the page)\n\n"

    msg += f"🔗 <a href='{book_url}'>GO BOOK NOW!</a>"
    return msg


def send_telegram(message):
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

    if not TELEGRAM_BOT_TOKEN:
        log("ERROR: TELEGRAM_BOT_TOKEN not set!")
        sys.exit(1)
    if not TELEGRAM_CHAT_ID:
        log("ERROR: TELEGRAM_CHAT_ID not set!")
        sys.exit(1)

    old_state = load_state()
    is_first_run = not old_state.get("content_hash")
    log(f"First run: {is_first_run}")
    if not is_first_run:
        log(f"Last check: {old_state.get('last_check', 'unknown')}")
        log(f"Known hotels: {len(old_state.get('hotels', []))}")

    # ── Fetch the hotel list page ──────────────────────────────
    session = create_session()
    try:
        html, final_url = fetch_hotel_page(session)
    except Exception as e:
        log(f"ERROR: Failed to fetch page — {e}")
        if not is_first_run:
            send_telegram(
                f"⚠️ <b>Monitor Error</b>\n\n"
                f"Couldn't reach the booking page:\n<code>{e}</code>\n\n"
                f"Will retry in 5 minutes."
            )
        sys.exit(1)

    if not html:
        log("ERROR: Empty page received")
        sys.exit(1)

    log(f"Final URL: {final_url}")
    log(f"Page size: {len(html)} bytes")

    # ── Detect what page we ended up on ────────────────────────
    page_type = detect_page_type(html, final_url)
    log(f"Final page type: {page_type}")

    if page_type != "hotel_list":
        log(f"⚠ WARNING: Did not reach hotel list page! Got '{page_type}' instead.")
        save_debug_page(html, "unexpected_final_page")

    # ── Parse hotels ───────────────────────────────────────────
    hotels = parse_hotels(html)
    content_hash = compute_content_hash(html)

    log(f"Content hash: {content_hash[:16]}...")
    log(f"Hotels found: {len(hotels)}")
    for h in hotels:
        log(f"  → {h}")

    new_state = {
        "content_hash": content_hash,
        "hotels": hotels,
        "last_check": datetime.now(timezone.utc).isoformat(),
        "final_url": final_url,
        "page_type": page_type,
    }

    # ── Compare and notify ─────────────────────────────────────
    if is_first_run:
        log("First run — saving baseline and sending startup message")
        send_telegram(format_message(None, hotels, True))
        save_state(new_state)
    else:
        changes = detect_changes(old_state, content_hash, hotels)
        if has_meaningful_changes(changes):
            log("🚨 CHANGES DETECTED!")
            for key in ["new_hotels", "removed_hotels", "changed_hotels"]:
                if changes[key]:
                    log(f"  {key}: {changes[key]}")
            send_telegram(format_message(changes, hotels, False))
            save_state(new_state)
        else:
            log("No changes detected ✓")
            old_state["last_check"] = new_state["last_check"]
            save_state(old_state)

    log("Done!")


if __name__ == "__main__":
    main()
