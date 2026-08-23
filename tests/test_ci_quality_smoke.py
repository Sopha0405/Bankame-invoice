import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app


FIXTURES = Path(__file__).parent / "fixtures"

pytestmark = [
    pytest.mark.smoke,
    pytest.mark.skipif(
        os.getenv("CI_QUALITY_SMOKE") != "1" or not os.getenv("GEMINI_API_KEY"),
        reason="Smoke test real: requiere CI_QUALITY_SMOKE=1 y GEMINI_API_KEY.",
    ),
]

client = TestClient(app)


@pytest.mark.parametrize(
    "filename, espera_aceptable, issue_esperado",
    [
        ("ci_nitida.jpg", True, None),
        ("ci_borrosa.jpg", False, "blurry"),
        ("ci_oscura.jpg", False, "too_dark"),
        ("no_es_carnet.jpg", False, "not_identity_card"),
    ],
)
def test_smoke_contra_gemini(filename, espera_aceptable, issue_esperado):
    path = FIXTURES / filename
    if not path.exists():
        pytest.skip(f"Falta la imagen de prueba {filename}.")

    response = client.post(
        "/v1/ci-quality-check",
        files={"image": (filename, path.read_bytes(), "image/jpeg")},
        data={"side": "front"},
    )

    assert response.status_code == 200
    body = response.json()
    print(f"\n{filename} -> {body['issues']} | {body['message']} | {body['latencyMs']}ms")

    assert body["isAcceptable"] is espera_aceptable
    if issue_esperado:
        assert issue_esperado in body["issues"]
