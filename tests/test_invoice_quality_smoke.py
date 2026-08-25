import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app


FIXTURES = Path(__file__).parent / "fixtures"

pytestmark = [
    pytest.mark.smoke,
    pytest.mark.skipif(
        os.getenv("INVOICE_QUALITY_SMOKE") != "1" or not os.getenv("GEMINI_API_KEY"),
        reason="Smoke test real: requiere INVOICE_QUALITY_SMOKE=1 y GEMINI_API_KEY.",
    ),
]

client = TestClient(app)

MIMES = {
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


@pytest.mark.parametrize(
    "filename, espera_aceptable, issue_esperado",
    [
        ("factura_nitida.jpg", True, None),
        ("factura_digital.pdf", True, None),
        ("factura_borrosa.jpg", False, "blurry"),
        ("factura_oscura.jpg", False, "too_dark"),
        ("no_es_factura.jpg", False, "not_invoice"),
    ],
)
def test_smoke_contra_gemini(filename, espera_aceptable, issue_esperado):
    path = FIXTURES / filename
    if not path.exists():
        pytest.skip(f"Falta el archivo de prueba {filename}.")

    response = client.post(
        "/v1/invoice-quality-check",
        files={"file": (filename, path.read_bytes(), MIMES[path.suffix.lower()])},
    )

    assert response.status_code == 200
    body = response.json()
    print(f"\n{filename} -> {body['issues']} | {body['message']}")

    assert body["isAcceptable"] is espera_aceptable
    if issue_esperado:
        assert issue_esperado in body["issues"]
