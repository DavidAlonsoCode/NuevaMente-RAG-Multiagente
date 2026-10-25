"""MMR multidocumental real; candidatos explícitos y conteo BPE, sin proveedores."""

import math
import time

import pytest
from app.config import Configuracion
from app.core.rag.retriever import RetrieverMMR, tokens_evidencia
from app.core.rag.vectorstore import CoincidenciaVectorial, IndiceInconsistenteError
from app.jobs.manager import ContextoEjecucion, DeadlineExcedidoError, TrabajoCanceladoError
from app.schemas.internal import Chunk, EvidenciaRecuperada

pytestmark = pytest.mark.unit
WORKSPACE = "ws_multi"
HASHES = {f"doc_{letra}": letra * 64 for letra in "abcde"}


def contexto():
    return ContextoEjecucion("job_multi", WORKSPACE, "generation", time.monotonic() + 300)


def coincidencia(documento="doc_a", numero=0, *, similitud=1.0, vector=(1.0, 0.0), texto=None):
    chunk = Chunk(
        chunk_id=f"{documento}_{numero}",
        workspace_id=WORKSPACE,
        document_id=documento,
        document_hash=HASHES[documento],
        indice=numero,
        texto=texto or f"Hecho propio de {documento}, aporte {numero}.",
        cantidad_tokens=1,
        pagina=numero + 1,
        seccion="Sección compartida",
        seccion_id=f"sec_{numero}",
        source_name=f"{documento}.txt",
        source_type="txt",
        language="es",
    )
    return CoincidenciaVectorial(chunk, 1 - similitud, vector)


def evidencia(hit, consulta="consulta"):
    return EvidenciaRecuperada(chunk=hit.chunk, score=hit.similitud, consulta=consulta)


class IndiceCandidatos:
    def __init__(self, candidatos, al_buscar=None):
        self.candidatos = candidatos
        self.al_buscar = al_buscar
        self.consultas = []

    def buscar_multidocumental(self, workspace_id, document_ids, consulta, **opciones):
        self.consultas.append((workspace_id, list(document_ids), consulta, opciones))
        if self.al_buscar:
            self.al_buscar()
        if isinstance(self.candidatos, dict):
            return self.candidatos[consulta]
        return list(self.candidatos)


def recuperador(candidatos, *, k=5, max_tokens=12000, al_buscar=None):
    indice = IndiceCandidatos(candidatos, al_buscar)
    configuracion = Configuracion(
        _env_file=None,
        app_env="test",
        mock_gemini=True,
        mock_oci=True,
        retrieval_k=k,
        retrieval_fetch_k=max(k, 15),
        retrieval_max_tokens=max_tokens,
    )
    return RetrieverMMR(indice, configuracion=configuracion)


def recuperar(retriever, documentos=("doc_a", "doc_b"), **opciones):
    return retriever.recuperar_multidocumental(
        WORKSPACE,
        list(documentos),
        "consulta",
        contexto=opciones.pop("contexto", contexto()),
        source_hashes={d: HASHES[d] for d in documentos},
        **opciones,
    )


def verificar_presupuesto(resultado, retriever, presupuesto, previos=()):
    assert resultado.tokens == tokens_evidencia(resultado.evidencia, retriever.tokenizador)
    assert tokens_evidencia([*previos, *resultado.evidencia], retriever.tokenizador) <= presupuesto


@pytest.mark.parametrize("dominante", ["doc_a", "doc_b"])
@pytest.mark.parametrize("invertir", [False, True])
def test_reserva_fuente_minoritaria_antes_de_consumir_k(dominante, invertir):
    secundaria = "doc_b" if dominante == "doc_a" else "doc_a"
    candidatos = [coincidencia(dominante, n) for n in range(5)]
    candidatos.append(coincidencia(secundaria, similitud=0.4, vector=(0.4, math.sqrt(0.84))))
    if invertir:
        candidatos.reverse()
    retriever = recuperador(candidatos)

    resultado = recuperar(retriever)

    assert {e.chunk.document_id for e in resultado.evidencia} == {dominante, secundaria}
    assert len(resultado.evidencia) == 5
    verificar_presupuesto(resultado, retriever, 12000)
    assert len(retriever.indice.consultas) == 1


def test_empates_y_orden_de_fuentes_no_cambian_evidencia():
    candidatos = [coincidencia(doc, n) for doc in ("doc_a", "doc_b") for n in range(3)]
    primero, segundo = recuperador(candidatos, k=3), recuperador(list(reversed(candidatos)), k=3)

    uno = recuperar(primero)
    dos = recuperar(segundo, documentos=("doc_b", "doc_a"))

    assert {e.chunk.document_id for e in uno.evidencia} == {"doc_a", "doc_b"}
    assert [e.chunk.chunk_id for e in uno.evidencia] == [e.chunk.chunk_id for e in dos.evidencia]


