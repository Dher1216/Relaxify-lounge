# Relaxify Lounge — Accounting & Business Management System

A double-entry accounting web app built specifically for Relaxify Lounge, using your
actual Chart of Accounts, logo, and brand colors. Python (FastAPI) backend, server-rendered
frontend (no separate JS build step), PostgreSQL in production / SQLite for local testing.

## What's included

- Login (bcrypt-hashed passwords), session-based auth, two starter accounts (see "First login")
- Dashboard, Receipts & Disbursements entry with true double-entry validation
- Trial Balance, Income Statement, Balance Sheet, Statement of Changes in Equity,
  Statement of Cash Flows, Notes to Financial Statements, Daily Transaction Report
- Excel export (.xlsx) and print-friendly views on every report
- Account Codes management, User management (roles: ADMIN/ACCOUNTANT/STAFF/VIEWER)
- Full audit trail (logins, transactions, void reasons, account/user changes, report generation)
- Transactions are voided, never deleted — history is preserved

## First login

The source code no longer contains any passwords. On a **brand-new** database, the two starter accounts (`Dher` - ADMIN, `OdessaSan` - ACCOUNTANT) are created with:

- the passwords you set in the `INITIAL_ADMIN_PASSWORD` / `INITIAL_ACCOUNTANT_PASSWORD` environment variables, **or**
- if those aren't set, a random one-time password that is printed once in the startup log (on Render: the service's **Logs** tab).

Log in, then go to Others -> Users -> "Change My Password" and set your own. Existing users are never modified by the seed, so this doesn't affect a database that is already running.

---

## Staff "Record a Sale" screen

STAFF-role users only see a simplified sale screen (`/staff/sale`) built for weak or no signal: each sale is saved on the phone first, then sent in the background and retried automatically. A unique token per sale means it can never be recorded twice.

- **Discounts:** PWD/Senior (20%, requires an ID number) and Promotional (custom %). Discounts post to accounts 4201 / 4202 under "Sales Discounts" (4200).
- **PWD/Senior ID is mandatory.** The Submit button will not save a PWD/Senior sale with a blank (or spaces-only) ID, and a promo needs a percentage. The sale is simply not recorded until it is filled in.
- **Cancel an unsent sale:** every sale still waiting to send shows a **Cancel sale** button. Tap it to remove the sale from the phone.
- **Rejected sales clear themselves.** If the server refuses a sale because it breaks a rule (for example an old copy of the form let a blank ID through), the phone shows "NOT RECORDED: reason", removes it from the queue so it does **not** come back the next day, and the server logs it in the **Audit Trail** as "REJECTED unsent sale" so the admin/accountant can see what was entered and re-enter it. Sales waiting only because of a temporary problem (no signal, server busy) keep retrying.
- An "A required account is missing or inactive" message means account 4201/4202 (or 1000, 4101-4103) was deactivated or its code changed under Account Codes - reactivate it or restore the code.

---

## Running locally (optional, for testing before you deploy)

Requires Python 3.11+.

```bash
cd relaxify-lounge
pip install -r requirements.txt
python -m app.seed        # creates relaxify.db (SQLite), imports your 52 accounts, creates the 2 users
uvicorn app.main:app --reload
```

Visit http://localhost:8000 — log in with the one-time password printed by the seed command (see "First login").

Re-running `python -m app.seed` is safe; it only adds accounts/users that don't already exist.

---

## Deploying to Render + Neon (so it's reachable from any device)

This app runs on Render (free web hosting, no expiration) with the database on **Neon**
(free Postgres, no 30-day expiration) instead of Render's own built-in Postgres — Render's
free databases expire after 30 days, which isn't something you want for real bookkeeping
data. Neon's free tier doesn't run on that kind of timer.

### Step 1 — Create your Neon database

1. Go to https://neon.tech and sign up (free, no credit card required).
2. Create a new project — name it something like `relaxify-lounge`.
3. Neon gives you a **connection string** that looks like:
   `postgresql://user:password@ep-xxxx.neon.tech/dbname?sslmode=require`
   Copy this — you'll paste it into Render in Step 3.

### Step 2 — Push this project to GitHub

```bash
cd relaxify-lounge
git init
git add .
git commit -m "Initial Relaxify Lounge accounting system"
```

