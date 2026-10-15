"""Tarjetas con revisión nativa y estado reutilizable por progreso de estudio."""

import html
from collections.abc import Callable

import streamlit as st
from i18n import t

from components.content_common import SourceRenderer, badge, clave, fuentes, texto


def render_flashcards(
    contenido: dict,
    idioma: str,
    *,
    generation_id: str,
    key_prefix: str = "flashcards",
    render_sources: SourceRenderer | None = None,
    on_review: Callable[[str], None] | None = None,
) -> None:
    for numero, item in enumerate(contenido["items"], 1):
        identidad = clave(key_prefix, generation_id, item["id"])
        visible = st.session_state.get(identidad + ":visible", False)
        with st.container(border=True, key="nm_flashcard_" + identidad):
            badge(t("material.tarjeta", idioma).format(numero=numero, total=len(contenido["items"])))
            st.markdown("### " + texto(item["frente"]))
            if item.get("pista_didactica"):
                with st.expander(t("material.pista", idioma)):
                    st.markdown(texto(item["pista_didactica"]))
            etiqueta = "material.ocultar_respuesta" if visible else "material.mostrar_respuesta"
            if st.button(t(etiqueta, idioma), key=identidad + ":toggle"):
                st.session_state[identidad + ":visible"] = not visible
                if not visible:
                    # Fuera de la clave del widget: persiste al cambiar el idioma.
                    revisadas = st.session_state.setdefault("flashcards_revisadas", {})
                    recurso = clave("revision", generation_id, item["id"])
                    revisadas[recurso] = {"generation_id": generation_id, "flashcard_id": item["id"]}
                    if on_review:
                        on_review(item["id"])
                st.rerun()
            if visible:
                st.markdown('<div class="nm-reveal">' + texto_html(item["dorso"]) + "</div>", unsafe_allow_html=True)
                if item.get("etiquetas"):
                    st.caption(texto(" · ".join(item["etiquetas"])))
                fuentes(item, render_sources, identidad)


def texto_html(valor: str) -> str:
    return html.escape(valor).replace("\n", "<br>")
