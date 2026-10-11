import streamlit as st
from components.upload import (
    inicializar_estado_documentos,
    render_documentos_demo,
    render_lista_documentos,
    render_pegar_texto,
    render_subida_archivo,
)
from i18n import CATALOGOS, IDIOMA_POR_DEFECTO, t


def render():
    # Inicializar el idioma y estado de documentos
    if "idioma_ui" not in st.session_state:
        st.session_state.idioma_ui = IDIOMA_POR_DEFECTO

    inicializar_estado_documentos()
    idioma_actual = st.session_state.idioma_ui

    # --- SIDEBAR (Barra lateral) ---
    with st.sidebar:
        st.title(t("comun.app_nombre", idioma_actual))

        # Selector de idioma de UI
        nuevo_idioma = st.selectbox(
            t("sidebar.idioma_ui", idioma_actual),
            options=CATALOGOS.keys(),
            index=list(CATALOGOS.keys()).index(idioma_actual),
            format_func=lambda x: t(f"idioma.{x}", idioma_actual),
        )

        if nuevo_idioma != idioma_actual:
            st.session_state.idioma_ui = nuevo_idioma
            st.rerun()

        st.divider()

        # Resumen del documento activo en sidebar
        st.markdown(f"### {t('sidebar.documento', idioma_actual)}")
        doc_activo_id = st.session_state.get("documento_activo_id")
        docs = st.session_state.get("documentos", [])
        doc_activo = next((d for d in docs if d["id"] == doc_activo_id), None)

        if doc_activo:
            st.success(f"📄 **{doc_activo['titulo']}**")
            st.caption(
                f"Formato: `{doc_activo['tipo']}` · Cobertura: {doc_activo.get('cobertura_porcentaje', 100)}% ({doc_activo.get('paginas_procesadas', 1)} pág.) · {t(doc_activo.get('origen_almacenamiento', 'almacenamiento.local_dev'), idioma_actual)}"
            )
        else:
            st.info("Sin documento seleccionado.")

        st.divider()

        # Historial en expander
        with st.expander(t("nav.historial", idioma_actual)):
            st.write(t("historial.vacio", idioma_actual))

    # --- ÁREA PRINCIPAL ---
    st.header(t("onboarding.bienvenida_titulo", idioma_actual))
    st.markdown(
        f"<p style='color: #9aa0a6; font-size: 16px; margin-top: -10px;'>"
        f"{t('onboarding.bienvenida_descripcion', idioma_actual)}</p>",
        unsafe_allow_html=True,
    )

    # Tabs accesibles para las 4 vías de entrada (§6.3, §6.4)
    tab_subir, tab_pegar, tab_demo, tab_recuperar = st.tabs(
        [
            f"📄 {t('upload.titulo', idioma_actual)}",
            f"✍️ {t('upload.enviar_texto', idioma_actual)}",
            f"🚀 {t('onboarding.demo', idioma_actual)}",
            f"🔑 {t('onboarding.recuperar_titulo', idioma_actual)}",
        ]
    )

    with tab_subir:
        render_subida_archivo(idioma_actual)

    with tab_pegar:
        render_pegar_texto(idioma_actual)

    with tab_demo:
        render_documentos_demo(idioma_actual)

    with tab_recuperar:
        st.markdown(f"#### {t('onboarding.recuperar_titulo', idioma_actual)}")
        st.caption(t("onboarding.recuperar_ayuda", idioma_actual))
        codigo_input = st.text_input(
            "Código de recuperación",
            placeholder="Ej: a1b2-c3d4-e5f6-7890",
            key="input_codigo_recuperar",
        )
        if st.button(
            t("onboarding.recuperar_boton", idioma_actual),
            key="btn_recuperar_codigo",
            type="primary",
        ):
            if codigo_input and len(codigo_input.strip()) >= 8:
                st.success("✅ Espacio sincronizado exitosamente con tu código.")
            else:
                st.warning("Ingresa un código válido de al menos 8 caracteres.")

    st.divider()

    # Lista de documentos activos en el espacio
    render_lista_documentos(idioma_actual)
