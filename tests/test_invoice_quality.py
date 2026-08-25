from types import SimpleNamespace

from fastapi.testclient import TestClient
from google.genai.errors import ClientError, ServerError

from app.invoice_quality_rules import (
    DEFAULT_ACCEPTED_MESSAGE,
    DEFAULT_REJECTED_MESSAGE,
    KIND_IMAGE,
    KIND_PDF,
    build_prompt,
    normalize_verdict,
)
from app.main import app


client = TestClient(app)

JPEG_MAGIC = b"\xff\xd8\xff"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
PDF_MAGIC = b"%PDF-1.7"

VALID_JPEG = JPEG_MAGIC + b"\x00" * 512
VALID_PNG = PNG_MAGIC + b"\x00" * 512
VALID_PDF = PDF_MAGIC + b"\n" + b"\x00" * 512
# Contenedor RIFF: "RIFF" + tamano (4 bytes) + "WEBP".
VALID_WEBP = b"RIFF" + b"\x00\x02\x00\x00" + b"WEBP" + b"\x00" * 512

OK_PAYLOAD = (
    '{"isAcceptable": true, "issues": [], '
    '"message": "La factura se ve clara y se leen todos los datos."}'
)


def fake_response(text, finish_reason="STOP", with_candidates=True):
    candidates = (
        [SimpleNamespace(finish_reason=finish_reason)] if with_candidates else []
    )

    return SimpleNamespace(
        text=text,
        candidates=candidates,
        prompt_feedback=SimpleNamespace(block_reason=None),
        usage_metadata=SimpleNamespace(
            prompt_token_count=410,
            candidates_token_count=38,
        ),
    )


def sdk_error(error_class, code, message="upstream error"):
    error = error_class.__new__(error_class)
    Exception.__init__(error, message)
    error.code = code
    error.message = message
    error.status = "RESOURCE_EXHAUSTED" if code == 429 else "ERROR"
    error.details = None
    error.response = None
    return error


class FakeModels:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    async def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0) if self.outcomes else self.last_outcome
        self.last_outcome = outcome

        if isinstance(outcome, BaseException):
            raise outcome

        return outcome


class FakeClient:
    def __init__(self, outcomes):
        self.models = FakeModels(outcomes)
        self.aio = SimpleNamespace(models=self.models)


def install_fake_client(monkeypatch, *outcomes):
    fake = FakeClient(outcomes)
    monkeypatch.setattr("app.invoice_quality_agent.get_gemini_client", lambda: fake)
    # Sin espera real entre reintentos.
    monkeypatch.setattr("app.invoice_quality_agent.retry_delay_seconds", lambda: 0)
    return fake


def post_check(content=VALID_JPEG, mime="image/jpeg", filename="factura.jpg", **extra):
    return client.post(
        "/v1/invoice-quality-check",
        files={"file": (filename, content, mime)},
        data=dict(extra),
    )


def test_payload_feliz_devuelve_factura_aceptada(monkeypatch):
    install_fake_client(monkeypatch, fake_response(OK_PAYLOAD))

    response = post_check()

    assert response.status_code == 200
    body = response.json()
    assert body["isAcceptable"] is True
    assert body["issues"] == []
    assert body["message"]
    assert body["requestId"]


def test_documento_que_no_es_factura_devuelve_rechazo(monkeypatch):
    install_fake_client(
        monkeypatch,
        fake_response(
            '{"isAcceptable": false, "issues": ["not_invoice"], '
            '"message": "Esto no parece una factura de servicio, subi la factura."}'
        ),
    )

    response = post_check()

    assert response.status_code == 200
    body = response.json()
    assert body["isAcceptable"] is False
    assert body["issues"] == ["not_invoice"]


def test_foto_borrosa_y_oscura_devuelve_los_dos_codigos(monkeypatch):
    install_fake_client(
        monkeypatch,
        fake_response(
            '{"isAcceptable": false, "issues": ["blurry", "too_dark"], '
            '"message": "No se lee la factura, sacala de nuevo con mas luz."}'
        ),
    )

    body = post_check().json()

    assert body["isAcceptable"] is False
    assert body["issues"] == ["blurry", "too_dark"]


