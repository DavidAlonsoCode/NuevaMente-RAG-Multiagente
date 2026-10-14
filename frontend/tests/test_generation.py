"""Panel de parámetros y disparo de generación (issue #32)."""

from copy import deepcopy
from pathlib import Path
from typing import get_args

import api_client
import pytest
from api_client import APIClient, APIError
from components.generation import construir_peticion, documentos_ready, secciones_de
from i18n import t
from streamlit.testing.v1 import AppTest

APP = Path(__file__).resolve().parents[1] / "app.py"

DOC_READY = {
    "document_id": "doc_vcn",
    "titulo": "Redes VCN en OCI",
    "status": "ready",
    "metadata": None,
    "cobertura": {
        "paginas_procesadas": [],
        "paginas_omitidas": [],
        "paginas_visuales_pendientes": [],
        "secciones_procesadas": [
            {"seccion_id": "seccion:1", "titulo": "Subredes", "linea_inicio": 1, "linea_fin": 20},
            {"seccion_id": "seccion:2", "titulo": "Gateways", "linea_inicio": 21, "linea_fin": 40},
        ],
        "secciones_omitidas": [],
        "secciones_visuales_pendientes": [],
    },
    "error": None,
}
DOC_PROCESANDO = {**DOC_READY, "document_id": "doc_pendiente", "titulo": "Pendiente", "status": "processing"}
ACEPTADA = {
    "generation_id": "gen_1",
    "status": "queued",
    "status_url": "/api/generations/gen_1",
    "events_url": "/api/generations/gen_1/events",
}


@pytest.mark.unit
@pytest.mark.parametrize(
    ("literal", "enum"),
    [
        ("RecipientProfile", "RecipientProfile"),
        ("PedagogicalFormat", "PedagogicalFormat"),
        ("IndustryNiche", "IndustryNiche"),
        ("DetailLevel", "DetailLevel"),
        ("OutputLanguage", "OutputLanguage"),
        ("JobStatus", "JobStatus"),
        ("DocumentStatus", "DocumentStatus"),
    ],
)
def test_literales_del_frontend_coinciden_con_enums_del_contrato(literal, enum):
    from app.schemas import enums

    assert set(get_args(getattr(api_client, literal))) == {e.value for e in getattr(enums, enum)}


@pytest.mark.unit
@pytest.mark.parametrize("formato", api_client.FORMATOS)
@pytest.mark.parametrize("perfil", api_client.PERFILES)
def test_cada_formato_y_perfil_respeta_generate_request(perfil, formato):
    from app.schemas.requests import GenerateRequest

    for seccion_id in (None, "seccion:2"):
        peticion = construir_peticion("doc_vcn", perfil, formato, "fintech", "practico", "pt", seccion_id)
        validada = GenerateRequest.model_validate(peticion)
        assert validada.model_dump(mode="json", exclude_none=True) == peticion


@pytest.mark.unit
def test_peticion_exige_documento_y_seccion_no_vacia():
    with pytest.raises(ValueError):
        construir_peticion("", "principiante", "quiz", "general", "didactico", "es")
    with pytest.raises(ValueError):
        construir_peticion("doc", "principiante", "quiz", "general", "didactico", "es", " ")


@pytest.mark.unit
def test_solo_documentos_ready_y_secciones_reales():
    assert documentos_ready([DOC_READY, DOC_PROCESANDO, {"status": "ready"}, "basura"]) == [DOC_READY]
    assert documentos_ready(None) == []
    assert [s["seccion_id"] for s in secciones_de(DOC_READY)] == ["seccion:1", "seccion:2"]
    assert secciones_de({**DOC_READY, "cobertura": None}) == []
    assert secciones_de(None) == []


@pytest.fixture
def ui(monkeypatch):
    llamadas = []
    respuestas = []

    def crear(_self, token, payload, *, idempotency_key=None):
        from app.schemas.requests import GenerateRequest

        GenerateRequest.model_validate(payload)
        llamadas.append({"token": token, "payload": payload, "idempotency_key": idempotency_key})
        respuesta = respuestas.pop(0) if respuestas else ACEPTADA
        if isinstance(respuesta, Exception):
            raise respuesta
        return respuesta

    documentos = {"items": [DOC_READY], "next_cursor": None}
    monkeypatch.setattr(
        APIClient, "create_workspace", lambda _: {"workspace_id": "ws", "token": "tok", "recovery_code": "rc"}
    )
    monkeypatch.setattr(APIClient, "get_documents", lambda _self, token, params=None: documentos)
    monkeypatch.setattr(
        APIClient,
        "get_document",
        lambda _self, token, cid: next(d for d in documentos["items"] if d["document_id"] == cid),
    )
    monkeypatch.setattr(
        APIClient,
        "workspace_status",
        lambda *_: {
            "expira_en": "2026-11-12T12:00:00Z",
            "uso": {"documentos": 1, "limite_documentos": 5, "generaciones": 0, "limite_generaciones": 20},
            "almacenamiento": {"alerta": False, "escrituras_bloqueadas": False},
        },
    )
    monkeypatch.setattr(APIClient, "create_generation", crear)
    monkeypatch.setattr(
        APIClient, "get_generation", lambda _self, token, gid: {"generation_id": gid, "status": "queued"}
    )
    app = AppTest.from_file(str(APP)).run()
    assert not app.exception
    return app, llamadas, respuestas, documentos


