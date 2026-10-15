"""Evidencia autorizada compartida por materiales, chat y glosario."""

from typing import Any

import streamlit as st
from api_client import APIError
from i18n import t

from components.content_common import clave, texto
from components.errors import mostrar_error_ui


def validar_fuente(fuente: dict, referencia: dict, document_id: str) -> None:
    if (
        not isinstance(fuente, dict)
        or fuente.get("document_id") != document_id
        or fuente.get("chunk_id") != referencia["chunk_id"]
        or not isinstance(fuente.get("texto"), str)
        or not fuente["texto"].strip()
        or (fuente.get("pagina") is not None and (type(fuente["pagina"]) is not int or fuente["pagina"] < 1))
        or (fuente.get("seccion") is not None and not isinstance(fuente["seccion"], str))
        or (referencia.get("pagina") is not None and fuente.get("pagina") != referencia["pagina"])
        or (referencia.get("seccion") is not None and fuente.get("seccion") != referencia["seccion"])
    ):
        raise APIError("INVALID_RESPONSE", "Fuente incompatible con la cita.", 502)


def render_fuentes(
    document_id: str,
    referencias: list[dict[str, Any]],
    idioma: str,
    *,
    api_client,
    key_prefix: str = "fuentes",
    demo: bool = False,
) -> None:
    """Cada apertura revalida acceso; el texto y la imagen no se cachean entre sesiones."""
    vistos = set()
    for referencia in referencias:
        chunk_id = referencia["chunk_id"]
        if chunk_id in vistos:
            continue
        vistos.add(chunk_id)
        identidad = clave(key_prefix, document_id, chunk_id)
        rotulo = t("fuentes.fragmento", idioma).format(numero=len(vistos))
        if referencia.get("pagina"):
            rotulo += " · " + t("fuentes.pagina", idioma).format(numero=referencia["pagina"])
        if referencia.get("seccion"):
            rotulo += " · " + texto(referencia["seccion"])
        with st.expander(t("quiz.ver_fuente", idioma) + " · " + rotulo):
            abierta = st.session_state.get(identidad + ":abierta", False)
            if st.button(
                t("fuentes.cerrar" if abierta else "fuentes.cargar", idioma),
                key=identidad + ":abrir",
                disabled=api_client is None or not st.session_state.get("session_token"),
            ):
                st.session_state[identidad + ":abierta"] = not abierta
                st.rerun()
            if not abierta:
                continue
            try:
                fuente = api_client.get_document_source(
                    st.session_state.session_token, document_id, chunk_id, demo=demo
                )
                validar_fuente(fuente, referencia, document_id)
                interpretacion = fuente.get("source_type") == "image_description" or fuente.get("interpretacion_modelo")
                if interpretacion:
                    st.caption(t("fuentes.interpretacion", idioma))
                    if demo:
                        # El contrato demo textual no publica un endpoint de imagen.
                        st.warning(t("fuentes.imagen_no_disponible", idioma))
                    else:
                        imagen = api_client.get_document_source_image(
                            st.session_state.session_token, document_id, chunk_id
                        )
                        st.image(imagen, caption=t("fuentes.imagen_original", idioma), width="stretch")
                else:
                    st.caption(t("fuentes.texto_original", idioma))
                st.markdown(texto(fuente["texto"]))
                if fuente.get("pagina"):
                    st.caption(t("fuentes.pagina", idioma).format(numero=fuente["pagina"]))
                ubicacion = fuente.get("seccion")
                if ubicacion:
                    st.caption(texto(ubicacion))
            except APIError as error:
                mostrar_error_ui(error, idioma)
            except (ValueError, OSError):
                mostrar_error_ui(APIError("INVALID_RESPONSE", "Imagen ilegible.", 502), idioma)
