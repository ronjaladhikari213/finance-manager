# Personal Finance Manager

Your Python finance manager, rebuilt as a phone-friendly web app (installable
like a real app, works offline for browsing, no app-store account needed).
Same 9 features as your script, plus accounts so you and a friend can each
have your own private data on the same app.

## What changed from your script, and why

Your original script was a single-user, single-run terminal program. To work
"on your phone" and be shareable with a friend, it needed to become a
client/server web app — a browser (your phone) talking to one running server,
with each person's data kept separate. Along the way I fixed the bugs your
script had:

| # | Your code | Bug | Fix |
|---|---|---|---|
| 1 | `json.dump()` in case 4 | Missing required `f` argument — crashes immediately when you set a budget | Budget is saved properly, per month |
| 2 | `sum` in case 5's print | Built-in `sum` printed instead of your `summation` variable | Uses the actual computed total |
| 3 | case 6 | `rem_budget` only exists if you happened to add an expense in case 2 first in the *same run* — otherwise `NameError` | Budget remaining is always computed fresh from real totals |
| 4 | `financial_summary(...)` | Divides `(income-expenses)/income` with no guard — `ZeroDivisionError` if you have $0 income | Shows "—" instead of crashing when income is 0 |
| 5 | case 5/6 categories | `"food"` and `"Food"` were tracked as different categories | Categories are normalized (`Food`, `food`, `FOOD` all merge) |
| 6 | case 8 (delete) | Deletes **every** transaction matching type+category+amount, not just one | Each transaction has its own ID; delete removes exactly the one you tap |
| 7 | Everything | No `try/except` on file I/O — a missing/corrupt `finance.json` or `config.json` crashes the whole program on startup | Server-side storage (SQLite database) with proper error handling |
| 8 | Whole program | One shared `transactions` list — you and a friend would see and edit each other's data | Every user has a private login; your data and your friend's are fully separate |

Feature-for-feature, this covers everything your menu had:

1. Add income → **+ button → Income**
2. Add expense → **+ button → Expense**
3. View transactions → **History tab**
4. Set monthly budget → **Home → Set/edit budget**
5. View spending by category → **Categories tab**
6. View financial summary → **Home tab** (balance, income, expenses, saving rate)
7. Search transactions → **History tab** (by text, type, or amount range)
8. Delete transaction → **✕ on any transaction row**
9. Save & Exit → nothing to do — every change saves to the server immediately

Plus: a CSV export (**More → Download my data**) as a backup/Excel option,
and it works as an installable app icon on your phone.

## How it works (so you can explain/defend this if asked)

- **Backend:** Python + Flask. One process serves both the web page and a
  small JSON API (`/api/...`) for adding, listing, searching, and deleting
  transactions, and for budget/summary numbers.
- **Database:** SQLite by default (a single file, `finance.db`, zero setup —
  same idea as your `finance.json`, but a real database: concurrent-safe,
  can't get corrupted mid-write, and supports proper querying). Optionally
  Postgres (see **Deploying so a friend can use it** below) for hosts whose
  free tier wipes local files on redeploy.
- **Accounts:** passwords are hashed (never stored in plain text) using
  Werkzeug's `generate_password_hash`/`check_password_hash` (industry-standard
  PBKDF2). Login state uses a signed, HTTP-only session cookie. Every
  transaction/budget row is tagged with a `user_id`, and every query filters
  by `WHERE user_id = <you>` — this is what keeps your data and your friend's
  apart even though you're using the same app.
- **Frontend:** plain HTML/CSS/JavaScript (no framework/build step) styled as
  a mobile app: bottom tab bar, a floating **+** button, slide-up sheets for
  adding data, light/dark mode, and safe-area padding so it looks right
  behind the iPhone notch / Android gesture bar.
- **"Installable on a phone" (PWA):** a `manifest.webmanifest` + a service
  worker (`sw.js`) let the phone offer "Add to Home Screen", after which it
  opens full-screen with its own icon, like a native app. It still needs the
  internet to load/save data (it's not a local-only app) — the service worker
  just caches the *app shell* so it opens instantly and can show a friendly
  offline message instead of a blank page if your connection drops.
- **Security basics covered:** hashed passwords, CSRF tokens on every
  data-changing request, per-user data isolation, SQL injection prevented via
  parameterized queries everywhere, and a login attempt limiter (8 tries per
  5 minutes) to slow down password guessing.

## Running it yourself (5 minutes, on your computer)

You need Python 3.10+ installed.

```bash
cd finance_app
./run_local.sh
```

This creates a virtual environment, installs Flask, and starts the server.
Open the URL it prints (`http://localhost:5000`) in your browser. Sign up
with any username/password — that account is yours.

**To use it from your phone on the same Wi-Fi**, the script also prints a
second URL like `http://192.168.1.23:5000` — open that on your phone's
browser, sign up/log in, then use your browser's "Add to Home Screen" option
to install it. (This only works while your computer is on and running the
server, and both devices are on the same network — fine for testing, not for
handing to a friend elsewhere. See the next section for that.)

## Deploying so a friend (anywhere) can use it

For a friend on a different network to use it, the app needs to run on a
server that's always on — not your laptop. **Render.com** has a free tier
that works well for this. Two things to know before you start:

- Render's **free** plan has an *ephemeral filesystem*: any local file,
  including a SQLite database, gets wiped every time the service restarts or
  redeploys. So for a deployment that keeps your data, use a small free
  Postgres database instead (steps below) — the app supports both out of the
  box via the `DATABASE_URL` environment variable, no code changes needed.
- If you'd rather stick with SQLite, you can, but it requires a *paid* Render
  plan with a persistent disk attached (`render.yaml` has this path
  documented, commented out, if you go that route later).

### Steps

1. **Push this folder to a GitHub repo** (Render deploys from GitHub).
2. **Create a free Postgres database** — [Neon](https://neon.tech) is a good
   option (free forever, no card required): sign up, create a project, and
   copy the connection string it gives you (starts with `postgres://`).
3. **On [Render](https://render.com):** New → Blueprint → pick your repo.
   It reads `render.yaml` automatically and creates the web service.
4. In the Render dashboard for the new service, go to **Environment** and
   paste your Neon connection string as the value for `DATABASE_URL`.
5. Deploy. Render gives you a URL like `https://finance-manager-xyz.onrender.com`
   — send that to your friend. Each of you signs up for your own account the
   first time you visit it.

Free Render web services "spin down" after 15 minutes of no traffic and take
~30–60 seconds to wake back up on the next visit — normal for a free tier,
not a bug.

### Currency

Amounts are shown as `Rs 1,234.00` by default (edit the `CURRENCY` environment
variable — e.g. to `NPR`, `$`, or `€` — to change the label; it's just a
display prefix, no conversion happens).

## Project structure

```
finance_app/
  app.py                  Flask app: routes, validation, business logic
  db.py                   Database adapter (SQLite by default, Postgres via DATABASE_URL)
  templates/index.html    The single page (auth screen + app shell)
  static/style.css        All styling (mobile-first, light/dark mode)
  static/app.js           All frontend logic (no framework)
  static/manifest.webmanifest, static/sw.js, static/icons/   "Install on phone" support
  test_app.py             Automated backend test suite (63 checks)
  requirements.txt, Procfile, render.yaml, run_local.sh, .gitignore
```

## Testing

`test_app.py` is an automated test suite (uses Flask's test client — no
browser needed) covering all 9 original features, the bugs fixed above,
multi-user data isolation, and basic security (CSRF, SQL-injection strings,
XSS payloads stored as inert text). Run it with:

```bash
pip install -r requirements.txt
python3 test_app.py
```
