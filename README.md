# 🏨 MFF 2026 Hotel Monitor Bot

Monitors the [Midwest FurFest 2026 Passkey booking page](https://book.passkey.com/event/51314789/owner/3293004/list/hotels) every 5 minutes and sends you a Telegram message when hotels become available or anything changes.

**Runs 24/7 on GitHub's servers for free — no PC needed.**

---

## ⚡ Quick Setup (5 minutes)

### Step 1 — Create a new GitHub repo

1. Go to [github.com/new](https://github.com/new)
2. Name it: `passkey-monitor` (or whatever you want)
3. Set it to **Private**
4. **Don't** check any boxes (no README, no .gitignore)
5. Click **Create repository**

### Step 2 — Upload these files

On the repo page, click **"uploading an existing file"** link, then drag and drop **ALL** the files from this folder:

```
C:\Users\Admin\.gemini\antigravity\scratch\passkey-monitor\
```

Make sure the folder structure looks like this in your repo:
```
├── .github/
│   └── workflows/
│       └── monitor.yml
├── .gitignore
├── hotel_state.json
├── monitor.py
├── requirements.txt
└── README.md
```

> ⚠️ **Important:** The `.github` folder might be hidden on Windows.  
> In File Explorer, click **View** → check **Hidden items** to see it.

Click **Commit changes**.

### Step 3 — Add your Telegram secrets

1. In your repo, go to **Settings** → **Secrets and variables** → **Actions**
2. Click **New repository secret**
3. Add these two secrets:

| Name | Value |
|------|-------|
| `TELEGRAM_BOT_TOKEN` | `8871228109:AAEw7_KWUahMtcLOHIQD7iwj0x5a8Z7BBr8` |
| `TELEGRAM_CHAT_ID` | `5397005458` |

### Step 4 — Enable Actions & do a test run

1. Go to the **Actions** tab in your repo
2. If prompted, click **"I understand my workflows, go ahead and enable them"**
3. Click **"Passkey Hotel Monitor"** in the left sidebar
4. Click **"Run workflow"** → **"Run workflow"** (green button)
5. Wait ~30 seconds, then check your Telegram — you should get a startup message!

### 🎉 That's it!

The bot will now run every 5 minutes automatically. You'll get a Telegram message whenever hotels appear or availability changes.

---

## 📱 What the notifications look like

**First run (startup confirmation):**
```
🏨 Hotel Monitor Started — MFF 2026

No hotels listed yet — monitoring for when they appear.
Checking every 5 min. You'll be notified of any changes.

🔗 Booking page
```

**When hotels change:**
```
🚨 HOTEL UPDATE — MFF 2026

🆕 NEW HOTEL: Hyatt Regency O'Hare — $159/night
🔄 CHANGED: Donald E. Stephens Convention Center
   Availability: Sold Out → Available

🔗 GO BOOK NOW!
```

---

## ❓ FAQ

**Q: Is this really free?**  
A: Yes. GitHub gives free accounts 2,000 Actions minutes/month. This bot uses ~150 minutes/month.

**Q: How often does it check?**  
A: Every 5 minutes. GitHub Actions cron may occasionally delay by 1-2 minutes.

**Q: How do I stop it?**  
A: Go to **Actions** → **Passkey Hotel Monitor** → click the `...` menu → **Disable workflow**.

**Q: How do I change the check frequency?**  
A: Edit `.github/workflows/monitor.yml` and change `*/5` to `*/10` for every 10 minutes, etc.

**Q: I'm not getting notifications?**  
A: Make sure you've messaged your bot at least once on Telegram (just send it "hi"). Bots can't message you first until you've initiated a conversation.
