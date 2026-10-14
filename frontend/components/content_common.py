"""Presentación compartida: texto escapado y claves estables por recurso."""

from __future__ import annotations

import hashlib
import html
import re
from collections.abc import Callable
from typing import Any

import streamlit as st

SourceRenderer = Callable[[list[dict[str, Any]], str], None]
_MARKDOWN = re.compile(r"([\\`*_{}\[\]()#+\-.!|>~<$])")


def clave(prefijo: str, *partes: str) -> str:
    identidad = repr((st.session_state.get("workspace_id"), prefijo, partes))
    return "nm_" + hashlib.sha256(identidad.encode()).hexdigest()[:24]


def texto(valor: str) -> str:
    """El contenido no crea HTML, enlaces ni imágenes remotas."""
    return _MARKDOWN.sub(r"\\\1", valor)


def badge(valor: str) -> None:
    st.markdown(f'<span class="nm-badge">{html.escape(valor)}</span>', unsafe_allow_html=True)


def lista(valores: list[str]) -> None:
    for valor in valores:
        st.markdown("- " + texto(valor))


def fuentes(elemento: dict[str, Any], render_sources: SourceRenderer | None, prefijo: str) -> None:
    if render_sources and elemento.get("referencias"):
        render_sources(elemento["referencias"], prefijo)
