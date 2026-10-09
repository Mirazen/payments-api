from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Path, Response
from fastapi.exceptions import RequestValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionDep
from app.domain import UnknownPromoCodeError, calc_discount
from app.models import Payment, Tariff
from app.schemas import PaymentCreate, PaymentOut

router = APIRouter()

# id - 32-битное целое в базе. Число больше этого значения не может быть id платежа,
# а запрос с ним без проверки закончился бы ошибкой драйвера (500).
MAX_PAYMENT_ID = 2**31 - 1


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


@router.get("/payments/{payment_id}", response_model=PaymentOut)
async def get_payment(session: SessionDep, payment_id: int = Path(ge=1, le=MAX_PAYMENT_ID)):
    payment = await session.get(Payment, payment_id)
    if payment is None:
        raise HTTPException(status_code=404, detail="Payment not found")
    return payment
