"""Cobertura documental y contribuciones factuales de una síntesis."""

import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import test_critic as critic_cases
import test_writer as writer_cases
from app.core.agents.critic import DependenciasCritic, critic
from app.core.agents.critic_contracts import ImagenOriginal
from app.core.agents.graph_state import DependenciasGrafo, DocumentoGeneracion
from app.core.agents.supervisor import crear_estado_inicial
from app.core.agents.writer import DependenciasWriter, writer
from app.schemas.internal import Chunk, EvidenciaRecuperada
from app.schemas.responses import DocumentoFuente
from test_writer import aplicar, respuesta

entorno_writer = writer_cases.entorno
entorno = critic_cases.entorno
pytestmark = pytest.mark.unit


def preparar(entorno, *, seccion_b="sec_2", alcance="multi_documento", contenido="ambas", visual=False):
    original_writer, original_critic, _ = entorno
    docs = {
        document_id: DocumentoGeneracion(
            workspace_id="ws_test",
            fuente=DocumentoFuente(document_id=document_id, titulo=titulo, hash=huella, version="v1"),
            estado="ready",
            idioma_origen="en",
            secciones=(seccion,),
            contiene_material_visual=visual and document_id == "doc_b",
        )
        for document_id, titulo, huella, seccion in (
            ("doc_a", "Redes", "sha256:fuente-redes", "sec_1"),
            ("doc_b", "Persistencia", "sha256:fuente-persistencia", seccion_b),
        )
    }
    grafo = DependenciasGrafo(original_writer.grafo.ejecucion, lambda ws, doc_id: docs.get(doc_id))
    deps_writer = DependenciasWriter(grafo, original_writer.proveedor, original_writer.configuracion)
    deps = DependenciasCritic(grafo, original_critic.proveedor, original_critic.configuracion)
    estado = crear_estado_inicial(
        {
            "document_ids": ["doc_a", "doc_b"],
            "perfil_destinatario": "principiante",
            "formato_salida": "flashcards",
            "nicho_sector": "general",
            "nivel_detalle": "didactico",
            "idioma_salida": "pt",
            "alcance": {"tipo": alcance},
        },
        generation_id="gen_sintesis",
        dependencias=grafo,
    )
    assert estado.error is None
    estado.evidencia = [
        EvidenciaRecuperada(
            chunk=Chunk(
                chunk_id=chunk_id,
                document_id=document_id,
                workspace_id="ws_test",
                document_hash=docs[document_id].fuente.hash,
                indice=0,
                texto=texto,
                cantidad_tokens=15,
                pagina=pagina,
                seccion=titulo,
                seccion_id=seccion,
                es_diagrama=visual and document_id == "doc_b",
            ),
            score=0.9,
            consulta=titulo,
        )
        for document_id, chunk_id, pagina, seccion, titulo, texto in (
            ("doc_a", "ch_a", 1, "sec_1", "Redes", "Uma sub-rede divide o espaço de endereços."),
            ("doc_b", "ch_b", 2, seccion_b, "Persistência", "Uma transação agrupa operações de escrita."),
        )
    ]
    estado.secciones_cubiertas = list(dict.fromkeys(["sec_1", seccion_b]))
    estado.trazabilidad = writer_cases.estado_inicial((original_writer, entorno[2])).trazabilidad
    datos = respuesta(estado)
    plantilla = datos["contenido_adaptado"]["items"][0]
    items = []
    for indice, evidencia in enumerate(estado.evidencia):
        tarjeta = deepcopy(plantilla)
        tarjeta.update(
            id=f"tarjeta_{indice + 1}",
            frente="Qual é o conceito documentado?",
            dorso=evidencia.chunk.texto,
            referencias=[{"chunk_id": evidencia.chunk.chunk_id}],
        )
        items.append(tarjeta)
    if contenido == "solo_a":
        items = items[:1]
    elif contenido == "decorativa":
        items[1]["dorso"] = "Consulte o segundo documento para continuar a leitura."
    elif contenido == "no_utilizada":
        items = items[:1]
        items[0]["referencias"].append({"chunk_id": "ch_b"})
    datos["contenido_adaptado"]["items"] = items
    original_writer.proveedor.programar_generacion(json.dumps(datos))
    estado = aplicar(estado, writer(estado, deps_writer))
    assert estado.borrador is not None and estado.error is None
    return SimpleNamespace(estado=estado, deps=deps, docs=docs)


