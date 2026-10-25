"""Identidad interna del contexto de chat; no modifica cuerpos idempotentes."""

import hashlib
import json
from collections.abc import Iterable

from app.schemas.chat import ChatAnswer
from app.schemas.responses import DocumentoFuente


def fuentes_respuesta(respuesta: ChatAnswer) -> dict[str, DocumentoFuente]:
    """Un legado unidocumental acredita ID y hash, sin inventar otros campos."""
    ids = respuesta.document_ids
    if not ids or len(ids) != len(set(ids)) or respuesta.document_id not in ids or not respuesta.document_hash:
        raise ValueError("Identidad documental incoherente")
    fuentes = {f.document_id: f for f in respuesta.documentos_fuente}
    if not respuesta.documentos_fuente:
        if ids != [respuesta.document_id]:
            raise ValueError("Un resultado multidocumental requiere procedencia por fuente")
        return {}
    if (
        len(fuentes) != len(respuesta.documentos_fuente)
        or set(fuentes) != set(ids)
        or fuentes[respuesta.document_id].hash != respuesta.document_hash
    ):
        raise ValueError("El snapshot no corresponde a las fuentes declaradas")
    return fuentes


def scope_historial(fuentes: Iterable[DocumentoFuente]) -> str:
    identidad = sorted((f.document_id, f.hash, f.version) for f in fuentes)
    cuerpo = json.dumps(identidad, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(cuerpo.encode()).hexdigest()


def scope_historial_anterior(fuentes: Iterable[DocumentoFuente]) -> str:
    ordenadas = sorted(fuentes, key=lambda f: f.document_id)
    if len(ordenadas) == 1:
        return ordenadas[0].hash
    cuerpo = ";".join(f"{f.document_id}:{f.hash}" for f in ordenadas)
    return hashlib.sha256(cuerpo.encode()).hexdigest()
