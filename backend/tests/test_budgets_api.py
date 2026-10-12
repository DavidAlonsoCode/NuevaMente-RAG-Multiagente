"""Contrato HTTP de presupuestos y revocación inmediata (#50)."""

import pytest
import test_api_generate as generaciones
from app.schemas.enums import DocumentStatus
from app.storage.provider import StorageUnavailable
from test_api_generate import esperar, generar

pytestmark = pytest.mark.integration_mock
entorno = generaciones.entorno


def test_estado_uso_vencimiento_alerta_y_sin_datos_privados(entorno):
    e = entorno
    e.storage.presupuesto.bytes_maximos = 10000
    # Inventario conservador sintético para disparar la alerta en el HTTP real.
    e.storage.presupuesto.incorporar_inventario([("demo/capacidad-prueba", 8000)])
    res = e.cliente.get("/api/workspaces/current", headers=e.headers)
    assert res.status_code == 200
    datos = res.json()
    assert datos["workspace_id"] == e.ws["workspace_id"] and datos["expira_en"]
    assert datos["uso"] == {"documentos": 1, "limite_documentos": 5, "generaciones": 0, "limite_generaciones": 20}
    assert datos["almacenamiento"]["alerta"]
    assert not any(secreto in res.text for secreto in (e.ws["token"], e.ws["recovery_code"], "capacidad-prueba"))
    assert e.cliente.get("/api/workspaces/current").status_code == 401


def test_limites_http_antes_de_storage_o_llm(entorno):
    e = entorno
    ws = e.ws["workspace_id"]
    for n in range(4):
        e.db.registrar_documento("doc_extra_" + str(n), ws, "Red", DocumentStatus.READY)
    subida = e.cliente.post(
        "/api/documents/upload",
        headers=e.headers,
        json={"documento_titulo": "Red", "documento_contenido": "Una subred privada"},
    )
    assert subida.status_code == 429 and subida.json()["error"]["details"]["limite"] == 5
    for n in range(20):
        e.db.registrar_generacion("gen_extra_" + str(n), ws, e.doc)
    antes = e.doble.llamadas_generacion
    res = generar(e)
    assert res.status_code == 429 and res.json()["error"]["details"]["limite"] == 20
    assert e.doble.llamadas_generacion == antes


def test_borrar_http_confirma_revocacion_con_storage_caido(entorno, monkeypatch):
    e = entorno
    monkeypatch.setattr(e.storage, "upload", lambda *a, **k: (_ for _ in ()).throw(StorageUnavailable("privado")))
    assert e.cliente.delete("/api/workspaces/current", headers=e.headers).status_code == 204
    assert e.cliente.get("/api/workspaces/current", headers=e.headers).status_code == 401
    assert e.cliente.post("/api/sessions/recover", json={"recovery_code": e.ws["recovery_code"]}).status_code == 401
    assert e.db.limpiezas_espacios()[0]["estado"] == "pendiente"


def test_purga_real_de_original_indice_generacion_exports_y_sqlite(entorno):
    e = entorno
    recibo = generar(e).json()
    esperar(e, recibo)
    ws = e.ws["workspace_id"]
    assert e.storage.list("exports/" + ws + "/").items
    assert e.cliente.delete("/api/workspaces/current", headers=e.headers).status_code == 204
    e.app.state.workspace_lifecycle.reconciliar()
    assert e.db.limpiezas_espacios()[0]["estado"] == "datos_purgados"
    for raiz in ("source_documents", "outputs", "exports", "progress"):
        assert not e.storage.list(f"{raiz}/{ws}/").items
    assert not e.app.state.vectorstore._privada.get(where={"workspace_id": ws})["ids"]
    assert e.db.recursos_espacio(ws) == ([], [], False)
    assert e.db.obtener_sesion(e.ws["token"]) is None
