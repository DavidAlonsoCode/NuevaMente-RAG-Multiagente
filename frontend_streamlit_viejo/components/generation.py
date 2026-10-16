"""Panel de parámetros y disparo de generación (issue #32, §6.3 y §16.1).

El progreso SSE pertenece al issue #33 y los renderizadores del material al #34.
"""

from __future__ import annotations

import uuid
from typing import Any, TypedDict, cast

import streamlit as st
from api_client import (
    FORMATOS,
    IDIOMAS_SALIDA,
    NICHOS,
    NIVELES_DETALLE,
    PERFILES,
    AlcanceSolicitud,
    APIClient,
    APIError,
    DetailLevel,
    DocumentoResponse,
    GenerateRequest,
    IndustryNiche,
    JobStatus,
    OutputLanguage,
    PedagogicalFormat,
    RecipientProfile,
    SeccionDocumental,
)
from i18n import t

from components.errors import mensaje_error, mostrar_error_ui

LIMITE_PAGINA = 100
MAX_PAGINAS = 5

CLAVE_PENDIENTE = "generacion_pendiente"
CLAVE_ULTIMO_INTENTO = "generacion_ultimo_intento"
CLAVE_ACTIVA = "generacion_activa"
CLAVE_CONTROLES = "generacion_controles"


class GeneracionPendiente(TypedDict):
    peticion: GenerateRequest
    idempotency_key: str
    documento_titulo: str
    seccion_titulo: str | None


class GeneracionActiva(TypedDict):
    generation_id: str
    status: JobStatus
    status_url: str
    events_url: str
    parametros: GenerateRequest
    documento_titulo: str
    seccion_titulo: str | None


def construir_peticion(
    document_id: str,
    perfil: RecipientProfile,
    formato: PedagogicalFormat,
    nicho: IndustryNiche,
    detalle: DetailLevel,
    idioma_salida: OutputLanguage,
    seccion_id: str | None = None,
) -> GenerateRequest:
    """Body de POST /api/generate; sin seccion_id se adapta el documento completo."""
    if not document_id:
        raise ValueError("La generación exige un documento")
    alcance: AlcanceSolicitud = {"tipo": "documento_completo"}
    if seccion_id is not None:
        if not seccion_id.strip():
            raise ValueError("El alcance por sección exige seccion_id")
        alcance = {"tipo": "seccion", "seccion_id": seccion_id}
    return {
        "document_id": document_id,
        "perfil_destinatario": perfil,
        "formato_salida": formato,
        "nicho_sector": nicho,
        "nivel_detalle": detalle,
        "idioma_salida": idioma_salida,
        "alcance": alcance,
    }


def documentos_ready(items: Any) -> list[DocumentoResponse]:
    """Solo documentos ready con identificador; la API es la fuente del estado."""
    if not isinstance(items, list):
        return []
    return [
        cast(DocumentoResponse, d)
        for d in items
        if isinstance(d, dict) and d.get("status") == "ready" and isinstance(d.get("document_id"), str)
    ]


def secciones_de(documento: DocumentoResponse | None) -> list[SeccionDocumental]:
    """Secciones reales procesadas; cobertura desconocida no inventa secciones."""
    cobertura = (documento or {}).get("cobertura")
    secciones = cobertura.get("secciones_procesadas") if isinstance(cobertura, dict) else None
    if not isinstance(secciones, list):
        return []
    return [s for s in secciones if isinstance(s, dict) and isinstance(s.get("seccion_id"), str) and s["seccion_id"]]


def etiqueta_seccion(seccion: SeccionDocumental, idioma: str) -> str:
    return t("sidebar.seccion_lineas", idioma).format(
        titulo=seccion.get("titulo") or seccion["seccion_id"],
        inicio=seccion.get("linea_inicio"),
        fin=seccion.get("linea_fin"),
    )


def _cargar_documentos(api_client: APIClient, token: str, idioma: str) -> list[DocumentoResponse]:
    """Consulta la API en cada ejecución. El seguimiento de processing lo hace el issue 25."""
    items: list[Any] = []
    cursor: str | None = None
    try:
        for _ in range(MAX_PAGINAS):
            params: dict[str, object] = {"limit": LIMITE_PAGINA}
            if cursor:
                params["cursor"] = cursor
            page = api_client.get_documents(token, params=params)
            items.extend(page.get("items") or [])
            cursor = page.get("next_cursor")
            if not isinstance(cursor, str) or not cursor:
                break
    except APIError as error:
        if error.status_code == 401:
            mostrar_error_ui(error, idioma)
        else:
            st.warning(mensaje_error(error, idioma))
        return []
    return documentos_ready(items)


