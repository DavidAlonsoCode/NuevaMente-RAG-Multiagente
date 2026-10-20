"""Cliente de embeddings Gemini para el pipeline RAG (issue #13)"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Protocol

import httpx
from google import genai
from google.genai import types
from google.genai.errors import APIError, ClientError, ServerError

from app.config import Configuracion
from app.core.rag.tokenizer import TokenizadorBPE
from app.jobs.manager import ContextoEjecucion, CuotaAgotadaError, ReintentableError


# con este protocolo (interface formal de python) defino el contrato minimo que debe cumplir cualquier proveedor de vectores para desacoplar gemini de los tests
class EmbeddingProvider(Protocol):
    """Contrato minimo que necesita GeminiEmbeddings."""

    # aqui envio los textos al modelo de gemini configurando la cantidad de dimensiones y el tiempo limite de espera (timeout)
    def embed_sync(self, textos: list[str], *, timeout: float) -> list[list[float]]: ...


# con esta clase implemento el adaptador oficial que se comunica con el sdk de google genai para generar vectores de texto segun el issue 13
class GoogleGenAIEmbeddingProvider:
    """Adaptador minimo entre el pipeline RAG y el SDK google-genai."""

    def __init__(self, cliente: Any, *, modelo: str, dimensiones: int) -> None:
        self.cliente = cliente
        self.modelo = modelo
        self.dimensiones = dimensiones

    # con este metodo estatico analizo la estructura del error retornado por la api de google para saber si se agoto la cuota diaria (quota failure)
    @staticmethod
    def _es_cuota_diaria(error: ClientError) -> bool:
        if not isinstance(error.details, dict):
            return False

        cuerpo_error = error.details.get("error")
        if not isinstance(cuerpo_error, dict):
            return False

        detalles = cuerpo_error.get("details")
        if not isinstance(detalles, list):
            return False

        for detalle in detalles:
            if not isinstance(detalle, dict):
                continue

            if detalle.get("@type") != "type.googleapis.com/google.rpc.QuotaFailure":
                continue

            violaciones = detalle.get("violations")
            if not isinstance(violaciones, list):
                continue

            for violacion in violaciones:
                if not isinstance(violacion, dict):
                    continue

                quota_id = violacion.get("quotaId")
                if isinstance(quota_id, str) and "perday" in quota_id.lower():
                    return True

        return False

    # con esta funcion extraigo el tiempo de espera (retry after) desde los encabezados http para respetar los limites de peticiones
    @staticmethod
    def _obtener_retry_after(error: APIError) -> float | None:
        respuesta = error.response
        if respuesta is None:
            return None

        valor = respuesta.headers.get("Retry-After")
        if valor is None:
            return None

        try:
            segundos = float(valor)
            return max(0.0, segundos) if math.isfinite(segundos) else None
        except ValueError:
            try:
                fecha = parsedate_to_datetime(valor)
                if fecha.tzinfo is None:
                    fecha = fecha.replace(tzinfo=timezone.utc)

                return max(0.0, (fecha - datetime.now(timezone.utc)).total_seconds())
            except (ValueError, TypeError, OverflowError):
                return None

    def embed_sync(self, textos: list[str], *, timeout: float) -> list[list[float]]:
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout debe ser positivo y finito")

        try:
            respuesta = self.cliente.models.embed_content(
                model=self.modelo,
                contents=textos,
                config=types.EmbedContentConfig(
                    output_dimensionality=self.dimensiones,
                    http_options=types.HttpOptions(
                        timeout=max(1, int(timeout * 1000)), retry_options=types.HttpRetryOptions(attempts=1)
                    ),
                ),
            )

        except ServerError as error:
            raise ReintentableError(
                error.message or "Gemini temporalmente no disponible.",
                retry_after=self._obtener_retry_after(error),
            ) from error
        except httpx.TransportError as error:
            raise ReintentableError(str(error)) from error
        except ClientError as error:
            if error.code == 429 and self._es_cuota_diaria(error):
                raise CuotaAgotadaError(error.message or "Cuota diaria de Gemini agotada.") from error

            if error.code in (408, 429):
                raise ReintentableError(
                    error.message or "Gemini limito temporalmente las solicitudes.",
                    retry_after=self._obtener_retry_after(error),
                ) from error

            raise

        if not respuesta.embeddings:
            raise ValueError("Gemini no devolvio embeddings.")

        if len(respuesta.embeddings) != len(textos):
            raise ValueError(
                "Gemini devolvio una cantidad de embeddings distinta "
                f"a la cantidad de entradas: {len(respuesta.embeddings)} != {len(textos)}."
            )

        return [embedding.values for embedding in respuesta.embeddings]


# con esta clase adapto las llamadas de embeddings al pipeline de rag anadiendo los prefijos semanticos asimetricos exigidos por el modelo
class GeminiEmbeddings:
    """Adaptador de embeddings usado por el pipeline RAG"""

    def __init__(self, proveedor: EmbeddingProvider, *, modelo: str, dimensiones: int) -> None:
        self.proveedor = proveedor
        self.modelo = modelo
        self.dimensiones = dimensiones

    # aqui antepongo el prefijo requerido para documentos de modo que el modelo entienda que este texto sera almacenado e indexado
    @staticmethod
    def _preparar_documento(texto: str) -> str:
        return f"title: none | text: {texto}"

    # aqui antepongo el prefijo requerido para consultas de busqueda optimizando la comparacion asimetrica contra los documentos
    @staticmethod
    def _preparar_query(texto: str) -> str:
        return f"task: search result | query: {texto}"

    @property
    def collection_name(self) -> str:
        return f"embeddings-{self.modelo}-{self.dimensiones}"

    @property
    def preparation_version(self) -> str:
        return "asymmetric-retrieval-v1"

    # con este metodo transformo una lista de documentos en vectores numericos delegando en el contexto de ejecucion para controlar tiempo y presupuesto
    def embed_documents(self, textos: list[str], *, contexto: ContextoEjecucion) -> list[list[float]]:
        vectores: list[list[float]] = []

        for texto in textos:
            preparado = self._preparar_documento(texto)

            resultado = contexto.llamar(
                lambda timeout: self.proveedor.embed_sync([preparado], timeout=timeout),
                modelo=self.modelo,
                # Estimación local para TPM, incluidos los prefijos; no es facturación de Gemini.
                tokens_estimados=TokenizadorBPE().contar(preparado),
            )
            vectores.append(self._extraer_vector(resultado))

        return vectores

    # con este metodo transformo una consulta del usuario en un vector numerico individual para realizar la busqueda vectorial
    def embed_query(self, texto: str, *, contexto: ContextoEjecucion) -> list[float]:
        preparado = self._preparar_query(texto)
        resultado = contexto.llamar(
            lambda timeout: self.proveedor.embed_sync([preparado], timeout=timeout),
            modelo=self.modelo,
            tokens_estimados=TokenizadorBPE().contar(preparado),
        )

        return self._extraer_vector(resultado)

    # con esta funcion auxiliar valido que el vector devuelto por la api tenga la dimension esperada y contenga exclusivamente numeros reales finitos
    def _extraer_vector(self, resultado: list[list[float]]) -> list[float]:
        if len(resultado) != 1:
            raise ValueError(f"Gemini debe devolver exactamente un embedding por entrada; devolvio {len(resultado)}.")

        vector = resultado[0]

        if len(vector) != self.dimensiones:
            raise ValueError(
                f"Gemini devolvio un embedding de {len(vector)} dimensiones; se esperaban {self.dimensiones}."
            )

        if any(
            isinstance(valor, bool) or not isinstance(valor, (int, float)) or not math.isfinite(valor)
            for valor in vector
        ):
            raise ValueError("Gemini devolvio un embedding con valores no numericos o no finitos.")

        return vector


# con esta funcion de fabrica (factory function) instancio el cliente de embeddings configurado leyendo las credenciales y el modelo desde la configuracion
def crear_cliente_embeddings(
    *, configuracion: Configuracion, proveedor: EmbeddingProvider | None = None
) -> GeminiEmbeddings:
    """Construye el cliente RAG usando la configuracion oficial del backend."""
    if proveedor is None:
        if configuracion.mock_gemini:
            raise ValueError("MOCK_GEMINI=1 requiere inyectar explicitamente un proveedor de Gemini.")

        cliente_sdk = genai.Client(api_key=configuracion.google_api_key)

        proveedor = GoogleGenAIEmbeddingProvider(
            cliente=cliente_sdk,
            modelo=configuracion.gemini_embedding_model,
            dimensiones=configuracion.embedding_dimensions,
        )

    return GeminiEmbeddings(
        proveedor=proveedor,
        modelo=configuracion.gemini_embedding_model,
        dimensiones=configuracion.embedding_dimensions,
    )
