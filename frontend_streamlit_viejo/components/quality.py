"""Calidad, alcance y límites expresados sin promesas de verdad absoluta."""

import streamlit as st
from i18n import t

from components.content_common import lista, texto


def render_calidad(paquete: dict, idioma: str) -> None:
    evaluacion = paquete["evaluacion_calidad"]
    with st.expander(t("calidad.panel", idioma)):
        st.metric(t("calidad.anclaje", idioma), f"{evaluacion['anclaje_fuente_score']:.2f}")
        st.caption(t("calidad.explicacion_score", idioma))
        st.markdown(
            t("calidad.afirmaciones", idioma).format(
                respaldadas=evaluacion["cantidad_respaldadas"], total=evaluacion["cantidad_afirmaciones"]
            )
        )
        for campo in (
            "claridad_pedagogica",
            "adecuacion_perfil",
            "cobertura_objetivos",
            "coherencia_didactica",
            "verificacion_visual",
        ):
            st.markdown("**" + t("calidad." + campo, idioma) + ":** " + t("calidad.valor." + evaluacion[campo], idioma))
        st.markdown("**" + t("calidad.cobertura", idioma) + "**")
        secciones = paquete["metadatos"]["alcance"].get("secciones_cubiertas")
        if secciones:
            lista(secciones)
        else:
            st.caption(t("calidad.cobertura_no_detallada", idioma))
        st.markdown("**" + t("calidad.limitaciones", idioma) + "**")
        st.caption(t("calidad.limites", idioma))
        if evaluacion.get("observaciones"):
            st.markdown(texto(evaluacion["observaciones"]))
        st.caption(t("calidad.referencias", idioma).format(total=len({r["chunk_id"] for r in paquete["referencias"]})))
