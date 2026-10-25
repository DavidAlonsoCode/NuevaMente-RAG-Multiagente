"""Reintentos HTTP con fingerprints persistidos antes y durante la PR #146.

Los cuerpos de referencia son literales independientes de los serializadores
bajo prueba. Los históricos se contrastaron con develop 23d11c41b48e86ce57b95cd21ae24480e7922eec:
schemas/requests.py, schemas/chat.py y jobs/admission.py. Solo se sustituye el ID
aleatorio de los documentos creados por las fixtures.
"""

import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone

import pytest
import test_api_generate as generation_cases
import test_chat as chat_cases
import test_glossary as glossary_cases
from app.chat.service import ServicioChat
from app.generations.service import ServicioGeneraciones
from app.glossary.service import ServicioGlosario
from app.schemas.enums import JobStatus

pytestmark = pytest.mark.integration_mock
entorno_generacion = generation_cases.entorno
entorno_chat = chat_cases.entorno
entorno_glosario = glossary_cases.entorno

HISTORICOS = {
    "generate": '{"alcance": {"seccion_id": null, "tipo": "documento_completo"}, "document_id": "__DOC__", '
    '"formato_salida": "flashcards", "idioma_salida": "es", "nicho_sector": "general", '
    '"nivel_detalle": "didactico", "perfil_destinatario": "principiante"}',
    "chat": '{"document_id": "__DOC__", "idioma_salida": "es", "perfil_destinatario": "principiante", '
    '"pregunta": "¿Qué conecta una VCN?"}',
    "glossary": '{"document_id": "__DOC__", "idioma_salida": "es", "perfil_destinatario": "principiante"}',
}
PR_146 = {
    "generate": '{"alcance": {"seccion_id": null, "tipo": "documento_completo"}, "document_id": "__DOC__", '
    '"document_ids": ["__DOC__"], "formato_salida": "flashcards", "idioma_salida": "es", '
    '"nicho_sector": "general", "nivel_detalle": "didactico", "perfil_destinatario": "principiante"}',
    "chat": '{"document_id": "__DOC__", "document_ids": ["__DOC__"], "idioma_salida": "es", '
    '"perfil_destinatario": "principiante", "pregunta": "¿Qué conecta una VCN?"}',
    "glossary": '{"document_id": "__DOC__", "document_ids": ["__DOC__"], "idioma_salida": "es", '
    '"perfil_destinatario": "principiante"}',
}
BASICOS = {
    "historica": '{"alcance": {"seccion_id": null, "tipo": "documento_completo"}, "document_id": "__DOC__", '
    '"formato_salida": "flashcards", "idioma_salida": "es", "modo_generacion": "basico", '
    '"nicho_sector": "general", "nivel_detalle": "didactico", "perfil_destinatario": "principiante"}',
    "pr146": '{"alcance": {"seccion_id": null, "tipo": "documento_completo"}, "document_id": "__DOC__", '
    '"document_ids": ["__DOC__"], "formato_salida": "flashcards", "idioma_salida": "es", '
    '"modo_generacion": "basico", "nicho_sector": "general", "nivel_detalle": "didactico", '
    '"perfil_destinatario": "principiante"}',
}
MULTIDOCUMENTALES = {
    "generate": '{"alcance": {"document_ids": ["__A__", "__B__"], "seccion_id": null, '
    '"tipo": "multi_documento"}, "document_id": "__A__", "document_ids": ["__A__", "__B__"], '
    '"formato_salida": "flashcards", "idioma_salida": "es", "nicho_sector": "general", '
    '"nivel_detalle": "didactico", "perfil_destinatario": "principiante"}',
    "chat": '{"document_id": "__A__", "document_ids": ["__A__", "__B__"], "idioma_salida": "es", '
    '"perfil_destinatario": "principiante", "pregunta": "¿Qué conecta una VCN?"}',
    "glossary": '{"document_id": "__A__", "document_ids": ["__A__", "__B__"], "idioma_salida": "es", '
    '"perfil_destinatario": "principiante"}',
}
FIXTURES = {"generate": "entorno_generacion", "chat": "entorno_chat", "glossary": "entorno_glosario"}
RUTAS = {"generate": "/api/generate", "chat": "/api/chat", "glossary": "/api/glossaries"}
OPERACIONES = {"generate": "generate", "chat": "chat", "glossary": "glosario"}
ATRIBUTOS = {"generate": "generations", "chat": "chat", "glossary": "glossary"}
SERVICIOS = {"generate": ServicioGeneraciones, "chat": ServicioChat, "glossary": ServicioGlosario}


