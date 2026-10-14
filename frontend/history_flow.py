"""Historial paginado y resultados revalidados; sin generación ni caché de contenido."""

import hashlib
import json
from datetime import datetime, time, timezone
from typing import get_args

from api_client import FORMATOS, IDIOMAS_SALIDA, PERFILES, APIError, JobStatus

ESTADOS = set(get_args(JobStatus))


def fecha_utc(valor):
    fecha = datetime.fromisoformat(valor.replace("Z", "+00:00"))
    if fecha.tzinfo is None:
        raise ValueError("La fecha requiere zona horaria")
    return fecha.astimezone(timezone.utc)


def construir_filtros(documento=None, perfil=None, formato=None, idioma=None, desde=None, hasta=None):
    if desde and hasta and desde > hasta:
        raise ValueError("Intervalo inválido")
    filtros = {k: v for k, v in dict(documento=documento, perfil=perfil, formato=formato, idioma=idioma).items() if v}
    for k, dia, extremo in (("desde", desde, time.min), ("hasta", hasta, time.max)):
        if dia:
            filtros[k] = datetime.combine(dia, extremo, tzinfo=timezone.utc).isoformat()
    return filtros


def estado_historial(session, workspace_id):
    return session.setdefault("_historial_ui", {}).setdefault(
        workspace_id,
        {
            "filtros": {},
            "huella": None,
            "cursor": None,
            "anteriores": [],
            "opciones": {},
            "comparar": [None, None],
        },
    )


def cargar_pagina(api, token, estado, filtros, limite=20):
    huella = hashlib.sha256(json.dumps(filtros, sort_keys=True).encode()).hexdigest()[:16]
    if huella != estado["huella"]:
        estado.update(
            filtros=dict(filtros), huella=huella, cursor=None, anteriores=[], opciones={}, comparar=[None, None]
        )
    params = {**filtros, "limit": limite}
    if estado["cursor"]:
        params["cursor"] = estado["cursor"]
    pagina = api.get_generations(token, params)
    try:
        items, siguiente = pagina["items"], pagina["next_cursor"]
        if (
            not isinstance(items, list)
            or len(items) > limite
            or (siguiente is not None and (not isinstance(siguiente, str) or not siguiente))
        ):
            raise ValueError("Página inválida")
        ids = set()
        for item in items:
            if (
                not item["generation_id"]
                or item["generation_id"] in ids
                or not item["document_id"]
                or item["status"] not in ESTADOS
            ):
                raise ValueError("Fila inválida")
            ids.add(item["generation_id"])
            fecha_utc(item["created_at"])
            parametros = item.get("parametros")
            if parametros is not None and (
                not isinstance(parametros, dict)
                or parametros.get("document_id") != item["document_id"]
                or parametros.get("perfil_destinatario") not in PERFILES
                or parametros.get("formato_salida") not in FORMATOS
                or parametros.get("idioma_salida") not in IDIOMAS_SALIDA
            ):
                raise ValueError("Parámetros de historial inválidos")
        retenidas = {gid: fila for gid, fila in estado["opciones"].items() if gid in estado["comparar"]}
        actuales = {i["generation_id"]: i for i in items if i["status"] == "completed"}
        # Conservar el orden de la página: fijar A no debe reordenar las opciones
        # de B durante su selección (Streamlit transporta índices de opciones).
        actuales.update({gid: fila for gid, fila in retenidas.items() if gid not in actuales})
        estado["opciones"] = actuales
    except (KeyError, TypeError, ValueError, AttributeError):
        raise APIError("INVALID_RESPONSE", "Historial inválido", 502) from None
    return pagina


def cargar_detalle(api, token, generation_id):
    job = api.get_generation(token, generation_id)

    def exigir(condicion):
        if not condicion:
            raise ValueError("Detalle incoherente")

    try:
        exigir(job["generation_id"] == generation_id and job["status"] in ESTADOS)
        if job["status"] != "completed":
            exigir(job.get("contenido") is None)
            return job
        exigir(job["persistencia"]["status_upload"] == "completado")
        paquete = job["contenido"]
        exigir(paquete["generation_id"] == generation_id and paquete["status"] == "aprobado")
        fuente = paquete["documento_fuente"]
        exigir(all(isinstance(fuente[k], str) and fuente[k] for k in ("document_id", "hash", "version", "titulo")))
        meta, calidad = paquete["metadatos"], paquete["evaluacion_calidad"]
        exigir(
            meta["perfil_aplicado"] in PERFILES
            and meta["formato_generado"] in FORMATOS
            and meta["idioma_salida"] in IDIOMAS_SALIDA
        )
        exigir(paquete["contenido_adaptado"]["tipo"] == meta["formato_generado"])
        exigir(calidad["estado_evaluacion"] == "aprobada" and 0.85 <= calidad["anclaje_fuente_score"] <= 1)
        exigir(
            calidad["cantidad_afirmaciones"] > 0 and calidad["cantidad_respaldadas"] == calidad["cantidad_afirmaciones"]
        )
        exigir(calidad["cobertura_objetivos"] == "completa" and not calidad.get("razones_bloqueo"))
        exigir(calidad["verificacion_visual"] in {"aprobada", "no_aplica"})
        exigir(
            all(
                calidad[k] in {"alta", "media"}
                for k in ("claridad_pedagogica", "adecuacion_perfil", "coherencia_didactica")
            )
        )
        fecha_utc(paquete["created_at"])
        if meta["formato_generado"] == "quiz":
            exigir(
                all(
                    "correct_option_id" not in q and "justificacion" not in q
                    for q in paquete["contenido_adaptado"]["preguntas"]
                )
            )
    except (KeyError, TypeError, ValueError, AttributeError):
        raise APIError("INVALID_RESPONSE", "Detalle inválido", 502) from None
    return job


def comparar(api, token, izquierda, derecha):
    if izquierda == derecha:
        raise ValueError("Elegir dos adaptaciones diferentes")
    jobs = [cargar_detalle(api, token, gid) for gid in (izquierda, derecha)]
    if any(j["status"] != "completed" for j in jobs):
        raise ValueError("La comparación requiere aprobados persistidos")
    fuentes = [j["contenido"]["documento_fuente"] for j in jobs]
    if any(fuentes[0][k] != fuentes[1][k] for k in ("document_id", "hash", "version")):
        raise ValueError("La comparación requiere el mismo documento y versión")
    return [j["contenido"] for j in jobs]


def formatos_exportables(paquete):
    formatos = ["json", "md", "pdf"]
    if paquete["metadatos"]["formato_generado"] == "flashcards":
        formatos += ["csv", "tsv", "apkg"]
    return formatos
