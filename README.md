# Passkey Hotel Monitor

[![Python 3.12](https://img.shields.io/badge/Python-3.12+-3776ab.svg?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![Playwright](https://img.shields.io/badge/Playwright-Chromium-2ea44f.svg?style=flat-square&logo=playwright&logoColor=white)](https://playwright.dev)
[![Telegram](https://img.shields.io/badge/Telegram-Bot%20API-229ED9.svg?style=flat-square&logo=telegram&logoColor=white)](https://core.telegram.org/bots/api)
[![Platform](https://img.shields.io/badge/Platform-Ubuntu%2024.04%20%7C%20GitHub%20Actions-555555.svg?style=flat-square)](https://github.com/GoodDoggoDev/passkey-monitor)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=flat-square)](LICENSE)

Autonomous monitoring engine that tracks convention hotel blocks on **Passkey** and sends instant Telegram push alerts the second rooms become available.

Built by furs, for furs. 100% free and open-source.

---

## [ Architecture & Background ]

Passkey convention reservation portals (such as Midwest FurFest) present specific challenges for automated tracking:
- Sessions are killed after **10 idle minutes** with CSRF token expirations.
- Property availability is rendered through **client-side JavaScript** (basic `curl` or `requests` scrapers only retrieve empty container markup).
- Cancellations and inventory expansions release at odd hours with zero advance warning.

This bot uses **Playwright** to navigate the reservation flow with a headless Chromium instance: resolves CSRF tokens, inputs arrival and departure dates, awaits single-page DOM hydration, diffs the live inventory against local state snapshots, and sends notifications via Telegram webhook.

---

## [ Quickstart ]

### Method A: Ubuntu 24.04 Server (24/7 Daemon)

Runs continuous checks every 5 minutes in the background:

```bash
# 1. Update system & install dependencies
sudo apt update && sudo apt install -y python3 python3-pip python3-venv git

# 2. Clone the repository
git clone https://github.com/GoodDoggoDev/passkey-monitor.git
cd passkey-monitor

# 3. Create virtual environment & install requirements
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
playwright install chromium --with-deps

# 4. Set credentials in start.sh
nano start.sh
# Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID, then save (Ctrl+O, Enter, Ctrl+X)

# 5. Launch the daemon
chmod +x start.sh
nohup ./start.sh > monitor.log 2>&1 &

# 6. View live output
tail -f monitor.log
```

#### Process Management
```bash
# View live logs:
tail -f monitor.log

# Stop the monitor:
pkill -f start.sh

# Check if process is running:
ps aux | grep monitor
```

---

### Method B: GitHub Actions (Cloud Cron)

Runs on GitHub-hosted Ubuntu runners with zero server overhead:

1. **Fork this repository** to your account (`https://github.com/GoodDoggoDev/passkey-monitor`).
2. Go to **Settings** &rarr; **Secrets and variables** &rarr; **Actions** &rarr; click **New repository secret**.
3. Add your secrets:

| Secret Name | Example Value | Description |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | `1234567890:ABCdefGhIJKlmNoPQRsTUVwxyZ_123456` | API token from `@BotFather` |
| `TELEGRAM_CHAT_ID` | `987654321` | Numeric ID from `@userinfobot` |

4. Go to the **Actions** tab, enable workflows, and click **Run workflow** to test.

---

## [ Configuration ]

Parameters can be adjusted in `monitor.py` and `start.sh`:

```python
# monitor.py
EVENT_URL = "https://book.passkey.com/event/51314789/owner/3293004/home"
CHECKIN_DATE = "12/03/2026"
CHECKOUT_DATE = "12/07/2026"
```

To modify check intervals, edit the `sleep` duration in `start.sh`:
* `sleep 300` &mdash; 5 minutes (standard, recommended to avoid rate limits)
* `sleep 120` &mdash; 2 minutes (rush hours)
* `sleep 60` &mdash; 1 minute (high-frequency)

---

## [ Telegram Notification Previews ]

**Inventory Change Alert:**
```
[ALERT] HOTEL LIST CHANGED — MFF 2026

Active Properties (4):
  * Holiday Inn Chicago O'Hare Airport
  * Sonesta Chicago O'Hare Airport Rosemont
  * Spark by Hilton Des Plaines Chicago O'Hare
  * Staybridge Suites Chicago O'Hare - Rosemont

Reserve: https://book.passkey.com/event/51314789/owner/3293004/home
```

**Cycle Heartbeat:**
```
[STATUS] MFF 2026 Hotel Monitor

No new hotels — same list (4 properties active).
Next check in 5 minutes.
```

---

## [ Tech Stack ]

- **Python 3.12**
- **Playwright** &mdash; Headless Chromium automation
- **Telegram Bot API** &mdash; Real-time push delivery
- **JSON Snapshot Engine** &mdash; Differential state reconciliation

---

## [ Author ]

Maintained by **GoodDoggoLuna** ([@GoodDoggoDev](https://github.com/GoodDoggoDev))  
*First major engineering project — built for the community.*
