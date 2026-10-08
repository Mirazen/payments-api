from fastapi import APIRouter
from sqlalchemy import select

from app.db import SessionDep
from app.models import Tariff
from app.schemas import TariffOut

router = APIRouter()


@router.get("/tariffs", response_model=list[TariffOut])
async def list_tariffs(session: SessionDep):
    result = await session.scalars(select(Tariff).order_by(Tariff.price))
    return result.all()
