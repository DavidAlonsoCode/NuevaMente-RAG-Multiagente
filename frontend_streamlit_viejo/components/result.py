"""Material aprobado y estados del trabajo; SSE se integra desde el #33."""

import uuid

import streamlit as st
from api_client import APIError
from i18n import t

from components.content_common import badge, clave, lista, texto
from components.errors import mostrar_error_ui
from components.flashcard import render_flashcards
from components.progress import registrar_cara_flashcard, render_marca_concepto
from components.quality import render_calidad
from components.quiz import render_quiz
from components.quiz_interactive import render_quiz_interactivo
from components.result_validation import validar_paquete
from components.script import render_guion
from components.sources import render_fuentes
from components.summary import render_resumen
from components.tutorial import render_tutorial


def render_paquete(
    paquete: dict,
    idioma: str,
    *,
    api_client=None,
    key_prefix: str = "resultado",
    trabajo=None,
    registrar_estudio: bool = True,
) -> None:
    """Componente reutilizable por historial/comparación, sin leer controles de la sidebar."""
    try:
        validar_paquete(paquete)
    except ValueError:
        mostrar_error_ui(APIError("INVALID_RESPONSE", "Material incompatible.", 502), idioma)
        return
    contenido, meta = paquete["contenido_adaptado"], paquete["metadatos"]
    document_id = paquete["documento_fuente"]["document_id"]
    identidad = clave(key_prefix, paquete["generation_id"])
    st.markdown("## " + texto(contenido["titulo"]))
    badge(t("material.revisado", idioma))
    st.caption(
        " · ".join(
            (
                t("formato." + contenido["tipo"], idioma),
                t("perfil." + meta["perfil_aplicado"], idioma),
                t("idioma." + meta["idioma_salida"], idioma),
            )
        )
    )
    st.caption(t("material.tiempo", idioma).format(minutos=meta["tiempo_estimado_estudio_minutos"]))
    if meta["nicho_sector"] != "general":
        badge(t("material.ejemplo_ilustrativo", idioma))
        st.caption(t("material.ejemplo_ayuda", idioma).format(nicho=t("nicho." + meta["nicho_sector"], idioma)))
    if contenido.get("introduccion_contextualizada"):
        st.markdown(texto(contenido["introduccion_contextualizada"]))
    with st.expander(t("material.preparar", idioma)):
        for campo in ("objetivos_aprendizaje", "conceptos_clave", "prerrequisitos"):
            st.markdown("**" + t("material." + campo, idioma) + "**")
            if meta[campo]:
                lista(meta[campo])
                if campo == "conceptos_clave" and api_client is not None and registrar_estudio:
                    for concepto in dict.fromkeys(meta[campo]):
                        render_marca_concepto(
                            api_client,
                            idioma,
                            document_id=document_id,
                            concepto=concepto,
                            generation_id=paquete["generation_id"],
                            key_prefix=identidad,
                        )
            else:
                st.caption(t("material.sin_prerrequisitos", idioma))

    def fuentes(refs, prefijo):
        render_fuentes(document_id, refs, idioma, api_client=api_client, key_prefix=prefijo)

    kwargs = {"key_prefix": identidad, "render_sources": fuentes}
    if contenido["tipo"] == "flashcards":

        def revisada(item_id):
            # El frente permanece visible; el clic acaba de revelar el dorso.
            for cara in ("frente", "dorso"):
                registrar_cara_flashcard(
                    api_client, idioma, generation_id=paquete["generation_id"], flashcard_id=item_id, cara=cara
                )

        render_flashcards(
            contenido,
            idioma,
            generation_id=paquete["generation_id"],
            on_review=revisada if api_client is not None and registrar_estudio else None,
            **kwargs,
        )
    elif contenido["tipo"] == "quiz":
        if api_client is not None:
            with st.container(border=True, key="nm_quiz_" + identidad):
                render_quiz_interactivo(
                    api_client,
                    idioma,
                    token=st.session_state.session_token,
                    workspace_id=st.session_state.workspace_id,
                    generation_id=paquete["generation_id"],
                    render_fuentes=lambda doc, refs: render_fuentes(
                        doc, refs, idioma, api_client=api_client, key_prefix=identidad + ":feedback"
                    ),
                    key_prefix=identidad,
                    recibo=trabajo,
                )
        else:
            render_quiz(contenido, idioma, generation_id=paquete["generation_id"], key_prefix=identidad)
    else:
        {"tutorial": render_tutorial, "resumen_ejecutivo": render_resumen, "guion_clase": render_guion}[
            contenido["tipo"]
        ](contenido, idioma, **kwargs)
    render_calidad(paquete, idioma)
    st.markdown("### " + t("fuentes.panel", idioma))
    st.caption(texto(paquete["documento_fuente"]["titulo"]))
    fuentes(paquete["referencias"], identidad + ":panel")


