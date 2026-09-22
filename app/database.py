import os
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

# On Render/Railway, set DATABASE_URL to your managed Postgres connection string.
# Locally (or if unset), falls back to a SQLite file so you can run this without
# installing Postgres at all.
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./relaxify.db")

# Render/Railway sometimes hand out "postgres://" — SQLAlchemy needs "postgresql://"
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