def _guardar_control(clave: str) -> None:
    st.session_state[CLAVE_CONTROLES][clave] = st.session_state[clave]
    if clave == "gen_document_id":
        st.session_state.documento_activo_id = st.session_state[clave]
        st.session_state.documento_activo_demo = False


def _preparar_control(clave: str, opciones: list[str] | tuple[str, ...], defecto: str | None) -> None:
    # Los widgets cambian de identidad al traducir sus etiquetas. La selección
    # vive fuera de sus claves temporales y se restaura antes de registrarlos.
    controles = st.session_state.setdefault(CLAVE_CONTROLES, {})
    valor = controles.get(clave, defecto)
    if valor not in opciones:
        valor = defecto
    controles[clave] = valor
    st.session_state[clave] = valor


def render_panel_generacion(api_client: APIClient, idioma: str) -> None:
    """Controles de la sidebar; GENERAR solo se habilita con un documento ready."""
    st.markdown(f"### {t('sidebar.titulo', idioma)}")
    token = st.session_state.get("session_token")
    if not token:
        return

    documentos = _cargar_documentos(api_client, token, idioma)
    por_id = {d["document_id"]: d for d in documentos}
    ids = list(por_id)
    controles = st.session_state.setdefault(CLAVE_CONTROLES, {})
    activo = (st.session_state.get("documento_activo_id"), st.session_state.get("documento_activo_demo", False))
    if activo != st.session_state.get("gen_seleccion_anterior", (None, False)):
        controles["gen_document_id"] = activo[0] if not activo[1] and activo[0] in por_id else None
    _preparar_control("gen_document_id", ids, ids[0] if "gen_document_id" not in controles and ids else None)
    if activo[1]:
        st.warning(t("sidebar.demo_generacion_pendiente", idioma))
    if ids:
        document_id = st.selectbox(
            t("sidebar.documento", idioma),
            ids,
            format_func=lambda i: por_id[i].get("titulo") or i,
            key="gen_document_id",
            index=None,
            on_change=_guardar_control,
            args=("gen_document_id",),
        )
    else:
        document_id = None
        st.caption(t("sidebar.sin_documentos", idioma))

    if document_id is not None:
        st.session_state.documento_activo_id = document_id
        st.session_state.documento_activo_demo = False
    elif not activo[1]:
        st.session_state.documento_activo_id = None
    st.session_state.gen_seleccion_anterior = (
        st.session_state.get("documento_activo_id"),
        st.session_state.get("documento_activo_demo", False),
    )

    for clave, opciones, defecto in (
        ("gen_perfil", PERFILES, PERFILES[0]),
        ("gen_formato", FORMATOS, FORMATOS[0]),
        ("gen_nicho", NICHOS, "general"),
        ("gen_detalle", NIVELES_DETALLE, NIVELES_DETALLE[0]),
        ("gen_idioma_salida", IDIOMAS_SALIDA, "es"),
    ):
        _preparar_control(clave, opciones, defecto)

    perfil = st.selectbox(
        t("sidebar.perfil", idioma),
        PERFILES,
        format_func=lambda v: t(f"perfil.{v}", idioma),
        key="gen_perfil",
        on_change=_guardar_control,
        args=("gen_perfil",),
    )
    formato = st.selectbox(
        t("sidebar.formato", idioma),
        FORMATOS,
        format_func=lambda v: t(f"formato.{v}", idioma),
        key="gen_formato",
        on_change=_guardar_control,
        args=("gen_formato",),
    )
    nicho = st.selectbox(
        t("sidebar.nicho", idioma),
        NICHOS,
        format_func=lambda v: t(f"nicho.{v}", idioma),
        key="gen_nicho",
        on_change=_guardar_control,
        args=("gen_nicho",),
    )
    detalle = st.selectbox(
        t("sidebar.detalle", idioma),
        NIVELES_DETALLE,
        format_func=lambda v: t(f"detalle.{v}", idioma),
        key="gen_detalle",
        on_change=_guardar_control,
        args=("gen_detalle",),
    )
    idioma_salida = st.selectbox(
        t("sidebar.idioma_salida", idioma),
        IDIOMAS_SALIDA,
        format_func=lambda v: t(f"idioma.{v}", idioma),
        key="gen_idioma_salida",
        on_change=_guardar_control,
        args=("gen_idioma_salida",),
    )

    documento = por_id.get(document_id) if document_id else None
    secciones = secciones_de(documento)
    opciones_alcance = ("documento_completo", "seccion") if secciones else ("documento_completo",)
    _preparar_control("gen_alcance", opciones_alcance, "documento_completo")
    etiquetas_alcance = {
        "documento_completo": t("sidebar.alcance_completo", idioma),
        "seccion": t("sidebar.alcance_seccion", idioma),
    }
    alcance = st.radio(
        t("sidebar.alcance", idioma),
        opciones_alcance,
        format_func=etiquetas_alcance.get,
        key="gen_alcance",
        on_change=_guardar_control,
        args=("gen_alcance",),
    )
    if documento is not None and not secciones:
        st.caption(t("sidebar.sin_secciones", idioma))

    seccion_id: str | None = None
    seccion_titulo: str | None = None
    if alcance == "seccion":
        por_seccion = {s["seccion_id"]: s for s in secciones}
        _preparar_control("gen_seccion_id", list(por_seccion), next(iter(por_seccion)))
        seccion_id = st.selectbox(
            t("sidebar.seccion", idioma),
            list(por_seccion),
            format_func=lambda i: etiqueta_seccion(por_seccion[i], idioma),
            key="gen_seccion_id",
            on_change=_guardar_control,
            args=("gen_seccion_id",),
        )
        if seccion_id:
            seccion_titulo = por_seccion[seccion_id].get("titulo") or seccion_id

    peticion = (
        construir_peticion(document_id, perfil, formato, nicho, detalle, idioma_salida, seccion_id)
        if document_id and (alcance == "documento_completo" or seccion_id)
        else None
    )
    if st.button(t("sidebar.generar", idioma), type="primary", disabled=peticion is None, use_container_width=True):
        _preparar_confirmacion(peticion, (documento or {}).get("titulo") or document_id, seccion_titulo)
        st.rerun()


