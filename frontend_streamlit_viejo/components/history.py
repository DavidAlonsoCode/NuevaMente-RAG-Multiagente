"""Historial #42: navegación real; #34 aporta el renderer del material aprobado."""

from datetime import date

import streamlit as st
from api_client import FORMATOS, IDIOMAS_SALIDA, PERFILES, APIError
from history_flow import (
    cargar_detalle,
    cargar_pagina,
    comparar,
    construir_filtros,
    estado_historial,
    fecha_utc,
    formatos_exportables,
)
from i18n import t

from components.errors import mostrar_error_ui
from components.result_header import _escapar_markdown


def _etiqueta(fila, idioma):
    p = fila.get("parametros") or {}
    valores = [fecha_utc(fila["created_at"]).strftime("%Y-%m-%d %H:%M UTC")]
    for campo, namespace in (
        ("perfil_destinatario", "perfil"),
        ("formato_salida", "formato"),
        ("idioma_salida", "idioma"),
    ):
        if p.get(campo):
            valores.append(t(namespace + "." + p[campo], idioma))
    return " · ".join(valores) + " · " + fila["generation_id"][-8:]


def render_historial(idioma_actual, api_client=None):
    with st.expander(t("nav.historial", idioma_actual)):
        ws, token = st.session_state.get("workspace_id"), st.session_state.get("session_token")
        if not ws or not token or api_client is None:
            st.caption(t("historial.vacio", idioma_actual))
            return
        if st.button(t("historial.abrir_vista", idioma_actual), key="historial:abrir:" + ws):
            st.session_state.historial_abierto = True
            st.session_state.historial_detalle = None
            st.rerun()
        recientes = st.toggle(t("historial.recientes", idioma_actual), key="historial:recientes:" + ws)
        if recientes:
            try:
                pagina = cargar_pagina(
                    api_client, token, estado_historial(st.session_state, ws + ":sidebar"), {}, limite=5
                )
                for fila in pagina["items"]:
                    st.caption(_etiqueta(fila, idioma_actual))
                    st.caption(t("estados.trabajo." + fila["status"], idioma_actual))
                if not pagina["items"]:
                    st.caption(t("historial.vacio", idioma_actual))
            except APIError as error:
                mostrar_error_ui(error, idioma_actual)


def _filtros(api, token, estado, idioma, ws):
    documentos, cursor, vistos = {}, None, set()
    while True:
        params = {"limit": 100}
        if cursor:
            params["cursor"] = cursor
        pagina = api.get_documents(token, params)
        documentos.update({d["document_id"]: d["titulo"] for d in pagina["items"]})
        cursor = pagina["next_cursor"]
        if cursor is None:
            break
        if not isinstance(cursor, str) or not cursor or cursor in vistos:
            raise APIError("INVALID_RESPONSE", "Cursor documental inválido", 502)
        vistos.add(cursor)
    f = estado["filtros"]
    opciones = [None, *documentos]
    if f.get("documento") and f["documento"] not in opciones:
        opciones.append(f["documento"])
    doc = st.selectbox(
        t("historial.documento", idioma),
        opciones,
        index=opciones.index(f.get("documento")),
        format_func=lambda x: documentos.get(x, x) if x else t("historial.todos", idioma),
        key=ws + ":doc",
    )
    perfil = _selector("perfil", PERFILES, f.get("perfil"), idioma, ws)
    formato = _selector("formato", FORMATOS, f.get("formato"), idioma, ws)
    lenguaje = _selector("idioma", IDIOMAS_SALIDA, f.get("idioma"), idioma, ws)
    desde = st.date_input(
        t("historial.desde", idioma),
        value=date.fromisoformat(f["desde"][:10]) if f.get("desde") else None,
        key=ws + ":desde",
    )
    hasta = st.date_input(
        t("historial.hasta", idioma),
        value=date.fromisoformat(f["hasta"][:10]) if f.get("hasta") else None,
        key=ws + ":hasta",
    )
    return construir_filtros(doc, perfil, formato, lenguaje, desde, hasta)


def _selector(campo, valores, elegido, idioma, key):
    opciones = [None, *valores]
    return st.selectbox(
        t("historial." + campo, idioma),
        opciones,
        index=opciones.index(elegido),
        format_func=lambda x: t(campo + "." + x, idioma) if x else t("historial.todos", idioma),
        key=key + ":" + campo,
    )


def _diagnostico(job, idioma):
    st.warning(t("estados.trabajo." + job["status"], idioma))
    error = job.get("error")
    if error:
        from api_client import APIError

        from components.errors import mensaje_error

        seguro = APIError(error["code"], "", 409)
        st.info(mensaje_error(seguro, idioma))
        causa = (error.get("details") or {}).get("causa")
        claves = {"CUOTA_AGOTADA", "PRESUPUESTO_AGOTADO", "STORAGE_UNAVAILABLE", "PROVIDER_UNAVAILABLE"}
        if causa in claves:
            st.caption(t("historial.causa." + causa, idioma))
    st.caption(t("historial.sin_borrador", idioma))


def _mostrar_paquete(paquete, idioma, render_resultado, prefijo):
    fuente, meta = paquete["documento_fuente"], paquete["metadatos"]
    st.markdown("**" + _escapar_markdown(fuente["titulo"]) + "**")
    st.caption(
        t("historial.fecha_fuente", idioma).format(
            fecha=fecha_utc(paquete["created_at"]).strftime("%Y-%m-%d %H:%M UTC"), version=fuente["version"]
        )
    )
    st.caption(
        " · ".join(
            t(n + "." + meta[k], idioma)
            for k, n in (("perfil_aplicado", "perfil"), ("formato_generado", "formato"), ("idioma_salida", "idioma"))
        )
    )
    if render_resultado:
        render_resultado(paquete, idioma, key_prefix=prefijo)
    else:
        st.info(t("historial.visor_pendiente", idioma))