def test_codigo_desconocido_se_descarta_del_array(monkeypatch):
    install_fake_client(
        monkeypatch,
        fake_response(
            '{"isAcceptable": false, "issues": ["not_invoice", "expired"], '
            '"message": "Subi una factura de servicio."}'
        ),
    )

    body = post_check().json()

    assert body["issues"] == ["not_invoice"]


def test_json_malformado_devuelve_503(monkeypatch):
    install_fake_client(monkeypatch, fake_response("no soy json"))

    response = post_check()

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "upstream_unavailable"


def test_respuesta_sin_candidates_devuelve_503(monkeypatch):
    install_fake_client(monkeypatch, fake_response(OK_PAYLOAD, with_candidates=False))

    assert post_check().status_code == 503


def test_finish_reason_de_seguridad_devuelve_503(monkeypatch):
    install_fake_client(monkeypatch, fake_response(OK_PAYLOAD, finish_reason="SAFETY"))

    assert post_check().status_code == 503


def test_timeout_devuelve_503_despues_de_un_reintento(monkeypatch):
    fake = install_fake_client(
        monkeypatch,
        TimeoutError("timeout"),
        TimeoutError("timeout"),
    )

    response = post_check()

    assert response.status_code == 503
    assert len(fake.models.calls) == 2


def test_rate_limit_devuelve_429_con_retry_after(monkeypatch):
    install_fake_client(
        monkeypatch,
        sdk_error(ClientError, 429),
        sdk_error(ClientError, 429),
    )

    response = post_check()

    assert response.status_code == 429
    assert response.json()["detail"]["code"] == "upstream_rate_limited"
    assert response.headers["Retry-After"] == "10"


def test_error_de_servidor_se_reintenta_y_puede_salir_bien(monkeypatch):
    fake = install_fake_client(
        monkeypatch,
        sdk_error(ServerError, 503),
        fake_response(OK_PAYLOAD),
    )

    response = post_check()

    assert response.status_code == 200
    assert len(fake.models.calls) == 2


def test_error_de_cliente_no_reintentable_no_reintenta(monkeypatch):
    fake = install_fake_client(monkeypatch, sdk_error(ClientError, 403))

    response = post_check()

    assert response.status_code == 503
    assert len(fake.models.calls) == 1


def test_request_id_se_propaga_desde_el_backend(monkeypatch):
    install_fake_client(monkeypatch, fake_response(OK_PAYLOAD))

    body = post_check(request_id="bankame-123").json()

    assert body["requestId"] == "bankame-123"


def test_request_id_se_genera_si_el_backend_no_lo_manda(monkeypatch):
    install_fake_client(monkeypatch, fake_response(OK_PAYLOAD))

    body = post_check().json()

    assert len(body["requestId"]) == 32


# --- Prompt segun tipo de archivo ---------------------------------------


def test_pdf_usa_el_prompt_de_pdf(monkeypatch):
    fake = install_fake_client(monkeypatch, fake_response(OK_PAYLOAD))

    response = post_check(content=VALID_PDF, mime="application/pdf", filename="f.pdf")

    assert response.status_code == 200
    prompt = fake.models.calls[0]["contents"][0]
    assert prompt == build_prompt(KIND_PDF)
    assert "PDF digital" in prompt


def test_imagen_usa_el_prompt_de_imagen(monkeypatch):
    fake = install_fake_client(monkeypatch, fake_response(OK_PAYLOAD))

    post_check()

    prompt = fake.models.calls[0]["contents"][0]
    assert prompt == build_prompt(KIND_IMAGE)


def test_los_dos_prompts_piden_nitidez_e_iluminacion():
    for kind in (KIND_IMAGE, KIND_PDF):
        prompt = build_prompt(kind)
        assert '"blurry"' in prompt
        assert '"too_dark"' in prompt
        assert '"not_invoice"' in prompt


# --- Invariantes del normalizador ---------------------------------------


def test_invariante_1_issues_no_vacio_fuerza_no_aceptable():
    verdict, anomalies = normalize_verdict(
        {"isAcceptable": True, "issues": ["not_invoice"], "message": "hola"}
    )

    assert verdict.is_acceptable is False
    assert "is_acceptable_incoherente_con_issues" in anomalies


