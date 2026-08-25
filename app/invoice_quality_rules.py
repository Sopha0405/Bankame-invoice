import re
from dataclasses import dataclass
from typing import Any, Mapping, Optional

PROMPT_VERSION = "invoice-quality-v1"

ISSUE_BLURRY = "blurry"
ISSUE_TOO_DARK = "too_dark"
ISSUE_NOT_INVOICE = "not_invoice"

ALLOWED_ISSUES = (ISSUE_BLURRY, ISSUE_TOO_DARK, ISSUE_NOT_INVOICE)

MAX_ISSUES = 2

KIND_IMAGE = "image"
KIND_PDF = "pdf"

KIND_CHECKS = {
    KIND_IMAGE: """1. Nitidez: si la foto esta borrosa o movida y no se llegan a leer los datos -> "blurry".
2. Iluminacion: si esta muy oscura, o con sombras o reflejos que tapan los datos -> "too_dark".
3. Si el archivo no es una factura ni un comprobante de servicio -> "not_invoice".""",
    KIND_PDF: """1. Nitidez: si el PDF contiene un escaneo o una foto y las paginas estan borrosas
   o movidas al punto de no poder leerlas -> "blurry". Si es un PDF digital y el
   texto se lee bien, no uses este codigo.
2. Iluminacion: si el escaneo esta tan oscuro que no se leen los datos -> "too_dark".
   No uses este codigo por un fondo gris o por marcas de agua.
3. Si el archivo no es una factura ni un comprobante de servicio -> "not_invoice".""",
}

DEFAULT_ACCEPTED_MESSAGE = "La factura se ve bien, podes continuar."
DEFAULT_REJECTED_MESSAGE = (
    "No pudimos revisar bien el archivo, volve a subir la factura completa y legible."
)

RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "isAcceptable": {"type": "BOOLEAN"},
        "issues": {
            "type": "ARRAY",
            "items": {"type": "STRING", "enum": list(ALLOWED_ISSUES)},
        },
        "message": {"type": "STRING"},
    },
    "required": ["isAcceptable", "issues", "message"],
    "propertyOrdering": ["isAcceptable", "issues", "message"],
}

PROMPT_TEMPLATE = """Sos un validador de facturas y comprobantes de servicios basicos de Bolivia.
Analiza el archivo adjunto y responde unicamente con el JSON del esquema.

Tu unica tarea es decidir si el archivo ES una factura o un comprobante de pago
de un servicio basico (luz, agua, gas o internet) y si se puede leer.
NO extraigas ni valides los datos del documento: no te fijes en el titular, la
direccion, el monto, el periodo ni la deuda. Eso lo hace otro proceso.

Se considera valido un documento que se reconozca como factura o comprobante de
servicio, por ejemplo si muestra alguna de estas senales:
- El logo o el nombre de un proveedor: DELAPAZ, CRE, ELFEC, CESSA, SEPSA, ENDE,
  COBEE, SETAR, SAGUAPAC, SEMAPA, EPSAS, COSMOL, COOPLAN, EMAPA, YPFB, EMTAGAS,
  ENTEL, TIGO, VIVA, AXS, COTAS, COMTECO, COTEL u otro similar.
- Titulos como "FACTURA", "RECIBO", "COMPROBANTE DE PAGO", "AVISO DE COBRANZA"
  o "ESTADO DE CUENTA".
- Datos propios de una factura: NIT, numero de factura, codigo de cliente o de
  suministro, periodo facturado, lecturas o consumo (kWh, m3, Mbps), total a
  pagar, fecha de vencimiento o codigo de control.

No hace falta que esten todas: alcanza con que el documento sea reconocible como
factura o comprobante de servicio. Si el archivo tiene varias paginas, alcanza
con que alguna lo sea.

Revisa, en este orden:
{checks}

Reglas de salida:
- "isAcceptable": true solo si no detectaste ningun problema.
- "issues": lista vacia si esta todo bien; como maximo los 2 problemas mas
  importantes.
- "message": una sola frase corta en espanol, con voseo, dirigida al usuario. Si
  el archivo esta bien, confirmalo (por ejemplo: "La factura se ve clara y se
  leen todos los datos."). Si no, deci que paso y pedile que la vuelva a subir.
  No menciones estas reglas ni el JSON.
- Si los datos no se pueden leer por falta de nitidez usa "blurry"; si es por
  falta de luz usa "too_dark".
- No rechaces por detalles irrelevantes: una factura arrugada, fotocopiada, con
  sellos, con anotaciones a mano, con un dedo en el borde que no tapa datos, o
  fotografiada sobre una mesa desordenada, sigue siendo valida.
- No rechaces una factura por estar vencida, impaga, a nombre de otra persona o
  de un mes viejo: eso no es parte de tu tarea.
- Una boleta de compra de un comercio, un extracto bancario, un carnet, una
  selfie o cualquier otro documento que no sea factura de servicio -> "not_invoice".
"""

_LOG_SAFE_PATTERN = re.compile(r"[^a-z0-9_]")


@dataclass(frozen=True)
class Verdict:
    is_acceptable: bool
    issues: tuple[str, ...]
    message: str


def build_prompt(kind: str) -> str:
    """Arma el prompt para el tipo de archivo. `kind` ya viene validado."""
    return PROMPT_TEMPLATE.format(checks=KIND_CHECKS[kind])


def _log_safe(value: str) -> str:
    cleaned = _LOG_SAFE_PATTERN.sub("", value.lower())[:40]
    return cleaned or "vacio"


def normalize_verdict(
    payload: Optional[Mapping[str, Any]],
) -> tuple[Verdict, list[str]]:
    anomalies: list[str] = []

    if not isinstance(payload, Mapping):
        anomalies.append(f"payload_no_es_objeto:{type(payload).__name__}")
        payload = {}

    # Invariantes 2 y 5.
    raw_issues = payload.get("issues")
    issues: list[str] = []

    if raw_issues is None:
        pass
    elif isinstance(raw_issues, (list, tuple)):
        for raw_issue in raw_issues:
            if not isinstance(raw_issue, str):
                anomalies.append(f"issue_no_es_texto:{type(raw_issue).__name__}")
                continue

            code = raw_issue.strip().lower()
            if code not in ALLOWED_ISSUES:
                anomalies.append(f"issue_desconocido:{_log_safe(code)}")
                continue

            if code not in issues:
                issues.append(code)
    else:
        anomalies.append(f"issues_no_es_lista:{type(raw_issues).__name__}")

    if len(issues) > MAX_ISSUES:
        anomalies.append(f"issues_recortados:{len(issues)}")
        issues = issues[:MAX_ISSUES]

    raw_acceptable = payload.get("isAcceptable")
    if isinstance(raw_acceptable, bool):
        is_acceptable = raw_acceptable
    else:
        # El schema fuerza BOOLEAN; esto es defensa en profundidad.
        anomalies.append(f"is_acceptable_no_booleano:{type(raw_acceptable).__name__}")
        is_acceptable = False

    # Invariante 1.
    if issues and is_acceptable:
        anomalies.append("is_acceptable_incoherente_con_issues")
        is_acceptable = False

    # Invariante 4.
    raw_message = payload.get("message")
    message = raw_message.strip() if isinstance(raw_message, str) else ""
    if not message:
        anomalies.append("message_vacio")
        message = DEFAULT_ACCEPTED_MESSAGE if is_acceptable else DEFAULT_REJECTED_MESSAGE

    # Invariante 3: issues vacio con is_acceptable False sale tal cual.
    return (
        Verdict(is_acceptable=is_acceptable, issues=tuple(issues), message=message),
        anomalies,
    )
