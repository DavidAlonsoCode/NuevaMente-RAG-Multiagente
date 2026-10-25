"""Reserva idempotente de trabajos comunes, con el orden gestor → SQLite."""

import json
import uuid

from app.schemas.errors import ErrorAplicacion, ErrorCode


def consultar_intencion(db, workspace_id, tipo, clave, cuerpos):
    """Compara formas conocidas exactas, sin reservar ni modificar la intención.

    El llamador mantiene la admisión serializada y repite esta consulta dentro
    de la transacción que admite un trabajo nuevo.
    """
    if not cuerpos:
        raise ValueError("La comparación idempotente requiere un cuerpo")
    for cuerpo in cuerpos:
        previo = db.idempotencia_consultar(workspace_id, tipo, clave, cuerpo)
        if previo is None or previo[0] == "duplicada":
            return previo
    return previo


def admitir_trabajo(
    gestor,
    workspace_id,
    document_id,
    tipo,
    cuerpo,
    ejecutar,
    *,
    clave=None,
    cuerpos_alternativos=(),
    cache_key=None,
    document_ids=None,
    scope_hash=None,
    preparar=lambda: None,
):
    if clave is not None and (not clave.strip() or not 1 <= len(clave) <= 128):
        raise ErrorAplicacion(ErrorCode.INVALID_REQUEST, "Idempotency-Key debe tener entre 1 y 128 caracteres.")
    db = gestor.store
    cuerpos = tuple(json.dumps(datos, sort_keys=True, ensure_ascii=False) for datos in (cuerpo, *cuerpos_alternativos))
    with gestor.admision_serializada():
        cache = db.job_cache_glosario(workspace_id, cache_key) if cache_key else None
        job_id = cache["job_id"] if cache else "job_" + uuid.uuid4().hex[:16]
        if clave is not None:
            with db.transaccion():
                estado, previo = consultar_intencion(db, workspace_id, tipo, clave, cuerpos) or db.idempotencia_iniciar(
                    workspace_id, tipo, clave, cuerpos[0], job_id
                )
                if estado == "conflicto":
                    raise ErrorAplicacion(
                        ErrorCode.IDEMPOTENCY_CONFLICT, "Clave idempotente reutilizada con otro cuerpo."
                    )
                if estado == "duplicada":
                    job_id = previo
            existente = db.obtener_job(job_id)
            if existente is not None:
                return existente
        elif cache is not None:
            return cache
        try:
            preparar()
            gestor.enqueue(
                workspace_id,
                tipo,
                ejecutar,
                job_id=job_id,
                document_id=document_id,
                cache_key=cache_key,
                document_ids=document_ids,
                scope_hash=scope_hash,
            )
        except Exception:
            if clave is not None:
                db.abandonar_intencion_job(workspace_id, tipo, clave, job_id)
            raise
        return db.obtener_job(job_id)
