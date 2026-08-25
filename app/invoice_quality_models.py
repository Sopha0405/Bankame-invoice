from pydantic import BaseModel, Field


class InvoiceQualityResponse(BaseModel):
    isAcceptable: bool
    issues: list[str] = Field(default_factory=list)
    message: str
    requestId: str


class InvoiceQualityErrorDetail(BaseModel):
    code: str
    message: str
    requestId: str