@pytest.mark.parametrize("k", [2, 5])
def test_cinco_fuentes_tienen_base_aunque_k_configurado_sea_menor(k):
    candidatos = [coincidencia(doc) for doc in HASHES]
    retriever = recuperador(candidatos, k=k)

    resultado = recuperar(retriever, documentos=tuple(HASHES))

    assert {e.chunk.document_id for e in resultado.evidencia} == set(HASHES)
    assert len(resultado.evidencia) == 5
    verificar_presupuesto(resultado, retriever, 12000)


def test_alternativa_pequena_habilita_base_completa_sin_recortar_chunks():
    grande = coincidencia(texto="Información extensa de la fuente A. " * 200)
    pequena = coincidencia(numero=1, similitud=0.6, texto="Dato breve A.")
    secundaria = coincidencia("doc_b", similitud=0.4, texto="Hecho breve B.")
    retriever = recuperador([grande, pequena, secundaria], k=2)
    limite = tokens_evidencia([evidencia(pequena), evidencia(secundaria)], retriever.tokenizador)
    assert tokens_evidencia([evidencia(grande), evidencia(secundaria)], retriever.tokenizador) > limite

    resultado = recuperar(retriever, presupuesto_tokens=limite)

    assert {e.chunk.chunk_id for e in resultado.evidencia} == {pequena.chunk.chunk_id, secundaria.chunk.chunk_id}
    assert [e.chunk.texto for e in resultado.evidencia if e.chunk.document_id == "doc_a"] == [pequena.chunk.texto]
    verificar_presupuesto(resultado, retriever, limite)


def test_presupuesto_cero_y_ambito_vacio_no_consultan_indice():
    retriever = recuperador([coincidencia()])

    cero = recuperar(retriever, presupuesto_tokens=0)
    vacio = recuperar(retriever, documentos=())

    assert cero.evidencia == [] and cero.tokens == 0 and cero.avisos
    assert vacio.evidencia == [] and vacio.tokens == 0
    assert retriever.indice.consultas == []


def test_metadata_completa_cuenta_y_no_se_envia_un_chunk_truncado():
    hit = coincidencia(texto="Dato.")
    retriever = recuperador([hit])
    limite = tokens_evidencia([evidencia(hit)], retriever.tokenizador) - 1
    assert limite > hit.chunk.cantidad_tokens

    resultado = recuperar(retriever, documentos=("doc_a",), presupuesto_tokens=limite)

    assert resultado.evidencia == [] and resultado.tokens == 0
    assert any("doc_a" in aviso for aviso in resultado.avisos)
    assert hit.chunk.chunk_id in resultado.chunks_omitidos


@pytest.mark.parametrize("similitud", [-0.4, 0.0])
def test_fuentes_sin_candidatos_pertinentes_se_declaran_sin_forzar_evidencia(similitud):
    retriever = recuperador([coincidencia(), coincidencia("doc_b", similitud=similitud)])

    resultado = recuperar(retriever, documentos=("doc_a", "doc_b", "doc_c"))

    assert {e.chunk.document_id for e in resultado.evidencia} == {"doc_a"}
    for faltante in ("doc_b", "doc_c"):
        assert any(faltante in aviso for aviso in resultado.avisos)


def test_texto_identico_en_documentos_distintos_conserva_ambas_procedencias():
    a = coincidencia(texto="Texto compartido de procedencia documental distinta.")
    b = coincidencia("doc_b", texto=a.chunk.texto)
    duplicado_a = coincidencia(numero=1, texto=a.chunk.texto)
    retriever = recuperador([a, duplicado_a, b])

    resultado = recuperar(retriever)

    assert {e.chunk.document_id for e in resultado.evidencia} == {"doc_a", "doc_b"}
    assert len(resultado.evidencia) == 2
    assert {e.como_referencia().document_id for e in resultado.evidencia} == {"doc_a", "doc_b"}


@pytest.mark.parametrize("defecto", ["workspace", "documento", "hash", "cero", "nan", "dimension", "distancia", "id"])
def test_candidato_invalido_falla_aunque_no_se_elegiria_por_relevancia(defecto):
    a, b = coincidencia(), coincidencia("doc_b")
    malo = coincidencia(numero=10, similitud=0.0)
    chunk, vector, distancia = malo.chunk, malo.vector, malo.distancia
    if defecto == "workspace":
        chunk = chunk.model_copy(update={"workspace_id": "ws_ajeno"})
    elif defecto == "documento":
        chunk = chunk.model_copy(update={"document_id": "doc_ajeno"})
    elif defecto == "hash":
        chunk = chunk.model_copy(update={"document_hash": "f" * 64})
    elif defecto == "cero":
        vector = (0.0, 0.0)
    elif defecto == "nan":
        vector = (float("nan"), 1.0)
    elif defecto == "dimension":
        vector = (1.0, 0.0, 0.0)
    elif defecto == "distancia":
        distancia = float("inf")
    else:
        chunk = chunk.model_copy(update={"chunk_id": a.chunk.chunk_id})
    retriever = recuperador([a, b, CoincidenciaVectorial(chunk, distancia, vector)], k=2)

    with pytest.raises(IndiceInconsistenteError):
        recuperar(retriever)


