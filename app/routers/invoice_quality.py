import logging
import re
import time
import uuid
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.invoice_quality_agent import InvoiceQualityUpstreamError, request_verdict
from app.invoice_quality_models import InvoiceQualityErrorDetail, InvoiceQualityResponse
from app.invoice_quality_rules import KIND_IMAGE, KIND_PDF, PROMPT_VERSION, normalize_verdict

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["invoice-quality"])

MAX_FILE_BYTES = 10 * 1024 * 1024

FILE_KINDS = {
    "application/pdf": KIND_PDF,
    "image/jpeg": KIND_IMAGE,
    "image/png": KIND_IMAGE,
    "image/webp": KIND_IMAGE,
}

MIME_ALIASES = {"image/jpg": "image/jpeg"}

CODE_INVALID_REQUEST = "invalid_request"
CODE_INVALID_FILE = "invalid_file"
CODE_INTERNAL_ERROR = "internal_error"

REQUEST_ID_UNSAFE = re.compile(r"[^A-Za-z0-9._:-]")
MAX_REQUEST_ID_LENGTH = 64


def matches_signature(content_type: str, data: bytes) -> bool:
    if content_type == "application/pdf":
        return data.startswith(b"%PDF-")

    if content_type == "image/jpeg":
        return data.startswith(b"\xff\xd8\xff")

    if content_type == "image/png":
        return data.startswith(b"\x89PNG\r\n\x1a\n")

    if content_type == "image/webp":
        return data.startswith(b"RIFF") and data[8:12] == b"WEBP"

    return False


def resolve_request_id(raw_request_id: Optional[str]) -> str:
    if raw_request_id:
        cleaned = REQUEST_ID_UNSAFE.sub("", raw_request_id)[:MAX_REQUEST_ID_LENGTH]
        if cleaned:
            return cleaned

    return uuid.uuid4().hex


def http_error(
    status_code: int,
    code: str,
    message: str,
    request_id: str,
    retry_after: Optional[int] = None,
) -> HTTPException:
    headers = {"Retry-After": str(retry_after)} if retry_after else None

    return HTTPException(
        status_code=status_code,
        detail=InvoiceQualityErrorDetail(
            code=code,
            message=message,
            requestId=request_id,
        ).model_dump(),
        headers=headers,
    )


@router.post("/invoice-quality-check", response_model=InvoiceQualityResponse)
async def invoice_quality_check(
    file: UploadFile = File(...),
    request_id: Optional[str] = Form(None),
):
    started_at = time.perf_counter()
    resolved_request_id = resolve_request_id(request_id)

    raw_content_type = (file.content_type or "").split(";")[0].strip().lower()
    content_type = MIME_ALIASES.get(raw_content_type, raw_content_type)
    if content_type not in FILE_KINDS:
        raise http_error(
            415,
            CODE_INVALID_REQUEST,
            "Tipo de archivo no soportado. Use PDF, JPEG, PNG o WEBP.",
            resolved_request_id,
        )

    kind = FILE_KINDS[content_type]

    try:
        file_bytes = await file.read(MAX_FILE_BYTES + 1)
    finally:
        await file.close()

    file_kb = round(len(file_bytes) / 1024, 1)

    if len(file_bytes) > MAX_FILE_BYTES:
        raise http_error(
            400,
            CODE_INVALID_FILE,
            "El archivo supera el maximo de 10 MB.",
            resolved_request_id,
        )

    if not file_bytes:
        raise http_error(
            400,
            CODE_INVALID_FILE,
            "El archivo esta vacio.",
            resolved_request_id,
        )

    if not matches_signature(content_type, file_bytes):
        raise http_error(
            400,
            CODE_INVALID_FILE,
            "El archivo no se pudo decodificar o no coincide con su tipo declarado.",
            resolved_request_id,
        )

    try:
        result = await request_verdict(
            file_bytes=file_bytes,
            mime_type=content_type,
            kind=kind,
            request_id=resolved_request_id,
        )

    except InvoiceQualityUpstreamError as error:
        latency_ms = int((time.perf_counter() - started_at) * 1000)
        logger.warning(
            "invoice_quality_check upstream_error request_id=%s kind=%s file_kb=%s "
            "mime=%s code=%s status=%s latency_ms=%s",
            resolved_request_id,
            kind,
            file_kb,
            content_type,
            error.code,
            error.status_code,
            latency_ms,
        )
        raise http_error(
            error.status_code,
            error.code,
            str(error),
            resolved_request_id,
            retry_after=error.retry_after,
        )

    except HTTPException:
        raise

    except Exception as error:
        latency_ms = int((time.perf_counter() - started_at) * 1000)
        logger.exception(
            "invoice_quality_check error_interno request_id=%s kind=%s file_kb=%s "
            "error_type=%s latency_ms=%s",
            resolved_request_id,
            kind,
            file_kb,
            type(error).__name__,
            latency_ms,
        )
        raise http_error(
            500,
            CODE_INTERNAL_ERROR,
            "Error interno procesando el archivo.",
            resolved_request_id,
        )

    verdict, anomalies = normalize_verdict(result.payload)
    latency_ms = int((time.perf_counter() - started_at) * 1000)

    logger.info(
        "invoice_quality_check request_id=%s kind=%s is_acceptable=%s issues=%s "
        "latency_ms=%s model=%s prompt_version=%s file_kb=%s mime=%s "
        "prompt_tokens=%s output_tokens=%s finish_reason=%s",
        resolved_request_id,
        kind,
        verdict.is_acceptable,
        ",".join(verdict.issues) or "-",
        latency_ms,
        result.model,
        PROMPT_VERSION,
        file_kb,
        content_type,
        result.prompt_tokens,
        result.output_tokens,
        result.finish_reason,
    )

    if anomalies:
        logger.warning(
            "invoice_quality_check anomalias request_id=%s model=%s anomalies=%s",
            resolved_request_id,
            result.model,
            ",".join(anomalies),
        )

    return InvoiceQualityResponse(
        isAcceptable=verdict.is_acceptable,
        issues=list(verdict.issues),
        message=verdict.message,
        requestId=resolved_request_id,
    )
