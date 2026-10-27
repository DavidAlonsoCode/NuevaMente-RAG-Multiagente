"""Researcher multidocumental con RetrieverMMR real y fuentes independientes."""

import json
import time
from types import SimpleNamespace

import pytest
from app.config import Configuracion
from app.core.agents.graph_state import DependenciasGrafo, DocumentoGeneracion, EstadoGrafo
from app.core.agents.researcher import DependenciasResearcher, researcher
from app.core.agents.supervisor import crear_estado_inicial
from app.core.rag.embeddings import GeminiEmbeddings
from app.core.rag.retriever import RetrieverMMR
from app.core.rag.tokenizer import TokenizadorBPE
from app.core.rag.vectorstore import CoincidenciaVectorial, VectorStoreChroma
from app.jobs.manager import ContextoEjecucion, CuotasModelo, CuotasProveedor
from app.schemas.enums import DocumentStatus, JobStatus
from app.schemas.internal import Chunk, EvidenciaRecuperada
from app.schemas.responses import DocumentoFuente

pytestmark = pytest.mark.unit
WORKSPACE = "espacio-a"
HASHES = {"doc-a": "a" * 64, "doc-b": "b" * 64}
SOLICITUD = {
    "document_ids": ["doc-a", "doc-b"],
    "perfil_destinatario": "principiante",
    "formato_salida": "flashcards",
    "nicho_sector": "general",
    "nivel_detalle": "didactico",
    "idioma_salida": "es",
    "alcance": {"tipo": "multi_documento"},
}


def chunk(documento, numero, texto=None):
    return Chunk(
        chunk_id=f"{documento}-{numero}",
        workspace_id=WORKSPACE,
        document_id=documento,
        document_hash=HASHES[documento],
        indice=numero,
        texto=texto or f"La contribución propia de {documento} explica el concepto {numero}.",
        seccion_id=f"seccion:{numero}",
        seccion=f"Concepto {numero}",
        cantidad_tokens=1,
        source_type="text",
        source_name=f"{documento}.txt",
    )


def hit(fragmento, similitud=0.5, vector=(1.0, 0.0)):
    return CoincidenciaVectorial(fragmento, 1 - similitud, vector)


def evidencia(fragmento, consulta="consulta previa", score=0.5):
    return EvidenciaRecuperada(chunk=fragmento, score=score, consulta=consulta)


def conteo_serializado(elementos):
    """Esperado independiente: contar el JSON completo, incluidos IDs y metadatos."""
    serializado = json.dumps(
        [elemento.model_dump(mode="json") for elemento in elementos], ensure_ascii=False, separators=(",", ":")
    )
    return TokenizadorBPE().contar(serializado) if elementos else 0


class IndiceControlado:
    embeddings = SimpleNamespace(modelo="gemini-embedding-2")

    def __init__(self, chunks):
        self.chunks = chunks
        self.busquedas = []
        self.inventarios = []
        self.respuestas = {}
        self.predeterminada = [hit(c) for c in chunks]
        self.despues = lambda: None

    def listar_chunks(self, workspace, document, *, contexto, source_hash):
        contexto.chequear()
        assert workspace == contexto.workspace_id == WORKSPACE
        assert source_hash == HASHES[document]
        self.inventarios.append(document)
        return [c for c in self.chunks if c.document_id == document]

    def buscar_multidocumental(self, workspace, documents, consulta, *, contexto, limite):
        contexto.chequear()
        assert workspace == contexto.workspace_id == WORKSPACE
        assert documents == ["doc-a", "doc-b"]
        self.busquedas.append((consulta, limite))
        resultado = self.respuestas.get(consulta, self.predeterminada)
        self.despues()
        return resultado


def entorno(chunks=None, *, k=5, max_tokens=12000):
    chunks = chunks or [chunk("doc-a", 0), chunk("doc-b", 0)]
    cfg = Configuracion(
        _env_file=None,
        app_env="test",
        mock_oci=True,
        mock_gemini=True,
        retrieval_k=k,
        retrieval_max_tokens=max_tokens,
    )
    ctx = ContextoEjecucion(
        "job-multi",
        WORKSPACE,
        "generacion",
        time.monotonic() + 300,
        cuotas=CuotasProveedor({"gemini-embedding-2": CuotasModelo(100, 1_000_000, 100)}),
        base_backoff_segundos=0,
    )
    documentos = {
        document_id: DocumentoGeneracion(
            workspace_id=WORKSPACE,
            fuente=DocumentoFuente(document_id=document_id, titulo=f"Fuente {document_id}", hash=hash_, version="v1"),
            estado=DocumentStatus.READY,
            idioma_origen="es",
            secciones=tuple(dict.fromkeys(c.seccion_id for c in chunks if c.document_id == document_id)),
        )
        for document_id, hash_ in HASHES.items()
    }
    grafo = DependenciasGrafo(
        ctx, lambda workspace, document: documentos.get(document) if workspace == WORKSPACE else None
    )
    indice = IndiceControlado(chunks)
    retriever = RetrieverMMR(indice, configuracion=cfg)
    dependencias = DependenciasResearcher(grafo, retriever, parser_version="parser-prueba-v1")
    estado = crear_estado_inicial(SOLICITUD, generation_id="gen-multi", dependencias=grafo)
    assert estado.status == JobStatus.RUNNING
    return SimpleNamespace(
        estado=estado,
        dependencias=dependencias,
        grafo=grafo,
        documentos=documentos,
        indice=indice,
        retriever=retriever,
        contexto=ctx,
    )


