"""Resumen ejecutivo con impacto y decisiones en dos columnas adaptables."""

import streamlit as st
from i18n import t

from components.content_common import SourceRenderer, badge, fuentes, lista, texto


def render_resumen(
    contenido: dict, idioma: str, *, key_prefix: str, render_sources: SourceRenderer | None = None
) -> None:
    with st.container(key="nm_summary_" + key_prefix):
        impacto, implicaciones = st.columns(2, gap="medium")
        with impacto, st.container(border=True):
            badge(t("material.impacto", idioma))
            st.markdown(texto(contenido["impacto_cualitativo"]))
            st.markdown("### " + t("material.puntos_clave", idioma))
            lista(contenido["puntos_clave"])
        with implicaciones, st.container(border=True):
            badge(t("material.implicaciones", idioma))
            lista(contenido["implicaciones"])
            st.markdown("### " + t("material.acciones", idioma))
            lista(contenido["acciones"])
        fuentes(contenido, render_sources, key_prefix + ":resumen")
