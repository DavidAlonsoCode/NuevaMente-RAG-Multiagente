"""La compatibilidad de todo el espacio no autoriza fingerprints ni vínculos arbitrarios."""

import hashlib
import json

import pytest
import test_space_idempotency as space_cases
from test_space_idempotency import (
    HISTORICA_AB,
    congelado,
    intencion,
    pedir,
    prohibir_admision,
    sembrar_historica,
    terminado,
)

pytestmark = pytest.mark.integration_mock
espacio = space_cases.espacio


@pytest.mark.parametrize("variante", ["hash", "fuentes", "primario", "ajeno", "alcance_ids", "alcance"])
def test_procedencia_incompatible_rechaza_sin_reescribir_ni_admitir(espacio, monkeypatch, variante):
    e = espacio
    e.documento("doc_a")
    e.documento("doc_b")
    original = terminado(e, pedir(e, None))
    generation_id = original["generation_id"]
    parametros = json.loads(HISTORICA_AB)
    if variante == "fuentes":
        parametros["document_ids"] = ["doc_a"]
        parametros["alcance"].pop("document_ids")
    elif variante == "primario":
        parametros["document_id"] = "doc_b"
    elif variante == "alcance_ids":
        parametros["alcance"]["document_ids"] = ["doc_a", "doc_c"]
    elif variante == "alcance":
        parametros["alcance"]["tipo"] = "multi_documento"
    cuerpo = json.dumps(parametros, sort_keys=True, ensure_ascii=False)
    sembrar_historica(e, generation_id, cuerpo)
    with e.db.transaccion():
        if variante == "hash":
            e.db._conn.execute(
                "UPDATE idempotency_keys SET hash_cuerpo=? WHERE workspace_id='ws_a' AND operacion='generate'",
                (hashlib.sha256(b"cuerpo distinto y desconocido").hexdigest(),),
            )
        elif variante == "ajeno":
            # Los parámetros, hash y vínculos coinciden; solo la pertenencia
            # documental es incompatible con el espacio de la intención.
            e.db._conn.execute("UPDATE documents SET workspace_id='ws_b' WHERE document_id='doc_b'")
    previo = congelado(e, generation_id)
    prohibir_admision(e, monkeypatch)
    respuesta = pedir(e)
    assert respuesta.status_code == 409, respuesta.text
    assert respuesta.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert congelado(e, generation_id) == previo


def test_cuota_agotada_no_afecta_reintento_y_no_deja_reservas_huerfanas(espacio, monkeypatch):
    e = espacio
    e.documento("doc_a")
    original = terminado(e, pedir(e))
    generation_id = original["generation_id"]
    e.cfg.workspace_max_generations = 1
    previo = congelado(e, generation_id)
    cuota = pedir(e, "otra-clave")
    assert cuota.status_code == 429, cuota.text
    assert cuota.json()["error"]["code"] == "RATE_LIMITED"
    assert cuota.json()["error"]["details"]["motivo"] == "workspace_resource_limit"
    assert intencion(e, "otra-clave") is None
    assert congelado(e, generation_id) == previo
    assert e.db._conn.execute("SELECT COUNT(*) FROM generation_documents").fetchone()[0] == 1
    assert e.db._conn.execute("SELECT COUNT(*) FROM job_documents").fetchone()[0] == 1
    prohibir_admision(e, monkeypatch)
    repetida = pedir(e)
    assert repetida.status_code == 202, repetida.text
    assert repetida.json() == original
    assert congelado(e, generation_id) == previo
