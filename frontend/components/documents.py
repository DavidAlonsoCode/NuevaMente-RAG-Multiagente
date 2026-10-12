"""Catálogo y seguimiento: metadata, cobertura y almacenamiento desde la API."""

import streamlit as st
from api_client import APIError
from i18n import CATALOGOS, t

from components.errors import mostrar_error_ui
from components.upload import DOCUMENTOS_DEMO, formatear_tamano, inicializar_estado_documentos


def obtener_catalogo(api, token, *, demo=False):
    funcion = api.get_demo_documents if demo else api.get_documents
    items, cursor, vistos = [], None, set()
    while True:
        pagina = funcion(token, {"limit": 100, **({"cursor": cursor} if cursor else {})})
        if not isinstance(pagina.get("items"), list):
            raise APIError("INVALID_RESPONSE", "Catálogo inválido", 502)
        items.extend(pagina["items"])
        cursor = pagina.get("next_cursor")
        if not cursor:
            return items
        if cursor in vistos or len(vistos) >= 20:
            raise APIError("INVALID_RESPONSE", "Cursor inválido", 502)
        vistos.add(cursor)


def sincronizar_documentos(api, token):
    inicializar_estado_documentos()
    documentos = obtener_catalogo(api, token)
    st.session_state.documentos = documentos
    ids = {d["document_id"] for d in documentos}
    trabajos = st.session_state.trabajos_documentales
    for cid in list(trabajos):
        if cid not in ids:
            del trabajos[cid]
            continue
        seguimiento = trabajos[cid]
        if seguimiento["estado"] not in ("queued", "running"):
            continue
        trabajo = api.get_job(token, seguimiento["job_id"])
        seguimiento.update(estado=trabajo["status"], error=trabajo.get("error"))
        evento = api.read_job_progress(token, seguimiento["job_id"], seguimiento["cursor"])
        if evento:
            seguimiento.update(cursor=evento["id"], progreso=evento["data"])
    if not st.session_state.documento_activo_demo:
        elegido = next((d for d in documentos if d["document_id"] == st.session_state.documento_activo_id), None)
        if elegido is None or elegido["status"] != "ready":
            st.session_state.documento_activo_id = None
    return documentos


def seleccionar_documento(api, token, cid, *, demo=False):
    documento = (api.get_demo_document if demo else api.get_document)(token, cid)
    if documento["status"] != "ready":
        st.session_state.documento_activo_id = None
        return False
    st.session_state.documento_activo_id = cid
    st.session_state.documento_activo_demo = demo
    return True


def eliminar_documento(api, token, cid):
    # No modificar catálogo/selección hasta recibir confirmación HTTP.
    api.delete_document(token, cid)
    st.session_state.documentos = [d for d in st.session_state.documentos if d["document_id"] != cid]
    st.session_state.trabajos_documentales.pop(cid, None)
    if st.session_state.documento_activo_id == cid and not st.session_state.documento_activo_demo:
        st.session_state.documento_activo_id = None


def presentar_documento(documento, idioma, *, provider=None):
    st.text(documento["titulo"])  # Nunca interpretar título remoto como HTML/Markdown.
    estado = documento["status"]
    st.caption(t("estados.documento." + estado, idioma))
    metadata = documento.get("metadata")
    if metadata:
        st.caption(metadata["extension"].upper() + " · " + formatear_tamano(metadata["tamano_bytes"]))
        if provider in ("mock", "oci"):
            st.caption(t("almacenamiento.local_dev" if provider == "mock" else "upload.guardado_oci", idioma))
    cobertura = documento.get("cobertura")
    if cobertura:
        if metadata and metadata["extension"] != "pdf":
            st.caption(
                t("upload.cobertura_texto", idioma).format(
                    secciones=len(cobertura.get("secciones_procesadas", [])),
                    tokens=metadata.get("cantidad_tokens") or "—",
                )
            )
        else:
            total = metadata.get("cantidad_paginas") if metadata else None
            st.caption(
                t("upload.cobertura", idioma).format(
                    procesadas=len(cobertura["paginas_procesadas"]), total=total if total is not None else "—"
                )
            )
        if pendientes := cobertura["paginas_visuales_pendientes"]:
            st.warning(t("upload.vision_pendiente", idioma).format(paginas=", ".join(map(str, pendientes))))
        if omitidas := cobertura["paginas_omitidas"]:
            st.warning(t("upload.omitidas", idioma).format(paginas=", ".join(map(str, omitidas))))
    elif estado == "processing":
        st.caption(t("upload.cobertura_pendiente", idioma))
    seguimiento = st.session_state.trabajos_documentales.get(documento["document_id"], {})
    if progreso := seguimiento.get("progreso"):
        clave = "upload.etapa_" + progreso["step"]
        if clave in CATALOGOS["es"]:
            st.caption(t("generar.etapa", idioma).format(etapa=t(clave, idioma)))
    if estado == "failed" or seguimiento.get("estado") in ("failed", "cancelled"):
        codigo = (seguimiento.get("error") or documento.get("error") or {}).get("code", "")
        clave = "errors." + codigo
        if codigo.startswith("PARSER_"):
            clave = "upload.parser_" + codigo.removeprefix("PARSER_").lower()
        elif codigo == "INGESTION_VISION_OMITIDA":
            clave = "upload.parser_extraccion_insuficiente"
        st.error(t(clave if clave in CATALOGOS["es"] else "upload.fallido", idioma))
        st.caption(t("upload.reintentar_fuente", idioma))


