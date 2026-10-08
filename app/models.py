from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    MetaData,
    String,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.enums import PaymentMethod, PaymentStatus

# Имена ограничений задаём явно: Alembic без этого генерирует None-имена,
# и такие ограничения потом нельзя изменить или удалить по имени.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _text_enum(enum_class: type[StrEnum]) -> Enum:
    """Колонка-строка для enum: в базе хранится значение ('pending'), а не имя.

    Нативный enum Postgres не используем: его неудобно менять в миграциях.
    Допустимые значения проверяет явный CHECK в __table_args__ (см. _in_values).
    """
    return Enum(
        enum_class,
        native_enum=False,
        create_constraint=False,
        length=16,
        values_callable=lambda members: [member.value for member in members],
    )


def _in_values(column: str, enum_class: type[StrEnum]) -> str:
    values = ", ".join(f"'{member.value}'" for member in enum_class)
    return f"{column} IN ({values})"


class Tariff(Base):
    __tablename__ = "tariffs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    title: Mapped[str] = mapped_column(String(64))
    price: Mapped[int] = mapped_column(BigInteger)  # копейки


class Payment(Base):
    __tablename__ = "payments"
    __table_args__ = (
        CheckConstraint(_in_values("status", PaymentStatus), name="status_allowed"),
        CheckConstraint(_in_values("method", PaymentMethod), name="method_allowed"),
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("discount >= 0", name="discount_non_negative"),
        # Рассрочка и срок идут вместе: срок задан тогда и только тогда, когда метод installment.
        CheckConstraint(
            "(method = 'installment') = (installment_months IS NOT NULL)",
            name="installment_months_matches_method",
        ),
        CheckConstraint(
            "installment_months IS NULL OR installment_months IN (3, 6, 12)",
            name="installment_months_allowed",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    status: Mapped[PaymentStatus] = mapped_column(
        _text_enum(PaymentStatus), default=PaymentStatus.PENDING
    )
    tariff_id: Mapped[str] = mapped_column(ForeignKey("tariffs.id"))
    amount: Mapped[int] = mapped_column(BigInteger)  # к оплате, копейки
    discount: Mapped[int] = mapped_column(BigInteger)  # скидка, копейки
    method: Mapped[PaymentMethod] = mapped_column(_text_enum(PaymentMethod))
    installment_months: Mapped[int | None]
    email: Mapped[str] = mapped_column(String(320))
    # Уникальность гарантирует идемпотентность; NULL-ов может быть сколько угодно.
    idempotency_key: Mapped[str | None] = mapped_column(String(255), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