@pytest.mark.parametrize("motivo", ["cancelacion", "deadline"])
@pytest.mark.parametrize("tras_busqueda", [False, True])
def test_cancelacion_y_deadline_no_devuelven_evidencia_parcial(motivo, tras_busqueda):
    ctx = contexto()

    def interrumpir():
        if motivo == "cancelacion":
            ctx._evento_cancelacion.set()
        else:
            ctx.deadline = time.monotonic() - 1

    retriever = recuperador([coincidencia(), coincidencia("doc_b")], al_buscar=interrumpir if tras_busqueda else None)
    if not tras_busqueda:
        interrumpir()
    error = TrabajoCanceladoError if motivo == "cancelacion" else DeadlineExcedidoError

    with pytest.raises(error):
        recuperar(retriever, contexto=ctx)

    assert len(retriever.indice.consultas) == int(tras_busqueda)


@pytest.mark.parametrize("ajuste", [0, -1])
def test_evidencia_previa_deduplica_y_presupuesta_el_json_conjunto(ajuste):
    previo = evidencia(coincidencia())
    nuevo = coincidencia("doc_b")
    retriever = recuperador([coincidencia(), nuevo], k=2)
    limite = tokens_evidencia([previo, evidencia(nuevo)], retriever.tokenizador) + ajuste
    assert tokens_evidencia([evidencia(nuevo)], retriever.tokenizador) < limite

    resultado = recuperar(retriever, presupuesto_tokens=limite, evidencia_previa=[previo])

    assert all(e.chunk.chunk_id != previo.chunk.chunk_id for e in resultado.evidencia)
    if ajuste == 0:
        assert [e.chunk.chunk_id for e in resultado.evidencia] == [nuevo.chunk.chunk_id]
    else:
        assert resultado.evidencia == []
        assert any("doc_b" in aviso for aviso in resultado.avisos)
    verificar_presupuesto(resultado, retriever, limite, [previo])


def test_k_por_consulta_no_limita_evidencia_previa_y_rondas_respetan_tope_global():
    consultas = {
        f"consulta {ronda}": [coincidencia("doc_a", ronda), coincidencia("doc_b", ronda)] for ronda in range(3)
    }
    retriever = recuperador(consultas, k=2)
    esperada = [evidencia(hit, consulta) for consulta, hits in list(consultas.items())[:2] for hit in hits]
    limite = tokens_evidencia(esperada, retriever.tokenizador)
    previos = []
    for consulta, hits in consultas.items():
        resultado = retriever.recuperar_multidocumental(
            WORKSPACE,
            ["doc_a", "doc_b"],
            consulta,
            contexto=contexto(),
            source_hashes=HASHES,
            presupuesto_tokens=limite,
            evidencia_previa=previos,
        )
        verificar_presupuesto(resultado, retriever, limite, previos)
        assert not ({e.chunk.chunk_id for e in previos} & {e.chunk.chunk_id for e in resultado.evidencia})
        if len(previos) < 4:
            assert {e.chunk.chunk_id for e in resultado.evidencia} == {h.chunk.chunk_id for h in hits}
        else:
            assert resultado.evidencia == []
        previos.extend(resultado.evidencia)
    assert len(previos) == 4
    assert len(retriever.indice.consultas) == 3


def test_evidencia_previa_deduplica_texto_solo_dentro_de_su_documento():
    previo = evidencia(coincidencia(texto="Un mismo hecho conservado en dos fuentes."))
    repetido_a = coincidencia(numero=1, texto=previo.chunk.texto)
    independiente_b = coincidencia("doc_b", texto=previo.chunk.texto)
    retriever = recuperador([repetido_a, independiente_b], k=2)

    resultado = recuperar(retriever, evidencia_previa=[previo])

    assert [e.chunk.chunk_id for e in resultado.evidencia] == [independiente_b.chunk.chunk_id]
    verificar_presupuesto(resultado, retriever, 12000, [previo])


