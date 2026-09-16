"""
Run once (locally or via Render's shell) to initialize the database:

    python -m app.seed

Safe to re-run: it only inserts rows that don't already exist. This also runs
automatically on every app startup (see main.py), so in normal use you never
need to run this by hand.
"""
import datetime
from .database import Base, engine, SessionLocal
from .models import User, Account, AuditLog, ChairRate, Setting
from .auth import hash_password
from .coa_seed import COA_SEED

INITIAL_USERS = [
    # username, password, role
    ("Dher", "Derps1216", "ADMIN"),
    ("OdessaSan", "Teebuskuipee16", "ACCOUNTANT"),
]

# Sub-accounts to add under an existing parent, keyed by the PARENT's code.
# Each entry: (child_code, child_name)
SUB_ACCOUNTS = {
    "4100": [  # Service Revenue
        ("4101", "Service Revenue - Deluxe Chair"),
        ("4102", "Service Revenue - King Chair"),
        ("4103", "Service Revenue - Add-ons"),
    ],
    "4200": [  # Sales Discounts (renamed from "Discounts Given")
        ("4201", "PWD/Senior Discount"),
        ("4202", "Promotional Discount"),
    ],
}

# If the parent's name still matches its original seeded name, rename it to this
# (only renames if untouched, so it never clobbers a manual rename you've made).
PARENT_RENAMES = {
    "4200": ("Discounts Given", "Sales Discounts"),
}

CHAIR_RATES = [
    # chair_type, duration_minutes, price
    ("Deluxe", 15, 50), ("Deluxe", 30, 100), ("Deluxe", 45, 150), ("Deluxe", 60, 200),
    ("King", 10, 45), ("King", 15, 60), ("King", 30, 110), ("King", 45, 160), ("King", 60, 210),
]

DEFAULT_SETTINGS = {
    "eye_massager_short_price": "20",   # add-on price for sessions <=15 min
    "eye_massager_short_threshold": "15",
    "eye_massager_free_threshold": "30",  # sessions >= this many minutes: free
}


def run():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        # --- Chart of Accounts (top-level, from the uploaded template) ---
        existing_codes = {a.code for a in db.query(Account).all()}
        added_accounts = 0
        for row in COA_SEED:
            if row["code"] in existing_codes:
                continue
            db.add(Account(
                code=row["code"], name=row["name"], account_type=row["account_type"],
                category=row.get("category"), normal_balance=row["normal_balance"],
                notes=row.get("notes"), is_active=True,
            ))
            added_accounts += 1
        db.flush()

        # --- Parent renames (only if untouched from the original seed) ---
        for code, (old_name, new_name) in PARENT_RENAMES.items():
            acc = db.query(Account).filter(Account.code == code).first()
            if acc and acc.name == old_name:
                acc.name = new_name
        db.flush()

        # --- Sub-accounts under existing parents ---
        existing_codes = {a.code for a in db.query(Account).all()}
        added_sub_accounts = 0
        for parent_code, children in SUB_ACCOUNTS.items():
            parent = db.query(Account).filter(Account.code == parent_code).first()
            if not parent:
                continue
            for child_code, child_name in children:
                if child_code in existing_codes:
                    continue
                db.add(Account(
                    code=child_code, name=child_name, account_type=parent.account_type,
                    category=parent.category, normal_balance=parent.normal_balance,
                    notes=None, is_active=True, parent_id=parent.id,
                ))
                added_sub_accounts += 1
                existing_codes.add(child_code)
        db.flush()

        # --- Chair rates ---
        added_rates = 0
        existing_rate_keys = {
            (r.chair_type, r.duration_minutes)
            for r in db.query(ChairRate).all()
        }
        for chair_type, duration, price in CHAIR_RATES:
            if (chair_type, duration) in existing_rate_keys:
                continue
            db.add(ChairRate(
                chair_type=chair_type, duration_minutes=duration, price=price,
                effective_from=datetime.date(2026, 1, 1), is_active=True,
            ))
            added_rates += 1

        # --- Settings ---
        added_settings = 0
        existing_setting_keys = {s.key for s in db.query(Setting).all()}
        for key, value in DEFAULT_SETTINGS.items():
            if key in existing_setting_keys:
                continue
            db.add(Setting(key=key, value=value))
            added_settings += 1

        # --- Users ---
        existing_usernames = {u.username for u in db.query(User).all()}
        added_users = 0
        for username, password, role in INITIAL_USERS:
            if username in existing_usernames:
                continue
            db.add(User(username=username, password_hash=hash_password(password), role=role, is_active=True))
            added_users += 1

        if added_accounts or added_sub_accounts or added_rates or added_settings or added_users:
            db.add(AuditLog(
                username="SYSTEM",
                action=(
                    f"Seeded database: {added_accounts} accounts, {added_sub_accounts} sub-accounts, "
                    f"{added_rates} chair rates, {added_settings} settings, {added_users} users"
                ),
                module="SYSTEM",
            ))

        db.commit()
        print(
            f"Seed complete. Accounts added: {added_accounts}. Sub-accounts added: {added_sub_accounts}. "
            f"Chair rates added: {added_rates}. Settings added: {added_settings}. Users added: {added_users}."
        )
    finally:
        db.close()


if __name__ == "__main__":
    run()
