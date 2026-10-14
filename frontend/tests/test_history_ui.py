"""#42: navegación, filtros, versiones y descargas sin contenido no aprobado."""

import sys
from copy import deepcopy
from datetime import date

import pytest
from api_client import APIError
from history_flow import cargar_detalle, cargar_pagina, comparar, construir_filtros, estado_historial
from i18n import t
from streamlit.runtime.pages_manager import PagesManager
from streamlit.testing.v1 import AppTest

pytestmark = pytest.mark.unit
APP = """
import streamlit as st
from components.history import render_historial_completo
if "api" in st.session_state:
    def material(paquete, idioma, *, key_prefix):
        st.write("material:" + paquete["generation_id"])
    render_historial_completo(st.session_state.api, st.session_state.lang, render_resultado=material)
"""


@pytest.fixture(autouse=True)
def aislar_principal_streamlit(monkeypatch):
    monkeypatch.setitem(sys.modules, "__main__", sys.modules["__main__"])
    monkeypatch.setattr(PagesManager, "uses_pages_directory", None)


def paquete(gid, formato="flashcards", perfil="principiante", idioma="es"):
    return dict(
        generation_id=gid,
        status="aprobado",
        created_at="2026-10-13T15:00:00+00:00",
        documento_fuente=dict(document_id="d1", hash="hash-1", version="v1", titulo="Redes VCN"),
        metadatos=dict(perfil_aplicado=perfil, formato_generado=formato, idioma_salida=idioma),
        contenido_adaptado=dict(tipo=formato, preguntas=[]),
        evaluacion_calidad=dict(
            estado_evaluacion="aprobada",
            anclaje_fuente_score=1,
            cantidad_afirmaciones=1,
            cantidad_respaldadas=1,
            cobertura_objetivos="completa",
            verificacion_visual="no_aplica",
            claridad_pedagogica="alta",
            adecuacion_perfil="alta",
            coherencia_didactica="alta",
            razones_bloqueo=None,
        ),
    )


class API:
    def __init__(self):
        self.items = [
            dict(
                generation_id=gid,
                document_id="d1",
                status="completed",
                created_at="2026-10-13T15:00:00+00:00",
                parametros=dict(
                    document_id="d1", perfil_destinatario=perfil, formato_salida=formato, idioma_salida=idioma
                ),
            )
            for gid, perfil, formato, idioma in (
                ("g1", "principiante", "flashcards", "es"),
                ("g2", "ejecutivo", "resumen_ejecutivo", "pt"),
            )
        ]
        self.jobs = {
            r["generation_id"]: dict(
                generation_id=r["generation_id"],
                status="completed",
                contenido=paquete(
                    r["generation_id"],
                    r["parametros"]["formato_salida"],
                    r["parametros"]["perfil_destinatario"],
                    r["parametros"]["idioma_salida"],
                ),
                persistencia={"status_upload": "completado"},
                error=None,
            )
            for r in self.items
        }
        self.calls, self.exports, self.next_cursor = [], [], None

    def workspace_status(self, token):
        return dict(workspace_id="w1", expira_en="2026-11-12T15:00:00+00:00")

    def get_documents(self, token, params):
        return dict(items=[dict(document_id="d1", titulo="Redes VCN")], next_cursor=None)

    def get_generations(self, token, params):
        self.calls.append(deepcopy(params))
        fields = {"perfil": "perfil_destinatario", "formato": "formato_salida", "idioma": "idioma_salida"}
        items = [
            i
            for i in self.items
            if all(not params.get(k) or i["parametros"][v] == params[k] for k, v in fields.items())
        ]
        return dict(items=deepcopy(items), next_cursor=self.next_cursor)

    def get_generation(self, token, gid):
        return deepcopy(self.jobs[gid])

    def download_export(self, token, gid, formato):
        self.exports.append((gid, formato))
        return b"artifact"


def app(api, lang="es"):
    a = AppTest.from_string(APP)
    for k, v in dict(api=api, lang=lang, workspace_id="w1", session_token="test-token").items():
        a.session_state[k] = v
    return a.run()


@pytest.mark.parametrize("lang", ["es", "en", "pt"])
def test_comparacion_a_c_misma_fuente_y_expiracion_visible(lang):
    api = API()
    api.items.reverse()  # La más nueva es C, pero el usuario elige A primero.
    a = app(api, lang)
    assert not a.exception
    assert any("2026-11-12" in c.value for c in a.caption)
    for i, gid in enumerate(("g1", "g2")):
        next(s for s in a.selectbox if s.label == t("historial.adaptacion_" + str(i), lang)).set_value(gid).run()
    assert not a.exception
    assert {m.value for m in a.markdown if m.value.startswith("material:")} == {"material:g1", "material:g2"}
    assert len(a.session_state.api.calls) == 3


