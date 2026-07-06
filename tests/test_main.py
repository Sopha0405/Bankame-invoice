from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_root_returns_service_status():
    response = client.get("/")

    assert response.status_code == 200
    assert response.json() == {
        "service": "Factura Analyzer AI",
        "status": "running",
        "docs": "/docs",
        "health": "/health",
    }


def test_health_returns_ok():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_invoice_endpoint_rejects_unsupported_file_type():
    response = client.post(
        "/ocr/factura/",
        files={"file": ("factura.txt", b"contenido", "text/plain")},
        data={
            "nombres": "Juan",
            "apellido_paterno": "Perez",
            "apellido_materno": "Lopez",
            "direccion": "UV 1",
            "geolocalizacion": "-17.7833,-63.1821",
            "tipo_vivienda": "1",
        },
    )

    assert response.status_code == 415


def test_invoice_endpoint_maps_gemini_error(monkeypatch):
    from app.gemini_agent import GeminiAnalysisError

    def fail_analysis(**kwargs):
        raise GeminiAnalysisError("quota exceeded", 429)

    monkeypatch.setattr("app.main.analizar_factura", fail_analysis)

    response = client.post(
        "/ocr/factura/",
        files={"file": ("factura.pdf", b"%PDF-1.4", "application/pdf")},
        data={
            "nombres": "Juan",
            "apellido_paterno": "Perez",
            "apellido_materno": "Lopez",
            "direccion": "UV 1",
            "geolocalizacion": "-17.7833,-63.1821",
            "tipo_vivienda": "1",
        },
    )

    assert response.status_code == 429
    assert response.json()["detail"] == "Limite de Gemini excedido temporalmente"


def test_average_amount_uses_last_month_amount_when_average_is_zero():
    from app.gemini_agent import apply_average_amount_fallback

    data = {
        "average_amount": 0,
        "last_month_amount": 187.5,
        "invoice_amount": 220,
    }

    apply_average_amount_fallback(data)

    assert data["average_amount"] == 187.5


def test_average_amount_uses_invoice_amount_when_last_month_is_missing():
    from app.gemini_agent import apply_average_amount_fallback

    data = {
        "average_amount": 0,
        "last_month_amount": 0,
        "invoice_amount": "220.4",
    }

    apply_average_amount_fallback(data)

    assert data["average_amount"] == 220.4
