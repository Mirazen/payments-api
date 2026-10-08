from fastapi import APIRouter, HTTPException, Path
from fastapi.exceptions import RequestValidationError

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


@router.post("/payments", response_model=PaymentOut, status_code=201)
async def create_payment(body: PaymentCreate, session: SessionDep):
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
    )
    session.add(payment)
    await session.commit()
    return payment


@router.get("/payments/{payment_id}", response_model=PaymentOut)
async def get_payment(session: SessionDep, payment_id: int = Path(ge=1, le=MAX_PAYMENT_ID)):
    payment = await session.get(Payment, payment_id)
    if payment is None:
        raise HTTPException(status_code=404, detail="Payment not found")
    return payment