def programar_juez(caso, *, indices=(0, 1), usar_solo_a=False, metadata_b=False, modificar=None):
    """Veredictos explícitos en segmentos reales, con rúbrica perfecta."""
    if metadata_b:
        caso.estado.borrador.metadatos.conceptos_clave[0] = "Uma transação agrupa operações de escrita."
    doble = caso.deps.proveedor
    revisar = doble._revisar

    def responder(**entrada):
        datos = json.loads(entrada["prompt"])
        fase = datos["tarea"]
        if fase == "descomponer":
            afirmaciones = [
                {
                    "texto": datos["segmentos"][f"/contenido_adaptado/items/{indice}/dorso"]["texto"],
                    "ubicacion": f"/contenido_adaptado/items/{indice}/dorso",
                }
                for indice in indices
            ]
            if metadata_b:
                afirmaciones.append(
                    {"texto": "Uma transação agrupa operações de escrita.", "ubicacion": "/metadatos/conceptos_clave/0"}
                )
            salida = {"afirmaciones": afirmaciones}
        elif fase == "juzgar":
            salida = {
                "juicios": [
                    {
                        "id_afirmacion": afirmacion["id"],
                        "estado": "respaldada",
                        "motivo": "La proposición está respaldada por la fuente citada.",
                        "referencias": [
                            "ch_a"
                            if usar_solo_a
                            else "ch_b"
                            if metadata_b and indice == len(datos["afirmaciones"]) - 1
                            else afirmacion["citas_permitidas"][0]
                        ],
                    }
                    for indice, afirmacion in enumerate(datos["afirmaciones"])
                ]
            }
        elif fase == "pedagogia":
            salida = critic_cases.rubrica()
        else:
            salida = {"chunk_id": datos["chunk_visual"], "estado": "aprobada", "descripcion": "Imagen coherente."}
        if modificar:
            salida = modificar(fase, salida, datos)
        doble.programar_revision(json.dumps(salida))
        return revisar(**entrada)

    doble._revisar = Mock(side_effect=responder)


def exigir_no_publicado(estado):
    assert estado.paquete_aprobado is None and estado.persistencia is None and estado.almacenamiento is None
    assert estado.status != "completed"


@pytest.mark.parametrize("alcance", ["multi_documento", "espacio_completo"])
@pytest.mark.parametrize("seccion_b", ["sec_2", "sec_1"])
def test_sintesis_valida_con_secciones_distintas_u_homonimas(entorno, alcance, seccion_b):
    caso = preparar(entorno, alcance=alcance, seccion_b=seccion_b)
    programar_juez(caso)
    nuevo = aplicar(caso.estado, critic(caso.estado, caso.deps))
    assert nuevo.error is None and nuevo.destino_revision == "finalizer"
    assert nuevo.evaluacion_pedagogica.estado_evaluacion == "aprobada"
    assert nuevo.evaluacion_pedagogica.anclaje_fuente_score == 1
    assert {r.document_id for r in nuevo.referencias} == {"doc_a", "doc_b"}
    assert nuevo.borrador.metadatos.alcance.secciones_cubiertas == caso.estado.secciones_cubiertas
    exigir_no_publicado(nuevo)


@pytest.mark.parametrize("alcance", ["multi_documento", "espacio_completo"])
@pytest.mark.parametrize("seccion_b", ["sec_2", "sec_1"])
def test_evidencia_de_ambas_no_acredita_contenido_solo_a(entorno, alcance, seccion_b):
    caso = preparar(entorno, alcance=alcance, seccion_b=seccion_b, contenido="solo_a")
    programar_juez(caso, indices=(0,))
    nuevo = aplicar(caso.estado, critic(caso.estado, caso.deps))
    assert nuevo.error is None
    assert nuevo.evaluacion_pedagogica.anclaje_fuente_score == 1
    assert nuevo.evaluacion_pedagogica.estado_evaluacion == "requiere_revision"
    assert nuevo.destino_revision in {"writer", "researcher"}
    assert "doc_b" in " ".join([*nuevo.feedback, *nuevo.solicitudes_evidencia])
    exigir_no_publicado(nuevo)


@pytest.mark.parametrize("decoracion", ["referencia_sin_juicio", "metadata", "cita_no_utilizada"])
def test_referencia_b_decorativa_no_cuenta_como_contribucion(entorno, decoracion):
    caso = preparar(entorno, contenido="no_utilizada" if decoracion == "cita_no_utilizada" else "decorativa")
    programar_juez(
        caso, indices=(0,), usar_solo_a=decoracion == "cita_no_utilizada", metadata_b=decoracion == "metadata"
    )
    nuevo = aplicar(caso.estado, critic(caso.estado, caso.deps))
    assert nuevo.error is None
    assert nuevo.evaluacion_pedagogica.anclaje_fuente_score == 1
    assert nuevo.evaluacion_pedagogica.estado_evaluacion == "requiere_revision"
    assert "doc_b" in " ".join([*nuevo.feedback, *nuevo.solicitudes_evidencia])
    assert nuevo.destino_revision != "finalizer"
    exigir_no_publicado(nuevo)


