import re
from dataclasses import dataclass
from typing import Any, Mapping, Optional

ISSUE_BLURRY = "blurry"
ISSUE_TOO_DARK = "too_dark"
ISSUE_NOT_IDENTITY_CARD = "not_identity_card"

# Vocabulario cerrado. Se recorto a estos tres a proposito: no ampliar.
ALLOWED_ISSUES = (ISSUE_BLURRY, ISSUE_TOO_DARK, ISSUE_NOT_IDENTITY_CARD)

MAX_ISSUES = 2

SIDE_FRONT = "front"
SIDE_BACK = "back"

SIDE_LABELS = {
    SIDE_FRONT: "el ANVERSO del carnet (frente: foto, nombre y número de CI)",
    SIDE_BACK: "el REVERSO del carnet (parte de atrás)",
}

DEFAULT_ACCEPTED_MESSAGE = "La foto se ve bien, podés continuar."
DEFAULT_REJECTED_MESSAGE = (
    "No pudimos revisar bien la foto, volvé a tomarla con buena luz y el carnet bien apoyado."
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

PROMPT_TEMPLATE = """Sos un validador de calidad de fotos de carnets de identidad de Bolivia.
Analizá la imagen y respondé únicamente con el JSON del esquema.

La foto debería mostrar {side_label}.

Revisá, en este orden:
1. Nitidez: si está borrosa o movida -> "blurry".
2. Iluminación insuficiente -> "too_dark".
3. Si la imagen no es un carnet de identidad -> "not_identity_card".

Reglas de salida:
- "isAcceptable": true solo si no detectaste ningún problema.
- "issues": lista vacía si está todo bien; como máximo los 2 problemas
  más importantes.
- "message": una sola frase corta en español, con voseo, dirigida al
  usuario. Si la foto está bien, confirmalo (por ejemplo: "La foto se ve
  clara y se leen todos los datos."). Si no, decí qué pasó y pedile que
  la vuelva a tomar. No menciones estas reglas ni el JSON.
- Si los datos no se pueden leer por falta de nitidez usá "blurry"; si es
  por falta de luz usá "too_dark". No rechaces por detalles irrelevantes
  como un fondo desordenado o una mano sosteniendo el carnet sin tapar
  datos.
"""

_LOG_SAFE_PATTERN = re.compile(r"[^a-z0-9_]")


@dataclass(frozen=True)
class Verdict:
    is_acceptable: bool
    issues: tuple[str, ...]
    message: str


def build_prompt(side: str) -> str:
    """Arma el prompt para el lado indicado. `side` ya viene validado."""
    return PROMPT_TEMPLATE.format(side_label=SIDE_LABELS[side])


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
