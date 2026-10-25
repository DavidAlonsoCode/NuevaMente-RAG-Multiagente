"""Recuperación MMR síncrona (#18); relevancia no equivale a fidelidad."""

from __future__ import annotations

import json
import math
from collections.abc import Callable

from pydantic import BaseModel, ConfigDict, Field

from app.config import Configuracion
from app.core.rag.tokenizer import Tokenizador, TokenizadorBPE
from app.core.rag.vectorstore import (
    DEMO_WORKSPACE_ID,
    CoincidenciaVectorial,
    IndiceInconsistenteError,
    VectorStoreChroma,
    seccion_del_chunk,
)
from app.jobs.manager import ContextoEjecucion
from app.schemas.internal import EvidenciaRecuperada


# con este modelo de pydantic encapsulo los fragmentos recuperados el conteo de tokens utilizados y los avisos de presupuesto segun el issue 18
class ResultadoRecuperacion(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    evidencia: list[EvidenciaRecuperada] = Field(default_factory=list)
    tokens: int = Field(default=0, ge=0)
    chunks_omitidos: list[str] = Field(default_factory=list)
    avisos: list[str] = Field(default_factory=list)

    @property
    def recortado(self) -> bool:
        return bool(self.chunks_omitidos)


# con esta funcion mido con precision la cantidad real de tokens bpe que ocupan los fragmentos y sus metadatos antes de enviarlos al modelo
def tokens_evidencia(evidencia: list[EvidenciaRecuperada], tokenizador: Tokenizador) -> int:
    """Cuenta texto y metadatos reales; nunca confía en cantidad_tokens del índice."""
    if not evidencia:
        return 0
    datos = json.dumps([e.model_dump(mode="json") for e in evidencia], ensure_ascii=False, separators=(",", ":"))
    return tokenizador.contar(datos)


def identidad_texto(texto: str) -> str:
    return " ".join(texto.split())


# aqui calculo la similitud coseno entre dos vectores normalizados utilizando la funcion math hypot de python para calcular la longitud vectorial
def _coseno(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    na, nb = math.hypot(*a), math.hypot(*b)
    return max(-1.0, min(1.0, sum((x / na) * (y / nb) for x, y in zip(a, b, strict=True))))


# aqui implemento el recuperador con diversificacion mmr (maximal marginal relevance) para seleccionar fragmentos relevantes y diversos segun el issue 18
class RetrieverMMR:
    """Mismos filtros/contexto de Chroma; sin traducción generativa ni retries propios.

    Las consultas ES/EN/PT conservan sus términos originales. GeminiEmbeddings
    aplica la preparación asimétrica de consulta compatible con el índice (#13).
    Los chunks que no caben se omiten completos y se identifican en el resultado.
    """

    # en el constructor guardo la referencia al indice vectorial de chromadb la configuracion general y el tokenizador bpe
    def __init__(
        self,
        indice: VectorStoreChroma,
        *,
        configuracion: Configuracion | None = None,
        tokenizador: Tokenizador | None = None,
    ) -> None:
        self.indice = indice
        self.configuracion = configuracion or Configuracion()
        self.tokenizador = tokenizador or TokenizadorBPE()

    @property
    def max_tokens(self) -> int:
        return self.configuracion.retrieval_max_tokens

    # con este metodo inicio la recuperacion para espacios de trabajo privados delegando la consulta al indice vectorial autorizado
    def recuperar(
        self,
        workspace_id: str,
        document_id: str,
        consulta: str,
        *,
        contexto: ContextoEjecucion,
        seccion: str | None = None,
        presupuesto_tokens: int | None = None,
        source_hash: str | None = None,
    ) -> ResultadoRecuperacion:
        return self._recuperar(
            lambda: self.indice.buscar(
                workspace_id,
                document_id,
                consulta.strip(),
                contexto=contexto,
                limite=self.configuracion.retrieval_fetch_k,
                seccion=seccion,
            ),
            workspace_id,
            document_id,
            consulta,
            contexto,
            seccion,
            presupuesto_tokens,
            source_hash,
        )

    # con este metodo realizo la busqueda en la biblioteca demo publica sin requerir permisos de un espacio privado
    def recuperar_demo(
        self,
        document_id: str,
        consulta: str,
        *,
        contexto: ContextoEjecucion,
        seccion: str | None = None,
        presupuesto_tokens: int | None = None,
        source_hash: str | None = None,
    ) -> ResultadoRecuperacion:
        return self._recuperar(
            lambda: self.indice.buscar_demo(
                document_id,
                consulta.strip(),
                contexto=contexto,
                limite=self.configuracion.retrieval_fetch_k,
                seccion=seccion,
            ),
            DEMO_WORKSPACE_ID,
            document_id,
            consulta,
            contexto,
            seccion,
            presupuesto_tokens,
            source_hash,
        )

    # aqui implemento el algoritmo central de recuperacion validando parametros presupuestos y descartando evidencia inconsistente o ajena
    def _recuperar(
        self,
        buscar: Callable[[], list[CoincidenciaVectorial]],
        workspace_id: str,
        document_id: str,
        consulta: str,
        contexto: ContextoEjecucion,
        seccion: str | None,
        presupuesto_tokens: int | None,
        source_hash: str | None,
    ) -> ResultadoRecuperacion:
        contexto.chequear()
        if not isinstance(consulta, str) or not consulta.strip() or len(consulta) > 2000:
            raise ValueError("La consulta debe contener entre 1 y 2000 caracteres")
        if seccion is not None and (not isinstance(seccion, str) or not seccion.strip()):
            raise ValueError("La sección debe tener un identificador no vacío")
        presupuesto = self.max_tokens if presupuesto_tokens is None else presupuesto_tokens
        if type(presupuesto) is not int or not 0 <= presupuesto <= self.max_tokens:
            raise ValueError("El presupuesto debe estar entre cero y RETRIEVAL_MAX_TOKENS")
        # descarto inmediatamente si el presupuesto de tokens recibido es cero para evitar consumo innecesario de recursos
        if presupuesto == 0:
            return ResultadoRecuperacion(avisos=["Presupuesto de evidencia agotado; no se ejecutó una consulta."])
        candidatos = buscar()
        contexto.chequear()
        ids, textos, pendientes = {}, set(), []
        dimensiones = None
        version = source_hash
        # ordeno los candidatos preliminares por su similitud y verifico que pertenezcan exactamente al documento y espacio autorizados
        for hit in sorted(candidatos, key=lambda h: (-h.similitud, h.chunk.indice, h.chunk.chunk_id)):
            chunk = hit.chunk
            if (chunk.workspace_id, chunk.document_id) != (workspace_id, document_id):
                raise IndiceInconsistenteError("La evidencia pertenece a otro documento o espacio")
            if version is None:
                version = chunk.document_hash
            if (
                not version
                or chunk.document_hash != version
                or (seccion is not None and seccion_del_chunk(chunk) != seccion)
            ):
                raise IndiceInconsistenteError("La evidencia pertenece a otra versión o sección")
            if dimensiones is None:
                dimensiones = len(hit.vector)
            norma = math.hypot(*hit.vector)
            if (
                not dimensiones
                or len(hit.vector) != dimensiones
                or not math.isfinite(hit.distancia)
                or any(isinstance(v, bool) or not math.isfinite(v) for v in hit.vector)
                or not math.isfinite(norma)
                or norma == 0
            ):
                raise IndiceInconsistenteError("El índice contiene vectores o distancias inválidos")
            if chunk.chunk_id in ids and ids[chunk.chunk_id] != chunk:
                raise IndiceInconsistenteError("Un chunk_id identifica contenidos diferentes")
            clave = identidad_texto(chunk.texto)
            if chunk.chunk_id not in ids and clave not in textos:
                pendientes.append(hit)
            ids[chunk.chunk_id] = chunk
            textos.add(clave)
        elegidos, evidencia, omitidos = [], [], []
        lam = self.configuracion.retrieval_lambda_mult
        # con este bucle implemento la seleccion por relevancia marginal maxima (mmr) equilibrando relevancia frente a la consulta con novedad frente a lo ya elegido
        while pendientes and len(evidencia) < self.configuracion.retrieval_k:
            contexto.chequear()
            hit = max(
                pendientes,
                key=lambda h: (
                    lam * h.similitud
                    - (1 - lam)
                    * max(
                        (_coseno(h.vector, elegido.vector) for elegido in elegidos),
                        default=0.0,
                    )
                ),
            )
            pendientes.remove(hit)
            item = EvidenciaRecuperada(chunk=hit.chunk, score=hit.similitud, consulta=consulta.strip())
            # si un fragmento seleccionado sobrepasa el presupuesto maximo de tokens permitido lo omito por completo y registro su aviso
            if tokens_evidencia([*evidencia, item], self.tokenizador) > presupuesto:
                omitidos.append(hit.chunk.chunk_id)
                continue
            elegidos.append(hit)
            evidencia.append(item)
        contexto.chequear()
        return ResultadoRecuperacion(
            evidencia=evidencia,
            tokens=tokens_evidencia(evidencia, self.tokenizador),
            chunks_omitidos=omitidos,
            avisos=["Se omitieron chunks completos por el presupuesto de evidencia."] if omitidos else [],
        )

    def recuperar_multidocumental(
        self,
        workspace_id: str,
        document_ids: list[str],
        consulta: str,
        *,
        contexto: ContextoEjecucion,
        presupuesto_tokens: int | None = None,
        source_hashes: dict[str, str] | None = None,
        evidencia_previa: list[EvidenciaRecuperada] | None = None,
    ) -> ResultadoRecuperacion:
        """Base pertinente por fuente, seguida de complementos MMR.

        El presupuesto limita el JSON conjunto de evidencia previa y nueva. El
        resultado contiene solo incorporaciones y sus tokens, sin volver a contar
        duplicados como plazas nuevas. La similitud orienta selección, no fidelidad.
        """
        contexto.chequear()
        if not document_ids:
            return ResultadoRecuperacion()
        if not isinstance(consulta, str) or not consulta.strip() or len(consulta) > 2000:
            raise ValueError("La consulta debe contener entre 1 y 2000 caracteres")
        presupuesto = self.max_tokens if presupuesto_tokens is None else presupuesto_tokens
        if type(presupuesto) is not int or not 0 <= presupuesto <= self.max_tokens:
            raise ValueError("El presupuesto debe estar entre cero y RETRIEVAL_MAX_TOKENS")

        documentos = sorted(set(document_ids))
        ids, textos_por_doc, previos = {}, {}, []
        versiones = dict(source_hashes or {})

        def registrar(chunk):
            if chunk.workspace_id != workspace_id or chunk.document_id not in documentos:
                raise IndiceInconsistenteError("La evidencia pertenece a otro documento o espacio")
            esperado = versiones.setdefault(chunk.document_id, chunk.document_hash)
            if not esperado or chunk.document_hash != esperado:
                raise IndiceInconsistenteError("La evidencia pertenece a otra versión")
            if chunk.chunk_id in ids and ids[chunk.chunk_id] != chunk:
                raise IndiceInconsistenteError("Un chunk_id identifica contenidos diferentes")
            nuevo = chunk.chunk_id not in ids
            ids[chunk.chunk_id] = chunk
            return nuevo

        for item in evidencia_previa or []:
            contexto.chequear()
            nuevo = registrar(item.chunk)
            textos_doc = textos_por_doc.setdefault(item.chunk.document_id, set())
            clave = identidad_texto(item.chunk.texto)
            if nuevo and clave not in textos_doc:
                previos.append(item)
                textos_doc.add(clave)
        if tokens_evidencia(previos, self.tokenizador) > presupuesto:
            raise ValueError("La evidencia previa excede el presupuesto compartido")
        if presupuesto == 0:
            return ResultadoRecuperacion(avisos=["Presupuesto de evidencia agotado; no se ejecutó una consulta."])

        limite_fetch = min(100, max(self.configuracion.retrieval_fetch_k, 15 * len(documentos)))
        candidatos = self.indice.buscar_multidocumental(
            workspace_id,
            documentos,
            consulta.strip(),
            contexto=contexto,
            limite=limite_fetch,
        )
        contexto.chequear()

        pendientes = []
        dimensiones = None
        for hit in sorted(
            candidatos, key=lambda h: (-h.similitud, h.chunk.document_id, h.chunk.indice, h.chunk.chunk_id)
        ):
            contexto.chequear()
            chunk = hit.chunk
            nuevo = registrar(chunk)
            if dimensiones is None:
                dimensiones = len(hit.vector)
            norma = math.hypot(*hit.vector)
            if (
                not dimensiones
                or len(hit.vector) != dimensiones
                or not math.isfinite(hit.distancia)
                or any(isinstance(v, bool) or not math.isfinite(v) for v in hit.vector)
                or not math.isfinite(norma)
                or norma == 0
            ):
                raise IndiceInconsistenteError("El índice contiene vectores o distancias inválidos")
            # La afinidad positiva permite intentar representación; no certifica
            # respaldo factual ni impone el antiguo umbral del bonus de equidad.
            if (
                nuevo
                and identidad_texto(chunk.texto) not in textos_por_doc.get(chunk.document_id, set())
                and hit.similitud > 0.0
            ):
                pendientes.append(hit)

        items = {
            h.chunk.chunk_id: EvidenciaRecuperada(chunk=h.chunk, score=h.similitud, consulta=consulta.strip())
            for h in pendientes
        }
        elegidos, omitidos = [], set()
        lam = self.configuracion.retrieval_lambda_mult
        cubiertas = {e.chunk.document_id for e in previos}
        grupos = {d: [h for h in pendientes if h.chunk.document_id == d] for d in documentos if d not in cubiertas}
        sin_candidatos = [d for d, pool in grupos.items() if not pool]
        grupos = {d: pool for d, pool in grupos.items() if pool}
        k_efectivo = max(self.configuracion.retrieval_k, len(documentos))

        def costo(hits):
            contexto.chequear()
            return tokens_evidencia([*previos, *(items[h.chunk.chunk_id] for h in hits)], self.tokenizador)

        # Reservar primero una base económica permite representar B incluso si
        # el candidato preferido de A necesita una alternativa más pequeña.
        while grupos:
            opciones = [(costo([*elegidos, h]), h) for pool in grupos.values() for h in pool]
            tokens, hit = min(
                opciones,
                key=lambda par: (
                    par[0],
                    -par[1].similitud,
                    par[1].chunk.document_id,
                    par[1].chunk.indice,
                    par[1].chunk.chunk_id,
                ),
            )
            if tokens > presupuesto:
                omitidos.update(h.chunk.chunk_id for pool in grupos.values() for h in pool)
                break
            elegidos.append(hit)
            del grupos[hit.chunk.document_id]

        # Con la representación reservada, preferir los candidatos más relevantes
        # que caben sin desplazar ninguna otra fuente de la base.
        for posicion, actual in enumerate(elegidos):
            for hit in pendientes:
                if hit.chunk.document_id != actual.chunk.document_id:
                    continue
                propuesta = [*elegidos[:posicion], hit, *elegidos[posicion + 1 :]]
                if costo(propuesta) <= presupuesto:
                    elegidos[posicion] = hit
                    break
                omitidos.add(hit.chunk.chunk_id)

        fuentes_cubiertas = cubiertas | {h.chunk.document_id for h in elegidos}
        faltantes = set(documentos) - fuentes_cubiertas

        def sin_duplicados(hits):
            claves = {(h.chunk.document_id, identidad_texto(h.chunk.texto)) for h in elegidos}
            return [h for h in hits if (h.chunk.document_id, identidad_texto(h.chunk.texto)) not in claves]

        # Las alternativas con texto equivalente siguen disponibles hasta saber
        # cuál cabe con sus propios IDs y metadatos. Solo se sirve una por fuente.
        pendientes = sin_duplicados(pendientes)
        # Sin base completa no se gastan plazas ni tokens en complementos.
        while not faltantes and pendientes and len(elegidos) < k_efectivo:
            contexto.chequear()

            hit = max(
                pendientes,
                key=lambda h: (
                    lam * h.similitud - (1 - lam) * max((_coseno(h.vector, el.vector) for el in elegidos), default=0.0)
                ),
            )
            pendientes.remove(hit)
            if costo([*elegidos, hit]) > presupuesto:
                omitidos.add(hit.chunk.chunk_id)
                continue
            elegidos.append(hit)
            pendientes = sin_duplicados(pendientes)

        evidencia = [items[h.chunk.chunk_id] for h in elegidos]
        tokens = tokens_evidencia(evidencia, self.tokenizador)
        contexto.chequear()
        omitidos.difference_update(h.chunk.chunk_id for h in elegidos)
        avisos = []
        if omitidos:
            avisos.append("Se omitieron chunks completos por el presupuesto de evidencia.")
        if sin_candidatos:
            avisos.append("No se encontró evidencia pertinente para las fuentes: " + ", ".join(sin_candidatos) + ".")
        sin_presupuesto = sorted(faltantes - set(sin_candidatos))
        if sin_presupuesto:
            avisos.append("El presupuesto no permite representar las fuentes: " + ", ".join(sin_presupuesto) + ".")
        return ResultadoRecuperacion(
            evidencia=evidencia,
            tokens=tokens,
            chunks_omitidos=sorted(omitidos),
            avisos=avisos,
        )