def ejecutar(e, estado=None):
    estado = estado or e.estado
    return EstadoGrafo.model_validate({**estado.model_dump(), **researcher(estado, e.dependencias)})


def observar_resultados(e, monkeypatch):
    resultados = []
    recuperar = e.retriever.recuperar_multidocumental

    def observar(*args, **kwargs):
        resultado = recuperar(*args, **kwargs)
        resultados.append(resultado)
        return resultado

    monkeypatch.setattr(e.retriever, "recuperar_multidocumental", observar)
    return resultados


def test_cinco_candidatos_a_y_b_pertinente_no_se_pierde_ni_duplica_consulta():
    originales = [*(chunk("doc-a", n) for n in range(5)), chunk("doc-b", 0)]
    e = entorno(originales)
    e.indice.predeterminada = [
        *(hit(c, 1 - n / 100) for n, c in enumerate(originales[:5])),
        hit(originales[-1], 0.4, (0.0, 1.0)),
    ]
    previo = e.estado.model_dump_json()
    resultado = ejecutar(e)
    assert resultado.status == JobStatus.RUNNING
    assert len(resultado.evidencia) == 5
    assert {item.chunk.document_id for item in resultado.evidencia} == {"doc-a", "doc-b"}
    assert len(e.indice.busquedas) == 1
    assert all(f"Fuente {doc}" in e.indice.busquedas[0][0] for doc in HASHES)
    assert resultado.presupuesto.usadas == e.estado.presupuesto.usadas == 0
    assert e.estado.model_dump_json() == previo


def test_fuente_sin_candidatos_se_declara_sin_repetir_busqueda_por_documento():
    e = entorno()
    e.indice.predeterminada = [hit(e.indice.chunks[0], 0.9)]
    resultado = ejecutar(e)
    assert resultado.status == JobStatus.REJECTED_QUALITY
    assert resultado.error.code == "COVERAGE_INCOMPLETE"
    assert "doc-b" in resultado.error.message
    assert len(e.indice.busquedas) == 1
    assert resultado.evidencia == [] and resultado.borrador is None and resultado.paquete_aprobado is None


def test_omitir_a_grande_no_atribuye_a_presupuesto_la_falta_de_b_pertinente(monkeypatch):
    pequeno = chunk("doc-a", 0)
    grande = chunk("doc-a", 1, "Explicación extensa de la fuente A. " * 1000)
    secundaria = chunk("doc-b", 0)
    e = entorno([pequeno, grande, secundaria], max_tokens=1000)
    e.indice.predeterminada = [hit(grande, 1.0), hit(pequeno, 0.8), hit(secundaria, 0.0)]
    recuperaciones = observar_resultados(e, monkeypatch)
    resultado = ejecutar(e)
    assert recuperaciones[0].chunks_omitidos == [grande.chunk_id]
    assert resultado.status == JobStatus.REJECTED_QUALITY
    assert resultado.error.code == "COVERAGE_INCOMPLETE"
    assert "doc-b" in resultado.error.message
    assert len(e.indice.busquedas) == 1
    assert resultado.evidencia == [] and resultado.paquete_aprobado is None


