import os

# Значение по умолчанию подходит для `docker compose up db` на локальной машине.
DEFAULT_DATABASE_URL = "postgresql+asyncpg://payments:payments@localhost:5433/payments"


def get_database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)
