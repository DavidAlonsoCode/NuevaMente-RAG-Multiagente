"""Biblioteca de resultados curados: los renderizadores del material son compartidos."""

import streamlit as st
from api_client import APIError
from i18n import t

from components.content_common import texto
from components.errors import mostrar_error_ui
from components.result import render_paquete
from components.sources import render_fuentes


class ClienteEjemplo:
    """Limita fuentes/quiz al snapshot elegido, nunca a endpoints privados."""

    def __init__(self, api, generation_id, document_id):
        self.api, self.gid, self.doc = api, generation_id, document_id

    def get_document_source(self, token, document_id, chunk_id, *, demo=False):
        if document_id != self.doc:
            raise APIError("INVALID_RESPONSE", "Fuente ajena al ejemplo.", 502)
        return self.api.get_demo_generation_source(token, self.gid, chunk_id)

    def get_document_source_image(self, token, document_id, chunk_id):
        if document_id != self.doc:
            raise APIError("INVALID_RESPONSE", "Fuente ajena al ejemplo.", 502)
        contenido = self.api.get_demo_generation_source(token, self.gid, chunk_id, image=True)
        if not isinstance(contenido, bytes) or not contenido.startswith(b"\x89PNG\r\n\x1a\n"):
            raise APIError("INVALID_RESPONSE", "Imagen incompatible.", 502)
        return contenido

    def answer_quiz(self, token, generation_id, payload):
        if generation_id != self.gid:
            raise APIError("INVALID_RESPONSE", "Quiz ajeno al ejemplo.", 502)
        return self.api.answer_demo_quiz(token, self.gid, payload)


def render_catalogo(api, idioma):
    st.subheader(t("demo.ejemplos", idioma))
    try:
        catalogo = api.get_demo_generations(st.session_state.session_token)
        if catalogo is None or not catalogo.get("items"):
            st.info(t("demo.vacio", idioma))
            return
        if catalogo.get("provider") == "mock":
            st.warning(t("almacenamiento.local_dev", idioma))
        st.caption(t("demo.ayuda", idioma))
        for item in catalogo["items"]:
            with st.container(border=True):
                parametros = item["parametros"]
                st.markdown("**" + texto(item["fuente"]) + "**")
                st.caption(
                    " · ".join(
                        (
                            t("perfil." + parametros["perfil_destinatario"], idioma),
                            t("formato." + parametros["formato_salida"], idioma),
                            t("nicho." + parametros["nicho_sector"], idioma),
                            t("idioma." + parametros["idioma_salida"], idioma),
                        )
                    )
                )
                st.caption(t("historial.ejemplo_guardado", idioma))
                st.caption(item["created_at"][:10] + " · " + t("demo.tiempo", idioma).format(segundos=item["segundos"]))
                if item["status"] == "rejected_quality":
                    st.warning(texto(item["diagnostico"]))
                elif st.button(
                    t("demo.abrir", idioma), key="abrir_demo_" + item["generation_id"], use_container_width=True
                ):
                    st.session_state.ejemplo_demo_id = item["generation_id"]
                    st.session_state.historial_abierto = False
                    st.rerun()
    except (KeyError, TypeError, ValueError):
        mostrar_error_ui(APIError("INVALID_RESPONSE", "Catálogo incompatible.", 502), idioma)
    except APIError as error:
        mostrar_error_ui(error, idioma)


def render_ejemplo(api, idioma):
    if st.button(t("nav.documentos", idioma), key="volver_biblioteca_demo"):
        st.session_state.pop("ejemplo_demo_id", None)
        st.session_state.vista_onboarding = "demo"
        st.session_state.mostrando_documentos = True
        st.rerun()
    gid, token = st.session_state.ejemplo_demo_id, st.session_state.session_token
    try:
        catalogo = api.get_demo_generations(token)
        item = next(
            (i for i in (catalogo or {}).get("items", []) if i["generation_id"] == gid and i["status"] == "completed"),
            None,
        )
        if item is None:
            raise APIError("NOT_FOUND", "Ejemplo no encontrado.", 404)
        paquete = api.get_demo_generation(token, gid)
        if (
            paquete.get("generation_id") != gid
            or paquete["documento_fuente"]["document_id"] != item["parametros"]["document_id"]
        ):
            raise APIError("INVALID_RESPONSE", "Ejemplo incompatible.", 502)
        cliente = ClienteEjemplo(api, gid, paquete["documento_fuente"]["document_id"])
        st.info(t("historial.ejemplo_guardado", idioma))
        if catalogo["provider"] == "mock":
            st.warning(t("almacenamiento.local_dev", idioma))
        st.caption(item["created_at"][:10] + " · " + t("demo.tiempo", idioma).format(segundos=item["segundos"]))
        st.caption(t("demo.practica", idioma))
        render_paquete(
            paquete,
            idioma,
            api_client=cliente,
            key_prefix="demo:" + gid,
            trabajo={"generation_id": gid, "status": "completed", "contenido": paquete},
            registrar_estudio=False,
        )
        diagrama = catalogo.get("diagrama")
        if diagrama and diagrama["respuesta"]["document_id"] == cliente.doc:
            with st.expander(t("demo.diagrama", idioma)):
                respuesta = diagrama["respuesta"]
                st.markdown(texto(respuesta["pregunta"]))
                st.markdown(texto(respuesta["respuesta"]))
                render_fuentes(
                    cliente.doc, respuesta["referencias"], idioma, api_client=cliente, key_prefix="demo:diagrama:" + gid
                )
    except (KeyError, TypeError, ValueError):
        mostrar_error_ui(APIError("INVALID_RESPONSE", "Ejemplo incompatible.", 502), idioma)
    except APIError as error:
        mostrar_error_ui(error, idioma)