Create a new empty repository on github.com (don't initialize it with a README), then:

```bash
git remote add origin https://github.com/YOUR_USERNAME/relaxify-lounge.git
git branch -M main
git push -u origin main
```

### Step 3 — Deploy on Render using the included blueprint

1. Go to https://dashboard.render.com and sign in (or create a free account).
2. Click **New +** → **Blueprint**.
3. Connect your GitHub account and select the `relaxify-lounge` repo.
4. Render reads `render.yaml` automatically and provisions the free web service, with an
   auto-generated `SECRET_KEY`. It will prompt you for `DATABASE_URL` since that's marked
   `sync: false` — paste in your Neon connection string here.
   (If it doesn't prompt during setup, go to the service's **Environment** tab afterward and
   add `DATABASE_URL` there manually.)
5. Click **Apply** / **Create Web Service**. Render will build and deploy — a few minutes
   the first time.

### Step 4 — Seed the database (one-time)

Once the service is live, open its **Shell** tab in the Render dashboard and run:

```bash
python -m app.seed
```

This creates your 52 Chart-of-Accounts entries and the two initial users directly in your
Neon database.

### Step 5 — You're live

Render gives you a URL like `https://relaxify-lounge.onrender.com` — this works from any
phone, tablet, or computer with internet access. Bookmark it on each device.

**Free-tier notes:**
- Render's free web service spins down after ~15 minutes of inactivity and takes ~30–60
  seconds to wake up on the next visit — not a data-loss issue, just a delay. It does not
  expire outright.
- Neon's free database does not expire on a fixed timer, but very long stretches of
  inactivity can cause it to auto-suspend (it wakes up automatically on the next connection,
  same as the web service does).
- Still back up periodically regardless (see the note in "Architecture notes" below) —
  "doesn't expire" isn't the same guarantee as "can never be lost."

---

## Architecture notes / what to know before relying on this for real bookkeeping

- **Money math** uses `DECIMAL(18,2)` throughout — never floating point.
- **Every transaction save is atomic** — a transaction and all its lines commit together or
  not at all (standard SQLAlchemy session behavior here).
- **The Statement of Cash Flows** uses a simplified classification method (it looks at the
  account on the other side of each cash-affecting line to decide Operating vs Investing vs
  Financing). This is flagged in the report itself. If your transactions get more complex
  (loans, equipment purchases financed over time, etc.), this logic may need refinement —
  it's isolated in one function (`cash_flow_statement` in `app/accounting.py`) specifically
  so it's easy to adjust later without touching anything else.
- **No period-closing entries**: rather than requiring a formal month-end/year-end close,
  the Balance Sheet folds cumulative net income into equity automatically as "Current
  Earnings" as of any date you pick. This is standard practice for small-business books that
  don't do formal closing entries, but if you later want real closing entries (zeroing out
  revenue/expense accounts into Retained Earnings at year end), that would be a deliberate
  addition, not a bug fix.
- **Security honesty**: passwords are bcrypt-hashed in the database (not plaintext), sessions
  use a signed cookie, and all validation happens server-side (not just in the browser JS).
  That said, this app does not yet have HTTPS enforcement, rate-limiting on login attempts,
  or CSRF tokens on forms — reasonable next hardening steps before this holds anything
  beyond your own internal books.
- **Browser close protection**: as explained earlier, no web app can fully block the browser's
  X button — this app uses the browser's native "leave site?" prompt when a transaction form
  has unsaved changes, which is the strongest guarantee a browser allows.

## What wasn't built (structured for, but not implemented)

Per your spec's section on future expansion, these are *not* built, but nothing above blocks
adding them later: inventory, payroll, customer/CRM records, chair/session usage tracking,
receivables/payables aging, tax reports, bank reconciliation, multi-branch support, or
automated backups (Render's paid Postgres tiers include automated backups; the free tier does
not — export the Trial Balance / Daily Transaction Report to Excel periodically as a manual
backup in the meantime).

## Project structure

```
relaxify-lounge/
  app/
    main.py            FastAPI app, mounts routers + static files
    database.py         SQLAlchemy engine/session (SQLite locally, Postgres on Render)
    models.py            All database tables
    auth.py               Password hashing, session helpers, role permissions
    accounting.py    Double-entry validation + all report calculations
    coa_seed.py         Your 52 accounts, extracted from the uploaded file
    seed.py                One-time setup script
    routers/              One file per feature area (transactions, reports, accounts, users, audit)
    templates/           HTML pages (black-and-gold theme)
    static/                 CSS, your logo, your background image
  requirements.txt
  Procfile                  Tells Render how to start the app
  render.yaml              Render blueprint (web service only — DATABASE_URL points to your Neon database)
```
