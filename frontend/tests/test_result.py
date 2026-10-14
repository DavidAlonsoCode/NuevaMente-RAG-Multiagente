"""Renderizadores con fixtures del contrato #03, sin proveedores reales."""

import sys
from copy import deepcopy

import pytest
import streamlit as st
from api_client import APIError
from app.schemas.responses import PedagogicalStudentOutput
from components.content_common import texto
from components.result_validation import validar_paquete
from i18n import t
from streamlit.runtime.pages_manager import PagesManager
from streamlit.testing.v1 import AppTest
from test_schemas import TODOS_LOS_FORMATOS, salida_valida

pytestmark = pytest.mark.integration_mock

SCRIPT = """
import streamlit as st
from components.result import render_generacion, render_paquete
from test_result import ClienteResultado
if st.session_state.get('mostrando_recuperacion'):
    st.info('RECUPERACION')
    st.stop()
if st.session_state.get('solo_paquete'):
    render_paquete(st.session_state.paquete, st.session_state.idioma_ui,
                   api_client=ClienteResultado())
else:
    render_generacion(ClienteResultado(), st.session_state.activa, st.session_state.idioma_ui)
"""


def paquete(formato="flashcards"):
    canonico = salida_valida(deepcopy(TODOS_LOS_FORMATOS[formato][0]))
    return PedagogicalStudentOutput.desde_canonico(canonico).model_dump(mode="json")


class ClienteResultado:
    def answer_quiz(self, token, generation_id, payload):
        st.session_state.llamadas.append(("respuesta", payload))
        pregunta = TODOS_LOS_FORMATOS["quiz"][0]["preguntas"][0]
        return {
            "correcta": payload["option_id"] == pregunta["correct_option_id"],
            "correct_option_id": pregunta["correct_option_id"],
            "justificacion": pregunta["justificacion"],
            "referencias": pregunta["referencias"],
        }

    def get_generation(self, token, gid):
        st.session_state.llamadas.append(("estado", gid))
        return st.session_state.job

    def get_document_source(self, token, document_id, chunk_id, *, demo=False):
        st.session_state.llamadas.append(("fuente", document_id, chunk_id))
        error = st.session_state.get("error_fuente")
        if error:
            raise APIError(error, "Error de prueba", 401 if error == "SESSION_INVALID" else 404)
        return st.session_state.fuente

    def get_document_source_image(self, token, document_id, chunk_id):
        from base64 import b64decode

        st.session_state.llamadas.append(("imagen", document_id, chunk_id))
        return b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jP1UAAAAASUVORK5CYII=")

    def cancel_generation(self, token, gid):
        st.session_state.llamadas.append(("cancelar", gid))
        st.session_state.job["status"] = "cancelled"

    def persist_generation(self, token, gid, *, idempotency_key):
        st.session_state.llamadas.append(("persistir", gid, idempotency_key))
        if st.session_state.get("fallar_guardado"):
            raise APIError("STORAGE_UNAVAILABLE", "Error de prueba", 503)
        st.session_state.job["status"] = "queued"


@pytest.fixture(autouse=True)
def aislar_apptest():
    principal = sys.modules.get("__main__")
    usa_paginas = PagesManager.uses_pages_directory
    yield
    if principal is not None:
        sys.modules["__main__"] = principal
    PagesManager.uses_pages_directory = usa_paginas


def ui(formato="flashcards", idioma="es", estado="completed"):
    p = paquete(formato)
    app = AppTest.from_string(SCRIPT)
    for key, value in {
        "workspace_id": "ws_test",
        "session_token": "token_test",
        "idioma_ui": idioma,
        "llamadas": [],
        "paquete": p,
        "activa": {
            "generation_id": p["generation_id"],
            "parametros": {
                "document_id": p["documento_fuente"]["document_id"],
                "formato_salida": formato,
                "perfil_destinatario": "principiante",
                "nicho_sector": "general",
                "nivel_detalle": "didactico",
                "idioma_salida": "es",
                "alcance": {"tipo": "documento_completo"},
            },
        },
        "job": {
            "generation_id": p["generation_id"],
            "status": estado,
            "contenido": p,
            "persistencia": {"status_upload": "completado", "provider": "oci"},
            "error": None,
            "posicion_cola": 2,
        },
        "fuente": {
            "document_id": p["documento_fuente"]["document_id"],
            "chunk_id": "chk_001",
            "texto": "EVIDENCIA_ORIGINAL",
            "pagina": 2,
            "seccion": "Conceptos",
            "source_type": "text",
            "interpretacion_modelo": False,
        },
    }.items():
        app.session_state[key] = value
    return app.run()


def boton(app, label):
    return next(b for b in app.button if b.label == label)


