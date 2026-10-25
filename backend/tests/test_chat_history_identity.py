"""Identidad del historial en SQLite; cuerpos históricos independientes del scope nuevo."""

import hashlib
import json
from datetime import datetime, timedelta

import pytest
from app.chat.identity import scope_historial
from app.jobs.store import RegistroOperativo
from app.schemas.enums import DocumentStatus, JobStatus
from test_chat_source_authorization import chat_env as chat_env
from test_chat_source_authorization import respuesta_chat

pytestmark = pytest.mark.integration_mock


def guardar(e, job_id, fuentes, scope, *, legado=False, workspace="ws_a", resultado=None):
    salida = resultado or respuesta_chat(fuentes, legado=legado, pregunta=job_id)
    e.db.encolar_job(
        job_id,
        workspace,
        "chat",
        document_id=fuentes[0].document_id,
        document_ids=[f.document_id for f in fuentes],
        scope_hash=scope,
    )
    e.db.iniciar_job(job_id)
    e.db.finalizar_job(job_id, JobStatus.COMPLETED, resultado=json.dumps(salida))
    # Windows puede entregar el mismo timestamp para varias altas. Fijar
    # la cronología de la fixture sin cambiar el desempate público por ID.
    e.orden_historial = getattr(e, "orden_historial", 0) + 1
    fecha = datetime.fromisoformat(e.db.obtener_job("job_chat")["creado_en"]) + timedelta(seconds=e.orden_historial)
    with e.db.transaccion():
        e.db._conn.execute("UPDATE jobs SET creado_en=? WHERE job_id=?", (fecha.isoformat(), job_id))
    return salida


def historial(e, fuentes, limite=10, workspace="ws_a"):
    return e.db.historial_chat(
        workspace,
        fuentes[0].document_id,
        fuentes[0].hash,
        limite,
        scope_hash=scope_historial(fuentes),
        fuentes=fuentes,
    )


def test_documentos_con_igual_hash_no_comparten_historial_ni_heredan_legados(chat_env):
    e = chat_env
    for doc_id in ("doc_a", "doc_b"):
        e.db.actualizar_documento(doc_id, DocumentStatus.READY, hash_documento="hash_identico")
    a, b = [e.consulta.actual("ws_a", d) for d in ("doc_a", "doc_b")]
    # Formas anteriores: el scope era el hash crudo, o NULL antes del backfill.
    esperado_a = guardar(e, "legado_a", [a], "hash_identico", legado=True)
    esperado_b = guardar(e, "legado_b", [b], "hash_identico", legado=True)
    esperado_a_nulo = guardar(e, "legado_a_nulo", [a], None, legado=True)
    assert historial(e, [a]) == [esperado_a, esperado_a_nulo]
    assert historial(e, [b]) == [esperado_b]
    assert e.db.historial_chat("ws_a", "doc_b", "hash_identico", 10) == [esperado_b]
    assert scope_historial([a]) != scope_historial([b])
    # Un legado anterior a N:M también acredita únicamente SU documento.
    with e.db.transaccion():
        e.db._conn.execute("DELETE FROM job_documents WHERE job_id='legado_a'")
    assert historial(e, [a]) == [esperado_a, esperado_a_nulo]
    assert historial(e, [b]) == [esperado_b]


