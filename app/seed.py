"""
Run once (locally or via Render's shell) to initialize the database:

    python -m app.seed

Safe to re-run: it only inserts accounts/users that don't already exist.
"""
from .database import Base, engine, SessionLocal
from .models import User, Account, AuditLog
from .auth import hash_password
from .coa_seed import COA_SEED

INITIAL_USERS = [
    # username, password, role
    ("Dher", "Derps1216", "ADMIN"),
    ("OdessaSan", "Teebuskuipee16", "ACCOUNTANT"),
]


def run():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        # --- Chart of Accounts ---
        existing_codes = {a.code for a in db.query(Account).all()}
        added_accounts = 0
        for row in COA_SEED:
            if row["code"] in existing_codes:
                continue
            db.add(Account(
                code=row["code"],
                name=row["name"],
                account_type=row["account_type"],
                category=row.get("category"),
                normal_balance=row["normal_balance"],
                notes=row.get("notes"),
                is_active=True,
            ))
            added_accounts += 1

        # --- Users ---
        existing_usernames = {u.username for u in db.query(User).all()}
        added_users = 0
        for username, password, role in INITIAL_USERS:
            if username in existing_usernames:
                continue
            db.add(User(
                username=username,
                password_hash=hash_password(password),
                role=role,
                is_active=True,
            ))
            added_users += 1

        if added_accounts or added_users:
            db.add(AuditLog(
                username="SYSTEM",
                action=f"Seeded database: {added_accounts} accounts, {added_users} users",
                module="SYSTEM",
            ))

        db.commit()
        print(f"Seed complete. Accounts added: {added_accounts}. Users added: {added_users}.")
    finally:
        db.close()


if __name__ == "__main__":
    run()
