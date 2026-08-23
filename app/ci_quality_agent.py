import asyncio
import json
import logging
import random
from dataclasses import dataclass
from typing import Any, Optional

from google.genai import types
from google.genai.errors import ClientError, ServerError

from app.ci_quality_rules import RESPONSE_SCHEMA, build_prompt
from app.gemini_agent import MODEL_NAMES, get_gemini_client

logger = logging.getLogger(__name__)

FALLBACK_MODEL = "gemini-2.5-flash"

TIMEOUT_MS = 20_000
RETRY_AFTER_SECONDS = 10

MAX_ATTEMPTS = 2
RETRY_BASE_DELAY_SECONDS = 0.75

TIMEOUT_MARGIN_SECONDS = 0.5

NORMAL_FINISH_REASONS = ("STOP", "FINISH_REASON_STOP")

CODE_RATE_LIMITED = "upstream_rate_limited"
CODE_UNAVAILABLE = "upstream_unavailable"


class CiQualityUpstreamError(Exception):

    def __init__(
        self,
        code: str,
        message: str,
        status_code: int,
        retry_after: Optional[int] = None,
    ):
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.retry_after = retry_after


@dataclass(frozen=True)
class GeminiCallResult:
    payload: Optional[dict]
    model: str
    finish_reason: Optional[str]
    prompt_tokens: Optional[int]
    output_tokens: Optional[int]


def primary_model() -> str:
    return MODEL_NAMES[0] if MODEL_NAMES else FALLBACK_MODEL


def attempt_plan() -> list[str]:
    return [primary_model()] * MAX_ATTEMPTS


def retry_delay_seconds() -> float:
    return RETRY_BASE_DELAY_SECONDS + random.uniform(0, RETRY_BASE_DELAY_SECONDS)


def enum_name(value: Any) -> Optional[str]:
    if value is None:
        return None
    return getattr(value, "name", None) or str(value)


def status_code_of(error: Exception) -> Optional[int]:
    return getattr(error, "code", None) or getattr(error, "status_code", None)


def parse_response(response: Any, model_name: str, request_id: str) -> GeminiCallResult:
    candidates = getattr(response, "candidates", None) or []
    finish_reason = (
        enum_name(getattr(candidates[0], "finish_reason", None)) if candidates else None
    )
    block_reason = enum_name(
        getattr(getattr(response, "prompt_feedback", None), "block_reason", None)
    )

    usage = getattr(response, "usage_metadata", None)
    prompt_tokens = getattr(usage, "prompt_token_count", None)
    output_tokens = getattr(usage, "candidates_token_count", None)

    if not candidates:
        logger.warning(
            "ci_quality gemini_sin_candidates request_id=%s model=%s block_reason=%s",
            request_id,
            model_name,
            block_reason,
        )
        raise CiQualityUpstreamError(
            CODE_UNAVAILABLE,
            "Gemini no devolvio una respuesta utilizable.",
            503,
        )

    # finish_reason None se acepta: hay versiones del SDK que no lo pueblan.
    if finish_reason is not None and finish_reason not in NORMAL_FINISH_REASONS:
        logger.warning(
            "ci_quality gemini_finish_reason_anomalo request_id=%s model=%s "
            "finish_reason=%s block_reason=%s",
            request_id,
            model_name,
            finish_reason,
            block_reason,
        )
        raise CiQualityUpstreamError(
            CODE_UNAVAILABLE,
            f"Gemini corto la respuesta ({finish_reason}).",
            503,
        )

    raw_text = getattr(response, "text", None)
    if not raw_text or not raw_text.strip():
        logger.warning(
            "ci_quality gemini_respuesta_vacia request_id=%s model=%s finish_reason=%s",
            request_id,
            model_name,
            finish_reason,
        )
        raise CiQualityUpstreamError(
            CODE_UNAVAILABLE,
            "Gemini devolvio una respuesta vacia.",
            503,
        )

    try:
        payload = json.loads(raw_text)
    except (TypeError, ValueError):
        # No se loguea raw_text: puede traer datos del carnet.
        logger.warning(
            "ci_quality gemini_json_invalido request_id=%s model=%s "
            "finish_reason=%s longitud=%s",
            request_id,
            model_name,
            finish_reason,
            len(raw_text),
        )
        raise CiQualityUpstreamError(
            CODE_UNAVAILABLE,
            "Gemini devolvio una respuesta no parseable.",
            503,
        )

    if not isinstance(payload, dict):
        logger.warning(
            "ci_quality gemini_json_no_objeto request_id=%s model=%s tipo=%s",
            request_id,
            model_name,
            type(payload).__name__,
        )
        raise CiQualityUpstreamError(
            CODE_UNAVAILABLE,
            "Gemini devolvio una respuesta con forma inesperada.",
            503,
        )

    return GeminiCallResult(
        payload=payload,
        model=model_name,
        finish_reason=finish_reason,
        prompt_tokens=prompt_tokens,
        output_tokens=output_tokens,
    )


