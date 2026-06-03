import re
import unicodedata
from difflib import SequenceMatcher
from typing import Optional


def normalize_text(value: Optional[str]) -> str:
    if not value:
        return ""

    value = unicodedata.normalize("NFKD", value)
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = value.upper()
    value = re.sub(r"[^A-Z0-9\s]", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def compare_name(
    holder_name: Optional[str],
    nombres: str,
    apellido_paterno: str,
    apellido_materno: str,
    tipo_vivienda: int,
) -> tuple[str, bool]:
    holder = normalize_text(holder_name)
    first_names = normalize_text(nombres).split()
    last_names = [
        name for name in [
            normalize_text(apellido_paterno),
            normalize_text(apellido_materno),
        ]
        if name
    ]

    if not holder:
        return "no_detectado", False

    required_tokens = [*first_names, *last_names]
    if required_tokens and all(token in holder.split() for token in required_tokens):
        return "coincide_nombre_completo", True

    if any(last_name in holder.split() for last_name in last_names):
        return "coincide_al_menos_un_apellido", True

    if tipo_vivienda in (3, 4):
        return "coincide_beneficiario_titular", True

    return "no_coincide", False


def compare_address(declared_address: str, extracted_address: Optional[str]) -> tuple[float, str]:
    declared = normalize_text(declared_address)
    extracted = normalize_text(extracted_address)

    if not declared or not extracted:
        return 0.0, "no_detectado"

    score = round(SequenceMatcher(None, declared, extracted).ratio(), 4)

    if score >= 0.8:
        return score, "coincidencia_alta"
    if score >= 0.55:
        return score, "coincidencia_media"
    if score >= 0.35:
        return score, "coincidencia_baja"
    return score, "no_coincide"


def build_validation_status(
    missing_fields: list[str],
    name_matched: bool,
    address_level: str,
    uv_matched: bool,
) -> str:
    if missing_fields:
        return "requiere_revision"

    address_ok = address_level in ("coincidencia_alta", "coincidencia_media") or uv_matched
    if name_matched and address_ok:
        return "validacion_aprobada"

    return "requiere_revision"