def test_ampliacion_vacia_no_hereda_omisiones_y_pasa_al_writer_con_aviso(monkeypatch):
    primaria, secundaria = chunk("doc-a", 0), chunk("doc-b", 0)
    nuevo = chunk("doc-a", 1)
    grande = chunk("doc-a", 2, "Otro complemento extenso de la fuente A. " * 1000)
    e = entorno([primaria, secundaria, nuevo, grande], max_tokens=1000)
    e.estado = e.estado.model_copy(
        update={
            "evidencia": [evidencia(primaria), evidencia(secundaria)],
            "solicitudes_evidencia": ["ronda uno", "ronda dos"],
        }
    )
    e.indice.respuestas = {"ronda uno": [hit(grande, 0.9), hit(nuevo)], "ronda dos": []}
    recuperaciones = observar_resultados(e, monkeypatch)
    resultado = ejecutar(e)
    assert [r.chunks_omitidos for r in recuperaciones] == [[grande.chunk_id], []]
    # Sin evidencia nueva para "ronda dos", el Writer reescribe con la disponible en vez de rechazar.
    assert resultado.status == JobStatus.RUNNING and resultado.error is None
    assert [consulta for consulta, _ in e.indice.busquedas] == ["ronda uno", "ronda dos"]
    assert [item.chunk for item in resultado.evidencia] == [primaria, secundaria, nuevo]
    assert any("ronda dos" in nota and "ronda uno" not in nota for nota in resultado.feedback)
    assert resultado.paquete_aprobado is None


def preparar_rondas(*, delta=0):
    a0, a1, a2, b0, b1 = chunk("doc-a", 0), chunk("doc-a", 1), chunk("doc-a", 2), chunk("doc-b", 0), chunk("doc-b", 1)
    previos = [evidencia(a0), evidencia(a1), evidencia(b0)]
    nuevos = [evidencia(b1, "ronda uno"), evidencia(a2, "ronda dos")]
    limite = conteo_serializado([*previos, *nuevos]) + delta
    e = entorno([a0, a1, a2, b0, b1], k=2, max_tokens=limite)
    e.indice.respuestas = {
        "ronda uno": [hit(a0, 0.9), hit(b1)],
        "ronda dos": [hit(b0, 0.9), hit(a2)],
    }
    e.estado = e.estado.model_copy(
        update={
            "evidencia": previos,
            "intento": 1,
            "solicitudes_evidencia": ["ronda uno", "ronda uno", "ronda dos"],
            "feedback": ["Conservar contribuciones previas"],
        }
    )
    return e, previos, nuevos, limite


def test_dos_ampliaciones_conservan_todos_los_previos_y_frontera_json_exacta():
    e, previos, nuevos, limite = preparar_rondas()
    antes = e.estado.model_dump_json()
    resultado = ejecutar(e)
    assert resultado.status == JobStatus.RUNNING
    assert resultado.evidencia == [*previos, *nuevos]
    assert conteo_serializado(resultado.evidencia) == limite
    assert [consulta for consulta, _ in e.indice.busquedas] == ["ronda uno", "ronda dos"]
    assert len({item.chunk.chunk_id for item in resultado.evidencia}) == 5
    assert resultado.solicitudes_evidencia == []
    assert resultado.intento == 1 and resultado.presupuesto.usadas == 0
    assert "Conservar contribuciones previas" in resultado.feedback
    assert e.estado.model_dump_json() == antes


def test_un_token_menos_no_publica_ni_acumula_union_fuera_del_presupuesto():
    e, previos, nuevos, limite = preparar_rondas(delta=-1)
    resultado = ejecutar(e)
    assert resultado.status == JobStatus.RUNNING
    assert [consulta for consulta, _ in e.indice.busquedas] == ["ronda uno", "ronda dos"]
    # Entra lo que cabe; la ampliación que se pasa por un token queda afuera y se avisa al Writer.
    assert resultado.evidencia == [*previos, nuevos[0]]
    assert conteo_serializado(resultado.evidencia) <= limite
    assert any("ronda dos" in nota for nota in resultado.feedback)
    assert resultado.borrador is None and resultado.paquete_aprobado is None


def test_previos_que_no_caben_no_se_descartan_para_admitir_una_ampliacion():
    e = entorno()
    previos = [evidencia(c) for c in e.indice.chunks]
    e.retriever.configuracion.retrieval_max_tokens = conteo_serializado(previos) - 1
    e.estado = e.estado.model_copy(update={"evidencia": previos, "solicitudes_evidencia": ["más detalle"]})
    resultado = ejecutar(e)
    assert resultado.status == JobStatus.REJECTED_QUALITY and resultado.error.code == "EVIDENCE_BUDGET"
    assert e.indice.busquedas == []
    assert resultado.evidencia == []


@pytest.mark.parametrize("con_peticion", [False, True])
def test_presupuesto_previo_exacto_no_dispara_nuevas_consultas(con_peticion):
    e = entorno()
    previos = [evidencia(c) for c in e.indice.chunks]
    e.retriever.configuracion.retrieval_max_tokens = conteo_serializado(previos)
    e.estado = e.estado.model_copy(
        update={"evidencia": previos, "solicitudes_evidencia": ["más detalle"] if con_peticion else []}
    )
    resultado = ejecutar(e)
    assert e.indice.busquedas == []
    assert resultado.status == JobStatus.RUNNING and resultado.evidencia == previos
    assert any("más detalle" in nota for nota in resultado.feedback) is con_peticion


