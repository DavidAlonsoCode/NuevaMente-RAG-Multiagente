from functools import partial
from typing import Any

import streamlit as st
from api_client import APIError
from components.documents import render_documentos_demo, render_lista_documentos
from components.errors import mostrar_error_ui
from components.history import render_historial_completo
from components.progress import render_progreso
from components.result import render_generacion, render_paquete
from components.result_header import render_cabecera_resultado
from components.session import limpiar_sesion_local
from components.sidebar import render_sidebar
from components.study import render_glosario_contexto, render_herramientas_documento, render_tutor
from components.upload import (
    inicializar_estado_documentos,
    render_pegar_texto,
)
from i18n import t


def render(api_client, idioma_actual: str):
    inicializar_estado_documentos()

    # Renderizar panel lateral con controles
    render_sidebar(api_client, idioma_actual)
    if aviso := st.session_state.pop("aviso_sesion", None):
        st.success(t(aviso, idioma_actual))

    # --- Banner del código de recuperación
    _render_banner_recuperacion(idioma_actual)
    if st.session_state.get("mostrando_recuperacion"):
        _render_pantalla_recuperacion(api_client, idioma_actual)
    elif st.session_state.get("historial_abierto"):
        render_historial_completo(
            api_client, idioma_actual, render_resultado=partial(render_paquete, api_client=api_client)
        )
    elif (generacion := st.session_state.get("generacion_activa")) and not st.session_state.get("mostrando_documentos"):
        render_cabecera_resultado(generacion, idioma_actual)
        material, tutor, glosario, progreso = st.tabs(
            [
                t("nav.resultado", idioma_actual),
                t("chat.titulo", idioma_actual),
                t("nav.glosario", idioma_actual),
                t("nav.progreso", idioma_actual),
            ]
        )
        with material:
            render_generacion(api_client, generacion, idioma_actual)
        with tutor:
            render_tutor(api_client, idioma_actual, generacion["parametros"])
        with glosario:
            render_glosario_contexto(api_client, idioma_actual, generacion["parametros"])
        with progreso:
            render_progreso(api_client, idioma_actual)
        if st.button(t("nav.documentos", idioma_actual), key="volver_documentos"):
            st.session_state.mostrando_documentos = True
            st.rerun()
    else:
        if st.session_state.get("generacion_activa") and st.button(t("generar.volver_resultado", idioma_actual)):
            st.session_state.mostrando_documentos = False
            st.rerun()
        _render_pantalla_onboarding(idioma_actual, api_client=api_client)


def _render_banner_recuperacion(idioma_actual: str):
    """Muestra el aviso amarillo del código de recuperación (solo emisión única)."""
    if not st.session_state.recovery_code or st.session_state.mostrando_recuperacion:
        return

    st.warning(
        f"⚠️ **{t('onboarding.codigo_titulo', idioma_actual)}**: {t('onboarding.codigo_advertencia', idioma_actual)}"
    )
    st.caption(t("onboarding.codigo_descripcion", idioma_actual))

    col_code, col_btn = st.columns([3, 1])
    with col_code:
        st.code(st.session_state.recovery_code)
    with col_btn:
        st.download_button(
            label=t("onboarding.descargar_nota", idioma_actual),
            data=(
                f"{t('onboarding.codigo_titulo', idioma_actual)}:\n"
                f"{st.session_state.recovery_code}\n"
                f"Workspace ID: {st.session_state.workspace_id}"
            ),
            file_name="nuevamente_codigo_recuperacion.txt",
            mime="text/plain",
        )
        if st.button(t("onboarding.codigo_guardado", idioma_actual)):
            st.session_state.recovery_code = None
            st.rerun()


def _render_pantalla_recuperacion(api_client, idioma_actual: str):
    """Renderiza el formulario para ingresar el código de recuperación."""
    if st.session_state.error_sesion_invalida:
        st.warning(f"⚠️ {t('errors.SESSION_INVALID', idioma_actual)}")

    st.subheader(t("onboarding.recuperar_titulo", idioma_actual))
    st.caption(t("onboarding.recuperar_ayuda", idioma_actual))

    with st.form("form_recuperacion", clear_on_submit=True):
        codigo_input = st.text_input(
            label=t("onboarding.recuperar_titulo", idioma_actual),
            placeholder="xxxx-xxxx-xxxx-xxxx-xxxx-xxxx-xxxx-xxxx",
            type="password",
        )
        btn_submit = st.form_submit_button(t("onboarding.recuperar_boton", idioma_actual))

        if btn_submit:
            try:
                res = api_client.recover_session(codigo_input.strip())
                limpiar_sesion_local()
                st.session_state.session_token = res["token"]
                st.session_state.workspace_id = res["workspace_id"]
                st.session_state.recovery_code = None
                st.session_state.mostrando_recuperacion = False
                st.session_state.error_sesion_invalida = False
                st.session_state.aviso_sesion = "onboarding.recuperado"
                st.rerun()
            except APIError as e:
                mostrar_error_ui(e, idioma_actual)

    if st.button(t("comun.atras", idioma_actual)):
        st.session_state.mostrando_recuperacion = False
        st.session_state.error_sesion_invalida = False
        st.rerun()


def _render_pantalla_onboarding(idioma_actual: str, api_client: Any = None):
    """Renderiza las 4 acciones principales de la pantalla de bienvenida y el flujo de documentos."""
    st.header(t("onboarding.bienvenida_titulo", idioma_actual))
    st.markdown(
        f"<p style='color: #9aa0a6; font-size: 16px; margin-top: -10px;'>"
        f"{t('onboarding.bienvenida_descripcion', idioma_actual)}</p>",
        unsafe_allow_html=True,
    )

    if "vista_onboarding" not in st.session_state:
        st.session_state.vista_onboarding = "upload"

    col1, col2, col3, col4 = st.columns(4)

    with col1:
        if st.button(t("upload.titulo", idioma_actual), use_container_width=True):
            st.session_state.vista_onboarding = "upload"
            st.rerun()
    with col2:
        if st.button(t("upload.enviar_texto", idioma_actual), use_container_width=True):
            st.session_state.vista_onboarding = "texto"
            st.rerun()
    with col3:
        if st.button(t("onboarding.demo", idioma_actual), use_container_width=True):
            st.session_state.vista_onboarding = "demo"
            st.rerun()
    with col4:
        if st.button(t("onboarding.recuperar_titulo", idioma_actual), use_container_width=True):
            st.session_state.mostrando_recuperacion = True
            st.rerun()

    st.divider()

    # Renderizar el panel según la opción seleccionada
    vista = st.session_state.get("vista_onboarding", "upload")
    if vista == "upload":
        st.info(t("upload.ayuda_sidebar", idioma_actual))
    elif vista == "texto":
        render_pegar_texto(idioma_actual, api_client=api_client)
    elif vista == "demo":
        render_documentos_demo(idioma_actual, api_client)

    st.divider()

    # Lista de documentos activos en el espacio
    render_lista_documentos(api_client, idioma_actual)
    render_herramientas_documento(api_client, idioma_actual)
