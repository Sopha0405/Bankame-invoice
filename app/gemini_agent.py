import json
import logging
import os
import time

from dotenv import load_dotenv
from google import genai
from google.genai import types

from app.uv_validator import validate_uv_match
from app.validation import build_validation_status, compare_address, compare_name

load_dotenv()

logger = logging.getLogger(__name__)

MODEL_NAMES = [
    model.strip()
    for model in os.getenv(
        "GEMINI_MODELS",
        "gemini-2.5-flash,gemini-2.5-flash-lite",
    ).split(",")
    if model.strip()
]


def get_gemini_client():
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY no esta configurada.")

    return genai.Client(api_key=api_key)


def limpiar_json(texto: str):
    texto = texto.strip()
    texto = texto.replace("```json", "")
    texto = texto.replace("```", "")

    inicio = texto.find("{")
    fin = texto.rfind("}")

    if inicio == -1 or fin == -1:
        raise Exception(f"Gemini no devolvio JSON valido:\n{texto}")

    return json.loads(texto[inicio:fin + 1])


def service_type_label(service_type):
    labels = {
        1: "luz",
        2: "agua",
        3: "gas",
        4: "internet",
    }
    try:
        return labels.get(int(service_type))
    except (TypeError, ValueError):
        return None


def missing_required_fields(data: dict) -> list[str]:
    required_fields = {
        "nombre_titular": data.get("holder_name") or data.get("nombre_en_factura"),
        "direccion_factura": data.get("direccion_en_factura"),
        "tipo_servicio": data.get("basic_service_type"),
        "monto_promedio": data.get("average_amount"),
        "facturas_adeudadas": data.get("unpaid_invoice_count"),
    }

    missing = []
    for field_name, value in required_fields.items():
        if value is None or value == "":
            missing.append(field_name)

    return missing


def ejecutar_gemini(prompt: str, file_bytes: bytes, mime_type: str):
    last_error = None

    for model_name in MODEL_NAMES:
        for intento in range(3):
            try:
                client = get_gemini_client()
                response = client.models.generate_content(
                    model=model_name,
                    contents=[
                        prompt,
                        types.Part.from_bytes(
                            data=file_bytes,
                            mime_type=mime_type,
                        ),
                    ],
                    config=types.GenerateContentConfig(
                        temperature=0,
                        response_mime_type="application/json",
                    ),
                )

                if response.text:
                    usage = response.usage_metadata
                    logger.info(
                        "Gemini invoice analysis completed model=%s input_tokens=%s output_tokens=%s",
                        model_name,
                        getattr(usage, "prompt_token_count", None),
                        getattr(usage, "candidates_token_count", None),
                    )
                    return response.text

            except Exception as e:
                last_error = e
                logger.warning(
                    "Gemini attempt failed model=%s attempt=%s error_type=%s",
                    model_name,
                    intento + 1,
                    type(e).__name__,
                )
                time.sleep(2 * (intento + 1))

    raise last_error