def textos(app):
    return " ".join(e.value for grupo in (app.markdown, app.caption, app.info, app.error, app.success) for e in grupo)


@pytest.mark.parametrize("formato", list(TODOS_LOS_FORMATOS))
@pytest.mark.parametrize("idioma", ["es", "en", "pt"])
def test_cinco_formatos_en_tres_idiomas(formato, idioma):
    app = ui(formato, idioma)
    assert not app.exception
    assert texto(paquete(formato)["contenido_adaptado"]["titulo"]) in textos(app)
    assert t("material.revisado", idioma) in textos(app)
    assert t("material.guardado", idioma) in textos(app)
    assert t("calidad.explicacion_score", idioma) in textos(app)
    assert app.metric[0].value == "1.00"
    if formato == "quiz":
        assert app.radio[0].value is None
        assert "correct_option_id" not in textos(app)
        assert TODOS_LOS_FORMATOS["quiz"][0]["preguntas"][0]["justificacion"] not in textos(app)


def test_flashcard_revela_oculta_y_registra_revision_con_id_estable():
    app = ui()
    dorso = paquete()["contenido_adaptado"]["items"][0]["dorso"]
    assert dorso not in textos(app)
    boton(app, t("material.mostrar_respuesta")).click().run()
    assert not app.exception and dorso in textos(app)
    assert list(app.session_state.flashcards_revisadas.values()) == [
        {"generation_id": "gen_01HX", "flashcard_id": "fc_001"}
    ]
    app.session_state.idioma_ui = "pt"
    app.run()
    assert dorso in textos(app) and boton(app, t("material.ocultar_respuesta", "pt"))
    boton(app, t("material.ocultar_respuesta", "pt")).click().run()
    assert dorso not in textos(app)


def test_quiz_no_expone_canonico_y_conserva_opcion_al_traducir():
    app = ui("quiz")
    app.radio[0].set_value("B").run()
    app.session_state.idioma_ui = "en"
    app.run()
    assert not app.exception and app.radio[0].value == "B"
    job = app.session_state.job
    job["contenido"]["contenido_adaptado"] = deepcopy(TODOS_LOS_FORMATOS["quiz"][0])
    app.session_state.job = job
    app.run()
    assert app.error and not app.radio
    assert "Un rango CIDR" not in textos(app)


def test_quiz_integrado_corrige_y_enlaza_fuentes_sin_consultas_duplicadas():
    app = ui("quiz")
    assert app.session_state.llamadas == [("estado", "gen_01HX")]
    app.radio[0].set_value("B").run()
    boton(app, t("quiz.responder")).click().run()
    assert not app.exception and any(t("quiz.incorrecto") == e.value for e in app.error)
    assert texto(TODOS_LOS_FORMATOS["quiz"][0]["preguntas"][0]["justificacion"]) in textos(app)
    boton(app, t("fuentes.cargar")).click().run()
    assert texto("EVIDENCIA_ORIGINAL") in textos(app)


@pytest.mark.parametrize("estado", ["queued", "running", "rejected_quality", "failed", "cancelled"])
def test_estados_no_exponen_borradores_ni_guardado(estado):
    app = ui(estado=estado)
    assert not app.exception
    assert not app.metric and not app.radio and not app.success
    assert not any(b.label == t("material.mostrar_respuesta") for b in app.button)
    assert "Dominando Redes" not in textos(app)
    if estado == "queued":
        assert t("generar.posicion").format(posicion=2) in textos(app)


def test_cancelar_y_persistir_no_disparan_generacion_nueva():
    app = ui(estado="running")
    boton(app, t("generar.cancelar")).click().run()
    assert ("cancelar", "gen_01HX") in app.session_state.llamadas
    assert t("material.cancelado") in textos(app)
    app = ui(estado="failed")
    job = app.session_state.job
    job["error"] = {"code": "STORAGE_UNAVAILABLE"}
    app.session_state.job = job
    app.session_state.fallar_guardado = True
    app.run()
    boton(app, t("material.persist_reintentar")).click().run()
    boton(app, t("material.persist_reintentar")).click().run()
    llamadas = [x for x in app.session_state.llamadas if x[0] == "persistir"]
    assert len(llamadas) == 2 and llamadas[0][2] == llamadas[1][2]
    assert set(x[0] for x in app.session_state.llamadas) == {"estado", "persistir"}


