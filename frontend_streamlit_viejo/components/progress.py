"""Panel #41 y hooks para los renderizadores #34; sin contadores optimistas."""

import streamlit as st
from api_client import APIError
from i18n import t
from progress_flow import cara_flashcard, concepto_revisado, consultar_progreso, enviar_evento, pendientes

from components.errors import mostrar_error_ui


def _registrar(api, idioma_ui, estado):
    if estado["listo"] and not estado["intentado"]:
        try:
            enviar_evento(api, st.session_state.session_token, estado)
        except APIError as error:
            mostrar_error_ui(error, idioma_ui)
    return estado["confirmado"]


def registrar_concepto_revisado(api, idioma_ui, *, document_id, concepto, generation_id=None):
    """Llamar al abrir/ marcar un concepto concreto, nunca sólo por montar el panel."""
    if not st.session_state.get("workspace_id") or not st.session_state.get("session_token"):
        return False
    estado = concepto_revisado(st.session_state, st.session_state.workspace_id, document_id, concepto, generation_id)
    return _registrar(api, idioma_ui, estado)


def registrar_cara_flashcard(api, idioma_ui, *, generation_id, flashcard_id, cara):
    """El renderer llama sólo para el lado realmente mostrado: frente o dorso."""
    if not st.session_state.get("workspace_id") or not st.session_state.get("session_token"):
        return False
    estado = cara_flashcard(st.session_state, st.session_state.workspace_id, generation_id, flashcard_id, cara)
    return _registrar(api, idioma_ui, estado)


def render_marca_concepto(api, idioma_ui, *, document_id, concepto, generation_id=None, key_prefix="concepto"):
    if not st.session_state.get("workspace_id") or not st.session_state.get("session_token"):
        return
    # Preparar una intención no la envía: sólo el botón inicia el registro.
    estado = concepto_revisado(st.session_state, st.session_state.workspace_id, document_id, concepto, generation_id)
    if not estado["intentado"]:
        estado["listo"] = False
    if st.button(
        t("progreso.marcar_concepto", idioma_ui).format(concepto=concepto),
        key=key_prefix + ":" + estado["event_id"] + ":marcar",
        disabled=estado["confirmado"],
    ):
        estado["listo"] = True
        _registrar(api, idioma_ui, estado)
        st.rerun()


def render_progreso(api, idioma_ui):
    token, ws = st.session_state.get("session_token"), st.session_state.get("workspace_id")
    if not token or not ws:
        return
    st.subheader(t("progreso.titulo", idioma_ui))
    registros = pendientes(st.session_state, ws)
    if registros:
        st.warning(t("progreso.pendientes", idioma_ui).format(cantidad=len(registros)))
        # Reintento explícito y acotado; nunca un bucle automático de escrituras.
        evento = registros[0]
        if len(registros) > 1:
            por_id = {e["event_id"]: e for e in registros}

            def etiqueta(event_id):
                dato = por_id[event_id]["datos"]
                clave = "concepto" if dato["tipo"] == "concepto_revisado" else "flashcard_id"
                return dato[clave] + " · " + event_id[-8:]

            elegido = st.selectbox(
                t("progreso.elegir_registro", idioma_ui),
                list(por_id),
                format_func=etiqueta,
                key="progreso:pending:" + ws,
            )
            evento = por_id[elegido]
        if st.button(t("progreso.reintentar_evento", idioma_ui), key=evento["event_id"] + ":retry"):
            try:
                enviar_evento(api, token, evento)
                st.rerun()
            except APIError as error:
                mostrar_error_ui(error, idioma_ui)
    if st.button(t("progreso.actualizar", idioma_ui), key="progreso:refresh:" + ws):
        st.rerun()
    try:
        datos = consultar_progreso(api, token)
    except APIError as error:
        mostrar_error_ui(error, idioma_ui)
        st.info(t("progreso.no_disponible", idioma_ui))
        return
    izquierda, derecha = st.columns(2)
    with izquierda:
        st.metric(t("progreso.conceptos", idioma_ui), datos["conceptos_revisados"])
        st.metric(
            t("progreso.primer_intento", idioma_ui),
            str(datos["quiz_aciertos_primer_intento"]) + " / " + str(datos["quiz_total_primer_intento"]),
        )
        st.metric(t("progreso.intentos", idioma_ui), datos["quiz_intentos"])
    with derecha:
        st.metric(t("progreso.flashcards", idioma_ui), datos["flashcards_vistas"])
        st.metric(t("progreso.aciertos_posteriores", idioma_ui), datos["quiz_aciertos_posteriores"])
        st.metric(
            t("progreso.tiempo_restante", idioma_ui),
            t("progreso.minutos", idioma_ui).format(minutos=datos["tiempo_estimado_restante_minutos"]),
        )
    st.caption(t("progreso.orientativo", idioma_ui))
    st.caption(t("progreso.recuperacion", idioma_ui))
    st.caption(t("progreso.version", idioma_ui).format(version=datos["version"]))
