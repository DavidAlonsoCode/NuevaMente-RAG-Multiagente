"""Carga documental: sólo el servidor publica ready y cobertura."""

import hashlib
import os
from uuid import uuid4

import streamlit as st
from api_client import APIError
from i18n import t

from components.errors import mostrar_error_ui

MAX_PDF_BYTES = 20 * 1024 * 1024
MAX_TEXT_BYTES = 5 * 1024 * 1024
FORMATOS_PERMITIDOS = (".pdf", ".md", ".txt")
DOCUMENTOS_DEMO = (
    {"id": "demo-redes-vcn", "clave": "upload.demo_vcn", "formato": "PDF"},
    {"id": "demo-pagos-fintech", "clave": "upload.demo_pagos", "formato": "Markdown"},
    {"id": "demo-salud-gobernanza", "clave": "upload.demo_salud", "formato": "Markdown"},
)


def validar_archivo(nombre_archivo, tamano_bytes):
    extension = os.path.splitext(nombre_archivo.lower())[1]
    if extension not in FORMATOS_PERMITIDOS:
        return False, "upload.formato_invalido"
    if tamano_bytes <= 0:
        return False, "upload.vacio"
    limite = MAX_PDF_BYTES if extension == ".pdf" else MAX_TEXT_BYTES
    return (False, "errors.DOCUMENT_TOO_LARGE") if tamano_bytes > limite else (True, None)


def validar_texto_pegado(titulo, contenido):
    if not titulo.strip() or len(titulo.strip()) > 300:
        return False, "upload.titulo_invalido"
    if not contenido.strip():
        return False, "upload.vacio"
    return (False, "errors.DOCUMENT_TOO_LARGE") if len(contenido.encode("utf-8")) > MAX_TEXT_BYTES else (True, None)


def formatear_tamano(tamano_bytes):
    if tamano_bytes < 1024:
        return f"{tamano_bytes} B"
    if tamano_bytes < 1024 * 1024:
        return f"{tamano_bytes / 1024:.1f} KiB"
    return f"{tamano_bytes / (1024 * 1024):.1f} MiB"


def inicializar_estado_documentos():
    st.session_state.setdefault("documentos", [])
    st.session_state.setdefault("documento_activo_id", None)
    st.session_state.setdefault("documento_activo_demo", False)
    st.session_state.setdefault("trabajos_documentales", {})


def clave_envio(flujo, titulo, contenido):
    """Misma intención tras un 503/transporte incierto; nunca reenviar automáticamente."""
    huella = hashlib.sha256(titulo.encode("utf-8") + b"\0" + contenido).hexdigest()
    nombre = "envio_documental_" + flujo
    previo = st.session_state.get(nombre)
    if previo is None or previo["huella"] != huella:
        previo = {"huella": huella, "clave": uuid4().hex}
        st.session_state[nombre] = previo
    return previo["clave"]


def admitir_documento(api, idioma, *, titulo, contenido, archivo=None, media_type=None):
    token = st.session_state.get("session_token")
    if not token:
        return False
    inicializar_estado_documentos()
    flujo = "archivo" if archivo else "texto"
    clave = clave_envio(flujo, (archivo or "") + "\0" + titulo, contenido)
    try:
        if archivo:
            recibo = api.upload_file(token, archivo, contenido, media_type, title=titulo, idempotency_key=clave)
        else:
            recibo = api.upload_document(
                token,
                {"documento_titulo": titulo, "documento_contenido": contenido.decode("utf-8")},
                idempotency_key=clave,
            )
    except APIError as error:
        mostrar_error_ui(error, idioma)
        return False
    st.session_state.trabajos_documentales[recibo["document_id"]] = {
        "job_id": recibo["job_id"],
        "cursor": 0,
        "progreso": None,
        "estado": "queued",
        "error": None,
    }
    st.session_state.pop("envio_documental_" + flujo, None)
    return True


def render_subida_archivo(idioma, api_client):
    st.markdown("### " + t("upload.titulo", idioma))
    st.caption(t("upload.formatos_soportados", idioma))
    st.caption(t("upload.limite", idioma))
    archivo = st.file_uploader(t("upload.arrastrar", idioma), type=["pdf", "md", "txt"], key="uploader_archivo")
    valido, error = validar_archivo(archivo.name, archivo.size) if archivo else (False, None)
    if error:
        st.error(t(error, idioma))  # Antes de leer bytes o enviar ninguna petición.
    permiso = st.checkbox(t("upload.confirmar_publico", idioma), key="check_confirmar_publico")
    if st.button(
        t("upload.procesar", idioma),
        type="primary",
        use_container_width=True,
        disabled=not (valido and permiso),
        key="btn_procesar_archivo",
    ):
        extension = os.path.splitext(archivo.name.lower())[1]
        mime = {".pdf": "application/pdf", ".md": "text/markdown", ".txt": "text/plain"}[extension]
        if admitir_documento(
            api_client,
            idioma,
            titulo=archivo.name[:300],
            contenido=archivo.getvalue(),
            archivo=archivo.name,
            media_type=mime,
        ):
            st.rerun()


def render_pegar_texto(idioma, api_client):
    st.subheader(t("upload.enviar_texto", idioma))
    st.caption(t("upload.limite", idioma))
    titulo = st.text_input(t("upload.texto_titulo", idioma), max_chars=300, key="input_texto_titulo")
    contenido = st.text_area(t("upload.texto_contenido", idioma), height=220, key="input_texto_contenido")
    permiso = st.checkbox(t("upload.confirmar_publico", idioma), key="permiso_texto")
    if st.button(t("upload.enviar_texto", idioma), type="primary", disabled=not permiso, key="btn_guardar_texto"):
        valido, error = validar_texto_pegado(titulo, contenido)
        if not valido:
            st.error(t(error, idioma))
        elif admitir_documento(api_client, idioma, titulo=titulo.strip(), contenido=contenido.encode("utf-8")):
            st.rerun()
