"""Herramientas de estudio ligadas al documento y parámetros visibles."""

from itertools import count

import streamlit as st
from i18n import t

from components.chat import render_chat
from components.glossary import render_glosario
from components.progress import render_progreso
from components.sources import render_fuentes


def render_tutor(api, idioma, parametros):
    indices = count()

    def fuentes(document_id, referencias):
        render_fuentes(
            document_id,
            referencias,
            idioma,
            api_client=api,
            key_prefix=f"chat:{document_id}:{parametros['perfil_destinatario']}:{parametros['idioma_salida']}:{next(indices)}",
        )

    render_chat(
        api,
        idioma,
        token=st.session_state.get("session_token"),
        workspace_id=st.session_state.get("workspace_id"),
        document_id=parametros["document_id"],
        perfil_destinatario=parametros["perfil_destinatario"],
        idioma_salida=parametros["idioma_salida"],
        render_fuentes=fuentes,
    )


def render_glosario_contexto(api, idioma, parametros):
    render_glosario(
        api,
        idioma,
        document_id=parametros["document_id"],
        perfil=parametros["perfil_destinatario"],
        idioma_salida=parametros["idioma_salida"],
        render_sources=lambda refs, prefijo: render_fuentes(
            parametros["document_id"], refs, idioma, api_client=api, key_prefix="glosario:" + prefijo
        ),
    )


def render_herramientas_documento(api, idioma):
    document_id = st.session_state.get("documento_activo_id")
    if not document_id or st.session_state.get("documento_activo_demo"):
        with st.expander(t("nav.progreso", idioma)):
            render_progreso(api, idioma)
        return
    parametros = {
        "document_id": document_id,
        "perfil_destinatario": st.session_state.get("gen_perfil", "principiante"),
        "idioma_salida": st.session_state.get("gen_idioma_salida", "es"),
    }
    with st.expander(t("estudio.herramientas", idioma)):
        tutor, glosario, progreso = st.tabs(
            [t("chat.titulo", idioma), t("nav.glosario", idioma), t("nav.progreso", idioma)]
        )
        with tutor:
            render_tutor(api, idioma, parametros)
        with glosario:
            render_glosario_contexto(api, idioma, parametros)
        with progreso:
            render_progreso(api, idioma)
