import os

# Значение по умолчанию подходит для `docker compose up db` на локальной машине.
DEFAULT_DATABASE_URL = "postgresql+asyncpg://payments:payments@localhost:5433/payments"


def get_database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)


def get_webhook_secret() -> str:
    """Секрет подписи вебхуков. Обязателен: без него приложение не должно стартовать."""
    secret = os.environ.get("WEBHOOK_SECRET")
    if not secret:
        raise RuntimeError("Environment variable WEBHOOK_SECRET is required and must not be empty")
    return secret