def _boton(app, clave):
    return next(b for b in app.button if b.label == t(clave))


def _click(app, clave):
    _boton(app, clave).click().run()
    assert not app.exception


@pytest.mark.integration_mock
def test_genera_con_seccion_y_cabecera_conserva_parametros(ui):
    app, llamadas, _, _ = ui
    assert app.selectbox(key="gen_document_id").options == ["Redes VCN en OCI"]
    assert not _boton(app, "sidebar.generar").disabled
    app.selectbox(key="gen_perfil").set_value("lider_tecnico").run()
    app.selectbox(key="gen_formato").set_value("quiz").run()
    app.selectbox(key="gen_idioma_salida").set_value("en").run()
    app.radio(key="gen_alcance").set_value("seccion").run()
    app.selectbox(key="gen_seccion_id").set_value("seccion:2").run()

    _click(app, "sidebar.generar")
    assert not llamadas
    _click(app, "generar.confirmar")

    assert llamadas[0]["token"] == "tok"
    assert llamadas[0]["idempotency_key"].startswith("gen-")
    assert llamadas[0]["payload"] == {
        "document_id": "doc_vcn",
        "perfil_destinatario": "lider_tecnico",
        "formato_salida": "quiz",
        "nicho_sector": "general",
        "nivel_detalle": "didactico",
        "idioma_salida": "en",
        "alcance": {"tipo": "seccion", "seccion_id": "seccion:2"},
    }
    assert app.session_state.generacion_activa["generation_id"] == "gen_1"
    assert app.session_state.generacion_pendiente is None

    app.selectbox(key="gen_perfil").set_value("ejecutivo").run()
    app.selectbox(key="gen_idioma_salida").set_value("pt").run()
    assert len(llamadas) == 1
    assert app.session_state.generacion_activa["parametros"]["perfil_destinatario"] == "lider_tecnico"
    cabecera = " ".join(m.value for m in app.markdown)
    assert t("perfil.lider_tecnico") in cabecera and t("perfil.ejecutivo") not in cabecera
    assert "Gateways" in cabecera
    assert any(t("generar.enviada") == item.value for item in app.info)


@pytest.mark.integration_mock
def test_cola_llena_conserva_seleccion_y_reutiliza_la_clave(ui):
    app, llamadas, respuestas, _ = ui
    respuestas.append(APIError("QUEUE_FULL", "privado", 429, "req_cola", retry_after="30"))
    app.selectbox(key="gen_formato").set_value("tutorial").run()
    _click(app, "sidebar.generar")
    _click(app, "generar.confirmar")
    assert t("errors.QUEUE_FULL") in app.error[0].value
    assert "generacion_activa" not in app.session_state
    assert app.selectbox(key="gen_formato").value == "tutorial"

    _click(app, "generar.confirmar")
    assert len(llamadas) == 2
    assert llamadas[0]["idempotency_key"] == llamadas[1]["idempotency_key"]
    assert app.session_state.generacion_activa["generation_id"] == "gen_1"


@pytest.mark.integration_mock
def test_volver_no_envia_y_otro_cuerpo_usa_otra_clave(ui):
    app, llamadas, _, _ = ui
    _click(app, "sidebar.generar")
    clave = app.session_state.generacion_pendiente["idempotency_key"]
    _click(app, "comun.atras")
    assert not llamadas and app.session_state.generacion_pendiente is None
    _click(app, "sidebar.generar")
    assert app.session_state.generacion_pendiente["idempotency_key"] == clave
    _click(app, "comun.atras")
    app.selectbox(key="gen_nicho").set_value("salud").run()
    _click(app, "sidebar.generar")
    assert app.session_state.generacion_pendiente["idempotency_key"] != clave


@pytest.mark.integration_mock
def test_sin_documento_ready_no_se_puede_generar(ui):
    app, _, _, documentos = ui
    documentos["items"] = []
    app.run()
    assert not app.exception
    assert _boton(app, "sidebar.generar").disabled
    assert any(t("sidebar.sin_documentos") == c.value for c in app.caption)