def _preparar_confirmacion(peticion: GenerateRequest, documento_titulo: str, seccion_titulo: str | None) -> None:
    """Un mismo cuerpo reutiliza la Idempotency-Key hasta obtener el 202."""
    ultimo = st.session_state.get(CLAVE_ULTIMO_INTENTO)
    clave = ultimo["idempotency_key"] if ultimo and ultimo["peticion"] == peticion else f"gen-{uuid.uuid4().hex}"
    pendiente: GeneracionPendiente = {
        "peticion": peticion,
        "idempotency_key": clave,
        "documento_titulo": documento_titulo,
        "seccion_titulo": seccion_titulo,
    }
    st.session_state[CLAVE_ULTIMO_INTENTO] = pendiente
    st.session_state[CLAVE_PENDIENTE] = pendiente


def confirmacion_pendiente() -> GeneracionPendiente | None:
    return st.session_state.get(CLAVE_PENDIENTE)


def cancelar_confirmacion() -> None:
    st.session_state[CLAVE_PENDIENTE] = None


def enviar_confirmacion(api_client: APIClient, idioma: str) -> bool:
    pendiente = confirmacion_pendiente()
    if pendiente is None:
        return False
    return _enviar(api_client, pendiente, idioma)


def _enviar(api_client: APIClient, pendiente: GeneracionPendiente, idioma: str) -> bool:
    try:
        respuesta = api_client.create_generation(
            st.session_state.session_token, pendiente["peticion"], idempotency_key=pendiente["idempotency_key"]
        )
    except APIError as e:
        mostrar_error_ui(e, idioma)
        return False
    activa: GeneracionActiva = {
        "generation_id": respuesta["generation_id"],
        "status": respuesta["status"],
        "status_url": respuesta["status_url"],
        "events_url": respuesta["events_url"],
        "parametros": pendiente["peticion"],
        "documento_titulo": pendiente["documento_titulo"],
        "seccion_titulo": pendiente["seccion_titulo"],
    }
    st.session_state[CLAVE_ACTIVA] = activa
    st.session_state.pop("ejemplo_demo_id", None)
    st.session_state.mostrando_documentos = False
    st.session_state[CLAVE_PENDIENTE] = None
    st.session_state[CLAVE_ULTIMO_INTENTO] = None
    return True
