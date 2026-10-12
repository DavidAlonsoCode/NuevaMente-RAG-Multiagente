"""#39 con ingesta/Chroma/grafo/worker reales y proveedores explícitamente simulados."""

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
import test_api_generate as generaciones
from app.core.rag.multimodal import InterpretadorVisual
from app.glossary.service import ServicioGlosario
from app.jobs.manager import CuotasModelo, CuotasProveedor
from app.storage.object_keys import clave_documento, clave_workspace
from doubles.gemini import DobleGemini
from test_api_generate import esperar
from test_multimodal import respuesta_visual

pytestmark = pytest.mark.integration_mock
entorno_generacion = generaciones.entorno


@pytest.fixture
def entorno(entorno_generacion, monkeypatch):
    e = entorno_generacion
    e.entradas_glosario, e.revisiones_glosario = [], []
    e.insuficientes, e.contradiccion, e.omitir_revision = set(), False, False
    e.cita_invalida, e.traducir_api, e.json_invalido = False, False, 0
    original_juez = e.doble._revisar

    def generar(prompt, **kwargs):
        if e.doble.bloquear is not None:
            e.doble.bloquear.wait(10)
        datos = json.loads(prompt)
        e.entradas_glosario.append((datos, kwargs))
        idioma = datos["parametros"]["idioma_salida"]
        terminos = []
        for c in datos["candidatos"]:
            if c["termino_original"] in e.insuficientes:
                terminos.append(
                    dict(
                        id=c["id"], estado="sin_definicion_suficiente", definicion=None, traduccion=None, referencias=[]
                    )
                )
                continue
            chunk = next(
                (
                    r["chunk"]
                    for r in datos["evidencia"]
                    if c["termino_original"].casefold() in r["chunk"]["texto"].casefold()
                ),
                datos["evidencia"][0]["chunk"],
            )
            texto = {
                "es": "Una VCN conecta subredes privadas y conserva aislamiento.",
                "en": "A VCN connects private subnets and preserves isolation.",
                "pt": "Uma VCN conecta sub-redes privadas e mantém o isolamento.",
            }[idioma]
            terminos.append(
                dict(
                    id=c["id"],
                    estado="definido",
                    definicion=texto,
                    traduccion="API traducida" if e.traducir_api and c["es_identificador"] else None,
                    referencias=[
                        {
                            k: ("ajeno" if e.cita_invalida and k == "chunk_id" else chunk[k])
                            for k in ("chunk_id", "pagina", "seccion")
                        }
                    ],
                )
            )
        e.doble.programar_generacion("no es json" if e.json_invalido else json.dumps({"terminos": terminos}))
        e.json_invalido = max(0, e.json_invalido - 1)
        return DobleGemini.generar_sync(e.doble, prompt, **kwargs)

    def revisar(**entrada):
        datos = json.loads(entrada["prompt"])
        e.revisiones_glosario.append(datos)
        if "tarea" not in datos:  # Visión de ingestión: simulación explícita sobre página original.
            e.doble.programar_revision(respuesta_visual())
            return DobleGemini._revisar(e.doble, **entrada)
        if datos["tarea"] == "descomponer":
            dorsos = {r: v for r, v in datos["segmentos"].items() if r.endswith("/dorso")}
            salida = {
                "afirmaciones": []
                if e.omitir_revision
                else [{"texto": v["texto"], "ubicacion": r} for r, v in dorsos.items()]
            }
        elif datos["tarea"] == "juzgar" and e.contradiccion:
            salida = {
                "juicios": [
                    {
                        "id_afirmacion": a["id"],
                        "estado": "contradicha",
                        "motivo": "Definición sin respaldo",
                        "referencias": a["citas_permitidas"],
                    }
                    for a in datos["afirmaciones"]
                ]
            }
        else:
            return original_juez(**entrada)
        e.doble.programar_revision(json.dumps(salida))
        return DobleGemini._revisar(e.doble, **entrada)

    monkeypatch.setattr(e.doble, "generar_sync", generar)
    monkeypatch.setattr(e.doble, "_revisar", revisar)
    e.solicitud_glosario = dict(document_id=e.doc, perfil_destinatario="principiante", idioma_salida="es")
    yield e
    if e.doble.bloquear is not None:
        e.doble.bloquear.set()


def glosario(e, clave=None, **datos):
    return e.cliente.post(
        "/api/glossaries",
        json={**e.solicitud_glosario, **datos},
        headers={**e.headers, **({"Idempotency-Key": clave} if clave else {})},
    )