@pytest.mark.integration_mock
def test_cambiar_idioma_ui_conserva_todos_los_parametros(ui):
    app, llamadas, _, _ = ui
    for key, value in {
        "gen_perfil": "ejecutivo",
        "gen_formato": "guion_clase",
        "gen_nicho": "salud",
        "gen_detalle": "tecnico_profundo",
        "gen_idioma_salida": "pt",
    }.items():
        app.selectbox(key=key).set_value(value).run()
    app.radio(key="gen_alcance").set_value("seccion").run()
    app.selectbox(key="gen_seccion_id").set_value("seccion:2").run()
    app.selectbox[0].set_value("en").run()
    assert not app.exception
    assert app.session_state.idioma_ui == "en"
    assert app.selectbox(key="gen_perfil").value == "ejecutivo"
    assert app.selectbox(key="gen_formato").value == "guion_clase"
    assert app.selectbox(key="gen_nicho").value == "salud"
    assert app.selectbox(key="gen_detalle").value == "tecnico_profundo"
    assert app.selectbox(key="gen_idioma_salida").value == "pt"
    assert app.radio(key="gen_alcance").value == "seccion"
    assert app.selectbox(key="gen_seccion_id").value == "seccion:2"
    assert not llamadas


@pytest.mark.integration_mock
def test_seleccion_del_catalogo_es_la_fuente_de_generacion(ui):
    app, llamadas, _, documentos = ui
    segundo = deepcopy(DOC_READY)
    segundo.update(document_id="doc_segundo", titulo="Segunda fuente")
    documentos["items"].append(segundo)
    app.run()
    app.button(key="seleccionar_doc_segundo").click().run()
    assert not app.exception
    assert app.selectbox(key="gen_document_id").value == "doc_segundo"
    _click(app, "sidebar.generar")
    _click(app, "generar.confirmar")
    assert llamadas[0]["payload"]["document_id"] == "doc_segundo"


@pytest.mark.integration_mock
@pytest.mark.parametrize("perfil", api_client.PERFILES)
@pytest.mark.parametrize("formato", api_client.FORMATOS)
def test_matriz_de_perfiles_y_formatos_se_envia_desde_la_ui(ui, perfil, formato):
    app, llamadas, _, _ = ui
    app.selectbox(key="gen_perfil").set_value(perfil).run()
    app.selectbox(key="gen_formato").set_value(formato).run()
    _click(app, "sidebar.generar")
    _click(app, "generar.confirmar")
    assert llamadas[0]["payload"]["perfil_destinatario"] == perfil
    assert llamadas[0]["payload"]["formato_salida"] == formato


@pytest.mark.integration_mock
def test_demo_seleccionada_no_genera_otra_fuente_del_espacio(ui):
    app, llamadas, _, _ = ui
    app.session_state.documento_activo_id = "demo-pagos-fintech"
    app.session_state.documento_activo_demo = True
    app.run()
    assert not app.exception
    assert app.selectbox(key="gen_document_id").value is None
    assert _boton(app, "sidebar.generar").disabled
    assert any(t("sidebar.demo_generacion_pendiente") == w.value for w in app.warning)
    assert not llamadas
    app.selectbox(key="gen_document_id").set_value("doc_vcn").run()
    assert not app.session_state.documento_activo_demo
    assert app.session_state.documento_activo_id == "doc_vcn"
    assert not _boton(app, "sidebar.generar").disabled


@pytest.mark.integration_mock
def test_catalogo_sigue_accesible_despues_de_generar_sin_perder_cabecera(ui):
    app, llamadas, _, _ = ui
    _click(app, "sidebar.generar")
    _click(app, "generar.confirmar")
    activa = deepcopy(app.session_state.generacion_activa)
    app.button(key="volver_documentos").click().run()
    assert not app.exception
    assert any(b.key == "seleccionar_doc_vcn" for b in app.button)
    _click(app, "generar.volver_resultado")
    assert app.session_state.generacion_activa == activa
    assert len(llamadas) == 1


@pytest.mark.integration_mock
@pytest.mark.parametrize("code,status", [("IDEMPOTENCY_CONFLICT", 409), ("SERVICE_UNAVAILABLE", 503)])
def test_error_conserva_parametros_y_reintenta_el_mismo_trabajo(ui, code, status):
    app, llamadas, respuestas, _ = ui
    respuestas.append(APIError(code, "privado", status))
    app.selectbox(key="gen_formato").set_value("quiz").run()
    _click(app, "sidebar.generar")
    _click(app, "generar.confirmar")
    assert t("errors." + code) in app.error[0].value
    assert app.selectbox(key="gen_formato").value == "quiz"
    _click(app, "generar.confirmar")
    assert llamadas[0]["idempotency_key"] == llamadas[1]["idempotency_key"]
