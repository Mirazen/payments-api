from datetime import datetime
from typing import Self

from pydantic import BaseModel, ConfigDict, EmailStr, Field, computed_field, model_validator

from app.domain import INSTALLMENT_MONTHS, build_schedule
from app.enums import PaymentMethod, PaymentStatus

# id - 32-битное целое в базе. Число больше этого значения не может быть id платежа,
# а запрос с ним без проверки закончился бы ошибкой драйвера (500).
MAX_PAYMENT_ID = 2**31 - 1


class TariffOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    price: int  # копейки


class PaymentCreate(BaseModel):
    # Сумму и скидку клиент не передаёт: лишние поля в теле игнорируются,
    # amount и discount всегда считает сервер.
    tariff_id: str
    email: EmailStr
    method: PaymentMethod
    installment_months: int | None = None
    promo_code: str | None = None

    @model_validator(mode="after")
    def check_installment_months(self) -> Self:
        if self.method == PaymentMethod.INSTALLMENT:
            if self.installment_months not in INSTALLMENT_MONTHS:
                raise ValueError(f"installment_months must be one of {INSTALLMENT_MONTHS}")
        elif self.installment_months is not None:
            raise ValueError("installment_months is allowed only for installment method")
        return self


class BankWebhook(BaseModel):
    payment_id: int = Field(ge=1, le=MAX_PAYMENT_ID)
    status: PaymentStatus


class PaymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: PaymentStatus
    tariff_id: str
    amount: int  # к оплате, копейки
    discount: int  # скидка, копейки
    method: PaymentMethod
    installment_months: int | None
    email: str
    created_at: datetime

    @computed_field
    @property
    def schedule(self) -> list[int] | None:
        """График не хранится в базе, а считается из amount и срока при каждом ответе."""
        if self.installment_months is None:
            return None
        return build_schedule(self.amount, self.installment_months)