def test_texto_identico_conserva_procedencia_de_ambos_documentos():
    e = entorno(
        [
            chunk("doc-a", 0, "La misma definición está publicada por ambas fuentes."),
            chunk("doc-b", 0, "La misma definición está publicada por ambas fuentes."),
        ]
    )
    resultado = ejecutar(e)
    assert resultado.status == JobStatus.RUNNING
    assert len(resultado.evidencia) == 2
    assert {item.chunk.document_id for item in resultado.evidencia} == {"doc-a", "doc-b"}
    assert len(e.indice.busquedas) == 1


@pytest.mark.parametrize(
    "alteracion", [{"workspace_id": "ajeno"}, {"document_hash": "c" * 64}, {"document_id": "doc-ajeno"}]
)
def test_evidencia_previa_ajena_u_obsoleta_falla_antes_de_recuperar(alteracion):
    e = entorno()
    previa = evidencia(e.indice.chunks[0].model_copy(update=alteracion))
    e.estado = e.estado.model_copy(update={"evidencia": [previa]})
    resultado = ejecutar(e)
    assert resultado.status == JobStatus.FAILED and resultado.error.code == "INVALID_STATE"
    assert resultado.evidencia == [] and e.indice.busquedas == []


@pytest.mark.parametrize(
    "cambio,status,codigo",
    [
        ("hash", JobStatus.FAILED, "INVALID_STATE"),
        ("retirada", JobStatus.FAILED, "NOT_FOUND"),
        ("cancelacion", JobStatus.CANCELLED, "CANCELLED"),
    ],
)
def test_cambio_durante_recuperacion_detiene_y_limpia(cambio, status, codigo):
    e = entorno()

    def cambiar():
        if cambio == "hash":
            e.documentos["doc-b"].fuente.hash = "c" * 64
        elif cambio == "retirada":
            e.documentos.pop("doc-b")
        else:
            e.contexto._evento_cancelacion.set()

    e.indice.despues = cambiar
    resultado = ejecutar(e)
    assert resultado.status == status and resultado.error.code == codigo
    assert resultado.evidencia == [] and resultado.borrador is None and resultado.trazabilidad is None
    assert len(e.indice.busquedas) == 1


@pytest.mark.integration_mock
def test_chroma_real_comparte_un_embedding_de_consulta_para_ambas_fuentes(tmp_path):
    from test_retriever import ProveedorVCN

    e = entorno()
    proveedor = ProveedorVCN()
    indice = VectorStoreChroma(
        tmp_path / "chroma", GeminiEmbeddings(proveedor, modelo="gemini-embedding-2", dimensiones=3)
    )
    for document_id in HASHES:
        indice.indexar(
            WORKSPACE, document_id, [c for c in e.indice.chunks if c.document_id == document_id], contexto=e.contexto
        )
    antes = len(proveedor.entradas)
    e.dependencias = DependenciasResearcher(
        e.grafo, RetrieverMMR(indice, configuracion=e.retriever.configuracion), parser_version="parser-prueba-v1"
    )
    resultado = ejecutar(e)
    assert resultado.status == JobStatus.RUNNING
    assert {item.chunk.document_id for item in resultado.evidencia} == {"doc-a", "doc-b"}
    assert len(proveedor.entradas) == antes + 1
    assert resultado.presupuesto.usadas == 0


def test_doble_integracion_representa_ambas_fuentes_con_un_embedding_de_consulta():
    from test_multidocument_generation import DobleMultiDocumento

    e = entorno()
    doble = DobleMultiDocumento(16)
    embeddings = GeminiEmbeddings(doble, modelo="gemini-embedding-2", dimensiones=16)
    textos = [
        "Contenido sobre redes y servidores." * 20,
        "Contenido sobre almacenamiento y bases de datos." * 20,
    ]
    documentos = embeddings.embed_documents(textos, contexto=e.contexto)
    antes = doble.llamadas_embeddings
    consulta = embeddings.embed_query(
        "Fuente 1 | Fuente 2 | definiciones conceptos relaciones | fundamentos t\u00e9rminos b\u00e1sicos",
        contexto=e.contexto,
    )
    assert doble.llamadas_embeddings == antes + 1
    assert len(doble.entradas_embeddings[-1]) == 1
    assert documentos[0] != documentos[1]
    for vector in [*documentos, consulta]:
        assert len(vector) == 16
        assert sum(componente**2 for componente in vector) == pytest.approx(1.0)
    assert all(sum(a * b for a, b in zip(documento, consulta, strict=True)) > 0 for documento in documentos)
    assert e.estado.presupuesto.usadas == 0
