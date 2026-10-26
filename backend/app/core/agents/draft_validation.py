"""Validación compartida del borrador y sus citas (Writer/Critic)."""

import json

from pydantic import ValidationError

from app.core.agents.graph_state import BorradorPedagogico, EstadoGrafo
from app.core.agents.prompts import modelo_borrador
from app.core.rag.vectorstore import seccion_del_chunk
from app.schemas.pedagogical import Referencia


class BorradorInvalidoError(ValueError):
    """Corrección pedagógica de estructura/citas, distinta del retry técnico."""


# con este gancho (hook) de python para json rechazo cualquier respuesta que contenga claves duplicadas para prevenir inyecciones o ambiguedades
def _sin_duplicados(pares):
    resultado = {}
    for clave, valor in pares:
        if clave in resultado:
            raise BorradorInvalidoError("El JSON contiene claves duplicadas; entregar un objeto sin duplicados.")
        resultado[clave] = valor
    return resultado


# con esta funcion rechazo valores especiales como nan o infinito en el json devuelto por el modelo para mantener integridad numerica
def _constante_invalida(valor):
    raise BorradorInvalidoError("El JSON no admite NaN ni valores infinitos.")


# aqui proceso la cadena json con controles rigurosos evitando estructuras malformadas antes de pasarlas a pydantic
def parsear_json_estricto(texto: str):
    """Rechaza claves duplicadas, NaN/inf y cercas Markdown en cualquier rol."""
    return json.loads(texto, object_pairs_hook=_sin_duplicados, parse_constant=_constante_invalida)


def evidencia_autorizada(estado: EstadoGrafo):
    """Índice interno de evidencia: una cita nunca cambia de fuente o versión."""
    documentos = estado.document_ids or [estado.document_id]
    hashes = estado.source_hashes or {estado.document_id: estado.source_hash}
    if set(hashes) != set(documentos):
        raise ValueError("Faltan versiones de las fuentes seleccionadas.")
    permitidas = {}
    for evidencia in estado.evidencia:
        chunk = evidencia.chunk
        if (
            chunk.workspace_id != estado.workspace_id
            or chunk.document_id not in documentos
            or chunk.document_hash != hashes[chunk.document_id]
        ):
            raise ValueError("La evidencia no pertenece a las fuentes y versiones autorizadas.")
        anterior = permitidas.get(chunk.chunk_id)
        if anterior is not None and anterior.chunk != chunk:
            raise ValueError("Un chunk_id identifica evidencias diferentes.")
        permitidas[chunk.chunk_id] = evidencia
    return permitidas


def cobertura_documental(estado: EstadoGrafo) -> set[tuple[str, str]]:
    """La cobertura pública sigue siendo plana; su procedencia interna no lo es."""
    evidencia = evidencia_autorizada(estado)
    documentos = estado.document_ids or [estado.document_id]
    if len(documentos) > 1:
        if set(estado.secciones_por_documento) != set(documentos):
            raise ValueError("El mapa de secciones no corresponde a las fuentes seleccionadas.")
        for item in evidencia.values():
            chunk = item.chunk
            if seccion_del_chunk(chunk) not in estado.secciones_por_documento[chunk.document_id]:
                raise ValueError("La sección de la evidencia no pertenece a su documento.")
        recuperadas = {seccion_del_chunk(item.chunk) for item in evidencia.values()}
        if set(estado.secciones_cubiertas) - recuperadas:
            raise ValueError("La cobertura declara secciones sin evidencia recuperada.")
    elif set(estado.secciones_cubiertas) - set(estado.secciones_disponibles):
        raise ValueError("La cobertura contiene secciones ajenas al documento.")
    return {
        (item.chunk.document_id, seccion_del_chunk(item.chunk))
        for item in evidencia.values()
        if seccion_del_chunk(item.chunk) in estado.secciones_cubiertas
    }


