"""Uso y vencimiento del espacio: mensajes localizados, sin detalles administrativos."""

import streamlit as st
from api_client import APIError
from i18n import t

from components.errors import mostrar_error_ui


def render_presupuesto(api, idioma):
    token = st.session_state.get("session_token")
    if not token:
        return
    try:
        datos = api.workspace_status(token)
    except APIError as error:
        mostrar_error_ui(error, idioma)
        return
    if not isinstance(datos, dict):
        return
    st.caption(t("presupuesto.vencimiento", idioma).format(fecha=datos["expira_en"]))
    uso = datos["uso"]
    st.caption(t("presupuesto.recursos", idioma).format(**uso))
    almacenamiento = datos["almacenamiento"]
    if almacenamiento.get("escrituras_bloqueadas"):
        st.error(t("presupuesto.bloqueado", idioma))
    elif almacenamiento.get("alerta"):
        st.warning(t("presupuesto.alerta", idioma))
