"""Vista estudiante; la corrección y feedback son responsabilidad del #36."""

import streamlit as st
from i18n import t

from components.content_common import badge, clave, texto


def render_quiz(contenido: dict, idioma: str, *, generation_id: str, key_prefix: str = "quiz") -> None:
    st.caption(t("material.quiz_instrucciones", idioma))
    for numero, pregunta in enumerate(contenido["preguntas"], 1):
        identidad = clave(key_prefix, generation_id, pregunta["id"])
        opciones = {o["option_id"]: o["texto"] for o in pregunta["opciones"]}
        # Las selecciones sobreviven al cambio de idioma y nunca incluyen claves.
        seleccion = st.session_state.setdefault("quiz_selecciones", {}).get(identidad)
        st.session_state[identidad + ":opcion"] = seleccion if seleccion in opciones else None
        with st.container(border=True, key="nm_quiz_" + identidad):
            badge(t("quiz.pregunta", idioma).format(numero=numero, total=len(contenido["preguntas"])))
            st.markdown("### " + texto(pregunta["enunciado"]))
            st.radio(
                t("material.elegir_respuesta", idioma),
                list(opciones),
                index=None,
                format_func=lambda valor, opciones=opciones: texto(opciones[valor]),
                key=identidad + ":opcion",
                on_change=_guardar_seleccion,
                args=(identidad,),
            )


def _guardar_seleccion(identidad: str) -> None:
    st.session_state["quiz_selecciones"][identidad] = st.session_state[identidad + ":opcion"]