def test_homonimia_no_autoriza_seccion_que_no_pertenece_a_su_documento(entorno):
    caso = preparar(entorno)
    caso.estado.evidencia[1].chunk.seccion_id = "sec_1"
    caso.estado.secciones_cubiertas = ["sec_1"]
    caso.estado.borrador.metadatos.alcance.secciones_cubiertas = ["sec_1"]
    programar_juez(caso)
    nuevo = aplicar(caso.estado, critic(caso.estado, caso.deps))
    assert nuevo.status == "failed" and nuevo.error.code == "INVALID_STATE"
    assert nuevo.destino_revision is None and nuevo.borrador is None
    exigir_no_publicado(nuevo)


@pytest.mark.parametrize("seccion_b", ["sec_1", "sec_2"])
def test_cobertura_de_a_no_acredita_b_sin_evidencia_propia(entorno, seccion_b):
    caso = preparar(entorno, seccion_b=seccion_b, contenido="solo_a")
    caso.estado.evidencia = caso.estado.evidencia[:1]
    caso.estado.secciones_cubiertas = ["sec_1"]
    caso.estado.borrador.metadatos.alcance.secciones_cubiertas = ["sec_1"]
    programar_juez(caso, indices=(0,))
    nuevo = aplicar(caso.estado, critic(caso.estado, caso.deps))
    assert nuevo.error is None and nuevo.destino_revision == "researcher"
    assert nuevo.evaluacion_pedagogica.cobertura_objetivos == "parcial"
    assert "doc_b" in " ".join(nuevo.solicitudes_evidencia)
    exigir_no_publicado(nuevo)


@pytest.mark.parametrize("alteracion", ["workspace", "hash", "documento", "chunk_id"])
def test_evidencia_secundaria_con_identidad_invalida_no_llega_al_juez(entorno, alteracion):
    caso = preparar(entorno)
    chunk = caso.estado.evidencia[1].chunk
    if alteracion == "workspace":
        chunk.workspace_id = "ws_ajeno"
    elif alteracion == "hash":
        chunk.document_hash = caso.estado.source_hash
    elif alteracion == "documento":
        chunk.document_id = "doc_ajeno"
    else:
        chunk.chunk_id = caso.estado.evidencia[0].chunk.chunk_id
    nuevo = aplicar(caso.estado, critic(caso.estado, caso.deps))
    assert nuevo.status == "failed" and nuevo.error.code == "INVALID_STATE"
    assert caso.deps.proveedor.llamadas_verificacion == 0
    assert nuevo.borrador is None and nuevo.destino_revision is None
    exigir_no_publicado(nuevo)


@pytest.mark.parametrize("document_id", ["doc_a", "doc_ajeno"])
def test_cita_de_b_atribuida_a_otro_documento_no_se_aprueba(entorno, document_id):
    caso = preparar(entorno)
    caso.estado.borrador.contenido_adaptado.items[1].referencias[0].document_id = document_id
    programar_juez(caso)
    nuevo = aplicar(caso.estado, critic(caso.estado, caso.deps))
    assert nuevo.destino_revision == "writer" and nuevo.feedback
    assert nuevo.evaluacion_pedagogica is None
    exigir_no_publicado(nuevo)


@pytest.mark.parametrize("alteracion", ["retirada", "hash", "mapa"])
@pytest.mark.parametrize("fase_cambio", ["descomponer", "juzgar", "pedagogia"])
def test_revalida_secundaria_despues_de_cada_evaluacion(entorno, alteracion, fase_cambio):
    caso = preparar(entorno)

    def cambiar(fase, salida, datos):
        if fase == fase_cambio:
            if alteracion == "retirada":
                caso.docs.pop("doc_b")
            elif alteracion == "hash":
                caso.docs["doc_b"].fuente.hash = "sha256:version-nueva"
            else:
                caso.docs["doc_b"].secciones = ("sec_nueva",)
        return salida

    programar_juez(caso, modificar=cambiar)
    nuevo = aplicar(caso.estado, critic(caso.estado, caso.deps))
    assert nuevo.status == "failed"
    assert nuevo.error.code == ("NOT_FOUND" if alteracion == "retirada" else "INVALID_STATE")
    assert nuevo.borrador is None and not nuevo.referencias
    assert nuevo.destino_revision is None and nuevo.evaluacion_pedagogica is None
    exigir_no_publicado(nuevo)