def render_generacion(api_client, generacion: dict, idioma: str) -> None:
    """Consulta el recurso propio antes de mostrar contenido o confirmar almacenamiento."""
    token = st.session_state.get("session_token")
    if not token:
        return
    gid = generacion["generation_id"]
    try:
        job = api_client.get_generation(token, gid)
        if job.get("generation_id") != gid or job.get("status") not in (
            "queued",
            "running",
            "completed",
            "rejected_quality",
            "failed",
            "cancelled",
        ):
            raise APIError("INVALID_RESPONSE", "Estado de generación inválido.", 502)
        estado = job["status"]
        if estado == "completed":
            paquete, persistencia = job.get("contenido"), job.get("persistencia")
            if (
                not isinstance(persistencia, dict)
                or persistencia.get("status_upload") != "completado"
                or persistencia.get("provider") not in ("oci", "mock")
            ):
                raise APIError("INVALID_RESPONSE", "Persistencia no confirmada.", 502)
            try:
                validar_paquete(paquete)
                if (
                    paquete["generation_id"] != gid
                    or paquete["documento_fuente"]["document_id"] != generacion["parametros"]["document_id"]
                ):
                    raise ValueError("Resultado de otro recurso")
                campos = {
                    "formato_generado": "formato_salida",
                    "perfil_aplicado": "perfil_destinatario",
                    "nicho_sector": "nicho_sector",
                    "nivel_detalle": "nivel_detalle",
                    "idioma_salida": "idioma_salida",
                }
                if any(
                    paquete["metadatos"][salida] != generacion["parametros"][entrada]
                    for salida, entrada in campos.items()
                ):
                    raise ValueError("Parámetros incompatibles")
                pedido = generacion["parametros"]["alcance"]
                alcance = paquete["metadatos"]["alcance"]
                if alcance["tipo"] != pedido["tipo"] or (
                    pedido["tipo"] == "seccion" and alcance.get("seccion_id") != pedido["seccion_id"]
                ):
                    raise ValueError("Alcance incompatible")
            except ValueError:
                raise APIError("INVALID_RESPONSE", "Resultado incompatible con la solicitud.", 502) from None
            rotulo = "material.guardado" if persistencia["provider"] == "oci" else "almacenamiento.local_dev"
            st.success(t(rotulo, idioma))
            render_paquete(paquete, idioma, api_client=api_client, trabajo=job)
        elif estado in ("queued", "running"):
            st.info(t("generar.enviada", idioma))
            st.info(t("estados.trabajo." + estado, idioma))
            if estado == "queued" and type(job.get("posicion_cola")) is int:
                st.caption(t("generar.posicion", idioma).format(posicion=job["posicion_cola"]))
            st.caption(t("material.en_proceso", idioma))
            if st.button(t("material.actualizar", idioma), key="resultado_actualizar"):
                st.rerun()
            if st.button(t("generar.cancelar", idioma), key="resultado_cancelar"):
                api_client.cancel_generation(token, gid)
                st.rerun()
        elif estado == "cancelled":
            st.info(t("material.cancelado", idioma))
        else:
            _diagnostico(job, idioma)
            if estado == "failed" and (job.get("error") or {}).get("code") == "STORAGE_UNAVAILABLE":
                st.caption(t("material.persist_ayuda", idioma))
                if st.button(t("material.persist_reintentar", idioma), key="resultado_persist"):
                    identidad = clave("persist", gid)
                    idempotency_key = st.session_state.setdefault(identidad, "persist-" + uuid.uuid4().hex)
                    api_client.persist_generation(token, gid, idempotency_key=idempotency_key)
                    st.rerun()
    except APIError as error:
        mostrar_error_ui(error, idioma)


def _diagnostico(job: dict, idioma: str) -> None:
    if job["status"] == "rejected_quality":
        st.error(t("generar.rechazado_calidad", idioma))
        st.caption(t("material.rechazo_ayuda", idioma))
        causa = (job.get("error") or {}).get("details", {}).get("causa")
        if causa == "EVIDENCIA_INSUFICIENTE":
            st.caption(t("material.rechazo_evidencia", idioma))
    else:
        error = job.get("error") or {}
        mostrar_error_ui(APIError(error.get("code", "INTERNAL"), "Trabajo fallido.", 500), idioma)