async def request_verdict(
    image_bytes: bytes,
    mime_type: str,
    side: str,
    request_id: str,
) -> GeminiCallResult:
    config = types.GenerateContentConfig(
        temperature=0,
        response_mime_type="application/json",
        response_schema=RESPONSE_SCHEMA,
        http_options=types.HttpOptions(timeout=TIMEOUT_MS),
    )

    contents = [
        build_prompt(side),
        # Parte inline: el SDK hace el base64. Con 150-400 KB no hace falta File API.
        types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
    ]

    models = attempt_plan()
    hard_timeout = TIMEOUT_MS / 1000 + TIMEOUT_MARGIN_SECONDS
    last_error: Optional[CiQualityUpstreamError] = None

    for attempt, model_name in enumerate(models, start=1):
        try:
            client = get_gemini_client()
            response = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model=model_name,
                    contents=contents,
                    config=config,
                ),
                timeout=hard_timeout,
            )
            return parse_response(response, model_name, request_id)

        except CiQualityUpstreamError:
            # Respuesta impaseable o bloqueada: no se reintenta.
            raise

        except (asyncio.TimeoutError, TimeoutError):
            logger.warning(
                "ci_quality gemini_timeout request_id=%s model=%s intento=%s timeout_ms=%s",
                request_id,
                model_name,
                attempt,
                TIMEOUT_MS,
            )
            last_error = CiQualityUpstreamError(
                CODE_UNAVAILABLE,
                "Gemini no respondio dentro del tiempo limite.",
                503,
            )

        except ClientError as error:
            status_code = status_code_of(error) or 400
            logger.warning(
                "ci_quality gemini_client_error request_id=%s model=%s intento=%s "
                "status_code=%s status=%s",
                request_id,
                model_name,
                attempt,
                status_code,
                getattr(error, "status", None),
            )

            if status_code == 429:
                last_error = CiQualityUpstreamError(
                    CODE_RATE_LIMITED,
                    "Gemini esta limitando las solicitudes.",
                    429,
                    retry_after=RETRY_AFTER_SECONDS,
                )
            else:
                # 400/401/403: config o request nuestro. No se reintenta y no se
                # filtra el status del upstream al backend.
                logger.error(
                    "ci_quality gemini_no_reintentable request_id=%s status_code=%s",
                    request_id,
                    status_code,
                )
                raise CiQualityUpstreamError(
                    CODE_UNAVAILABLE,
                    "Gemini rechazo la solicitud.",
                    503,
                ) from error

        except ServerError as error:
            logger.warning(
                "ci_quality gemini_server_error request_id=%s model=%s intento=%s "
                "status_code=%s status=%s",
                request_id,
                model_name,
                attempt,
                status_code_of(error),
                getattr(error, "status", None),
            )
            last_error = CiQualityUpstreamError(
                CODE_UNAVAILABLE,
                "Gemini no esta disponible.",
                503,
            )

        except Exception as error:
            # Transporte o bug: no encaja en 429/5xx/timeout, no se reintenta.
            logger.warning(
                "ci_quality gemini_error_inesperado request_id=%s model=%s "
                "intento=%s error_type=%s",
                request_id,
                model_name,
                attempt,
                type(error).__name__,
            )
            raise CiQualityUpstreamError(
                CODE_UNAVAILABLE,
                "No se pudo consultar a Gemini.",
                503,
            ) from error

        if attempt < len(models):
            await asyncio.sleep(retry_delay_seconds())

    raise last_error or CiQualityUpstreamError(
        CODE_UNAVAILABLE,
        "No se pudo consultar a Gemini.",
        503,
    )
