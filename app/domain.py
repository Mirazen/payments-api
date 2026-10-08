"""Бизнес-правила: деньги, рассрочка, статусы.

Модуль чистый: не импортирует FastAPI, Pydantic и SQLAlchemy.
Все суммы - целые числа в копейках.
"""

from app.enums import PaymentStatus

# (id, title, price в копейках)
TARIFFS: tuple[tuple[str, str, int], ...] = (
    ("basic", "Basic", 990_000),
    ("standard", "Standard", 1_990_000),
    ("premium", "Premium", 2_990_000),
)

PROMO_CODE = "kvitto10"  # сравнение без учёта регистра, см. calc_discount
PROMO_DISCOUNT_PERCENT = 10

INSTALLMENT_MONTHS: tuple[int, ...] = (3, 6, 12)

# Из какого статуса в какие можно перейти. Остальные переходы запрещены.
ALLOWED_TRANSITIONS: dict[PaymentStatus, frozenset[PaymentStatus]] = {
    PaymentStatus.PENDING: frozenset({PaymentStatus.SUCCEEDED, PaymentStatus.FAILED}),
    PaymentStatus.SUCCEEDED: frozenset({PaymentStatus.REFUNDED}),
    PaymentStatus.FAILED: frozenset(),
    PaymentStatus.REFUNDED: frozenset(),
}


class UnknownPromoCodeError(ValueError):
    """Промокод не существует."""


def calc_discount(price: int, promo_code: str | None) -> int:
    """Скидка в копейках. None - промокода нет, скидка 0.

    Любая переданная строка, кроме KVITTO10 (в любом регистре), - ошибка.
    Пустая строка и код с пробелами по краям тоже считаются неизвестными.
    Скидка округляется вниз: целочисленная арифметика, без float.
    """
    if promo_code is None:
        return 0
    if promo_code.casefold() != PROMO_CODE:
        raise UnknownPromoCodeError(promo_code)
    return price * PROMO_DISCOUNT_PERCENT // 100


def build_schedule(amount: int, months: int) -> list[int]:
    """График рассрочки: суммы платежей в копейках.

    Сумма графика ровно равна amount, лишние копейки идут в первые платежи.
    """
    if months not in INSTALLMENT_MONTHS:
        raise ValueError(f"installment months must be one of {INSTALLMENT_MONTHS}")
    base, extra = divmod(amount, months)
    return [base + 1] * extra + [base] * (months - extra)


def can_transition(current: PaymentStatus, new: PaymentStatus) -> bool:
    """Разрешён ли переход current -> new. Переход в тот же статус запрещён."""
    return new in ALLOWED_TRANSITIONS[current]