def hay_procesamiento():
    return any(j["estado"] in ("queued", "running") for j in st.session_state.trabajos_documentales.values()) or any(
        d["status"] == "processing" and not (d.get("cobertura") or {}).get("paginas_visuales_pendientes")
        for d in st.session_state.documentos
    )


def render_lista_documentos(api_client, idioma):
    inicializar_estado_documentos()
    token = st.session_state.get("session_token")
    if not token:
        return
    pendientes = hay_procesamiento()

    @st.fragment(run_every=2 if pendientes else None)
    def panel():
        try:
            documentos = sincronizar_documentos(api_client, token)
            provider = api_client.workspace_status(token).get("almacenamiento", {}).get("provider")
        except APIError as error:
            mostrar_error_ui(error, idioma)
            return
        st.subheader(t("nav.documentos", idioma))
        if not documentos:
            st.info(t("upload.sin_documentos", idioma))
        if st.session_state.get("aviso_documento_borrado"):
            st.success(t("upload.borrado_recibido", idioma))
        for documento in documentos:
            cid = documento["document_id"]
            with st.container(border=True):
                presentar_documento(documento, idioma, provider=provider)
                activo = st.session_state.documento_activo_id == cid and not st.session_state.documento_activo_demo
                if st.button(
                    t("upload.activo" if activo else "upload.seleccionar_documento", idioma),
                    disabled=documento["status"] != "ready" or activo,
                    key="seleccionar_" + cid,
                ):
                    try:
                        if seleccionar_documento(api_client, token, cid):
                            st.rerun()
                    except APIError as error:
                        mostrar_error_ui(error, idioma)
                with st.popover(t("upload.eliminar", idioma)):
                    st.warning(t("upload.borrar_confirmacion", idioma))
                    if st.button(t("comun.confirmar", idioma), key="borrar_" + cid):
                        try:
                            eliminar_documento(api_client, token, cid)
                        except APIError as error:
                            mostrar_error_ui(error, idioma)
                        else:
                            st.session_state.aviso_documento_borrado = True
                            st.rerun()
        if pendientes != hay_procesamiento():
            st.rerun()  # Una transición terminal apaga el temporizador.

    panel()


def render_documentos_demo(idioma, api_client):
    st.subheader(t("onboarding.demo", idioma))
    token = st.session_state.session_token
    try:
        documentos = obtener_catalogo(api_client, token, demo=True)
    except APIError as error:
        mostrar_error_ui(error, idioma)
        return
    st.caption(t("upload.demo_ayuda", idioma))
    por_id = {d["document_id"]: d for d in documentos}
    if st.session_state.documento_activo_demo:
        elegido = por_id.get(st.session_state.documento_activo_id)
        if elegido is None or elegido["status"] != "ready":
            st.session_state.documento_activo_id = None
            st.session_state.documento_activo_demo = False
    for demo in DOCUMENTOS_DEMO:
        documento = por_id.get(demo["id"])
        with st.container(border=True):
            if documento:
                presentar_documento(documento, idioma)
            else:
                st.text(t(demo["clave"], idioma))
                st.caption(demo["formato"] + " · " + t("upload.demo_no_disponible", idioma))
            activo = st.session_state.documento_activo_demo and st.session_state.documento_activo_id == demo["id"]
            if st.button(
                t("upload.activo" if activo else "upload.seleccionar_documento", idioma),
                key="demo_" + demo["id"],
                disabled=activo or documento is None or documento["status"] != "ready",
            ):
                try:
                    if seleccionar_documento(api_client, token, demo["id"], demo=True):
                        st.rerun()
                except APIError as error:
                    mostrar_error_ui(error, idioma)
    if st.button(t("comun.reintentar", idioma), key="actualizar_demos"):
        st.rerun()
