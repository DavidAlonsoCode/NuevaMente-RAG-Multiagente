"""Escenas de clase con duración, narración y puntos de diapositiva."""

import streamlit as st
from i18n import t

from components.content_common import SourceRenderer, badge, fuentes, lista, texto


def render_guion(
    contenido: dict, idioma: str, *, key_prefix: str, render_sources: SourceRenderer | None = None
) -> None:
    with st.expander(t("material.objetivos", idioma)):
        lista(contenido["objetivos"])
    for numero, escena in enumerate(contenido["escenas"], 1):
        with st.container(border=True, key=f"nm_scene_{key_prefix}_{numero}"):
            badge(t("material.escena", idioma).format(numero=numero, minutos=f"{escena['duracion_min']:g}"))
            st.markdown("*" + texto(escena["narracion"]) + "*")
            st.markdown("**" + t("material.diapositiva", idioma) + "**")
            lista(escena["puntos_diapositiva"])
            if escena.get("pregunta_interactiva"):
                st.info(t("material.pregunta_interactiva", idioma) + ": " + texto(escena["pregunta_interactiva"]))
            fuentes(escena, render_sources, f"{key_prefix}:escena:{escena['id']}")
