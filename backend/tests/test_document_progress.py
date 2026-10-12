"""Las etapas visibles en UI proceden del pipeline real de ingestión (#25)."""

import json

import pytest
import test_api_generate as generaciones

pytestmark = pytest.mark.integration_mock
entorno = generaciones.entorno


def test_etapas_documentales_sse_corresponden_al_pipeline(entorno):
    e = entorno
    res = e.cliente.post(
        "/api/documents/upload",
        headers=e.headers,
        json={
            "documento_titulo": "Etapas",
            "documento_contenido": "Una VCN conserva subredes privadas aisladas. " * 25,
        },
    )
    assert res.status_code == 202
    generaciones.esperar(e, res.json())
    eventos = e.cliente.get(res.json()["events_url"], headers=e.headers)
    pasos = [
        json.loads(linea.removeprefix("data: "))["step"]
        for linea in eventos.text.splitlines()
        if linea.startswith("data: ")
    ]
    etapas = [p for p in pasos if p in {"fuente", "parser", "chunker", "indice", "persistencia"}]
    assert etapas == ["fuente", "parser", "chunker", "indice", "persistencia"]
