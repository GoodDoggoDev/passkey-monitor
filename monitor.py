#!/usr/bin/env python3
"""
Passkey Hotel Booking Monitor — Midwest FurFest 2026
Monitors hotel availability and sends Telegram alerts on changes.
Designed to run as a GitHub Actions cron job (no PC needed).

Uses Playwright (headless browser) because the Passkey hotel list
is a JavaScript SPA that loads hotel data via XHR/fetch calls.
"""

import json
import os
import sys
import re
import hashlib
import requests as http_requests
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

# ── Config (from GitHub Secrets / environment) ─────────────────
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8871228109:AAEw7_KWUahMtcLOHIQD7iwj0x5a8Z7BBr8")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "5397005458")

# ── Passkey URLs ───────────────────────────────────────────────
BASE_URL = "https://book.passkey.com"
EVENT_PATH = "/event/51314789/owner/3293004"
SPLASH_URL = f"{BASE_URL}{EVENT_PATH}/home"

# MFF 2026 event dates
CHECKIN_DATE = "12/03/2026"
CHECKOUT_DATE = "12/07/2026"

STATE_FILE = "hotel_state.json"


def log(msg):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{ts}] {msg}")


def fetch_hotels_with_browser():
    """
    Use a headless browser to navigate the full Passkey booking flow
    and extract hotel data from the rendered page.

    Flow:
      1. Load splash page
      2. Select "Attendee" → click "Make reservation"
      3. Fill in dates → click "Search"
      4. Wait for hotel list to load
      5. Extract hotel data from rendered DOM
    """
    hotels = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        )
        page = context.new_page()

        try:
            # ── STEP 1: Load splash page ──────────────────────
            log("STEP 1: Loading splash page...")
            page.goto(SPLASH_URL, wait_until="domcontentloaded", timeout=30000)
            log(f"  URL: {page.url}")

            # Accept cookies if banner appears
            try:
                accept_btn = page.locator("#acceptAllBtn")
                if accept_btn.is_visible(timeout=3000):
                    accept_btn.click()
                    log("  Accepted cookies")
            except Exception:
                log("  No cookie banner (or already accepted)")

            # ── STEP 2: Select "Attendee" and submit ──────────
            log("STEP 2: Selecting 'Attendee' and clicking 'Make reservation'...")

            # Select "Attendee" from dropdown
            page.select_option("#groupTypeId", value="221660598")
            page.wait_for_timeout(500)

            # Click "Make reservation"
            page.click("#submit-btn")
            log("  Submitted attendee form")

            # Wait for navigation to landing page
            page.wait_for_load_state("domcontentloaded", timeout=15000)
            log(f"  Landed on: {page.url}")

            # ── STEP 3: Select dates using calendar picker ──────
            log("STEP 3: Selecting dates via calendar picker...")
            page.wait_for_timeout(2000)

            # -- Click the Check-in input box to open calendar --
            log("  Opening check-in calendar...")
            try:
                # The check-in box is an input near the "Check-in" label
                # Try clicking directly on the input area
                checkin_input = page.locator("input").first
                # Find all inputs and click the first empty one (the check-in field)
                inputs = page.locator("input[type='text'], input:not([type])")
                for i in range(min(inputs.count(), 6)):
                    inp = inputs.nth(i)
                    try:
                        if inp.is_visible(timeout=500):
                            val = inp.input_value()
                            if not val or val.strip() == "":
                                inp.click()
                                log(f"  ✓ Clicked check-in input (input #{i})")
                                break
                    except Exception:
                        continue
            except Exception as e:
                log(f"  ⚠ Error clicking check-in: {e}")

            # Wait for calendar popup
            page.wait_for_timeout(1500)

            # -- Click day 3 in the calendar --
            log("  Clicking day 3 (check-in)...")
            day3_clicked = False
            try:
                # The calendar shows day numbers as clickable elements
                # Find all elements with text "3" and click the one that's
                # exactly "3" (not 13, 23, 30, 31)
                candidates = page.locator("text=/^3$/")
                count = candidates.count()
                log(f"  Found {count} elements matching exact '3'")
                for i in range(count):
                    el = candidates.nth(i)
                    try:
                        if el.is_visible(timeout=300):
                            el.click()
                            day3_clicked = True
                            log(f"  ✓ Clicked day 3 (match #{i})")
                            break
                    except Exception:
                        continue
            except Exception as e:
                log(f"  ⚠ Could not click day 3: {e}")

            page.wait_for_timeout(1500)

            # -- Now select checkout date (Dec 7) --
            log("  Selecting checkout date (Dec 7)...")

            # The checkout calendar might auto-open, or we need to click the checkout box
            # Try clicking the checkout input first
            try:
                inputs = page.locator("input[type='text'], input:not([type])")
                for i in range(min(inputs.count(), 6)):
                    inp = inputs.nth(i)
                    try:
                        if inp.is_visible(timeout=500):
                            val = inp.input_value()
                            # Checkout input: either empty, or the second visible input
                            # The check-in should now be filled (showing Dec 3)
                            if not val or val.strip() == "":
                                inp.click()
                                log(f"  ✓ Clicked checkout input (input #{i})")
                                break
                    except Exception:
                        continue
            except Exception as e:
                log(f"  ⚠ Error clicking checkout: {e}")

            page.wait_for_timeout(1500)

            # Click day 7 in the calendar
            log("  Clicking day 7 (checkout)...")
            day7_clicked = False
            try:
                candidates = page.locator("text=/^7$/")
                count = candidates.count()
                log(f"  Found {count} elements matching exact '7'")
                for i in range(count):
                    el = candidates.nth(i)
                    try:
                        if el.is_visible(timeout=300):
                            el.click()
                            day7_clicked = True
                            log(f"  ✓ Clicked day 7 (match #{i})")
                            break
                    except Exception:
                        continue
            except Exception as e:
                log(f"  ⚠ Could not click day 7: {e}")

            page.wait_for_timeout(1000)

            # -- Click Search button --
            log("  Clicking Search...")
            search_clicked = False
            for sel in ["button:has-text('Search')", "a:has-text('Search')"]:
                try:
                    btn = page.locator(sel).first
                    if btn.is_visible(timeout=2000):
                        btn.click()
                        search_clicked = True
                        log(f"  ✓ Clicked Search ({sel})")
                        break
                except Exception:
                    continue

            if not search_clicked:
                log("  ⚠ Could not click Search")

            # ── STEP 4: Wait for hotel list to load ───────────
            log("STEP 4: Waiting for hotel list to load...")
            try:
                page.wait_for_selector(
                    "text=Hotels for, text=Rooms available, text=Sold out",
                    timeout=20000,
                )
                log("  ✓ Hotel list content detected!")
            except PWTimeout:
                log("  ⚠ Timeout — checking page anyway...")

            # Extra time for all hotel cards to render
            page.wait_for_timeout(5000)
            log(f"  Final URL: {page.url}")

            # ── STEP 5: Extract hotel data from DOM ───────────
            log("STEP 5: Extracting hotel data...")

            # Get the full page text for debugging
            page_text = page.inner_text("body")
            log(f"  Page text length: {len(page_text)} chars")

            # Try to extract hotels using JavaScript in the page
            hotels = page.evaluate("""() => {
                const hotels = [];

                // Strategy 1: Look for elements that contain hotel info patterns
                // Find all elements that contain a price like "$XXX" or "XXX.XX USD"
                const allElements = document.querySelectorAll('*');
                const pricePattern = /\\$\\d{2,4}|\\d{2,4}\\.\\d{2}\\s*USD/;
                const hotelContainers = new Set();

                for (const el of allElements) {
                    // Look for elements with "Rooms available" or "Sold out" badges
                    const text = el.textContent || '';
                    if ((text.includes('Rooms available') || text.includes('Sold out') || text.includes('Waitlist'))
                        && pricePattern.test(text)
                        && text.length < 2000  // Not too large (not the whole page)
                        && text.length > 30    // Not too small
                        && !hotelContainers.has(el.parentElement)) {

                        // This might be a hotel card - check if it has siblings (other cards)
                        const parent = el.parentElement;
                        if (parent) {
                            const siblings = parent.querySelectorAll(':scope > ' + el.tagName);
                            if (siblings.length >= 2) {
                                // Found a list of cards!
                                siblings.forEach(sib => hotelContainers.add(sib));
                            } else {
                                hotelContainers.add(el);
                            }
                        }
                    }
                }

                // Extract data from each container
                for (const container of hotelContainers) {
                    const text = container.textContent || '';
                    const hotel = {};

                    // Find hotel name — usually in a heading or link
                    const nameEl = container.querySelector('h1, h2, h3, h4, h5, a[href*="hotel"]');
                    if (nameEl) {
                        hotel.name = nameEl.textContent.trim();
                    }

                    // If no heading found, try to get the first significant text
                    if (!hotel.name) {
                        const links = container.querySelectorAll('a');
                        for (const link of links) {
                            const linkText = link.textContent.trim();
                            if (linkText.length > 5 && linkText.length < 80
                                && !linkText.includes('View') && !linkText.includes('Select')
                                && !linkText.includes('Show') && !linkText.includes('map')) {
                                hotel.name = linkText;
                                break;
                            }
                        }
                    }

                    // Status
                    if (text.includes('Rooms available')) hotel.status = 'Rooms available';
                    else if (text.includes('Sold out')) hotel.status = 'Sold out';
                    else if (text.includes('Waitlist')) hotel.status = 'Waitlist';

                    // Price
                    const priceMatch = text.match(/(\\d{2,4}\\.\\d{2})\\s*USD/);
                    if (priceMatch) hotel.price = priceMatch[1] + ' USD';
                    const dollarMatch = text.match(/\\$(\\d{2,4})/);
                    if (!hotel.price && dollarMatch) hotel.price = '$' + dollarMatch[1];

                    // Distance
                    const distMatch = text.match(/([\\.\\d]+)\\s*miles?/i);
                    if (distMatch) hotel.distance = distMatch[1] + ' miles';

                    if (hotel.name && hotel.name.length > 3) {
                        hotels.push(hotel);
                    }
                }

                // Strategy 2: If Strategy 1 found nothing, try a broader text search
                if (hotels.length === 0) {
                    const bodyText = document.body.innerText;
                    const hotelBrands = [
                        'Aloft', 'Courtyard', 'Crowne Plaza', 'Hilton Garden Inn',
                        'Holiday Inn', 'Hyatt Centric', 'Hyatt Regency', 'Loews',
                        'Marriott Suites', 'Marriott', 'Sonesta', 'Spark by Hilton',
                        'Staybridge Suites', 'Staybridge', 'Hampton', 'DoubleTree',
                        'Embassy Suites', 'Radisson', 'Best Western', 'Comfort',
                        'Fairfield', 'SpringHill', 'Residence Inn', 'Homewood',
                    ];
                    for (const brand of hotelBrands) {
                        if (bodyText.includes(brand)) {
                            // Find the full name by looking at the surrounding text
                            const idx = bodyText.indexOf(brand);
                            const snippet = bodyText.substring(idx, idx + 100);
                            const nameMatch = snippet.match(/^([^\\n]+)/);
                            const name = nameMatch ? nameMatch[1].trim() : brand;
                            hotels.push({ name: name, source: 'text-search' });
                        }
                    }
                }

                return hotels;
            }""")

            log(f"  Hotels extracted: {len(hotels)}")
            for h in hotels:
                log(f"    → {h}")

            # If we still found nothing, dump page text for debugging
            if not hotels:
                lines = [l.strip() for l in page_text.split("\n") if l.strip()][:100]
                log("  ── DEBUG: Page text (first 100 lines) ──")
                for line in lines:
                    log(f"  | {line[:150]}")
                log("  ── END DEBUG ──")

            # Get page content hash for change detection
            content_hash = hashlib.sha256(page_text.encode("utf-8")).hexdigest()

        except Exception as e:
            log(f"ERROR during browser navigation: {e}")
            raise
        finally:
            browser.close()

    # Deduplicate hotels by name
    seen = set()
    unique = []
    for h in hotels:
        name = h.get("name", "").lower().strip()
        if name and name not in seen and len(name) > 3:
            seen.add(name)
            unique.append(h)

    return unique, content_hash


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
    book_url = f"{BASE_URL}{EVENT_PATH}/list/hotels"

    if is_first_run:
        msg = "🏨 <b>Hotel Monitor Started — MFF 2026</b>\n\n"
        if hotels:
            msg += f"Currently tracking <b>{len(hotels)}</b> hotel(s):\n\n"
            for h in hotels:
                status = h.get("status", "")
                if "avail" in status.lower():
                    icon = "🟢"
                elif "sold" in status.lower():
                    icon = "🔴"
                elif "wait" in status.lower():
                    icon = "🟡"
                else:
                    icon = "⚪"
                msg += f"{icon} <b>{h.get('name', '?')}</b>\n"
                if h.get("price"):
                    msg += f"   💰 {h['price']}\n"
                if h.get("distance"):
                    msg += f"   📍 {h['distance']}\n"
                if status:
                    msg += f"   {status}\n"
                msg += "\n"
        else:
            msg += "No hotels listed yet — monitoring for when they appear.\n\n"
        msg += "Checking every 5 min. You'll be notified of any changes."
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
            new_str = str(new_val)
            if "sold" in new_str.lower():
                msg += f"   {label}: {old_val} → 🔴 <b>{new_val}</b>\n"
            elif "avail" in new_str.lower():
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
        resp = http_requests.post(url, json=payload, timeout=15)
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

    # ── Fetch hotels using headless browser ────────────────────
    try:
        hotels, content_hash = fetch_hotels_with_browser()
    except Exception as e:
        log(f"ERROR: {e}")
        if not is_first_run:
            send_telegram(
                f"⚠️ <b>Monitor Error</b>\n\n"
                f"Failed to check hotels:\n<code>{str(e)[:200]}</code>\n\n"
                f"Will retry in 5 minutes."
            )
        sys.exit(1)

    log(f"Content hash: {content_hash[:16]}...")
    log(f"Hotels found: {len(hotels)}")

    new_state = {
        "content_hash": content_hash,
        "hotels": hotels,
        "last_check": datetime.now(timezone.utc).isoformat(),
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
