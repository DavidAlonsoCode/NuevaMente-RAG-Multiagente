"""Parámetros con que se pidió una generación (§6.5).

Lee la copia guardada al disparar: mover los controles de la sidebar no altera
lo que describe la cabecera del material mostrado.
"""

from __future__ import annotations

import re

import streamlit as st
from api_client import GenerateRequest
from i18n import t

from components.generation import GeneracionActiva

_MARKDOWN_ESPECIAL = re.compile(r"([\\`*_{}\[\]()#+\-.!|>~<$])")


def _escapar_markdown(texto: str) -> str:
    return _MARKDOWN_ESPECIAL.sub(r"\\\1", texto)


def etiquetas_parametros(
    parametros: GenerateRequest, documento_titulo: str, seccion_titulo: str | None, idioma: str
) -> list[tuple[str, str]]:
    """Pares (rótulo, valor) traducidos; los valores de máquina nunca se muestran crudos."""
    alcance = parametros["alcance"]
    if alcance["tipo"] == "seccion":
        valor_alcance = f"{t('sidebar.alcance_seccion', idioma)}: {seccion_titulo or alcance['seccion_id']}"
    else:
        valor_alcance = t("sidebar.alcance_completo", idioma)
    return [
        (t("sidebar.documento", idioma), documento_titulo),
        (t("sidebar.perfil", idioma), t(f"perfil.{parametros['perfil_destinatario']}", idioma)),
        (t("sidebar.formato", idioma), t(f"formato.{parametros['formato_salida']}", idioma)),
        (t("sidebar.nicho", idioma), t(f"nicho.{parametros['nicho_sector']}", idioma)),
        (t("sidebar.detalle", idioma), t(f"detalle.{parametros['nivel_detalle']}", idioma)),
        (t("sidebar.idioma_salida", idioma), t(f"idioma.{parametros['idioma_salida']}", idioma)),
        (t("sidebar.alcance", idioma), valor_alcance),
    ]


def render_resumen_parametros(
    parametros: GenerateRequest, documento_titulo: str, seccion_titulo: str | None, idioma: str
) -> None:
    for rotulo, valor in etiquetas_parametros(parametros, documento_titulo, seccion_titulo, idioma):
        st.markdown(f"**{rotulo}:** {_escapar_markdown(valor)}")


def render_cabecera_resultado(generacion: GeneracionActiva, idioma: str) -> None:
    st.subheader(t("generar.cabecera_titulo", idioma))
    render_resumen_parametros(
        generacion["parametros"], generacion["documento_titulo"], generacion["seccion_titulo"], idioma
    )
    st.info(t("generar.enviada", idioma))
    with st.expander(t("comun.detalles_tecnicos", idioma)):
        st.code(t("generar.id_generacion", idioma).format(generation_id=generacion["generation_id"]))
