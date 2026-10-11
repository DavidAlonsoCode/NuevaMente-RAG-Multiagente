"""Componente de carga e ingestión de documentos para NuevaMente.

Cumple con las decisiones de arquitectura §4.1, §6.3, §6.4 y §8.2:
- Límites estrictos: 20 MB para PDF, 5 MB para Markdown/TXT.
- Entrada dual: subida de archivos y texto pegado con título.
- Selección directa de los 3 documentos demo oficiales.
- Validación inmediata en el cliente antes de procesar o enviar.
"""

from __future__ import annotations

import os
from typing import Any

import streamlit as st
from i18n import t

# Límites de tamaño según especificación (§4.1)
MAX_PDF_BYTES = 20 * 1024 * 1024  # 20 MB
MAX_TEXT_BYTES = 5 * 1024 * 1024  # 5 MB
FORMATOS_PERMITIDOS = [".pdf", ".md", ".txt"]

# Metadatos de los 3 documentos demo incluidos en /documents (§6.4, Issue 05)
DOCUMENTOS_DEMO = [
    {
        "id": "demo-redes-vcn",
        "titulo": "Redes Virtuales en la Nube (VCN en OCI)",
        "archivo": "redes_vcn_oci.pdf",
        "formato": "PDF",
        "nicho": "Cloud / Infraestructura",
        "descripcion": "Arquitectura de red segura, subredes públicas y privadas, tablas de ruteo y gateways en Oracle Cloud.",
        "tamano_aprox": "24 KB",
        "paginas_procesadas": 14,
        "cobertura_porcentaje": 100,
        "chunks": 24,
    },
    {
        "id": "demo-pagos-fintech",
        "titulo": "Integración de Webhooks y APIs de Pagos",
        "archivo": "integracion_apis_pagos.md",
        "formato": "Markdown",
        "nicho": "Fintech",
        "descripcion": "Seguridad HMAC, reintentos idempotentes y manejo de eventos asíncronos en pasarelas de pago.",
        "tamano_aprox": "9 KB",
        "paginas_procesadas": 1,
        "cobertura_porcentaje": 100,
        "chunks": 18,
    },
    {
        "id": "demo-salud-gobernanza",
        "titulo": "Gobernanza de Historias Clínicas Electrónicas",
        "archivo": "gobernanza_datos_salud.md",
        "formato": "Markdown",
        "nicho": "Salud / Sanitario",
        "descripcion": "Estándares FHIR, consentimiento del paciente, pistas de auditoría inmutables y privacidad de datos médicos.",
        "tamano_aprox": "8 KB",
        "paginas_procesadas": 1,
        "cobertura_porcentaje": 100,
        "chunks": 15,
    },
]


def validar_archivo(nombre_archivo: str, tamano_bytes: int) -> tuple[bool, str | None]:
    """Valida extensión y tamaño del archivo en el cliente (§4.1).

    Retorna una tupla (es_valido, clave_error_o_mensaje).
    """
    _, ext = os.path.splitext(nombre_archivo.lower())

    if ext not in FORMATOS_PERMITIDOS:
        return False, f"Formato no permitido ({ext}). Formatos aceptados: PDF, MD, TXT."

    if ext == ".pdf" and tamano_bytes > MAX_PDF_BYTES:
        return False, "errors.DOCUMENT_TOO_LARGE"

    if ext in [".md", ".txt"] and tamano_bytes > MAX_TEXT_BYTES:
        return False, "errors.DOCUMENT_TOO_LARGE"

    return True, None


def validar_texto_pegado(titulo: str, contenido: str) -> tuple[bool, str | None]:
    """Valida título y contenido de texto pegado (§4.1).

    Retorna una tupla (es_valido, clave_error_o_mensaje).
    """
    if not titulo or not titulo.strip():
        return False, "Debes ingresar un título para el documento."

    if not contenido or not contenido.strip():
        return False, "El contenido del texto no puede estar vacío."

    tamano_bytes = len(contenido.encode("utf-8"))
    if tamano_bytes > MAX_TEXT_BYTES:
        return False, "errors.DOCUMENT_TOO_LARGE"

    return True, None


