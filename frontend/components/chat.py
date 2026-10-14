"""Tutor del #38; espera con el gestor común y fuentes inyectadas desde #34."""

import hashlib

import streamlit as st
from api_client import APIError
from chat_flow import conversacion_actual, pendiente_del_espacio
from i18n import t

from components.content_common import texto
from components.errors import mostrar_error_ui


def _fuentes(respuesta, idioma_ui, render_fuentes):
    if render_fuentes is not None:
        render_fuentes(respuesta["document_id"], respuesta["referencias"])
    else:
        with st.expander(t("chat.ver_cita", idioma_ui)):
            for referencia in respuesta["referencias"]:
                st.text(" · ".join(str(referencia[k]) for k in ("seccion", "pagina", "chunk_id") if referencia.get(k)))
            st.caption(t("chat.fuentes_pendientes", idioma_ui))


@st.fragment(run_every="5s")
def _espera(api, token, turno, idioma_ui, prefijo):
    antes = turno.status
    turno.consultar(api, token)
    if turno.error:
        mostrar_error_ui(turno.error, idioma_ui)
    if not turno.activo:
        if antes != turno.status:
            st.rerun()
        return
    if turno.job_id is None:
        st.info(t("chat.envio_indeterminado", idioma_ui))
        if st.button(t("chat.reenviar", idioma_ui), key=prefijo + "_reenviar"):
            turno.enviar(api, token)
            st.rerun()
    else:
        st.info(t("chat.cancelando" if turno.cancelacion_solicitada else "chat.pensando", idioma_ui))
        if st.button(t("chat.cancelar", idioma_ui), key=prefijo + "_cancelar", disabled=turno.cancelacion_solicitada):
            turno.cancelar(api, token)
            st.rerun()
        if st.button(t("chat.actualizar", idioma_ui), key=prefijo + "_actualizar"):
            turno.consultar(api, token)
            st.rerun()


def render_chat(
    api, idioma_ui, *, token, workspace_id, document_id, perfil_destinatario, idioma_salida, render_fuentes=None
):
    """El contenedor aporta la selección activa; una demo nunca sustituye un documento privado."""
    conversaciones = st.session_state.setdefault("chat_conversaciones", {})
    try:
        documento = api.get_document(token, document_id)
        if not isinstance(documento, dict) or documento.get("document_id") != document_id:
            raise APIError("INVALID_RESPONSE", "Documento inválido.", 502)
        if documento.get("status") != "ready":
            st.info(t("chat.documento_no_listo", idioma_ui))
            return
    except APIError as error:
        if error.status_code in (401, 404):
            for clave in list(conversaciones):
                if clave[:2] == (workspace_id, document_id):
                    del conversaciones[clave]
        mostrar_error_ui(error, idioma_ui)
        return
    conversacion = conversacion_actual(conversaciones, workspace_id, document_id, perfil_destinatario, idioma_salida)
    clave = (workspace_id, document_id, perfil_destinatario, idioma_salida)
    prefijo = "chat_" + hashlib.sha256(repr(clave).encode()).hexdigest()[:16]
    st.subheader(t("chat.titulo", idioma_ui))
    st.caption(
        t("chat.parametros", idioma_ui).format(
            perfil=t("perfil." + perfil_destinatario, idioma_ui), idioma=t("idioma." + idioma_salida, idioma_ui)
        )
    )
    for turno in conversacion.turnos:
        with st.chat_message("user"):
            st.markdown(texto(turno.pregunta))
        if turno.respuesta is not None:
            with st.chat_message("assistant"):
                respuesta = turno.respuesta
                if respuesta["estado"] == "fuera_de_alcance":
                    st.info(texto(respuesta["respuesta"]) or t("chat.abstencion", idioma_ui))
                else:
                    st.markdown(texto(respuesta["respuesta"]))
                    _fuentes(respuesta, idioma_ui, render_fuentes)
        elif not turno.activo:
            with st.chat_message("assistant"):
                st.caption(t("chat.cancelada" if turno.status == "cancelled" else "chat.sin_respuesta", idioma_ui))
    pendiente = pendiente_del_espacio(conversaciones, workspace_id)
    if pendiente:
        if pendiente is not conversacion.pendiente:
            st.info(t("chat.otro_pendiente", idioma_ui))
        _espera(api, token, pendiente, idioma_ui, prefijo)
    if st.button(
        t("chat.limpiar_vista", idioma_ui),
        key=prefijo + "_limpiar",
        help=t("chat.limpiar_ayuda", idioma_ui),
        disabled=pendiente is not None or not conversacion.turnos,
    ):
        conversacion.limpiar_vista()
        st.rerun()
    pregunta = st.chat_input(
        t("chat.placeholder", idioma_ui), max_chars=2000, disabled=pendiente is not None, key=prefijo + "_pregunta"
    )
    if pregunta and pregunta.strip():
        turno = conversacion.iniciar(pregunta)
        with st.spinner(t("chat.enviando", idioma_ui)):
            turno.enviar(api, token)
        st.rerun()