# con esta funcion valido exhaustivamente la estructura del borrador comprobando que coincida con el perfil formato y citas autorizadas
def validar_borrador(texto: str, estado: EstadoGrafo) -> tuple[BorradorPedagogico, list[Referencia]]:
    try:
        datos = parsear_json_estricto(texto)
        validado = modelo_borrador(estado.parametros.formato_salida).model_validate_json(
            json.dumps(datos, ensure_ascii=False, allow_nan=False), strict=True
        )
    except (ValidationError, ValueError, TypeError, RecursionError):
        raise BorradorInvalidoError(
            "Entregar JSON estricto con contenido y metadatos completos según el schema."
        ) from None
    solicitud = estado.parametros
    metadatos = validado.metadatos
    esperados = {
        "perfil_aplicado": solicitud.perfil_destinatario,
        "formato_generado": solicitud.formato_salida,
        "nicho_sector": solicitud.nicho_sector,
        "nivel_detalle": solicitud.nivel_detalle,
        "idioma_salida": solicitud.idioma_salida,
        "idioma_origen": estado.idioma_origen,
    }
    # compruebo que los metadatos aplicados en el borrador coincidan exactamente con la solicitud original del usuario
    if any(getattr(metadatos, campo) != valor for campo, valor in esperados.items()):
        raise BorradorInvalidoError(
            "Los metadatos deben respetar perfil, formato, nicho, detalle e idiomas solicitados."
        )
    campo_items = {"flashcards": "items", "tutorial": "pasos", "quiz": "preguntas"}.get(solicitud.formato_salida.value)
    if solicitud.cantidad_items is not None and campo_items is not None:
        if len(getattr(validado.contenido_adaptado, campo_items)) != solicitud.cantidad_items:
            raise BorradorInvalidoError(
                f"Generar exactamente {solicitud.cantidad_items} {campo_items} con evidencia suficiente."
            )
    alcance_esperado = solicitud.alcance.model_dump(exclude={"document_ids"})
    alcance_borrador = metadatos.alcance.model_dump(exclude={"secciones_cubiertas", "document_ids"})
    if alcance_borrador != alcance_esperado:
        raise BorradorInvalidoError("Conservar el alcance solicitado sin sustituir el documento o la sección.")
    if set(metadatos.alcance.secciones_cubiertas or []) != set(estado.secciones_cubiertas):
        raise BorradorInvalidoError("Declarar únicamente la cobertura recuperada por Researcher.")

    es_multidocumento = (
        len(getattr(estado, "document_ids", None) or ([estado.document_id] if estado.document_id else [])) > 1
    )
    cobertura_documental(estado)
    permitidas = {chunk_id: item.como_referencia() for chunk_id, item in evidencia_autorizada(estado).items()}
    referencias = {}
    contenido = validado.contenido_adaptado.model_dump(mode="json")

    # recorro recursivamente la estructura del contenido para validar que cada cita pertenezca a la evidencia recuperada y sustituirla por su forma canonica
    def recorrer(valor):
        if isinstance(valor, dict):
            for campo, item in valor.items():
                if campo == "referencias":
                    for indice, cita in enumerate(item):
                        referencia = Referencia.model_validate(cita)
                        original = permitidas.get(referencia.chunk_id)
                        if original is None:
                            raise BorradorInvalidoError("Usar solo chunk_id presentes en la evidencia autorizada.")
                        if (referencia.pagina is not None and referencia.pagina != original.pagina) or (
                            referencia.seccion is not None and referencia.seccion != original.seccion
                        ):
                            raise BorradorInvalidoError(
                                "Las ubicaciones de las citas deben coincidir con la evidencia."
                            )
                        if (
                            referencia.document_id is not None
                            and original.document_id is not None
                            and referencia.document_id != original.document_id
                        ):
                            raise BorradorInvalidoError(
                                "El document_id de la cita debe coincidir con la evidencia original."
                            )
                        if not es_multidocumento and referencia.document_id is None:
                            ref_salida = Referencia(
                                chunk_id=original.chunk_id,
                                pagina=original.pagina,
                                seccion=original.seccion,
                                document_id=None,
                            )
                        else:
                            ref_salida = original
                        item[indice] = ref_salida.model_dump(mode="json")
                        referencias[original.chunk_id] = ref_salida
                else:
                    recorrer(item)
        elif isinstance(valor, list):
            for item in valor:
                recorrer(item)

    recorrer(contenido)
    metadatos.alcance.secciones_cubiertas = list(estado.secciones_cubiertas)
    if es_multidocumento and getattr(estado, "document_ids", None):
        metadatos.alcance.document_ids = list(estado.document_ids)
    return BorradorPedagogico(contenido_adaptado=contenido, metadatos=metadatos), list(referencias.values())