@pytest.mark.parametrize("alteracion", ["retirada", "hash", "mapa"])
@pytest.mark.parametrize("resultado_loader", ["imagen", "ausente", "ambigua"])
def test_revalida_incluso_cuando_loader_visual_no_devuelve_imagen_util(entorno, alteracion, resultado_loader):
    caso = preparar(entorno, visual=True)
    llamadas = []

    def loader(workspace_id, document_id, source_hash, chunk_id):
        llamadas.append((workspace_id, document_id, source_hash, chunk_id))
        if alteracion == "retirada":
            caso.docs.pop("doc_b")
        elif alteracion == "hash":
            caso.docs["doc_b"].fuente.hash = "sha256:version-nueva"
        else:
            caso.docs["doc_b"].secciones = ("sec_nueva",)
        if resultado_loader == "ausente":
            return None
        return ImagenOriginal(
            workspace_id=workspace_id,
            document_id=document_id,
            source_hash=source_hash,
            chunk_id=chunk_id,
            contenido=critic_cases.PNG,
            mime_type="image/png",
            tokens_estimados=5,
            interpretacion_ambigua=resultado_loader == "ambigua",
        )

    caso.deps = DependenciasCritic(caso.deps.grafo, caso.deps.proveedor, caso.deps.configuracion, loader)
    programar_juez(caso)
    nuevo = aplicar(caso.estado, critic(caso.estado, caso.deps))
    assert llamadas == [("ws_test", "doc_b", "sha256:fuente-persistencia", "ch_b")]
    assert nuevo.status == "failed"
    assert nuevo.error.code == ("NOT_FOUND" if alteracion == "retirada" else "INVALID_STATE")
    assert nuevo.borrador is None and nuevo.destino_revision is None
    exigir_no_publicado(nuevo)


def test_no_exige_todas_las_secciones_de_cada_fuente(entorno):
    caso = preparar(entorno)
    for document_id, documento in caso.docs.items():
        documento.secciones += (f"sec_extra_{document_id}",)
    caso.estado.secciones_por_documento = {doc_id: doc.secciones for doc_id, doc in caso.docs.items()}
    caso.estado.secciones_disponibles = caso.docs["doc_a"].secciones
    programar_juez(caso)
    nuevo = aplicar(caso.estado, critic(caso.estado, caso.deps))
    assert nuevo.error is None and nuevo.destino_revision == "finalizer"
    assert nuevo.evaluacion_pedagogica.cobertura_objetivos == "completa"


def test_tercera_redaccion_sin_contribucion_secundaria_rechaza_sin_publicar(entorno):
    caso = preparar(entorno, contenido="solo_a")
    caso.estado.intento = 3
    programar_juez(caso, indices=(0,))
    nuevo = aplicar(caso.estado, critic(caso.estado, caso.deps))
    assert nuevo.status == "rejected_quality" and nuevo.error.code == "QUALITY_REJECTED"
    assert nuevo.intento == 3 and nuevo.borrador is None and not nuevo.referencias
    assert nuevo.destino_revision is None
    exigir_no_publicado(nuevo)


@pytest.mark.parametrize("tipo", ["chat", "glosario"])
def test_consumidores_no_exigen_cuota_de_sintesis_multidocumental(entorno, tipo):
    caso = preparar(entorno, contenido="solo_a")
    caso.deps.grafo.ejecucion.tipo = tipo
    programar_juez(caso, indices=(0,))
    nuevo = aplicar(caso.estado, critic(caso.estado, caso.deps))
    assert nuevo.error is None and nuevo.destino_revision == "finalizer"
    assert nuevo.evaluacion_pedagogica.estado_evaluacion == "aprobada"


def test_estado_no_puede_ocultar_fuente_aun_presente_en_solicitud(entorno):
    caso = preparar(entorno, contenido="solo_a")
    caso.estado.document_ids = ["doc_a"]
    caso.estado.documentos_fuente = [caso.docs["doc_a"].fuente]
    caso.estado.source_hashes = {"doc_a": caso.estado.source_hash}
    caso.estado.secciones_por_documento = {"doc_a": caso.docs["doc_a"].secciones}
    caso.estado.idiomas_origen = {"doc_a": "en"}
    caso.estado.evidencia = caso.estado.evidencia[:1]
    caso.estado.secciones_cubiertas = ["sec_1"]
    caso.estado.borrador.metadatos.alcance.secciones_cubiertas = ["sec_1"]
    programar_juez(caso, indices=(0,))
    nuevo = aplicar(caso.estado, critic(caso.estado, caso.deps))
    assert nuevo.status == "failed" and nuevo.error.code == "INVALID_STATE"
    assert nuevo.borrador is None and nuevo.destino_revision is None
    exigir_no_publicado(nuevo)