def test_filtros_literales_fechas_utc_y_paginacion():
    api = API()
    a = app(api)
    next(s for s in a.selectbox if s.label == t("historial.perfil", "es")).set_value("ejecutivo").run()
    assert api.calls[-1]["perfil"] == "ejecutivo"
    assert not any("Principiante" in m.value for m in a.markdown)
    parametros = construir_filtros("d1", "ejecutivo", "resumen_ejecutivo", "pt", date(2026, 10, 1), date(2026, 10, 13))
    assert parametros["desde"] == "2026-10-01T00:00:00+00:00"
    assert parametros["hasta"] == "2026-10-13T23:59:59.999999+00:00"
    estado = estado_historial({}, "w1")
    api.next_cursor = "cursor-1"
    cargar_pagina(api, "token", estado, parametros)
    estado["cursor"] = "cursor-1"
    cargar_pagina(api, "token", estado, parametros)
    assert api.calls[-1]["cursor"] == "cursor-1"
    cargar_pagina(api, "token", estado, {})
    assert "cursor" not in api.calls[-1]


def test_reapertura_y_descarga_solo_tras_confirmacion():
    api = API()
    a = app(api)
    next(b for b in a.button if b.key == "historial:w1:open:g1").click().run()
    assert not a.exception and any(m.value == "material:g1" for m in a.markdown)
    next(b for b in a.button if b.label == t("historial.preparar_descarga", "es")).click().run()
    assert api.exports == [("g1", "json")] and len(a.get("download_button")) == 1


@pytest.mark.parametrize("status", ["failed", "cancelled", "rejected_quality"])
def test_terminal_muestra_diagnostico_sin_borrador_ni_descargas(status):
    api = API()
    api.items[0]["status"] = status
    api.jobs["g1"].update(
        status=status,
        contenido=None,
        persistencia=None,
        error={"code": "INVALID_STATE", "message": "SECRETO", "details": {"causa": "CUOTA_AGOTADA"}},
    )
    a = app(api)
    next(b for b in a.button if b.key == "historial:w1:open:g1").click().run()
    assert not a.exception and not a.selectbox
    assert t("historial.causa.CUOTA_AGOTADA", "es") in [c.value for c in a.caption]
    assert not any("SECRETO" in m.value or "material:" in m.value for m in a.markdown)
    assert not api.exports and not a.get("download_button")


@pytest.mark.parametrize("campo", ["document_id", "hash", "version"])
def test_no_compara_distintos_documentos_o_versiones(campo):
    api = API()
    api.jobs["g2"]["contenido"]["documento_fuente"][campo] = "otro"
    with pytest.raises(ValueError):
        comparar(api, "token", "g1", "g2")


def test_no_expone_paquete_rechazado_ni_quiz_canonico():
    api = API()
    api.jobs["g1"]["status"] = "rejected_quality"
    with pytest.raises(APIError):
        cargar_detalle(api, "token", "g1")
    api.jobs["g1"].update(status="completed", contenido=paquete("g1", formato="quiz"))
    api.jobs["g1"]["contenido"]["contenido_adaptado"]["preguntas"] = [
        {"correct_option_id": "a", "justificacion": "SECRETO"}
    ]
    with pytest.raises(APIError):
        cargar_detalle(api, "token", "g1")


def test_borrado_o_sesion_revocada_no_reutiliza_resultados(monkeypatch):
    api = API()
    a = app(api)
    next(b for b in a.button if b.key == "historial:w1:open:g1").click().run()

    def revocado(*args):
        raise APIError("NOT_FOUND", "SECRETO", 404)

    monkeypatch.setattr(api, "get_generation", revocado)
    a.run()
    assert not any(m.value == "material:g1" for m in a.markdown) and not a.get("download_button")


def test_rango_invalido_no_consulta_historial():
    api = API()
    a = app(api)
    a.date_input[0].set_value(date(2026, 10, 14)).run()
    llamadas = len(api.calls)
    a.date_input[1].set_value(date(2026, 10, 1)).run()
    assert not a.exception and len(api.calls) == llamadas
    assert t("historial.rango_invalido", "es") in [w.value for w in a.warning]


def test_seleccion_comparable_conserva_adaptacion_de_pagina_anterior():
    api = API()
    estado = estado_historial({}, "w")
    cargar_pagina(api, "t", estado, {})
    estado["comparar"] = ["g1", None]
    api.items = [api.items[1]]
    cargar_pagina(api, "t", estado, {})
    assert set(estado["opciones"]) == {"g1", "g2"}
    assert estado_historial({}, "otro")["comparar"] == [None, None]