def test_base_que_no_cabe_declara_la_fuente_faltante_sin_exceder_presupuesto():
    candidatos = [coincidencia(), coincidencia("doc_b")]
    retriever = recuperador(candidatos, k=2)
    limite = max(tokens_evidencia([evidencia(hit)], retriever.tokenizador) for hit in candidatos)
    assert tokens_evidencia([evidencia(hit) for hit in candidatos], retriever.tokenizador) > limite

    resultado = recuperar(retriever, presupuesto_tokens=limite)

    assert len(resultado.evidencia) == 1
    faltantes = {"doc_a", "doc_b"} - {e.chunk.document_id for e in resultado.evidencia}
    assert all(any(doc in aviso for aviso in resultado.avisos) for doc in faltantes)
    verificar_presupuesto(resultado, retriever, limite)


def test_base_economica_mejora_relevancia_si_el_presupuesto_lo_permite():
    relevante = coincidencia(texto="Explicación pertinente y extensa de A. " * 80)
    economica = coincidencia(numero=1, similitud=0.6, texto="Dato breve A.")
    secundaria = coincidencia("doc_b", similitud=0.4, texto="Hecho breve B.")
    retriever = recuperador([relevante, economica, secundaria], k=2)

    resultado = recuperar(retriever)

    assert {e.chunk.chunk_id for e in resultado.evidencia} == {
        relevante.chunk.chunk_id,
        secundaria.chunk.chunk_id,
    }
    verificar_presupuesto(resultado, retriever, 12000)


@pytest.mark.parametrize("motivo", ["cancelacion", "deadline"])
def test_interrupcion_durante_seleccion_no_devuelve_evidencia_parcial(motivo):
    ctx = contexto()
    retriever = recuperador([coincidencia(doc, n) for doc in ("doc_a", "doc_b") for n in range(3)])
    original = retriever.tokenizador

    class TokenizadorInterrumpido:
        identidad = original.identidad
        mediciones = 0

        def contar(self, texto):
            cantidad = original.contar(texto)
            self.mediciones += 1
            if self.mediciones == 3:
                if motivo == "cancelacion":
                    ctx._evento_cancelacion.set()
                else:
                    ctx.deadline = time.monotonic() - 1
            return cantidad

    retriever.tokenizador = TokenizadorInterrumpido()
    error = TrabajoCanceladoError if motivo == "cancelacion" else DeadlineExcedidoError

    with pytest.raises(error):
        recuperar(retriever, contexto=ctx)

    assert len(retriever.indice.consultas) == 1
    assert retriever.tokenizador.mediciones >= 3


def test_alternativa_de_igual_texto_con_metadata_menor_permite_cubrir_ambas_fuentes():
    texto = "Una misma definición válida y pertinente de la fuente A."
    grande = coincidencia(texto=texto)
    grande.chunk.seccion = "Encabezado original extenso de la fuente A. " * 200
    pequena = coincidencia(numero=1, similitud=0.6, texto=texto)
    pequena.chunk.seccion = "Encabezado breve"
    secundaria = coincidencia("doc_b", similitud=0.4, texto="Hecho pertinente de B.")
    retriever = recuperador([grande, pequena, secundaria], k=2)
    limite = tokens_evidencia([evidencia(pequena), evidencia(secundaria)], retriever.tokenizador)
    assert tokens_evidencia([evidencia(grande), evidencia(secundaria)], retriever.tokenizador) > limite

    resultado = recuperar(retriever, presupuesto_tokens=limite)

    assert {item.chunk.chunk_id for item in resultado.evidencia} == {
        pequena.chunk.chunk_id,
        secundaria.chunk.chunk_id,
    }
    assert len(resultado.evidencia) == 2
    assert sum(item.chunk.document_id == "doc_a" for item in resultado.evidencia) == 1
    assert tokens_evidencia(resultado.evidencia, retriever.tokenizador) == limite
    verificar_presupuesto(resultado, retriever, limite)


@pytest.mark.parametrize("similitud", [0.05, 0.1])
def test_afinidad_positiva_debil_no_se_confunde_con_falta_de_respaldo_factual(similitud):
    a = coincidencia()
    b = coincidencia("doc_b", similitud=similitud, vector=(similitud, math.sqrt(1 - similitud**2)))
    retriever = recuperador([a, b], k=2)

    resultado = recuperar(retriever)

    assert {e.chunk.document_id for e in resultado.evidencia} == {"doc_a", "doc_b"}
    assert len(resultado.evidencia) == 2
    seleccionada_b = next(e for e in resultado.evidencia if e.chunk.document_id == "doc_b")
    assert seleccionada_b.chunk == b.chunk
    assert seleccionada_b.score == pytest.approx(similitud)
    assert not any("doc_b" in aviso for aviso in resultado.avisos)
    verificar_presupuesto(resultado, retriever, 12000)
