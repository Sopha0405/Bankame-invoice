from pydantic import BaseModel, Field


class CiQualityResponse(BaseModel):
    isAcceptable: bool
    issues: list[str] = Field(default_factory=list)
    message: str
    requestId: str


class CiQualityErrorDetail(BaseModel):
    code: str
    message: str
    requestId: str
