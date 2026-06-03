import re
from typing import Optional

from app.uv_map import find_uv_by_geolocation


def extract_uv(text: str) -> Optional[str]:
    if not text:
        return None

    text = text.upper()

    patterns = [
        r"\bUV[\s\-.]*([0-9]{1,4})\b",
        r"\bU\.V\.[\s\-.]*([0-9]{1,4})\b",
        r"\bUNIDAD\s+VECINAL[\s\-.]*([0-9]{1,4})\b",
        r"\bUNID(?:AD)?\.?\s+VECINAL[\s\-.]*([0-9]{1,4})\b",
    ]

    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1).lstrip("0") or "0"

    return None


def validate_uv_match(
    cliente_direccion: str,
    factura_direccion: str,
    cliente_geolocalizacion: Optional[str] = None,
):
    cliente_uv = extract_uv(cliente_direccion)
    factura_uv = extract_uv(factura_direccion)
    geo_message = None

    if cliente_geolocalizacion:
        geo_uv, geo_message = find_uv_by_geolocation(cliente_geolocalizacion)
        cliente_uv = cliente_uv or geo_uv

    if not cliente_uv and not factura_uv:
        return {
            "address_matched": False,
            "cliente_uv": None,
            "factura_uv": None,
            "mensaje": geo_message or "No se encontro UV en la direccion, geolocalizacion ni factura.",
        }

    if cliente_uv and not factura_uv:
        return {
            "address_matched": False,
            "cliente_uv": cliente_uv,
            "factura_uv": None,
            "mensaje": "La direccion/geolocalizacion del cliente tiene UV, pero la factura no.",
        }

    if not cliente_uv and factura_uv:
        return {
            "address_matched": False,
            "cliente_uv": None,
            "factura_uv": factura_uv,
            "mensaje": geo_message or "La factura tiene UV, pero la direccion/geolocalizacion del cliente no.",
        }

    matched = cliente_uv == factura_uv

    return {
        "address_matched": matched,
        "cliente_uv": cliente_uv,
        "factura_uv": factura_uv,
        "mensaje": None if matched else f"La UV no coincide. Cliente UV {cliente_uv}, factura UV {factura_uv}.",
    }
