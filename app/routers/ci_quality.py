import logging
import re
import time
import uuid
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.ci_quality_agent import CiQualityUpstreamError, request_verdict
from app.ci_quality_models import CiQualityErrorDetail, CiQualityResponse
from app.ci_quality_rules import PROMPT_VERSION, SIDE_LABELS, normalize_verdict

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["ci-quality"])

MAX_IMAGE_BYTES = 8 * 1024 * 1024

# Magic bytes: descarta archivos corruptos o con mime mentido sin sumar Pillow.
IMAGE_SIGNATURES = {
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
}

# Alias defensivo: algunos clientes mandan image/jpg, que no es estandar.
# Borralo si preferis estrictez total.
MIME_ALIASES = {"image/jpg": "image/jpeg"}

CODE_INVALID_REQUEST = "invalid_request"
CODE_INVALID_IMAGE = "invalid_image"
CODE_INTERNAL_ERROR = "internal_error"

REQUEST_ID_UNSAFE = re.compile(r"[^A-Za-z0-9._:-]")
MAX_REQUEST_ID_LENGTH = 64


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
        detail=CiQualityErrorDetail(
            code=code,
            message=message,
            requestId=request_id,
        ).model_dump(),
        headers=headers,
    )


@router.post("/ci-quality-check", response_model=CiQualityResponse)
async def ci_quality_check(
    image: UploadFile = File(...),
    side: str = Form(...),
    request_id: Optional[str] = Form(None),
):
    started_at = time.perf_counter()
    resolved_request_id = resolve_request_id(request_id)

    normalized_side = (side or "").strip().lower()
    if normalized_side not in SIDE_LABELS:
        raise http_error(
            400,
            CODE_INVALID_REQUEST,
            "El campo side debe ser 'front' o 'back'.",
            resolved_request_id,
        )

    raw_content_type = (image.content_type or "").split(";")[0].strip().lower()
    content_type = MIME_ALIASES.get(raw_content_type, raw_content_type)
    if content_type not in IMAGE_SIGNATURES:
        # 415 y no 400: es lo que ya devuelve /ocr/factura/ para mime no soportado.
        raise http_error(
            415,
            CODE_INVALID_REQUEST,
            "Tipo de archivo no soportado. Use JPEG o PNG.",
            resolved_request_id,
        )

    try:
        # Se lee un byte de mas para detectar el exceso sin cargar mas que eso.
        image_bytes = await image.read(MAX_IMAGE_BYTES + 1)
    finally:
        # Cierre explicito: libera cuanto antes el buffer temporal de Starlette.
        await image.close()

    image_kb = round(len(image_bytes) / 1024, 1)

    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise http_error(
            400,
            CODE_INVALID_IMAGE,
            "La imagen supera el maximo de 8 MB.",
            resolved_request_id,
        )

    if not image_bytes:
        raise http_error(
            400,
            CODE_INVALID_IMAGE,
            "La imagen esta vacia.",
            resolved_request_id,
        )

    if not any(
        image_bytes.startswith(signature) for signature in IMAGE_SIGNATURES[content_type]
    ):
        raise http_error(
            400,
            CODE_INVALID_IMAGE,
            "La imagen no se pudo decodificar o no coincide con su tipo declarado.",
            resolved_request_id,
        )

    try:
        result = await request_verdict(
            image_bytes=image_bytes,
            mime_type=content_type,
            side=normalized_side,
            request_id=resolved_request_id,
        )

    except CiQualityUpstreamError as error:
        latency_ms = int((time.perf_counter() - started_at) * 1000)
        logger.warning(
            "ci_quality_check upstream_error request_id=%s side=%s image_kb=%s "
            "mime=%s code=%s status=%s latency_ms=%s",
            resolved_request_id,
            normalized_side,
            image_kb,
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
            "ci_quality_check error_interno request_id=%s side=%s image_kb=%s "
            "error_type=%s latency_ms=%s",
            resolved_request_id,
            normalized_side,
            image_kb,
            type(error).__name__,
            latency_ms,
        )
        raise http_error(
            500,
            CODE_INTERNAL_ERROR,
            "Error interno procesando la imagen.",
            resolved_request_id,
        )

    verdict, anomalies = normalize_verdict(result.payload)
    latency_ms = int((time.perf_counter() - started_at) * 1000)

    logger.info(
        "ci_quality_check request_id=%s side=%s is_acceptable=%s issues=%s "
        "latency_ms=%s model=%s prompt_version=%s image_kb=%s mime=%s "
        "prompt_tokens=%s output_tokens=%s finish_reason=%s",
        resolved_request_id,
        normalized_side,
        verdict.is_acceptable,
        ",".join(verdict.issues) or "-",
        latency_ms,
        result.model,
        PROMPT_VERSION,
        image_kb,
        content_type,
        result.prompt_tokens,
        result.output_tokens,
        result.finish_reason,
    )

    if anomalies:
        logger.warning(
            "ci_quality_check anomalias request_id=%s model=%s anomalies=%s",
            resolved_request_id,
            result.model,
            ",".join(anomalies),
        )

    return CiQualityResponse(
        isAcceptable=verdict.is_acceptable,
        issues=list(verdict.issues),
        message=verdict.message,
        requestId=resolved_request_id,
    )