def _descargas(api, token, paquete, idioma, prefijo):
    formato = st.selectbox(
        t("historial.formato_descarga", idioma),
        formatos_exportables(paquete),
        format_func=str.upper,
        key=prefijo + ":export",
    )
    if st.button(t("historial.preparar_descarga", idioma), key=prefijo + ":prepare"):
        # Revalidar estado/propiedad antes de exponer el artefacto.
        actual = cargar_detalle(api, token, paquete["generation_id"])
        if actual["status"] != "completed":
            _diagnostico(actual, idioma)
            return
        data = api.download_export(token, paquete["generation_id"], formato)
        mime = {
            "json": "application/json",
            "md": "text/markdown",
            "pdf": "application/pdf",
            "csv": "text/csv",
            "tsv": "text/tab-separated-values",
            "apkg": "application/octet-stream",
        }[formato]
        st.download_button(
            t("historial.descargar", idioma),
            data,
            file_name=paquete["generation_id"] + "." + formato,
            mime=mime,
            key=prefijo + ":download",
            on_click="ignore",
        )


def render_historial_completo(api, idioma, *, render_resultado=None):
    ws, token = st.session_state.get("workspace_id"), st.session_state.get("session_token")
    if not ws or not token:
        return
    estado = estado_historial(st.session_state, ws)
    prefijo = "historial:" + ws
    st.subheader(t("historial.titulo", idioma))
    if st.button(t("historial.cerrar", idioma), key=prefijo + ":close"):
        st.session_state.historial_abierto = False
        st.session_state.historial_detalle = None
        st.rerun()
    try:
        espacio = api.workspace_status(token)
        if espacio["workspace_id"] != ws:
            raise APIError("INVALID_RESPONSE", "Espacio inválido", 502)
        expira = fecha_utc(espacio["expira_en"]).strftime("%Y-%m-%d %H:%M UTC")
        st.caption(t("historial.retencion", idioma).format(fecha=expira))
        if gid := st.session_state.get("historial_detalle"):
            if st.button(t("historial.volver", idioma), key=prefijo + ":list"):
                st.session_state.historial_detalle = None
                st.rerun()
            detalle = cargar_detalle(api, token, gid)
            if detalle["status"] != "completed":
                _diagnostico(detalle, idioma)
                return
            _mostrar_paquete(detalle["contenido"], idioma, render_resultado, prefijo + ":" + gid)
            _descargas(api, token, detalle["contenido"], idioma, prefijo + ":" + gid)
            return
        with st.expander(t("historial.filtros", idioma)):
            try:
                filtros = _filtros(api, token, estado, idioma, prefijo)
            except ValueError:
                st.warning(t("historial.rango_invalido", idioma))
                return
        pagina = cargar_pagina(api, token, estado, filtros)
        if not pagina["items"]:
            st.info(t("historial.vacio", idioma))
        for fila in pagina["items"]:
            with st.container(border=True):
                st.write(_etiqueta(fila, idioma))
                st.caption(t("historial.documento", idioma) + ": " + fila["document_id"])
                st.caption(t("estados.trabajo." + fila["status"], idioma))
                if st.button(t("historial.abrir_resultado", idioma), key=prefijo + ":open:" + fila["generation_id"]):
                    st.session_state.historial_detalle = fila["generation_id"]
                    st.rerun()
        anterior, siguiente = st.columns(2)
        if anterior.button(t("historial.anterior", idioma), disabled=not estado["anteriores"], key=prefijo + ":prev"):
            estado["cursor"] = estado["anteriores"].pop()
            st.rerun()
        if siguiente.button(
            t("historial.siguiente", idioma), disabled=pagina["next_cursor"] is None, key=prefijo + ":next"
        ):
            estado["anteriores"].append(estado["cursor"])
            estado["cursor"] = pagina["next_cursor"]
            st.rerun()
        if estado["opciones"]:
            st.subheader(t("historial.comparar", idioma))
            opciones = [None, *estado["opciones"]]
            elegidos = []
            for i in range(2):
                elegido = estado["comparar"][i]
                elegidos.append(
                    st.selectbox(
                        t("historial.adaptacion_" + str(i), idioma),
                        opciones,
                        index=opciones.index(elegido),
                        format_func=lambda x: (
                            _etiqueta(estado["opciones"][x], idioma) if x else t("historial.elegir", idioma)
                        ),
                        key=prefijo + ":" + estado["huella"] + ":compare:" + str(i),
                    )
                )
            estado["comparar"] = elegidos
            if all(elegidos):
                paquetes = comparar(api, token, *elegidos)
                for i, columna in enumerate(st.columns(2)):
                    with columna:
                        _mostrar_paquete(
                            paquetes[i], idioma, render_resultado, prefijo + ":compare:" + paquetes[i]["generation_id"]
                        )
    except ValueError:
        st.warning(t("historial.comparacion_incompatible", idioma))
    except APIError as error:
        mostrar_error_ui(error, idioma)


def render_etiqueta_ejemplo(paquete, idioma):
    """Sólo para un catálogo demo explícito del #55; no inferir por título o ID."""
    st.info(t("historial.ejemplo_guardado", idioma))
    _mostrar_paquete(paquete, idioma, None, "ejemplo:" + paquete["generation_id"])