def test_invariante_2_codigo_fuera_del_vocabulario_se_descarta_y_se_reporta():
    verdict, anomalies = normalize_verdict(
        {"isAcceptable": False, "issues": ["cropped"], "message": "hola"}
    )

    assert verdict.issues == ()
    assert any(a.startswith("issue_desconocido") for a in anomalies)


def test_invariante_3_issues_vacio_con_rechazo_es_legitimo():
    verdict, anomalies = normalize_verdict(
        {"isAcceptable": False, "issues": [], "message": "No pudimos leerla."}
    )

    assert verdict.is_acceptable is False
    assert verdict.issues == ()
    assert anomalies == []


def test_invariante_4_message_vacio_se_rellena_segun_el_caso():
    aceptado, _ = normalize_verdict({"isAcceptable": True, "issues": [], "message": " "})
    rechazado, _ = normalize_verdict(
        {"isAcceptable": False, "issues": ["blurry"], "message": ""}
    )

    assert aceptado.message == DEFAULT_ACCEPTED_MESSAGE
    assert rechazado.message == DEFAULT_REJECTED_MESSAGE


def test_invariante_5_issues_se_deduplica_y_se_recorta_a_dos():
    verdict, anomalies = normalize_verdict(
        {
            "isAcceptable": False,
            "issues": ["blurry", "blurry", "too_dark", "not_invoice"],
            "message": "hola",
        }
    )

    assert verdict.issues == ("blurry", "too_dark")
    assert any(a.startswith("issues_recortados") for a in anomalies)


def test_payload_que_no_es_objeto_no_revienta():
    verdict, anomalies = normalize_verdict(None)

    assert verdict.is_acceptable is False
    assert any(a.startswith("payload_no_es_objeto") for a in anomalies)


# --- Validacion de entrada ----------------------------------------------


def test_sin_archivo_devuelve_422_del_framework():
    assert client.post("/v1/invoice-quality-check", data={}).status_code == 422


def test_mime_prohibido_devuelve_415():
    response = post_check(content=b"GIF89a", mime="image/gif", filename="f.gif")

    assert response.status_code == 415
    assert response.json()["detail"]["code"] == "invalid_request"


def test_archivo_de_11_mb_devuelve_400_invalid_file():
    response = post_check(content=JPEG_MAGIC + b"\x00" * (11 * 1024 * 1024))

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_file"


def test_archivo_corrupto_devuelve_400_invalid_file():
    response = post_check(content=b"no soy una imagen")

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_file"


def test_pdf_con_mime_de_imagen_devuelve_400_invalid_file():
    response = post_check(content=VALID_PDF, mime="image/png", filename="f.png")

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_file"


def test_archivo_vacio_devuelve_400_invalid_file():
    response = post_check(content=b"")

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_file"


def test_png_valido_es_aceptado(monkeypatch):
    install_fake_client(monkeypatch, fake_response(OK_PAYLOAD))

    response = post_check(content=VALID_PNG, mime="image/png", filename="f.png")

    assert response.status_code == 200


def test_webp_valido_es_aceptado(monkeypatch):
    install_fake_client(monkeypatch, fake_response(OK_PAYLOAD))

    response = post_check(content=VALID_WEBP, mime="image/webp", filename="f.webp")

    assert response.status_code == 200


def test_webp_con_riff_pero_sin_webp_devuelve_400():
    # Un WAV tambien es RIFF: el prefijo solo no alcanza.
    riff_wav = b"RIFF" + b"\x00\x02\x00\x00" + b"WAVE" + b"\x00" * 64

    response = post_check(content=riff_wav, mime="image/webp", filename="f.webp")

    assert response.status_code == 400


def test_alias_image_jpg_se_acepta(monkeypatch):
    install_fake_client(monkeypatch, fake_response(OK_PAYLOAD))

    response = post_check(mime="image/jpg")

    assert response.status_code == 200


def test_el_mime_enviado_a_gemini_es_el_normalizado(monkeypatch):
    fake = install_fake_client(monkeypatch, fake_response(OK_PAYLOAD))

    post_check(mime="image/jpg")

    part = fake.models.calls[0]["contents"][1]
    assert part.inline_data.mime_type == "image/jpeg"
