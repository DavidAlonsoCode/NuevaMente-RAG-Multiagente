"""Interacción del #36; el contenedor y el visor de fuentes pertenecen al #34."""

import hashlib

import streamlit as st
from api_client import APIError
from i18n import t
from quiz_flow import estado_quiz, validar_quiz

from components.errors import mostrar_error_ui


def render_quiz_interactivo(api, idioma_ui, *, token, workspace_id, generation_id, render_fuentes=None):
    """Leer siempre la vista estudiante autorizada; no descargar el canónico."""
    estados = st.session_state.setdefault("quizzes_interactivos", {})
    clave = (workspace_id, generation_id)
    try:
        document_id, preguntas = validar_quiz(api.get_generation(token, generation_id), generation_id)
    except APIError as error:
        if error.status_code in (401, 404):
            estados.pop(clave, None)
        mostrar_error_ui(error, idioma_ui)
        return
    estado = estado_quiz(estados, workspace_id, generation_id, document_id, preguntas)
    if estado.error:
        mostrar_error_ui(estado.error, idioma_ui)
    if estado.invalidado:
        return
    prefijo = "quiz_" + hashlib.sha256(repr(clave).encode()).hexdigest()[:16]
    resumen = estado.resumen()
    st.subheader(t("quiz.titulo", idioma_ui))
    st.caption(t("quiz.respondidas", idioma_ui).format(cantidad=resumen["respondidas"], total=len(preguntas)))
    st.progress(resumen["respondidas"] / len(preguntas))
    pregunta = preguntas[estado.indice]
    qid = pregunta["id"]
    historial = estado.intentos.get(qid, [])
    ultimo = historial[-1] if historial else None
    pendiente = ultimo is not None and ultimo.feedback is None
    bloqueada = ultimo is not None and qid not in estado.nuevos
    st.caption(t("quiz.pregunta", idioma_ui).format(numero=estado.indice + 1, total=len(preguntas)))
    st.write(pregunta["enunciado"])
    opciones = {o["option_id"]: o["texto"] for o in pregunta["opciones"]}
    seleccionado = ultimo.option_id if bloqueada else None
    modo = "nuevo" if qid in estado.nuevos else "actual"
    opcion = st.radio(
        t("quiz.elegir", idioma_ui),
        list(opciones),
        index=list(opciones).index(seleccionado) if seleccionado else None,
        format_func=lambda valor: opciones[valor],
        disabled=bloqueada,
        key=f"{prefijo}_{qid}_{len(historial)}_{modo}",
    )
    etiqueta = t("quiz.reenviar", idioma_ui) if pendiente else t("quiz.responder", idioma_ui)
    if st.button(etiqueta, key=f"{prefijo}_enviar", disabled=opcion is None or (bloqueada and not pendiente)):
        with st.spinner(t("quiz.enviando", idioma_ui)):
            estado.enviar(api, token, generation_id, pregunta, opcion)
        st.rerun()
    if ultimo and ultimo.feedback is not None:
        feedback = ultimo.feedback
        (st.success if feedback["correcta"] else st.error)(
            t("quiz.correcto" if feedback["correcta"] else "quiz.incorrecto", idioma_ui)
        )
        st.write(t("quiz.respuesta_correcta", idioma_ui).format(opcion=opciones[feedback["correct_option_id"]]))
        st.write(feedback["justificacion"])
        if render_fuentes is not None:
            render_fuentes(document_id, feedback["referencias"])
        else:
            with st.expander(t("quiz.referencias", idioma_ui)):
                for referencia in feedback["referencias"]:
                    st.text(
                        " · ".join(str(referencia[k]) for k in ("seccion", "pagina", "chunk_id") if referencia.get(k))
                    )
                st.caption(t("quiz.fuentes_pendientes", idioma_ui))
        if st.button(t("quiz.reintentar", idioma_ui), key=f"{prefijo}_nuevo"):
            estado.nuevo_intento(qid)
            st.rerun()
    anterior, siguiente = st.columns(2)
    if anterior.button(t("quiz.anterior", idioma_ui), key=f"{prefijo}_anterior", disabled=estado.indice == 0):
        estado.indice -= 1
        st.rerun()
    if siguiente.button(
        t("quiz.siguiente_pregunta", idioma_ui),
        key=f"{prefijo}_siguiente",
        disabled=estado.indice == len(preguntas) - 1,
    ):
        estado.indice += 1
        st.rerun()
    if resumen["respondidas"] == len(preguntas):
        st.subheader(t("quiz.resumen_sesion", idioma_ui))
        st.write(t("quiz.primeros_sesion", idioma_ui).format(cantidad=resumen["primeros"], total=len(preguntas)))
        st.write(t("quiz.posteriores_sesion", idioma_ui).format(cantidad=resumen["posteriores"]))
