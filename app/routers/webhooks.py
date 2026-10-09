from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import select, update

from app.db import SessionDep
from app.domain import can_transition
from app.enums import PaymentStatus
from app.models import Payment
from app.schemas import BankWebhook
from app.security import verify_webhook_signature

router = APIRouter()


@router.post("/webhooks/bank", dependencies=[Depends(verify_webhook_signature)])
async def bank_webhook(body: BankWebhook, session: SessionDep):
    # Из каких статусов можно перейти в новый (для "pending" таких нет).
    allowed_from = [status for status in PaymentStatus if can_transition(status, body.status)]

    # Один условный UPDATE: статус меняется, только если сейчас он допустимый.
    # Проверка и запись происходят в одной команде, поэтому два одновременных
    # вебхука не могут оба "выиграть": второй увидит уже изменённый статус.
    result = await session.execute(
        update(Payment)
        .where(Payment.id == body.payment_id, Payment.status.in_(allowed_from))
        .values(status=body.status)
    )
    if result.rowcount == 1:
        await session.commit()
        return {"result": "ok"}

    # Ничего не изменилось: либо платежа нет, либо переход запрещён.
    payment_id = await session.scalar(select(Payment.id).where(Payment.id == body.payment_id))
    if payment_id is None:
        raise HTTPException(status_code=404, detail="Payment not found")
    # Тело ответа задано в ТЗ ровно так, поэтому JSONResponse, а не HTTPException
    # (тот завернул бы его в {"detail": ...}).
    return JSONResponse(status_code=409, content={"error": "invalid_transition"})