@pytest.mark.parametrize("idioma", ["es", "en", "pt"])
def test_glosario_verificado_con_citas_y_cache_sin_llamadas(entorno, idioma):
    e = entorno
    primera = glosario(e, "intencion", idioma_salida=idioma)
    assert primera.status_code == 202, primera.text
    recibo = primera.json()
    assert recibo["glossary_id"] == recibo["job_id"] and recibo["cancel_url"]
    salida = esperar(e, recibo)["result"]
    assert salida["estado"] == "verificado" and salida["idioma_salida"] == idioma
    assert salida["evaluacion_calidad"]["estado_evaluacion"] == "aprobada"
    assert {t["termino_original"] for t in salida["terminos"]} == {"VCN"}
    chunks = {c.chunk_id for c in e.app.state.documents_query.chunks(e.ws["token"], e.doc)}
    assert all(r["chunk_id"] in chunks for t in salida["terminos"] for r in t["referencias"])
    assert e.cliente.get("/api/glossaries/" + recibo["glossary_id"], headers=e.headers).json() == salida
    llamadas = (e.doble.llamadas_generacion, e.doble.llamadas_verificacion, e.doble.llamadas_embeddings)
    for clave in (None, "intencion", "nueva-intencion"):
        cache = glosario(e, clave, idioma_salida=idioma)
        assert cache.status_code == 200 and cache.json()["result"] == salida
    assert llamadas == (e.doble.llamadas_generacion, e.doble.llamadas_verificacion, e.doble.llamadas_embeddings)
    # Recuperar aprobado sin runtimes de IA ni índice configurado.
    indice = e.app.state.documents_query.indice
    e.app.state.documents_query.indice = None
    e.app.state.glossary = ServicioGlosario(e.db, e.app.state.documents_query, e.gestor, e.app.state.config)
    try:
        assert glosario(e, idioma_salida=idioma).status_code == 200
        assert e.cliente.get(recibo["status_url"], headers=e.headers).status_code == 200
    finally:
        e.app.state.documents_query.indice = indice


def test_candidatos_se_detectan_durante_ingesta_sin_definir(entorno):
    e = entorno
    manifest = json.loads(e.storage.get(clave_documento(e.ws["workspace_id"], e.doc)))
    assert manifest["detector_terminos_version"] and manifest["terminos_candidatos"]
    assert manifest["terminos_candidatos"][0]["termino_original"] == "VCN"
    assert "definicion" not in manifest["terminos_candidatos"][0]
    assert not e.entradas_glosario


def test_sin_respaldo_declara_limitacion_y_no_fabrica_score(entorno):
    e = entorno
    e.insuficientes = {"VCN"}
    recibido = glosario(e).json()
    salida = esperar(e, recibido)["result"]
    assert salida["estado"] == "sin_definiciones" and salida["evaluacion_calidad"] is None
    termino = salida["terminos"][0]
    assert termino["definicion"] is None and termino["traduccion"] is None
    assert termino["limitacion"] == "Sin definición suficiente en la fuente."
    assert termino["referencias"] and glosario(e).status_code == 200


@pytest.mark.parametrize("defecto", ["contradiccion", "cita_invalida", "traducir_api"])
def test_revision_completa_rechaza_sin_cache_tras_tres_redacciones(entorno, defecto):
    e = entorno
    setattr(e, defecto, True)
    recibo = glosario(e).json()
    fallo = esperar(e, recibo, "rejected_quality")
    assert fallo["result"] is None and len(e.entradas_glosario) == 3
    assert e.cliente.get("/api/glossaries/" + recibo["glossary_id"], headers=e.headers).status_code == 409
    assert e.db.job_cache_glosario(e.ws["workspace_id"], e.db.obtener_job(recibo["job_id"])["cache_key"]) is None


def test_definicion_omitida_por_juez_es_fallo_tecnico_sin_aprobar(entorno):
    e = entorno
    e.omitir_revision = True
    fallo = esperar(e, glosario(e).json(), "failed")
    assert fallo["error"]["code"] == "INVALID_EVALUATION" and fallo["result"] is None


def test_json_invalido_se_corrige_en_redaccion_y_no_con_retry_tecnico(entorno):
    e = entorno
    e.json_invalido = 1
    esperar(e, glosario(e).json())
    assert len(e.entradas_glosario) == 2


def test_idempotencia_concurrente_mismo_job_conflicto_y_aislamiento(entorno):
    e = entorno
    e.doble.bloquear = threading.Event()
    with ThreadPoolExecutor(2) as executor:
        a, b = [executor.submit(glosario, e, "misma-intencion") for _ in range(2)]
        primera, segunda = a.result(10), b.result(10)
    assert primera.status_code == segunda.status_code == 202
    assert primera.json()["job_id"] == segunda.json()["job_id"]
    assert glosario(e, "misma-intencion", idioma_salida="en").status_code == 409
    otro = e.cliente.post("/api/workspaces").json()
    ajeno = {"Authorization": "Bearer " + otro["token"]}
    for ruta in (primera.json()["status_url"], "/api/glossaries/" + primera.json()["glossary_id"]):
        assert e.cliente.get(ruta, headers=ajeno).status_code == 404
    assert e.cliente.post("/api/glossaries", json=e.solicitud_glosario, headers=ajeno).status_code == 404
    e.doble.bloquear.set()
    esperar(e, primera.json())


