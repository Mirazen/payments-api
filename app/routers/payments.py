from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Path, Response
from fastapi.exceptions import RequestValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionDep
from app.domain import UnknownPromoCodeError, calc_discount
from app.enums import PaymentStatus
from app.models import Payment, Tariff
from app.schemas import MAX_PAYMENT_ID, PaymentCreate, PaymentOut

router = APIRouter()


def field_error(field: str, message: str, value: object) -> RequestValidationError:
    """422 в стандартном формате FastAPI для ошибки, которую видно только после запроса в базу."""
    return RequestValidationError(
        [{"type": "value_error", "loc": ("body", field), "msg": message, "input": value}]
    )


async def find_payment_by_key(session: AsyncSession, key: str) -> Payment | None:
    return await session.scalar(select(Payment).where(Payment.idempotency_key == key))


@router.post("/payments", response_model=PaymentOut, status_code=201)
async def create_payment(
    body: PaymentCreate,
    session: SessionDep,
    response: Response,
    # Пустой заголовок - ошибка 422: иначе все клиенты с пустым ключом получали бы один платёж.
    # 255 - длина колонки idempotency_key.
    idempotency_key: Annotated[str | None, Header(min_length=1, max_length=255)] = None,
):
    # Повтор с тем же ключом: возвращаем исходный платёж, тело запроса не сравниваем.
    if idempotency_key is not None:
        existing = await find_payment_by_key(session, idempotency_key)
        if existing is not None:
            response.status_code = 200
            return existing

    tariff = await session.get(Tariff, body.tariff_id)
    if tariff is None:
        raise field_error("tariff_id", "Unknown tariff", body.tariff_id)

    try:
        discount = calc_discount(tariff.price, body.promo_code)
    except UnknownPromoCodeError:
        raise field_error("promo_code", "Unknown promo code", body.promo_code) from None

    payment = Payment(
        tariff_id=tariff.id,
        amount=tariff.price - discount,
        discount=discount,
        method=body.method,
        installment_months=body.installment_months,
        email=body.email,
        idempotency_key=idempotency_key,
    )
    session.add(payment)
    try:
        await session.commit()
    except IntegrityError:
        # Два запроса с одним ключом пришли одновременно: оба не нашли платёж,
        # оба вставили, и уникальное ограничение в базе пропустило только первого.
        await session.rollback()
        if idempotency_key is None:
            raise
        existing = await find_payment_by_key(session, idempotency_key)
        if existing is None:
            raise
        response.status_code = 200
        return existing
    return payment


@router.get("/payments", response_model=list[PaymentOut])
async def list_payments(
    session: SessionDep,
    email: str | None = None,
    status: PaymentStatus | None = None,
):
    """Список платежей по порядку создания. Фильтры необязательны и объединяются через И.

    email сравнивается точно (с учётом регистра). Пагинации нет: она не нужна по ТЗ.
    """
    query = select(Payment).order_by(Payment.id)
    if email is not None:
        query = query.where(Payment.email == email)
    if status is not None:
        query = query.where(Payment.status == status)
    result = await session.scalars(query)
    return result.all()


@router.get("/payments/{payment_id}", response_model=PaymentOut)
async def get_payment(session: SessionDep, payment_id: int = Path(ge=1, le=MAX_PAYMENT_ID)):
    payment = await session.get(Payment, payment_id)
    if payment is None:
        raise HTTPException(status_code=404, detail="Payment not found")
    return payment