def analizar_factura(
    file_bytes: bytes,
    mime_type: str,
    nombres: str,
    apellido_paterno: str,
    apellido_materno: str,
    direccion: str,
    geolocalizacion: str,
    tipo_vivienda: int,
):
    prompt = f"""
Eres un auditor experto de facturas bolivianas.

Analiza el archivo adjunto imagen/PDF y responde SOLO JSON valido.

CLIENTE:
Nombres: {nombres}
Apellido paterno: {apellido_paterno}
Apellido materno: {apellido_materno}
Direccion declarada: {direccion}
Geolocalizacion declarada: {geolocalizacion}
Tipo vivienda: {tipo_vivienda}

OBJETIVO:
Extraer automaticamente la informacion clave de una factura de luz o recibo de domicilio.
Los campos no detectados deben devolverse como null. No inventes datos.

DATOS OBLIGATORIOS:
- holder_name: nombre del titular de la factura.
- direccion_en_factura: direccion registrada en la factura.
- basic_service_type: tipo de servicio.
- average_amount: monto promedio de facturas.
- unpaid_invoice_count: cantidad de facturas adeudadas, pendientes, vencidas o en mora.

NOMBRE:
Extrae el nombre del titular de la factura en holder_name y nombre_en_factura.
No clasifiques la coincidencia; eso lo calcula el sistema.

DIRECCION:
Extrae la direccion real de la factura.

Debes buscar especialmente:
- UV
- U.V.
- unidad vecinal
- mz
- manzano
- lote
- barrio
- avenida
- zona
- km
- urbanizacion

IMPORTANTE SOBRE UV:
Si la factura o la direccion del cliente menciona UV o Unidad Vecinal,
extrae el numero exacto de UV.
Ejemplos:
"UV 120" => "120"
"U.V. 45" => "45"
"Unidad Vecinal 32" => "32"

No inventes UV.
Si no aparece UV, devuelve null.
No uses coordenadas inventadas.
No inventes distancia.
Solo extrae la informacion visible en la factura.

PROVEEDORES BOLIVIA:

Electricidad:
DELAPAZ, CRE, ELFEC, CESSA, SEPSA, ENDE, COBEE, SETAR

Agua:
SAGUAPAC, SEMAPA, EPSAS, COSMOL, COOPLAN, EMAPA

Gas:
YPFB, YPFB GAS, EMTAGAS

Internet:
ENTEL, TIGO, VIVA, AXS, COTAS, COMTECO, COTEL

TIPO SERVICIO:
1 Light/luz = kWh, energia, alumbrado
2 Water/agua = m3, agua, alcantarillado
3 Gas = gas natural, cargo termico
4 Internet = Mbps, fibra, internet

Prioridad para tipo de servicio:
unidad de medida > concepto > proveedor

Extrae tambien:
invoice_amount
last_month_amount
average_amount
unpaid_invoice_count
currency
billing_period
texto_extraido

Responde exactamente con este JSON:

{{
  "basic_service_type": 2,
  "service_type_label": "agua",
  "service_provider": "SAGUAPAC",
  "mensaje_validacion": null,
  "holder_name": "",
  "nombre_en_factura": "",
  "direccion_en_factura": "",
  "cliente_uv": null,
  "factura_uv": null,
  "invoice_amount": 0,
  "last_month_amount": 0,
  "average_amount": 0,
  "unpaid_invoice_count": 0,
  "currency": "BOB",
  "billing_period": "",
  "confidence": 0.98,
  "texto_extraido": ""
}}
"""

    texto = ejecutar_gemini(prompt, file_bytes, mime_type)
    data = limpiar_json(texto)
    data["holder_name"] = data.get("holder_name") or data.get("nombre_en_factura")
    data["nombre_en_factura"] = data.get("nombre_en_factura") or data.get("holder_name")
    data["service_type_label"] = data.get("service_type_label") or service_type_label(data.get("basic_service_type"))

    name_category, name_matched = compare_name(
        holder_name=data.get("holder_name"),
        nombres=nombres,
        apellido_paterno=apellido_paterno,
        apellido_materno=apellido_materno,
        tipo_vivienda=tipo_vivienda,
    )
    address_score, address_level = compare_address(
        declared_address=direccion,
        extracted_address=data.get("direccion_en_factura"),
    )

    uv_validation = validate_uv_match(
        cliente_direccion=direccion,
        factura_direccion=data.get("direccion_en_factura", ""),
        cliente_geolocalizacion=geolocalizacion,
    )

    address_matched = address_level in ("coincidencia_alta", "coincidencia_media") or uv_validation["address_matched"]

    data["invoice_name_matched"] = name_matched
    data["name_match_category"] = name_category
    data["address_similarity_score"] = address_score
    data["address_match_level"] = address_level
    data["address_matched"] = address_matched
    data["cliente_uv"] = uv_validation["cliente_uv"]
    data["factura_uv"] = uv_validation["factura_uv"]

    if uv_validation["mensaje"]:
        data["mensaje_validacion"] = uv_validation["mensaje"]

    data["missing_fields"] = missing_required_fields(data)
    data["validation_status"] = build_validation_status(
        missing_fields=data["missing_fields"],
        name_matched=name_matched,
        address_level=address_level,
        uv_matched=uv_validation["address_matched"],
    )
    data["review_required"] = data["validation_status"] != "validacion_aprobada"
    data["normalized_address"] = data.get("direccion_en_factura")
    data["latitude"] = None
    data["longitude"] = None
    data["distance_meters"] = None

    return data