def cuerpo_fijo(endpoint, document_id, variante="historica"):
    return (HISTORICOS if variante == "historica" else PR_146)[endpoint].replace("__DOC__", document_id)


def peticion(e, endpoint, datos, clave=None, *, headers=None):
    return e.cliente.post(
        RUTAS[endpoint],
        json=datos,
        headers={**(e.headers if headers is None else headers), **({"Idempotency-Key": clave} if clave else {})},
    )


def fila_intencion(e, endpoint, clave, *, workspace_id=None):
    fila = e.db._conn.execute(
        "SELECT rowid,* FROM idempotency_keys WHERE workspace_id=? AND operacion=? AND clave=?",
        (workspace_id or e.ws["workspace_id"], OPERACIONES[endpoint], clave),
    ).fetchone()
    return dict(fila) if fila else None


def sembrar_intencion(e, endpoint, clave, cuerpo, recurso_id, *, estado="completada", workspace_id=None):
    with e.db.transaccion():
        e.db._conn.execute(
            """INSERT INTO idempotency_keys
               (workspace_id,operacion,clave,hash_cuerpo,recurso_id,estado,creado_en)
               VALUES (?,?,?,?,?,?,?)""",
            (
                workspace_id or e.ws["workspace_id"],
                OPERACIONES[endpoint],
                clave,
                hashlib.sha256(cuerpo.encode()).hexdigest(),
                recurso_id,
                estado,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
    return fila_intencion(e, endpoint, clave, workspace_id=workspace_id)


def recuentos(e):
    return tuple(
        e.db._conn.execute(f"SELECT COUNT(*) FROM {tabla}").fetchone()[0]
        for tabla in ("jobs", "generations", "idempotency_keys")
    )


def llamadas(e):
    return e.doble.llamadas_generacion, e.doble.llamadas_verificacion, e.doble.llamadas_embeddings


def deshabilitar_proveedores(e, endpoint, monkeypatch):
    consulta = e.app.state.documents_query
    monkeypatch.setattr(consulta, "indice", None)
    argumentos = (
        (e.db, e.storage, consulta, e.gestor, e.app.state.config)
        if endpoint == "generate"
        else (
            e.db,
            consulta,
            e.gestor,
            e.app.state.config,
        )
    )
    monkeypatch.setattr(e.app.state, ATRIBUTOS[endpoint], SERVICIOS[endpoint](*argumentos))


def subir(e, titulo="Otra fuente", *, headers=None):
    respuesta = e.cliente.post(
        "/api/documents/upload",
        headers=e.headers if headers is None else headers,
        json={"documento_titulo": titulo, "documento_contenido": f"{titulo}: VCN y redes privadas verificables. " * 20},
    )
    assert respuesta.status_code == 202, respuesta.text
    if headers is None:
        generation_cases.esperar(e, respuesta.json())
    else:
        propias = e.headers
        try:
            e.headers = headers
            generation_cases.esperar(e, respuesta.json())
        finally:
            e.headers = propias
    return respuesta.json()["document_id"]


@contextmanager
def trabajo_bloqueado(e, endpoint):
    iniciado, liberar = threading.Event(), threading.Event()

    def ejecutar(ctx):
        iniciado.set()
        assert liberar.wait(30), "El test no liberó su ejecutable local"
        ctx.chequear()

    generation_id = "gen_historica_en_curso" if endpoint == "generate" else None
    if generation_id:
        e.db.registrar_generacion(generation_id, e.ws["workspace_id"], e.doc)
    job_id = e.gestor.enqueue(
        e.ws["workspace_id"],
        "generation" if generation_id else OPERACIONES[endpoint],
        ejecutar,
        generation_id=generation_id,
        document_id=e.doc,
    )
    try:
        assert iniciado.wait(10)
        yield generation_id or job_id, job_id
    finally:
        e.gestor.cancelar(job_id)
        liberar.set()
        generation_cases.esperar(
            e,
            {"status_url": f"/api/jobs/{job_id}"}
            if not generation_id
            else {"status_url": f"/api/generations/{generation_id}"},
            "cancelled",
        )


@pytest.mark.parametrize("endpoint", ["generate", "chat", "glossary"])
@pytest.mark.parametrize("variante", ["historica", "pr146"])
def test_reintento_completado_con_hash_presembrado_sin_proveedores(request, monkeypatch, endpoint, variante):
    e = request.getfixturevalue(FIXTURES[endpoint])
    otro_doc = subir(e)
    datos = json.loads(cuerpo_fijo(endpoint, e.doc))
    primera = peticion(e, endpoint, datos)
    assert primera.status_code == 202, primera.text
    resultado = generation_cases.esperar(e, primera.json())
    id_campo = "generation_id" if endpoint == "generate" else "job_id"
    recurso_id = primera.json()[id_campo]
    if endpoint == "glossary":
        # La prueba de intención no depende de que otra caché resuelva la lectura.
        e.db._conn.execute("UPDATE jobs SET cache_key=NULL WHERE job_id=?", (recurso_id,))
    clave = "intencion-anterior-al-despliegue"
    original = sembrar_intencion(e, endpoint, clave, cuerpo_fijo(endpoint, e.doc, variante), recurso_id)
    deshabilitar_proveedores(e, endpoint, monkeypatch)
    previo, usos = recuentos(e), llamadas(e)
    variantes = [datos, {**datos, "document_ids": [e.doc]}, {**datos, "document_ids": []}]
    solo_lista = {**datos, "document_ids": [e.doc]}
    solo_lista.pop("document_id")
    variantes.append(solo_lista)
    if endpoint == "generate":
        variantes.append({**datos, "modo_generacion": "multiagente"})
    for entrada in variantes:
        repetida = peticion(e, endpoint, entrada, clave)
        assert repetida.status_code == (200 if endpoint == "glossary" else 202), repetida.text
        assert repetida.json()[id_campo] == recurso_id
        contenido = "contenido" if endpoint == "generate" else "result"
        assert repetida.json()[contenido] == resultado[contenido]
        assert fila_intencion(e, endpoint, clave) == original
        assert recuentos(e) == previo and llamadas(e) == usos
    cambios = [{"idioma_salida": "en"}, {"perfil_destinatario": "ejecutivo"}, {"document_id": otro_doc}]
    if endpoint == "chat":
        cambios.append({"pregunta": "¿Cómo se configura otra subred?"})
    elif endpoint == "generate":
        cambios.extend(
            [
                {"formato_salida": "quiz"},
                {"nivel_detalle": "practico"},
                {"nicho_sector": "fintech"},
                {"alcance": {"tipo": "seccion", "seccion_id": "seccion_distinta"}},
                {"modo_generacion": "basico"},
            ]
        )
    for cambio in cambios:
        conflicto = peticion(e, endpoint, {**datos, **cambio}, clave)
        assert conflicto.status_code == 409, conflicto.text
        assert conflicto.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
        assert fila_intencion(e, endpoint, clave) == original
        assert recuentos(e) == previo and llamadas(e) == usos
    nueva = peticion(e, endpoint, datos, "nueva-intencion-sin-proveedores")
    assert nueva.status_code == 503, nueva.text
    assert fila_intencion(e, endpoint, "nueva-intencion-sin-proveedores") is None
    assert recuentos(e) == previo and llamadas(e) == usos


@pytest.mark.parametrize("variante", ["historica", "pr146"])
def test_generacion_basica_presembrada_preserva_hash_y_contrato_11(entorno_generacion, monkeypatch, variante):
    e = entorno_generacion
    datos = {**json.loads(cuerpo_fijo("generate", e.doc)), "modo_generacion": "basico"}
    primera = peticion(e, "generate", datos)
    assert primera.status_code == 202, primera.text
    resultado = generation_cases.esperar(e, primera.json())
    assert resultado["contenido"]["schema_version"] == "1.1"
    assert resultado["contenido"]["modo_generacion"] == "basico"
    historial = e.cliente.get("/api/generations", headers=e.headers)
    assert historial.status_code == 200, historial.text
    assert historial.json()["items"][0]["parametros"] == json.loads(BASICOS["pr146"].replace("__DOC__", e.doc))
    clave = "basico-previo-al-despliegue"
    original = sembrar_intencion(
        e, "generate", clave, BASICOS[variante].replace("__DOC__", e.doc), primera.json()["generation_id"]
    )
    deshabilitar_proveedores(e, "generate", monkeypatch)
    previo, usos = recuentos(e), llamadas(e)
    for entrada in (datos, {**datos, "document_ids": [e.doc]}):
        repetida = peticion(e, "generate", entrada, clave)
        assert repetida.status_code == 202, repetida.text
        assert repetida.json()["generation_id"] == primera.json()["generation_id"]
        assert repetida.json()["contenido"] == resultado["contenido"]
    conflicto = peticion(e, "generate", {**datos, "modo_generacion": "multiagente"}, clave)
    assert conflicto.status_code == 409, conflicto.text
    assert conflicto.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert fila_intencion(e, "generate", clave) == original
    assert recuentos(e) == previo and llamadas(e) == usos


@pytest.mark.parametrize("endpoint", ["generate", "chat", "glossary"])
def test_nueva_intencion_unidocumental_concurrente_conserva_un_trabajo(request, monkeypatch, endpoint):
    e = request.getfixturevalue(FIXTURES[endpoint])
    iniciado, liberar = threading.Event(), threading.Event()
    ejecutados = []

    def ejecutar(ctx):
        ejecutados.append(ctx.job_id)
        iniciado.set()
        assert liberar.wait(30), "El test no liberó su ejecutable unidocumental"
        ctx.chequear()

    servicio = getattr(e.app.state, ATRIBUTOS[endpoint])
    if endpoint == "generate":
        monkeypatch.setattr(servicio, "_trabajo", lambda *args: ejecutar)
    else:
        monkeypatch.setattr(servicio, "ejecutar", lambda solicitud, ctx, *args: ejecutar(ctx))
    datos = json.loads(cuerpo_fijo(endpoint, e.doc))
    clave = "nueva-intencion-unidocumental-concurrente"
    previo, usos = recuentos(e), llamadas(e)
    job_id = None
    try:
        with ThreadPoolExecutor(2) as executor:
            futuros = [
                executor.submit(peticion, e, endpoint, entrada, clave)
                for entrada in (datos, {**datos, "document_ids": [e.doc]})
            ]
            respuestas = [futuro.result(15) for futuro in futuros]
        assert all(respuesta.status_code == 202 for respuesta in respuestas), [r.text for r in respuestas]
        id_campo = "generation_id" if endpoint == "generate" else "job_id"
        recurso_id = respuestas[0].json()[id_campo]
        assert {respuesta.json()[id_campo] for respuesta in respuestas} == {recurso_id}
        job_id = e.db.job_generacion(recurso_id)["job_id"] if endpoint == "generate" else recurso_id
        assert iniciado.wait(10)
        assert ejecutados == [job_id]
        original = fila_intencion(e, endpoint, clave)
        assert original["hash_cuerpo"] == hashlib.sha256(cuerpo_fijo(endpoint, e.doc).encode()).hexdigest()
        assert recuentos(e) == (previo[0] + 1, previo[1] + (endpoint == "generate"), previo[2] + 1)
        assert llamadas(e) == usos
        if endpoint == "generate":
            historial = e.cliente.get("/api/generations", headers=e.headers)
            assert historial.status_code == 200, historial.text
            # GeneracionHistorial tipa estos parámetros como GenerateRequest:
            # el JSON público ya incluía el modo por defecto, aunque su hash no.
            assert historial.json()["items"][0]["parametros"] == {
                **json.loads(cuerpo_fijo(endpoint, e.doc, "pr146")),
                "modo_generacion": "multiagente",
            }
        repetida = peticion(e, endpoint, datos, clave)
        assert repetida.status_code == 202, repetida.text
        assert fila_intencion(e, endpoint, clave) == original
        assert ejecutados == [job_id]
    finally:
        if job_id:
            e.gestor.cancelar(job_id)
        liberar.set()


@pytest.mark.parametrize("endpoint", ["generate", "chat", "glossary"])
@pytest.mark.parametrize("variante", ["historica", "pr146"])
def test_reintentos_concurrentes_de_intencion_en_curso_con_hash_presembrado(request, monkeypatch, endpoint, variante):
    e = request.getfixturevalue(FIXTURES[endpoint])
    with trabajo_bloqueado(e, endpoint) as (recurso_id, job_id):
        clave = "intencion-en-curso-anterior"
        original = sembrar_intencion(
            e, endpoint, clave, cuerpo_fijo(endpoint, e.doc, variante), recurso_id, estado="en_curso"
        )
        deshabilitar_proveedores(e, endpoint, monkeypatch)
        previo, usos = recuentos(e), llamadas(e)
        datos = json.loads(cuerpo_fijo(endpoint, e.doc))
        with ThreadPoolExecutor(2) as executor:
            futuros = [
                executor.submit(peticion, e, endpoint, entrada, clave)
                for entrada in (datos, {**datos, "document_ids": [e.doc]})
            ]
            respuestas = [futuro.result(15) for futuro in futuros]
        id_campo = "generation_id" if endpoint == "generate" else "job_id"
        assert all(respuesta.status_code == 202 for respuesta in respuestas), [r.text for r in respuestas]
        assert {respuesta.json()[id_campo] for respuesta in respuestas} == {recurso_id}
        assert e.db.obtener_job(job_id)["status"] == "running"
        assert fila_intencion(e, endpoint, clave) == original
        assert recuentos(e) == previo and llamadas(e) == usos


@pytest.mark.parametrize("endpoint", ["generate", "chat", "glossary"])
def test_http_multidocumental_normaliza_orden_sin_confundir_otras_fuentes(request, monkeypatch, endpoint):
    e = request.getfixturevalue(FIXTURES[endpoint])
    b, c = subir(e, "Fuente B"), subir(e, "Fuente C")
    a, b = sorted([e.doc, b])
    iniciado, liberar = threading.Event(), threading.Event()

    def ejecutar(ctx):
        iniciado.set()
        assert liberar.wait(30), "El test no liberó su ejecutable multidocumental"
        ctx.chequear()

    servicio = getattr(e.app.state, ATRIBUTOS[endpoint])
    if endpoint == "generate":
        monkeypatch.setattr(servicio, "_trabajo", lambda *args: ejecutar)
    else:
        monkeypatch.setattr(servicio, "ejecutar", lambda solicitud, ctx, *args: ejecutar(ctx))
    datos = json.loads(cuerpo_fijo(endpoint, a))
    datos.pop("document_id")
    clave = "misma-intencion-multidocumental"
    previo, usos = recuentos(e), llamadas(e)
    job_id = None
    try:
        with ThreadPoolExecutor(2) as executor:
            futuros = [
                executor.submit(peticion, e, endpoint, {**datos, "document_ids": ids}, clave)
                for ids in ([a, b], [b, a])
            ]
            respuestas = [futuro.result(15) for futuro in futuros]
        assert all(respuesta.status_code == 202 for respuesta in respuestas), [r.text for r in respuestas]
        id_campo = "generation_id" if endpoint == "generate" else "job_id"
        recurso_id = respuestas[0].json()[id_campo]
        assert {respuesta.json()[id_campo] for respuesta in respuestas} == {recurso_id}
        job_id = e.db.job_generacion(recurso_id)["job_id"] if endpoint == "generate" else recurso_id
        assert iniciado.wait(10)
        literal = MULTIDOCUMENTALES[endpoint].replace("__A__", a).replace("__B__", b)
        original = fila_intencion(e, endpoint, clave)
        assert original["hash_cuerpo"] == hashlib.sha256(literal.encode()).hexdigest()
        assert recuentos(e) == (previo[0] + 1, previo[1] + (endpoint == "generate"), previo[2] + 1)
        conflicto = peticion(e, endpoint, {**datos, "document_ids": [a, c]}, clave)
        assert conflicto.status_code == 409, conflicto.text
        assert conflicto.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
        assert fila_intencion(e, endpoint, clave) == original
        assert llamadas(e) == usos
    finally:
        if job_id:
            e.gestor.cancelar(job_id)
        liberar.set()


def test_cache_de_glosario_no_legitima_reutilizar_clave_con_otro_cuerpo(entorno_glosario, monkeypatch):
    e = entorno_glosario
    datos = json.loads(cuerpo_fijo("glossary", e.doc))
    primera = peticion(e, "glossary", datos)
    assert primera.status_code == 202, primera.text
    generation_cases.esperar(e, primera.json())
    recurso_id = primera.json()["job_id"]
    clave = "cache-no-reemplaza-identidad"
    cambiado = {**datos, "idioma_salida": "en"}
    cuerpo_ajeno = HISTORICOS["glossary"].replace("__DOC__", e.doc).replace('"es"', '"en"')
    original = sembrar_intencion(e, "glossary", clave, cuerpo_ajeno, recurso_id)
    deshabilitar_proveedores(e, "glossary", monkeypatch)
    previo, usos = recuentos(e), llamadas(e)
    assert peticion(e, "glossary", datos).status_code == 200
    conflicto = peticion(e, "glossary", datos, clave)
    assert conflicto.status_code == 409, conflicto.text
    assert conflicto.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert cambiado != datos
    assert fila_intencion(e, "glossary", clave) == original
    assert recuentos(e) == previo and llamadas(e) == usos


def test_intenciones_se_aislan_por_operacion_y_workspace(entorno_generacion, monkeypatch):
    e = entorno_generacion
    otro = e.cliente.post("/api/workspaces").json()
    headers_ajenos = {"Authorization": "Bearer " + otro["token"]}
    doc_ajeno = subir(e, "Fuente de otro espacio", headers=headers_ajenos)
    e.db.registrar_generacion("gen_propietaria", e.ws["workspace_id"], e.doc, JobStatus.QUEUED)
    clave = "clave-compartida-entre-ambitos"
    original = sembrar_intencion(e, "generate", clave, cuerpo_fijo("generate", e.doc), "gen_propietaria")
    for endpoint in ("generate", "chat", "glossary"):
        deshabilitar_proveedores(e, endpoint, monkeypatch)
    previo, usos = recuentos(e), llamadas(e)
    for endpoint in ("chat", "glossary"):
        respuesta = peticion(e, endpoint, json.loads(cuerpo_fijo(endpoint, e.doc)), clave)
        assert respuesta.status_code == 503, respuesta.text
        assert fila_intencion(e, endpoint, clave) is None
    respuesta = peticion(e, "generate", json.loads(cuerpo_fijo("generate", doc_ajeno)), clave, headers=headers_ajenos)
    assert respuesta.status_code == 503, respuesta.text
    assert fila_intencion(e, "generate", clave, workspace_id=otro["workspace_id"]) is None
    assert fila_intencion(e, "generate", clave) == original
    assert recuentos(e) == previo and llamadas(e) == usos