def test_conjunto_ordenado_y_versiones_conservan_scopes_historicos_sin_reescritura(chat_env):
    e = chat_env
    a, b = e.fuentes
    e.db.actualizar_documento("doc_c", DocumentStatus.READY, hash_documento=b.hash)
    c = e.consulta.actual("ws_a", "doc_c")
    # Fórmula literal de la PR anterior: IDs y hashes, sin versión.
    viejo_ab = hashlib.sha256(f"doc_a:{a.hash};doc_b:{b.hash}".encode()).hexdigest()
    esperado_ab = guardar(e, "historico_ab", [a, b], viejo_ab)
    esperado_ac = guardar(e, "actual_ac", [a, c], scope_historial([a, c]))
    nuevo_ab = guardar(e, "actual_ab", [a, b], scope_historial([a, b]))
    # Huella idempotente literal ajena al helper de historial: permanece intacta.
    cuerpo = '{"document_id":"doc_a","pregunta":"¿Qué indica la fuente?"}'
    huella = hashlib.sha256(cuerpo.encode()).hexdigest()
    with e.db.transaccion():
        e.db._conn.execute(
            """INSERT INTO idempotency_keys(workspace_id,operacion,clave,hash_cuerpo,recurso_id,estado,creado_en)
                VALUES ('ws_a','chat','clave',?,'historico_ab','completada',
                '2026-10-24T10:00:00+00:00')""",
            (huella,),
        )
    antes = [tuple(f) for f in e.db._conn.execute("SELECT * FROM jobs ORDER BY job_id")]
    esperados_ab = [e.resultado, esperado_ab, nuevo_ab]
    assert historial(e, [a, b]) == esperados_ab
    assert (
        e.db.historial_chat("ws_a", "doc_a", a.hash, 10, scope_hash=scope_historial([b, a]), fuentes=[b, a])
        == esperados_ab
    )
    assert scope_historial([a, b]) == scope_historial([b, a])
    assert scope_historial([a, b]) != scope_historial([a, c])
    assert historial(e, [a, c]) == [esperado_ac]
    assert historial(e, [a, b.model_copy(update={"version": "v2"})]) == []
    assert historial(e, [a, b.model_copy(update={"titulo": "otro título"})]) == []
    assert [tuple(f) for f in e.db._conn.execute("SELECT * FROM jobs ORDER BY job_id")] == antes
    assert e.db._conn.execute("SELECT hash_cuerpo FROM idempotency_keys WHERE clave='clave'").fetchone()[0] == huella
    # El mismo archivo, abierto por otra instancia, conserva ambos legados y sus huellas.
    e.gestor.detener()
    ruta = e.db.ruta_db
    e.db.cerrar()
    e.db = RegistroOperativo(ruta)
    assert historial(e, [a, b]) == esperados_ab
    assert e.db._conn.execute("SELECT hash_cuerpo FROM idempotency_keys WHERE clave='clave'").fetchone()[0] == huella


@pytest.mark.parametrize("caso", ["resultado_ajeno", "vinculo_extra", "vinculo_faltante", "snapshot_faltante"])
def test_scope_coincidente_no_basta_para_atribuir_conversacion(chat_env, caso):
    e = chat_env
    a, b = e.fuentes
    valido = guardar(e, "valido", [a, b], scope_historial([a, b]))
    resultado = respuesta_chat([a, b], pregunta="invalido")
    if caso == "resultado_ajeno":
        resultado["document_id"] = "doc_c"
    elif caso == "snapshot_faltante":
        resultado.pop("documentos_fuente")
    guardar(e, "invalido", [a, b], scope_historial([a, b]), resultado=resultado)
    with e.db.transaccion():
        if caso == "vinculo_extra":
            e.db._conn.execute("INSERT INTO job_documents VALUES ('invalido','doc_c')")
        elif caso == "vinculo_faltante":
            e.db._conn.execute("DELETE FROM job_documents WHERE job_id='invalido' AND document_id='doc_b'")
    # El límite se aplica a turnos acreditados: una fila reciente inválida no los oculta.
    assert historial(e, [a, b], limite=1) == [valido]


def test_historial_no_cruza_workspace_o_fuentes_retiradas(chat_env):
    e = chat_env
    a = e.fuentes[0]
    x = e.consulta.actual("ws_b", "doc_x")
    e.db.actualizar_documento("doc_x", DocumentStatus.READY, hash_documento=a.hash)
    x = x.model_copy(update={"hash": a.hash})
    guardar(e, "otro_workspace", [x], a.hash, legado=True, workspace="ws_b")
    propio = guardar(e, "propio", [a], a.hash, legado=True)
    assert historial(e, [a]) == [propio]
    assert historial(e, [a], workspace="ws_b") == []
    assert e.db.retirar_documento("token-a", "doc_a")
    assert historial(e, [a]) == []


def test_historial_nuevo_usa_identidad_y_version_no_solo_hash(chat_env):
    e = chat_env
    a, b = e.fuentes
    # Canonización independiente y explícita del valor almacenado.
    esperado = hashlib.sha256(
        json.dumps([[a.document_id, a.hash, "v1"], [b.document_id, b.hash, "v1"]], separators=(",", ":")).encode()
    ).hexdigest()
    assert scope_historial([b, a]) == esperado
    assert scope_historial([a, b]) != scope_historial([a, b.model_copy(update={"version": "v2"})])
    assert historial(e, [a], limite=0) == []
    for limite in (-1, 51):
        with pytest.raises(ValueError):
            historial(e, [a], limite=limite)