@pytest.mark.parametrize(
    "cambio",
    ["persistencia", "generation_id", "document_id", "formato", "alcance", "bloqueo", "score", "respuesta_invalida"],
)
def test_completed_inconsistente_no_publica_material(cambio):
    app = ui()
    job = deepcopy(app.session_state.job)
    if cambio == "persistencia":
        job["persistencia"]["status_upload"] = "fallido"
    elif cambio == "generation_id":
        job["contenido"]["generation_id"] = "gen_otro"
    elif cambio == "document_id":
        job["contenido"]["documento_fuente"]["document_id"] = "doc_otro"
    elif cambio == "formato":
        job["contenido"]["metadatos"]["formato_generado"] = "quiz"
    elif cambio == "alcance":
        job["contenido"]["metadatos"]["alcance"] = {"tipo": "seccion", "seccion_id": "otra"}
    elif cambio == "bloqueo":
        job["contenido"]["evaluacion_calidad"]["razones_bloqueo"] = ["Sin respaldo"]
    elif cambio == "score":
        job["contenido"]["evaluacion_calidad"]["anclaje_fuente_score"] = float("nan")
    else:
        job["contenido"]["contenido_adaptado"]["items"] = "invalido"
    app.session_state.job = job
    app.run()
    assert not app.exception and app.error and not app.success and not app.metric
    assert "Dominando Redes" not in textos(app)


def test_fuente_se_carga_bajo_demanda_y_no_sobrevive_a_revocacion():
    app = ui("tutorial")
    assert not any(x[0] == "fuente" for x in app.session_state.llamadas)
    boton(app, t("fuentes.cargar")).click().run()
    assert texto("EVIDENCIA_ORIGINAL") in textos(app)
    assert ("fuente", "doc_01HX", "chk_001") in app.session_state.llamadas
    app.session_state.error_fuente = "RESOURCE_NOT_FOUND"
    app.run()
    assert texto("EVIDENCIA_ORIGINAL") not in textos(app) and app.error
    app.session_state.error_fuente = "SESSION_INVALID"
    app.run()
    assert not app.exception and app.session_state.session_token is None
    assert "RECUPERACION" in textos(app)
    assert texto("EVIDENCIA_ORIGINAL") not in textos(app)


def test_fuente_incompatible_se_rechaza_y_diagrama_muestra_original():
    app = ui("tutorial")
    fuente = dict(app.session_state.fuente)
    fuente["chunk_id"] = "otro"
    app.session_state.fuente = fuente
    boton(app, t("fuentes.cargar")).click().run()
    assert app.error and texto("EVIDENCIA_ORIGINAL") not in textos(app)
    fuente.update(
        chunk_id="chk_001",
        source_type="image_description",
        interpretacion_modelo=True,
        imagen_url="https://sitio-ajeno.invalid/robar",
    )
    app.session_state.fuente = fuente
    app.run()
    assert not app.exception
    assert ("imagen", "doc_01HX", "chk_001") in app.session_state.llamadas
    assert app.get("imgs")
    assert t("fuentes.interpretacion") in textos(app)


def test_html_markdown_y_ejemplos_de_nicho_se_presentan_con_etiqueta():
    app = ui()
    p = deepcopy(app.session_state.paquete)
    p["contenido_adaptado"]["titulo"] = "<script>alert(1)</script> [clic](javascript:alert(1))"
    p["contenido_adaptado"]["items"][0]["dorso"] = '<img src="https://externo.invalid/pixel">'
    p["metadatos"]["nicho_sector"] = "fintech"
    app.session_state.paquete = p
    app.session_state.solo_paquete = True
    app.run()
    assert not app.exception
    assert t("material.ejemplo_ilustrativo") in textos(app)
    assert "\\<script\\>" in textos(app)
    boton(app, t("material.mostrar_respuesta")).click().run()
    assert "&lt;img" in textos(app) and '<img src="https://externo' not in textos(app)


def test_lectores_reutilizables_no_mezclan_revisiones_de_generaciones():
    app = ui()
    boton(app, t("material.mostrar_respuesta")).click().run()
    p = deepcopy(app.session_state.paquete)
    p["generation_id"] = "gen_distinta"
    app.session_state.paquete = p
    app.session_state.solo_paquete = True
    app.run()
    assert not app.exception and boton(app, t("material.mostrar_respuesta"))
    assert p["contenido_adaptado"]["items"][0]["dorso"] not in textos(app)


def test_validacion_no_acepta_cobertura_parcial_o_verificacion_insuficiente():
    for campo, valor in (
        ("cobertura_objetivos", "parcial"),
        ("verificacion_visual", "insuficiente"),
        ("estado_evaluacion", "requiere_revision"),
    ):
        p = paquete()
        p["evaluacion_calidad"][campo] = valor
        with pytest.raises(ValueError):
            validar_paquete(p)


@pytest.mark.parametrize("campo", ["documento_fuente", "metadatos", "contenido_adaptado", "evaluacion_calidad"])
def test_respuestas_truncadas_o_malformadas_fallan_sin_exponer_material(campo):
    p = paquete()
    p[campo] = None
    with pytest.raises(ValueError):
        validar_paquete(p)