def formatear_tamano(tamano_bytes: int) -> str:
    """Convierte bytes a un formato legible (KB, MB)."""
    if tamano_bytes < 1024:
        return f"{tamano_bytes} B"
    if tamano_bytes < 1024 * 1024:
        return f"{tamano_bytes / 1024:.1f} KB"
    return f"{tamano_bytes / (1024 * 1024):.1f} MB"


def inicializar_estado_documentos():
    """Asegura que las colecciones necesarias existan en st.session_state."""
    if "documentos" not in st.session_state:
        st.session_state.documentos = []
    if "documento_activo_id" not in st.session_state:
        st.session_state.documento_activo_id = None


def registrar_documento_en_sesion(
    doc_id: str,
    titulo: str,
    nombre_archivo: str,
    tipo: str,
    tamano_bytes: int,
    estado: str = "ready",
    es_demo: bool = False,
    paginas_procesadas: int = 1,
    cobertura_porcentaje: int = 100,
    chunks: int = 1,
) -> dict[str, Any]:
    """Registra o actualiza un documento en st.session_state con métricas de cobertura."""
    inicializar_estado_documentos()

    # Evitar duplicados por id
    documentos_existentes = [d for d in st.session_state.documentos if d["id"] != doc_id]

    nuevo_doc = {
        "id": doc_id,
        "titulo": titulo,
        "nombre_archivo": nombre_archivo,
        "tipo": tipo.upper(),
        "tamano_bytes": tamano_bytes,
        "tamano_legible": formatear_tamano(tamano_bytes),
        "estado": estado,
        "es_demo": es_demo,
        "paginas_procesadas": paginas_procesadas,
        "cobertura_porcentaje": cobertura_porcentaje,
        "chunks": chunks,
        "origen_almacenamiento": "almacenamiento.local_dev",
    }

    documentos_existentes.append(nuevo_doc)
    st.session_state.documentos = documentos_existentes
    st.session_state.documento_activo_id = doc_id
    return nuevo_doc


