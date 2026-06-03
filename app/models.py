from enum import IntEnum
from typing import Optional
from pydantic import BaseModel, Field


class BasicServiceType(IntEnum):
    Light = 1
    Water = 2
    Gas = 3
    Internet = 4


class TypeOfHouse(IntEnum):
    Own = 1
    Familiar = 2
    Rent = 3
    Anticretic = 4


class FacturaData(BaseModel):
    document_filename: Optional[str] = None
    document_content_type: Optional[str] = None

    basic_service_type: Optional[BasicServiceType] = None
    service_type_label: Optional[str] = None
    service_provider: Optional[str] = None

    invoice_name_matched: bool = False
    name_match_category: Optional[str] = None
    address_matched: bool = False
    address_similarity_score: Optional[float] = None
    address_match_level: Optional[str] = None
    validation_status: Optional[str] = None
    missing_fields: list[str] = Field(default_factory=list)
    review_required: bool = True

    mensaje_validacion: Optional[str] = None

    holder_name: Optional[str] = None
    nombre_en_factura: Optional[str] = None
    direccion_en_factura: Optional[str] = None

    normalized_address: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    distance_meters: Optional[float] = None

    invoice_amount: Optional[float] = None
    last_month_amount: Optional[float] = None
    average_amount: Optional[float] = None
    unpaid_invoice_count: Optional[int] = None
    currency: Optional[str] = None
    billing_period: Optional[str] = None

    confidence: Optional[float] = None
    texto_extraido: Optional[str] = None

    cliente_uv: Optional[str] = None
    factura_uv: Optional[str] = None

class FacturaResultado(BaseModel):
    success: bool
    message: str
    data: Optional[FacturaData] = None
