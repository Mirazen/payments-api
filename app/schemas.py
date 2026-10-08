from pydantic import BaseModel, ConfigDict


class TariffOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    price: int  # копейки