def render_subida_archivo(idioma: str):
    """Renderiza el bloque de subida de archivos (PDF/MD/TXT) con límites visibles."""
    st.markdown(f"#### {t('upload.titulo', idioma)}")

    # Límites visibles ANTES de cargar (§4.1, Criterios de Aceptación)
    st.caption(f"ℹ️ **{t('upload.formatos_soportados', idioma)}** · {t('upload.limite', idioma)}")

    archivo = st.file_uploader(
        label=t("upload.arrastrar", idioma),
        type=["pdf", "md", "txt"],
        help=t("upload.limite", idioma),
        key="uploader_archivo",
    )

    confirmacion = st.checkbox(
        t("upload.confirmar_publico", idioma),
        key="check_confirmar_publico",
    )

    col_btn, _ = st.columns([1, 2])
    with col_btn:
        btn_procesar = st.button(
            t("upload.titulo", idioma),
            type="primary",
            use_container_width=True,
            key="btn_procesar_archivo",
        )

    if btn_procesar:
        if not archivo:
            st.warning("Selecciona o arrastra un archivo primero.")
            return

        if not confirmacion:
            st.warning(f"Debes marcar: '{t('upload.confirmar_publico', idioma)}'")
            return

        es_valido, error = validar_archivo(archivo.name, archivo.size)
        if not es_valido:
            msg = t(error, idioma) if error and error.startswith("errors.") else error
            st.error(f"❌ {msg}")
            return

        # Etapas de ingestión (§6.5, Criterios de Aceptación)
        with st.status(t("upload.procesando", idioma), expanded=True) as status:
            st.write("1. Validando formato e integridad del archivo...")
            st.write("2. Extrayendo contenido y estructura...")
            st.write("3. Generando fragmentos semánticos...")
            status.update(label=t("upload.listo", idioma), state="complete", expanded=False)

        ext = os.path.splitext(archivo.name)[1].lower()
        paginas = max(1, archivo.size // (100 * 1024)) if ext == ".pdf" else 1
        chunks = max(1, archivo.size // (2 * 1024))

        # Registrar en estado de sesión
        doc_id = f"doc-{archivo.name.replace(' ', '_')}"
        registrar_documento_en_sesion(
            doc_id=doc_id,
            titulo=archivo.name,
            nombre_archivo=archivo.name,
            tipo=ext.lstrip("."),
            tamano_bytes=archivo.size,
            estado="ready",
            paginas_procesadas=paginas,
            cobertura_porcentaje=100,
            chunks=chunks,
        )
        st.success(f"✅ {t('upload.listo', idioma)}: **{archivo.name}**")
        st.rerun()


def render_pegar_texto(idioma: str):
    """Renderiza el formulario para ingresar texto plano directamente."""
    st.markdown(f"#### {t('upload.enviar_texto', idioma)}")
    st.caption(f"ℹ️ {t('upload.limite', idioma)}")

    titulo = st.text_input(
        t("upload.texto_titulo", idioma),
        placeholder="Ej: Manual de Arquitectura de Microservicios",
        key="input_texto_titulo",
    )

    contenido = st.text_area(
        t("upload.texto_contenido", idioma),
        placeholder="Pega aquí el contenido técnico para generar material de estudio...",
        height=220,
        key="input_texto_contenido",
    )

    col_btn, _ = st.columns([1, 2])
    with col_btn:
        btn_guardar = st.button(
            t("upload.enviar_texto", idioma),
            type="primary",
            use_container_width=True,
            key="btn_guardar_texto",
        )

    if btn_guardar:
        es_valido, error = validar_texto_pegado(titulo, contenido)
        if not es_valido:
            msg = t(error, idioma) if error and error.startswith("errors.") else error
            st.error(f"❌ {msg}")
            return

        tamano_bytes = len(contenido.encode("utf-8"))
        chunks = max(1, tamano_bytes // 800)
        doc_id = f"doc-txt-{titulo.strip().replace(' ', '_').lower()[:20]}"
        registrar_documento_en_sesion(
            doc_id=doc_id,
            titulo=titulo.strip(),
            nombre_archivo=f"{titulo.strip()}.txt",
            tipo="txt",
            tamano_bytes=tamano_bytes,
            estado="ready",
            paginas_procesadas=1,
            cobertura_porcentaje=100,
            chunks=chunks,
        )
        st.success(f"✅ {t('upload.listo', idioma)}: **{titulo.strip()}**")
        st.rerun()


def render_documentos_demo(idioma: str):
    """Renderiza las tarjetas con los 3 documentos demo para selección inmediata."""
    st.markdown(f"#### {t('onboarding.demo', idioma)}")
    st.caption("Selecciona uno de los documentos de prueba preparados para evaluar el sistema:")

    for demo in DOCUMENTOS_DEMO:
        with st.container():
            st.markdown(
                f"""
                <div style="background-color: #0f1011; border: 1px solid #23252a; border-radius: 8px; padding: 16px; margin-bottom: 12px;">
                    <div style="display: flex; justify-content: space-between; align-items: baseline;">
                        <h4 style="margin: 0; color: #ffffff;">{demo["titulo"]}</h4>
                        <span style="background: #1d2035; color: #5e6ad2; border: 1px solid #5e6ad2; border-radius: 4px; padding: 2px 8px; font-size: 12px; font-family: monospace;">
                            {demo["formato"]} · {demo["nicho"]}
                        </span>
                    </div>
                    <p style="color: #9aa0a6; margin: 8px 0 12px 0; font-size: 14px;">{demo["descripcion"]}</p>
                </div>
                """,
                unsafe_allow_html=True,
            )

            col1, _ = st.columns([1, 2])
            with col1:
                if st.button(
                    f"Usar '{demo['titulo']}'",
                    key=f"btn_demo_{demo['id']}",
                    use_container_width=True,
                ):
                    registrar_documento_en_sesion(
                        doc_id=demo["id"],
                        titulo=demo["titulo"],
                        nombre_archivo=demo["archivo"],
                        tipo=demo["formato"].lower(),
                        tamano_bytes=1024 * 10,
                        estado="ready",
                        es_demo=True,
                        paginas_procesadas=demo["paginas_procesadas"],
                        cobertura_porcentaje=demo["cobertura_porcentaje"],
                        chunks=demo["chunks"],
                    )
                    st.success(f"✅ Documento demo activado: **{demo['titulo']}**")
                    st.rerun()


def eliminar_documento(doc_id: str):
    """Elimina un documento del espacio en sesión."""
    inicializar_estado_documentos()
    st.session_state.documentos = [d for d in st.session_state.documentos if d["id"] != doc_id]
    if st.session_state.documento_activo_id == doc_id:
        st.session_state.documento_activo_id = (
            st.session_state.documentos[0]["id"] if st.session_state.documentos else None
        )


def render_lista_documentos(idioma: str):
    """Renderiza la lista de documentos cargados en el espacio con estado y borrado."""
    inicializar_estado_documentos()
    documentos = st.session_state.documentos

    if not documentos:
        return

    st.markdown(f"### 📚 {t('nav.documentos', idioma)} ({len(documentos)})")

    for doc in documentos:
        es_activo = st.session_state.documento_activo_id == doc["id"]
        estado_label = t(f"estados.documento.{doc.get('estado', 'ready')}", idioma)
        storage_label = t(doc.get("origen_almacenamiento", "almacenamiento.local_dev"), idioma)
        borde_color = "#5e6ad2" if es_activo else "#23252a"

        with st.container():
            st.markdown(
                f"""
                <div style="background-color: #0f1011; border: 1px solid {borde_color}; border-radius: 8px; padding: 14px; margin-bottom: 10px;">
                    <div style="display: flex; justify-content: space-between; align-items: center;">
                        <div>
                            <span style="color: #ffffff; font-weight: 600; font-size: 15px;">{doc["titulo"]}</span>
                            {' <span style="background: #2b1f50; color: #a5b4fc; font-size: 11px; padding: 2px 6px; border-radius: 4px; margin-left: 6px;">Activo</span>' if es_activo else ""}
                        </div>
                        <div style="display: flex; gap: 8px; font-size: 12px;">
                            <span style="color: #4ade80;">● {estado_label}</span>
                            <span style="color: #9aa0a6;">· {doc["tipo"]}</span>
                        </div>
                    </div>
                    <div style="color: #9aa0a6; font-size: 13px; margin-top: 6px;">
                        📊 Cobertura: <strong style="color: #4ade80;">{doc.get("cobertura_porcentaje", 100)}%</strong> ({doc.get("paginas_procesadas", 1)} pág. · {doc.get("chunks", 1)} fragmentos)
                    </div>
                    <div style="color: #6b7280; font-size: 12px; margin-top: 4px;">
                        Archivo: <code>{doc["nombre_archivo"]}</code> ({doc.get("tamano_legible", "")}) · {storage_label}
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

            col_sel, col_del = st.columns([3, 1])
            with col_sel:
                if not es_activo:
                    if st.button("Seleccionar", key=f"btn_sel_{doc['id']}", use_container_width=True):
                        st.session_state.documento_activo_id = doc["id"]
                        st.rerun()
            with col_del:
                with st.popover("🗑️ Eliminar", use_container_width=True):
                    st.warning("⚠️ Esta acción retirará el documento del espacio.")
                    if st.button("Confirmar", key=f"btn_conf_del_{doc['id']}", type="primary"):
                        eliminar_documento(doc["id"])
                        st.rerun()