def test_cache_separa_perfil_idioma_y_version_del_prompt(entorno, monkeypatch):
    import app.glossary.service as modulo

    e = entorno
    anterior = esperar(e, glosario(e).json())["job_id"]
    for datos in ({"perfil_destinatario": "ejecutivo"}, {"idioma_salida": "pt"}):
        nuevo = glosario(e, **datos)
        assert nuevo.status_code == 202 and nuevo.json()["job_id"] != anterior
        esperar(e, nuevo.json())
    monkeypatch.setattr(modulo, "GLOSSARY_PROMPT_VERSION", "nm-glossary-1.0.1")
    nuevo = glosario(e)
    assert nuevo.status_code == 202 and nuevo.json()["job_id"] != anterior
    esperar(e, nuevo.json())


def test_borrado_remoto_y_local_no_entregan_cache(entorno):
    e = entorno
    recibo = glosario(e).json()
    esperar(e, recibo)
    manifest = json.loads(e.storage.get(clave_workspace(e.ws["workspace_id"])))
    manifest["recursos_borrados"] = [
        {
            "recurso_tipo": "document",
            "recurso_id": e.doc,
            "solicitado_en": "2026-10-11T00:00:00Z",
            "purga_despues_en": "2026-11-11T00:00:00Z",
        }
    ]
    e.storage.upload(clave_workspace(e.ws["workspace_id"]), json.dumps(manifest))
    assert glosario(e).status_code == 404
    assert e.cliente.get(recibo["status_url"], headers=e.headers).status_code == 404
    assert e.cliente.get("/api/glossaries/" + recibo["glossary_id"], headers=e.headers).status_code == 404
    e.cliente.delete("/api/documents/" + e.doc, headers=e.headers)
    assert e.db.obtener_job(recibo["job_id"])["resultado"] is None


def test_cancelacion_y_cuota_no_generan_definiciones(entorno):
    e = entorno
    e.gestor.cuotas = CuotasProveedor({e.app.state.config.gemini_embedding_model: CuotasModelo(1000, 10_000_000, 1000)})
    fallo = esperar(e, glosario(e).json(), "failed")
    assert fallo["error"]["code"] == "RATE_LIMITED" and not e.entradas_glosario


def test_cancelar_no_publica_ni_cachea_y_permite_nueva_intencion(entorno):
    e = entorno
    e.doble.bloquear = threading.Event()
    recibo = glosario(e).json()
    limite = time.monotonic() + 5
    while e.db.obtener_job(recibo["job_id"])["status"] == "queued" and time.monotonic() < limite:
        time.sleep(0.01)
    assert e.cliente.post(recibo["cancel_url"], headers=e.headers).status_code == 200
    e.doble.bloquear.set()
    fallo = esperar(e, recibo, "cancelled")
    assert fallo["result"] is None
    nuevo = glosario(e)
    assert nuevo.status_code == 202 and nuevo.json()["job_id"] != recibo["job_id"]
    esperar(e, nuevo.json())


def test_pdf_vcn_principiante_es_con_fuente_y_citas_validas(entorno, documento_demo_vcn):
    e = entorno
    cfg = e.app.state.config
    e.app.state.documents_admission.procesar.vision = InterpretadorVisual(
        e.doble, modelo=cfg.gemini_generation_model, mock=True
    )
    carga = e.cliente.post(
        "/api/documents/upload",
        headers=e.headers,
        files={"file": ("redes_vcn_oci.pdf", documento_demo_vcn.read_bytes(), "application/pdf")},
    )
    assert carga.status_code == 202, carga.text
    esperar(e, carga.json())
    doc = carga.json()["document_id"]
    # Sólo VCN se define en este doble; los demás candidatos declaran límites.
    manifest = json.loads(e.storage.get(clave_documento(e.ws["workspace_id"], doc)))
    e.insuficientes = {t["termino_original"] for t in manifest["terminos_candidatos"] if t["termino_original"] != "VCN"}
    res = glosario(e, document_id=doc)
    assert res.status_code == 202, res.text
    salida = esperar(e, res.json())["result"]
    assert salida["estado"] == "verificado" and salida["perfil_destinatario"] == "principiante"
    assert any(t["termino_original"] == "VCN" and t["estado"] == "definido" for t in salida["terminos"])
    chunks = {c.chunk_id: c for c in e.app.state.documents_query.chunks(e.ws["token"], doc)}
    for termino in salida["terminos"]:
        for ref in termino["referencias"]:
            c = chunks[ref["chunk_id"]]
            assert ref["pagina"] == c.pagina and ref["seccion"] == c.seccion
