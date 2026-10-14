"""Panel componible #40; #34 proporciona la selección y el visor de fuentes."""

import hashlib
import json

import streamlit as st
from api_client import APIError
from glossary_flow import (
    TERMINALES,
    admitir_glosario,
    cancelar_glosario,
    consultar_glosario,
    estado_glosario,
    filtrar_terminos,
)
from i18n import t

from components.content_common import texto
from components.errors import mostrar_error_ui


def render_glosario(api, idioma_ui, *, document_id, perfil, idioma_salida, render_fuentes=None, render_sources=None):
    token, ws = st.session_state.get("session_token"), st.session_state.get("workspace_id")
    if not token or not ws or not document_id:
        return
    estado = estado_glosario(st.session_state, ws, document_id, perfil, idioma_salida)
    scope = hashlib.sha256(json.dumps([ws, document_id, perfil, idioma_salida]).encode()).hexdigest()[:20]
    st.subheader(t("glosario.titulo", idioma_ui))
    st.caption(
        t("glosario.parametros", idioma_ui).format(
            perfil=t("perfil." + perfil, idioma_ui), idioma=t("idioma." + idioma_salida, idioma_ui)
        )
    )

    @st.fragment(run_every="5s" if estado["status"] and estado["status"] not in TERMINALES else None)
    def panel():
        try:
            anterior = estado["status"]
            salida = consultar_glosario(api, token, estado)
            if anterior not in TERMINALES and estado["status"] in TERMINALES:
                st.rerun()
        except APIError as error:
            if error.status_code == 404:
                estado["status"] = "unavailable"
            mostrar_error_ui(error, idioma_ui)
            return
        if estado["pending"]:
            st.warning(t("glosario.admision_indeterminada", idioma_ui))
        if not estado["job_id"] or estado["status"] in TERMINALES:
            label = "glosario.reintentar" if estado["pending"] else "glosario.generar"
            if st.button(t(label, idioma_ui), key=scope + ":generar"):
                try:
                    admitir_glosario(api, token, estado)
                    st.rerun()
                except APIError as error:
                    mostrar_error_ui(error, idioma_ui)
        if estado["status"] and estado["status"] not in TERMINALES:
            st.info(t("glosario.cancelando" if estado["cancel_requested"] else "glosario.espera", idioma_ui))
            st.caption(t("estados.trabajo." + estado["status"], idioma_ui))
            if st.button(
                t("glosario.cancelar", idioma_ui), key=scope + ":cancelar", disabled=estado["cancel_requested"]
            ):
                try:
                    cancelar_glosario(api, token, estado)
                    st.rerun()
                except APIError as error:
                    mostrar_error_ui(error, idioma_ui)
            if st.button(t("glosario.actualizar", idioma_ui), key=scope + ":actualizar"):
                st.rerun()
            return
        if estado["status"] in {"failed", "rejected_quality", "cancelled", "unavailable"}:
            st.warning(t("glosario." + estado["status"], idioma_ui))
            return
        if salida is None:
            st.info(t("glosario.vacio", idioma_ui))
            return
        if salida["candidatos_omitidos"]:
            st.warning(t("glosario.omitidos", idioma_ui).format(cantidad=salida["candidatos_omitidos"]))
        if salida["estado"] == "sin_terminos":
            st.info(t("glosario.sin_terminos", idioma_ui))
            return
        consulta = st.text_input(t("glosario.buscar", idioma_ui), key=scope + ":buscar")
        terminos = filtrar_terminos(salida["terminos"], consulta)
        st.caption(t("glosario.resultados", idioma_ui).format(cantidad=len(terminos), total=len(salida["terminos"])))
        if not terminos:
            st.info(t("glosario.sin_resultados", idioma_ui))
        for termino in terminos:
            with st.container(border=True):
                st.markdown(texto(termino["termino_original"]))
                if termino["estado"] == "definido":
                    if termino.get("traduccion"):
                        st.caption(t("glosario.traduccion", idioma_ui) + ": " + texto(termino["traduccion"]))
                    st.markdown(texto(termino["definicion"]))
                else:
                    st.warning(t("glosario.limitacion", idioma_ui))
                    st.markdown(texto(termino["limitacion"]))
                with st.expander(t("glosario.fuente", idioma_ui)):
                    if render_sources:
                        render_sources(termino["referencias"], scope + ":" + termino["id"])
                    elif render_fuentes:
                        render_fuentes(document_id, termino["referencias"])
                    else:
                        st.caption(t("glosario.visor_pendiente", idioma_ui))
                        for ref in termino["referencias"]:
                            st.code(ref["chunk_id"])
                            if ref.get("pagina") is not None:
                                st.caption(t("glosario.pagina", idioma_ui).format(numero=ref["pagina"]))
                            if ref.get("seccion"):
                                st.write(ref["seccion"])

    panel()
