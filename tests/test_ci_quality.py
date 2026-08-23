import asyncio
from types import SimpleNamespace

from fastapi.testclient import TestClient
from google.genai.errors import ClientError, ServerError

from app.main import app


client = TestClient(app)

JPEG_MAGIC = b"\xff\xd8\xff"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

VALID_JPEG = JPEG_MAGIC + b"\x00" * 512


def fake_response(text, finish_reason="STOP", with_candidates=True):
    candidates = (
        [SimpleNamespace(finish_reason=finish_reason)] if with_candidates else []
    )

    return SimpleNamespace(
        text=text,
        candidates=candidates,
        prompt_feedback=SimpleNamespace(block_reason=None),
        usage_metadata=SimpleNamespace(
            prompt_token_count=310,
            candidates_token_count=42,
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
    monkeypatch.setattr("app.ci_quality_agent.get_gemini_client", lambda: fake)
    # Sin espera real entre reintentos.
    monkeypatch.setattr("app.ci_quality_agent.retry_delay_seconds", lambda: 0)
    return fake


def post_check(side="front", content=VALID_JPEG, mime="image/jpeg", **extra):
    data = {"side": side}
    data.update(extra)

    return client.post(
        "/v1/ci-quality-check",
        files={"image": ("ci.jpg", content, mime)},
        data=data,
    )


def test_payload_feliz_devuelve_foto_aceptada(monkeypatch):
    install_fake_client(
        monkeypatch,
        fake_response(
            '{"isAcceptable": true, "issues": [], '
            '"message": "La foto se ve clara y se leen todos los datos."}'
        ),
    )

    response = post_check(request_id="01JABCDEF")
    body = response.json()

    assert response.status_code == 200
    assert body["isAcceptable"] is True
    assert body["issues"] == []
    assert body["message"] == "La foto se ve clara y se leen todos los datos."
    assert body["requestId"] == "01JABCDEF"


def test_payload_con_issues_devuelve_rechazo(monkeypatch):
    install_fake_client(
        monkeypatch,
        fake_response(
            '{"isAcceptable": false, "issues": ["blurry"], '
            '"message": "La foto salio borrosa, volve a tomarla."}'
        ),
    )

    body = post_check().json()

    assert body["isAcceptable"] is False
    assert body["issues"] == ["blurry"]


def test_codigo_desconocido_se_descarta_del_array(monkeypatch):
    install_fake_client(
        monkeypatch,
        fake_response(
            '{"isAcceptable": false, "issues": ["glare", "too_dark"], '
            '"message": "Hay mucho reflejo."}'
        ),
    )

    body = post_check().json()

    assert body["issues"] == ["too_dark"]
    assert body["isAcceptable"] is False


def test_json_malformado_devuelve_503(monkeypatch):
    install_fake_client(monkeypatch, fake_response("no soy json {{"))

    response = post_check()

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "upstream_unavailable"


def test_respuesta_sin_candidates_devuelve_503(monkeypatch):
    install_fake_client(monkeypatch, fake_response(None, with_candidates=False))

    response = post_check()

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "upstream_unavailable"


def test_finish_reason_de_seguridad_devuelve_503(monkeypatch):
    install_fake_client(monkeypatch, fake_response("{}", finish_reason="SAFETY"))

    response = post_check()

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "upstream_unavailable"


def test_timeout_devuelve_503_despues_de_un_reintento(monkeypatch):
    fake = install_fake_client(
        monkeypatch,
        asyncio.TimeoutError(),
        asyncio.TimeoutError(),
    )

    response = post_check()

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "upstream_unavailable"
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
    assert response.headers["retry-after"] == "10"


def test_error_de_servidor_se_reintenta_y_puede_salir_bien(monkeypatch):
    fake = install_fake_client(
        monkeypatch,
        sdk_error(ServerError, 500),
        fake_response('{"isAcceptable": true, "issues": [], "message": "Se ve bien."}'),
    )

    response = post_check()

    assert response.status_code == 200
    assert len(fake.models.calls) == 2


def test_error_de_cliente_no_reintentable_no_reintenta(monkeypatch):
    fake = install_fake_client(monkeypatch, sdk_error(ClientError, 403))

    response = post_check()

    assert response.status_code == 503
    assert len(fake.models.calls) == 1


def test_modelo_usado_es_el_primero_de_gemini_models(monkeypatch):
    monkeypatch.setattr(
        "app.ci_quality_agent.MODEL_NAMES",
        ["gemini-9.9-turbo", "gemini-9.9-pro"],
    )
    fake = install_fake_client(
        monkeypatch,
        fake_response('{"isAcceptable": true, "issues": [], "message": "Se ve bien."}'),
    )

    post_check()

    assert fake.models.calls[0]["model"] == "gemini-9.9-turbo"


def test_el_reintento_repite_el_primer_modelo_y_no_escala_al_fallback(monkeypatch):
    monkeypatch.setattr(
        "app.ci_quality_agent.MODEL_NAMES",
        ["gemini-9.9-turbo", "gemini-9.9-pro"],
    )
    fake = install_fake_client(
        monkeypatch,
        sdk_error(ServerError, 500),
        fake_response('{"isAcceptable": true, "issues": [], "message": "Se ve bien."}'),
    )

    response = post_check()

    assert response.status_code == 200
    assert [call["model"] for call in fake.models.calls] == [
        "gemini-9.9-turbo",
        "gemini-9.9-turbo",
    ]


def test_modelo_cae_al_fallback_si_gemini_models_esta_vacia(monkeypatch):
    monkeypatch.setattr("app.ci_quality_agent.MODEL_NAMES", [])
    fake = install_fake_client(
        monkeypatch,
        fake_response('{"isAcceptable": true, "issues": [], "message": "Se ve bien."}'),
    )

    post_check()

    assert fake.models.calls[0]["model"] == "gemini-2.5-flash"


def test_request_id_se_genera_si_el_backend_no_lo_manda(monkeypatch):
    install_fake_client(
        monkeypatch,
        fake_response('{"isAcceptable": true, "issues": [], "message": "Se ve bien."}'),
    )

    assert post_check().json()["requestId"]


def test_side_back_usa_el_prompt_del_reverso(monkeypatch):
    fake = install_fake_client(
        monkeypatch,
        fake_response('{"isAcceptable": true, "issues": [], "message": "Se ve bien."}'),
    )

    post_check(side="back")

    prompt = fake.models.calls[0]["contents"][0]
    assert "REVERSO" in prompt
    assert "ANVERSO" not in prompt


def test_invariante_1_issues_no_vacio_fuerza_no_aceptable():
    from app.ci_quality_rules import normalize_verdict

    verdict, _ = normalize_verdict(
        {"isAcceptable": True, "issues": ["blurry"], "message": "Salio borrosa."}
    )

    assert verdict.is_acceptable is False


def test_invariante_2_codigo_fuera_del_vocabulario_se_descarta_y_se_reporta():
    from app.ci_quality_rules import normalize_verdict

    verdict, anomalies = normalize_verdict(
        {"isAcceptable": False, "issues": ["cropped", "blurry"], "message": "Mal."}
    )

    assert verdict.issues == ("blurry",)
    assert any(anomaly.startswith("issue_desconocido:") for anomaly in anomalies)


def test_invariante_3_issues_vacio_con_rechazo_es_legitimo():
    from app.ci_quality_rules import normalize_verdict

    verdict, _ = normalize_verdict(
        {"isAcceptable": False, "issues": [], "message": "No se ve el carnet completo."}
    )

    assert verdict.is_acceptable is False
    assert verdict.issues == ()
    assert verdict.message == "No se ve el carnet completo."


def test_invariante_4_message_vacio_se_rellena_segun_el_caso():
    from app.ci_quality_rules import (
        DEFAULT_ACCEPTED_MESSAGE,
        DEFAULT_REJECTED_MESSAGE,
        normalize_verdict,
    )

    aceptada, _ = normalize_verdict(
        {"isAcceptable": True, "issues": [], "message": "   "}
    )
    rechazada, _ = normalize_verdict(
        {"isAcceptable": False, "issues": ["too_dark"], "message": None}
    )

    assert aceptada.message == DEFAULT_ACCEPTED_MESSAGE
    assert rechazada.message == DEFAULT_REJECTED_MESSAGE


def test_invariante_5_issues_se_deduplica_y_se_recorta_a_dos():
    from app.ci_quality_rules import normalize_verdict

    verdict, _ = normalize_verdict(
        {
            "isAcceptable": False,
            "issues": [
                "blurry",
                "blurry",
                "too_dark",
                "not_identity_card",
            ],
            "message": "Mal.",
        }
    )

    assert verdict.issues == ("blurry", "too_dark")


# --- Validacion de entrada ----------------------------------------------


def test_side_invalido_devuelve_400():
    response = post_check(side="izquierda")

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_request"


def test_sin_archivo_devuelve_422_del_framework():
    response = client.post("/v1/ci-quality-check", data={"side": "front"})

    assert response.status_code == 422


def test_mime_prohibido_devuelve_415():
    response = post_check(content=b"%PDF-1.4", mime="application/pdf")

    assert response.status_code == 415
    assert response.json()["detail"]["code"] == "invalid_request"


def test_archivo_de_9_mb_devuelve_400_invalid_image():
    response = post_check(content=JPEG_MAGIC + b"\x00" * (9 * 1024 * 1024))

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_image"


def test_archivo_corrupto_devuelve_400_invalid_image():
    response = post_check(content=b"esto no es una imagen")

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_image"


def test_archivo_vacio_devuelve_400_invalid_image():
    response = post_check(content=b"")

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_image"


def test_png_valido_es_aceptado(monkeypatch):
    install_fake_client(
        monkeypatch,
        fake_response('{"isAcceptable": true, "issues": [], "message": "Se ve bien."}'),
    )

    response = post_check(content=PNG_MAGIC + b"\x00" * 256, mime="image/png")

    assert response.status_code == 200
