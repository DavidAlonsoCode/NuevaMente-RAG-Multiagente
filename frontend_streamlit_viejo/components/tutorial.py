"""Pasos numerados, código y verificación junto a su evidencia."""

import streamlit as st
from i18n import t

from components.content_common import SourceRenderer, badge, fuentes, lista, texto


def render_tutorial(
    contenido: dict, idioma: str, *, key_prefix: str, render_sources: SourceRenderer | None = None
) -> None:
    st.caption(t("material.audiencia", idioma) + ": " + texto(contenido["audiencia"]))
    if contenido["prerrequisitos"]:
        with st.expander(t("material.prerrequisitos", idioma)):
            lista(contenido["prerrequisitos"])
    for numero, paso in enumerate(contenido["pasos"], 1):
        with st.container(border=True, key=f"nm_step_{key_prefix}_{numero}"):
            badge(t("material.paso", idioma).format(numero=numero, total=len(contenido["pasos"])))
            st.markdown("### " + texto(paso["instruccion"]))
            if paso.get("codigo"):
                st.code(paso["codigo"], language=None, wrap_lines=True)
            st.markdown("**" + t("material.resultado_esperado", idioma) + "**")
            st.markdown(texto(paso["resultado_esperado"]))
            st.markdown("**" + t("material.verificacion", idioma) + "**")
            st.markdown(texto(paso["verificacion"]))
            fuentes(paso, render_sources, f"{key_prefix}:paso:{paso['id']}")
