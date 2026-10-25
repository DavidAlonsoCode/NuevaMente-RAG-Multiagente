"""Contrato real de espacio completo con una fuente y proveedores dobles."""

import json

import pytest
import test_api_generate as api_cases
from app.schemas.responses import cargar_paquete_json

pytestmark = pytest.mark.integration_mock
entorno = api_cases.entorno


@pytest.mark.parametrize(
    ("modo", "version", "status"),
    [(None, "1.0", "aprobado"), ("multiagente", "1.1", "aprobado"), ("basico", "1.1", "no_evaluado")],
)
def test_espacio_unidocumental_conserva_contrato_y_reintento(entorno, monkeypatch, modo, version, status):
    e = entorno
    servicio = e.app.state.generations
    generar_original, trabajo_original = servicio.generar, servicio._trabajo
    solicitudes, trabajos = [], []

    def observar_solicitud(token, solicitud, clave=None):
        campos = set(solicitud.model_fields_set)
        cuerpo = solicitud.model_dump(mode="json")
        alcance_campos = set(solicitud.alcance.model_fields_set)
        fila = generar_original(token, solicitud, clave)
        assert solicitud.model_fields_set == campos
        assert solicitud.alcance.model_fields_set == alcance_campos
        assert solicitud.model_dump(mode="json") == cuerpo
        solicitudes.append(solicitud)
        return fila

    def observar_trabajo(solicitud, generation_id, creado_en):
        trabajos.append(solicitud)
        return trabajo_original(solicitud, generation_id, creado_en)

    monkeypatch.setattr(servicio, "generar", observar_solicitud)
    monkeypatch.setattr(servicio, "_trabajo", observar_trabajo)
    cuerpo = {k: v for k, v in e.solicitud.items() if k != "document_id"}
    cuerpo["alcance"] = {"tipo": "espacio_completo"}
    if modo is not None:
        cuerpo["modo_generacion"] = modo
    headers = {**e.headers, "Idempotency-Key": "espacio-unidocumental"}
    respuesta = e.cliente.post("/api/generate", json=cuerpo, headers=headers)
    assert respuesta.status_code == 202, respuesta.text
    recibo = respuesta.json()
    completada = api_cases.esperar(e, recibo)
    paquete = completada["contenido"]
    assert (paquete["schema_version"], paquete["status"]) == (version, status)
    if modo is None:
        assert "modo_generacion" not in paquete
    else:
        assert paquete["modo_generacion"] == modo
    validado = cargar_paquete_json(json.dumps(paquete))
    assert validado.generation_id == recibo["generation_id"]
    assert paquete["documento_fuente"]["document_id"] == e.doc
    assert paquete["metadatos"]["alcance"]["tipo"] == "espacio_completo"
    assert "documentos_fuente" not in paquete
    assert len(trabajos) == 1
    assert trabajos[0].document_id == e.doc and trabajos[0].document_ids == [e.doc]
    assert ("modo_generacion" in trabajos[0].model_fields_set) is (modo is not None)
    assert e.doble.llamadas_generacion == 1
    if modo == "basico":
        assert e.doble.llamadas_verificacion == 0
    else:
        assert e.doble.llamadas_verificacion > 0

    generation_id = recibo["generation_id"]
    fila = e.db.obtener_generacion(generation_id)
    parametros = json.loads(fila["parametros_json"])
    assert parametros["document_id"] == e.doc and parametros["document_ids"] == [e.doc]
    assert parametros["alcance"] == {"tipo": "espacio_completo", "seccion_id": None}
    assert parametros.get("modo_generacion", "multiagente") == (modo or "multiagente")
    assert e.db.documentos_generacion(generation_id) == [e.doc]
    snapshot = e.db.snapshot_generacion(generation_id)
    assert snapshot == [paquete["documento_fuente"]]
    intencion = dict(
        e.db._conn.execute(
            "SELECT * FROM idempotency_keys WHERE workspace_id=? AND operacion='generate' AND clave=?",
            (e.ws["workspace_id"], "espacio-unidocumental"),
        ).fetchone()
    )
    llamadas = e.doble.llamadas_generacion, e.doble.llamadas_verificacion, e.doble.llamadas_embeddings
    trabajos_persistidos = e.db._conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    for atributo in ("generador", "verificador", "retriever"):
        monkeypatch.setattr(servicio, atributo, None)

    repetida = e.cliente.post("/api/generate", json=cuerpo, headers=headers)
    assert repetida.status_code == 202, repetida.text
    assert repetida.json()["generation_id"] == generation_id
    assert repetida.json()["contenido"] == paquete
    assert len(solicitudes) == 2 and len(trabajos) == 1
    assert e.db.obtener_generacion(generation_id) == fila
    assert e.db.documentos_generacion(generation_id) == [e.doc]
    assert e.db.snapshot_generacion(generation_id) == snapshot
    assert (
        dict(
            e.db._conn.execute(
                "SELECT * FROM idempotency_keys WHERE workspace_id=? AND operacion='generate' AND clave=?",
                (e.ws["workspace_id"], "espacio-unidocumental"),
            ).fetchone()
        )
        == intencion
    )
    assert (e.doble.llamadas_generacion, e.doble.llamadas_verificacion, e.doble.llamadas_embeddings) == llamadas
    assert e.db._conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == trabajos_persistidos
