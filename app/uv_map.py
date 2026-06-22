import re
from functools import lru_cache
from pathlib import Path
from typing import Optional
from xml.etree import ElementTree as ET

import requests


KML_PATH = Path(__file__).with_name("uv_santa_cruz.kml")
KML_NS = {"kml": "http://www.opengis.net/kml/2.2"}


def parse_geolocation(value: str) -> Optional[tuple[float, float]]:
    if not value:
        return None

    matches = re.findall(r"-?\d+(?:\.\d+)?", value)
    if len(matches) < 2:
        return None

    lat = float(matches[0])
    lon = float(matches[1])

    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None

    return lat, lon


def extract_uv_number(text: str) -> Optional[str]:
    if not text:
        return None

    text = text.upper()
    patterns = [
        r"\bUV[\s\-.]*([0-9]{1,4})\b",
        r"\bU\.V\.[\s\-.]*([0-9]{1,4})\b",
        r"\bUNIDAD\s+VECINAL[\s\-.]*([0-9]{1,4})\b",
        r"\bUNID(?:AD)?\.?\s+VECINAL[\s\-.]*([0-9]{1,4})\b",
        r"\b([0-9]{1,4})\b",
    ]

    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1).lstrip("0") or "0"

    return None


def point_in_polygon(lat: float, lon: float, polygon: list[tuple[float, float]]) -> bool:
    inside = False
    j = len(polygon) - 1

    for i, (point_lon, point_lat) in enumerate(polygon):
        prev_lon, prev_lat = polygon[j]
        intersects = (point_lat > lat) != (prev_lat > lat)

        if intersects:
            cross_lon = (prev_lon - point_lon) * (lat - point_lat) / (prev_lat - point_lat) + point_lon
            if lon < cross_lon:
                inside = not inside

        j = i

    return inside


def parse_coordinates(raw_coordinates: str) -> list[tuple[float, float]]:
    polygon = []

    for raw_coord in raw_coordinates.split():
        parts = raw_coord.split(",")
        if len(parts) < 2:
            continue

        lon = float(parts[0])
        lat = float(parts[1])
        polygon.append((lon, lat))

    return polygon


def read_kml_content() -> str:
    content = KML_PATH.read_text(encoding="utf-8")
    root = ET.fromstring(content)
    href = root.find(".//kml:NetworkLink/kml:Link/kml:href", KML_NS)

    if href is None or not href.text:
        return content

    response = requests.get(href.text.strip(), timeout=10)
    response.raise_for_status()
    return response.text


@lru_cache(maxsize=1)
def load_uv_polygons() -> tuple[tuple[str, tuple[tuple[float, float], ...]], ...]:
    content = read_kml_content()
    root = ET.fromstring(content)
    polygons = []

    for placemark in root.findall(".//kml:Placemark", KML_NS):
        name = placemark.findtext("kml:name", default="", namespaces=KML_NS)
        description = placemark.findtext("kml:description", default="", namespaces=KML_NS)
        uv_number = extract_uv_number(f"{name} {description}")

        if not uv_number:
            continue

        for coordinates in placemark.findall(".//kml:Polygon//kml:outerBoundaryIs//kml:coordinates", KML_NS):
            if not coordinates.text:
                continue

            polygon = parse_coordinates(coordinates.text)
            if len(polygon) >= 3:
                polygons.append((uv_number, tuple(polygon)))

    return tuple(polygons)


def find_uv_by_geolocation(geolocalizacion: str) -> tuple[Optional[str], Optional[str]]:
    point = parse_geolocation(geolocalizacion)
    if not point:
        return None, "No se pudo leer una latitud/longitud valida en geolocalizacion."

    try:
        polygons = load_uv_polygons()
    except Exception as exc:
        return None, f"No se pudo cargar el KML de Unidades Vecinales: {exc}"

    if not polygons:
        return None, "El KML no contiene poligonos de Unidades Vecinales."

    lat, lon = point
    for uv_number, polygon in polygons:
        if point_in_polygon(lat, lon, list(polygon)):
            return uv_number, None

    return None, "La geolocalizacion no cae dentro de una UV del KML."
