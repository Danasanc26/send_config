import re
from uuid import UUID
from typing import Optional, List, Literal
from pydantic import BaseModel, Field, field_validator

UnitCode   = Literal["1", "2", "4", "8"]             # 1=lb, 2=kg, 4=oz, 8=g
MotionCode = Literal["00", "01", "02", "03", "05", "10"]
CheckEntry = Literal["0", "1"]

class EPParams(BaseModel):
    prod_id: str = Field(..., min_length=1, max_length=20)
    under: int = Field(..., ge=0)
    over: int  = Field(..., ge=0)
    tare: int  = Field(..., ge=0)
    unit: UnitCode
    motion: MotionCode
    threshold: str = Field(..., pattern=r"^[0-9]{2}$")     # "01".."99"
    outputs: List[str] = Field(..., min_items=8, max_items=9)  # 8 o 9 pares hex
    check_entry: CheckEntry

    @field_validator("outputs")
    @classmethod
    def validate_outputs(cls, v: List[str]) -> List[str]:
        if not (8 <= len(v) <= 9):
            raise ValueError("outputs debe tener 8 o 9 pares hex (2 chars)")
        for i, pair in enumerate(v):
            if not re.fullmatch(r"[0-9A-Fa-f]{2}", pair or ""):
                raise ValueError(f"outputs[{i}]='{pair}' no es par hex válido")
        return [s.upper() for s in v]  # normaliza a mayúsculas

class EPRequest(BaseModel):
    id: str
    rid: Optional[UUID] = None
    payload: EPParams

class EPBatchRequest(BaseModel):
    ids: List[str]
    rid: Optional[UUID] = None
    payload: EPParams

class EPAck(BaseModel):
    id_balanza: str
    rid: str
    ack: str
    sent_len: int | None = None
    sent_hex: str | None = None
    updated_at: str
