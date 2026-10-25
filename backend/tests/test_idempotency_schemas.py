"""Cuerpos históricos fijados contra develop 23d11c4 y la PR e5fda685."""

import json

import pytest
from app.schemas.chat import ChatRequest
from app.schemas.requests import GenerateRequest, GlossaryRequest

pytestmark = pytest.mark.unit

PARAMETROS_GENERACION = {
    "perfil_destinatario": "principiante",
    "formato_salida": "flashcards",
    "nicho_sector": "general",
    "nivel_detalle": "didactico",
    "idioma_salida": "es",
}
GENERATE_HISTORICO = {
    "alcance": {"seccion_id": None, "tipo": "documento_completo"},
    "document_id": "doc_historico",
    "formato_salida": "flashcards",
    "idioma_salida": "es",
    "nicho_sector": "general",
    "nivel_detalle": "didactico",
    "perfil_destinatario": "principiante",
}
GENERATE_PR = {
    "alcance": {"seccion_id": None, "tipo": "documento_completo"},
    "document_id": "doc_historico",
    "document_ids": ["doc_historico"],
    "formato_salida": "flashcards",
    "idioma_salida": "es",
    "nicho_sector": "general",
    "nivel_detalle": "didactico",
    "perfil_destinatario": "principiante",
}
GENERATE_HISTORICO_JSON = (
    '{"alcance": {"seccion_id": null, "tipo": "documento_completo"}, '
    '"document_id": "doc_historico", "formato_salida": "flashcards", "idioma_salida": "es", '
    '"nicho_sector": "general", "nivel_detalle": "didactico", "perfil_destinatario": "principiante"}'
)


@pytest.mark.parametrize(
    "fuentes",
    [
        {"document_id": "doc_historico"},
        {"document_ids": ["doc_historico"]},
        {"document_id": "doc_historico", "document_ids": ["doc_historico", "doc_historico"]},
    ],
    ids=["historica", "lista", "ambos-con-duplicado"],
)
def test_generacion_unidocumental_conserva_cuerpo_develop_y_admite_forma_pr(fuentes):
    solicitud = GenerateRequest(**PARAMETROS_GENERACION, **fuentes)
    campos = solicitud.model_fields_set.copy()
    campos_alcance = solicitud.alcance.model_fields_set.copy()
    publico = solicitud.model_dump(mode="json")

    assert solicitud.parametros_persistidos() == GENERATE_HISTORICO
    assert solicitud.parametros_idempotentes() == (GENERATE_HISTORICO, GENERATE_PR)
    assert json.dumps(solicitud.parametros_persistidos(), sort_keys=True, ensure_ascii=False) == GENERATE_HISTORICO_JSON
    assert solicitud.model_fields_set == campos
    assert solicitud.alcance.model_fields_set == campos_alcance
    assert solicitud.model_dump(mode="json") == publico
    assert publico["document_ids"] == ["doc_historico"]
    assert publico["modo_generacion"] == "multiagente"
    assert "modo_generacion" not in solicitud.model_fields_set


@pytest.mark.parametrize(
    "modo,explicito,historico,forma_pr",
    [
        (None, False, GENERATE_HISTORICO, GENERATE_PR),
        ("multiagente", True, GENERATE_HISTORICO, GENERATE_PR),
        (
            "basico",
            True,
            {
                "alcance": {"seccion_id": None, "tipo": "documento_completo"},
                "document_id": "doc_historico",
                "formato_salida": "flashcards",
                "idioma_salida": "es",
                "modo_generacion": "basico",
                "nicho_sector": "general",
                "nivel_detalle": "didactico",
                "perfil_destinatario": "principiante",
            },
            {
                "alcance": {"seccion_id": None, "tipo": "documento_completo"},
                "document_id": "doc_historico",
                "document_ids": ["doc_historico"],
                "formato_salida": "flashcards",
                "idioma_salida": "es",
                "modo_generacion": "basico",
                "nicho_sector": "general",
                "nivel_detalle": "didactico",
                "perfil_destinatario": "principiante",
            },
        ),
    ],
    ids=["multiagente-implicito-1.0", "multiagente-explicito-1.1", "basico-1.1"],
)
def test_idempotencia_no_modifica_modo_explicito_ni_serializacion_publica(modo, explicito, historico, forma_pr):
    entrada = {**PARAMETROS_GENERACION, "document_id": "doc_historico"}
    if modo is not None:
        entrada["modo_generacion"] = modo
    solicitud = GenerateRequest.model_validate(entrada)
    campos = solicitud.model_fields_set.copy()
    publico = solicitud.model_dump_json()

    assert solicitud.parametros_idempotentes() == (historico, forma_pr)
    assert solicitud.parametros_persistidos() == historico
    assert solicitud.model_fields_set == campos
    assert ("modo_generacion" in solicitud.model_fields_set) is explicito
    assert solicitud.model_dump_json() == publico
    assert solicitud.modo_generacion == (modo or "multiagente")
    if modo == "basico":
        assert historico != GENERATE_HISTORICO
        assert forma_pr != GENERATE_PR


def test_seccion_conserva_identidad_historica_y_seccion_id():
    solicitud = GenerateRequest(
        **PARAMETROS_GENERACION, document_id="doc_historico", alcance={"tipo": "seccion", "seccion_id": "sec_3"}
    )

    assert solicitud.parametros_idempotentes() == (
        {
            "alcance": {"seccion_id": "sec_3", "tipo": "seccion"},
            "document_id": "doc_historico",
            "formato_salida": "flashcards",
            "idioma_salida": "es",
            "nicho_sector": "general",
            "nivel_detalle": "didactico",
            "perfil_destinatario": "principiante",
        },
        {
            "alcance": {"seccion_id": "sec_3", "tipo": "seccion"},
            "document_id": "doc_historico",
            "document_ids": ["doc_historico"],
            "formato_salida": "flashcards",
            "idioma_salida": "es",
            "nicho_sector": "general",
            "nivel_detalle": "didactico",
            "perfil_destinatario": "principiante",
        },
    )
    distinta = solicitud.model_copy(update={"alcance": solicitud.alcance.model_copy(update={"seccion_id": "sec_4"})})
    assert distinta.parametros_idempotentes() != solicitud.parametros_idempotentes()


def test_ids_redundantes_del_alcance_se_omiten_solo_del_cuerpo_historico():
    solicitud = GenerateRequest(
        **PARAMETROS_GENERACION,
        document_id="doc_historico",
        alcance={"tipo": "documento_completo", "document_ids": ["doc_historico"]},
    )

    assert solicitud.parametros_persistidos() == GENERATE_HISTORICO
    assert solicitud.parametros_idempotentes() == (
        GENERATE_HISTORICO,
        {
            "alcance": {"seccion_id": None, "tipo": "documento_completo", "document_ids": ["doc_historico"]},
            "document_id": "doc_historico",
            "document_ids": ["doc_historico"],
            "formato_salida": "flashcards",
            "idioma_salida": "es",
            "nicho_sector": "general",
            "nivel_detalle": "didactico",
            "perfil_destinatario": "principiante",
        },
    )
    assert solicitud.alcance.document_ids == ["doc_historico"]


def test_ids_distintos_del_alcance_no_se_convierten_en_identidad_historica():
    solicitud = GenerateRequest(
        **PARAMETROS_GENERACION,
        document_id="doc_historico",
        alcance={"tipo": "documento_completo", "document_ids": ["doc_otro"]},
    )

    assert solicitud.parametros_idempotentes() == (
        {
            "alcance": {"seccion_id": None, "tipo": "documento_completo", "document_ids": ["doc_otro"]},
            "document_id": "doc_historico",
            "formato_salida": "flashcards",
            "idioma_salida": "es",
            "nicho_sector": "general",
            "nivel_detalle": "didactico",
            "perfil_destinatario": "principiante",
        },
        {
            "alcance": {"seccion_id": None, "tipo": "documento_completo", "document_ids": ["doc_otro"]},
            "document_id": "doc_historico",
            "document_ids": ["doc_historico"],
            "formato_salida": "flashcards",
            "idioma_salida": "es",
            "nicho_sector": "general",
            "nivel_detalle": "didactico",
            "perfil_destinatario": "principiante",
        },
    )
    assert GENERATE_HISTORICO not in solicitud.parametros_idempotentes()
    assert GENERATE_PR not in solicitud.parametros_idempotentes()


def test_espacio_completo_unidocumental_no_admite_forma_historica():
    solicitud = GenerateRequest(
        **PARAMETROS_GENERACION, document_ids=["doc_historico"], alcance={"tipo": "espacio_completo"}
    )

    assert solicitud.parametros_idempotentes() == (
        {
            "alcance": {"seccion_id": None, "tipo": "espacio_completo"},
            "document_id": "doc_historico",
            "document_ids": ["doc_historico"],
            "formato_salida": "flashcards",
            "idioma_salida": "es",
            "nicho_sector": "general",
            "nivel_detalle": "didactico",
            "perfil_destinatario": "principiante",
        },
    )


@pytest.mark.parametrize("ids", [["doc_a", "doc_b"], ["doc_b", "doc_a", "doc_b"]], ids=["A+B", "B+A+B"])
def test_generacion_multidocumental_tiene_una_forma_ordenada_y_no_admite_otro_conjunto(ids):
    solicitud = GenerateRequest(**PARAMETROS_GENERACION, document_ids=ids)
    otro_conjunto = GenerateRequest(**PARAMETROS_GENERACION, document_ids=["doc_a", "doc_c"])

    assert solicitud.parametros_idempotentes() == (
        {
            "alcance": {"seccion_id": None, "tipo": "multi_documento", "document_ids": ["doc_a", "doc_b"]},
            "document_id": "doc_a",
            "document_ids": ["doc_a", "doc_b"],
            "formato_salida": "flashcards",
            "idioma_salida": "es",
            "nicho_sector": "general",
            "nivel_detalle": "didactico",
            "perfil_destinatario": "principiante",
        },
    )
    assert solicitud.parametros_idempotentes() != otro_conjunto.parametros_idempotentes()
    assert solicitud.document_ids == ["doc_a", "doc_b"]
    assert solicitud.alcance.document_ids == ["doc_a", "doc_b"]


@pytest.mark.parametrize(
    "modelo,entrada,historico,forma_pr,json_historico",
    [
        (
            ChatRequest,
            {"pregunta": "  Que es una VCN?  ", "perfil_destinatario": "principiante", "idioma_salida": "es"},
            {
                "document_id": "doc_historico",
                "pregunta": "Que es una VCN?",
                "perfil_destinatario": "principiante",
                "idioma_salida": "es",
            },
            {
                "document_id": "doc_historico",
                "document_ids": ["doc_historico"],
                "pregunta": "Que es una VCN?",
                "perfil_destinatario": "principiante",
                "idioma_salida": "es",
            },
            '{"document_id": "doc_historico", "idioma_salida": "es", "perfil_destinatario": "principiante", '
            '"pregunta": "Que es una VCN?"}',
        ),
        (
            GlossaryRequest,
            {"perfil_destinatario": "principiante", "idioma_salida": "es"},
            {"document_id": "doc_historico", "perfil_destinatario": "principiante", "idioma_salida": "es"},
            {
                "document_id": "doc_historico",
                "document_ids": ["doc_historico"],
                "perfil_destinatario": "principiante",
                "idioma_salida": "es",
            },
            '{"document_id": "doc_historico", "idioma_salida": "es", "perfil_destinatario": "principiante"}',
        ),
    ],
    ids=["chat", "glosario"],
)
@pytest.mark.parametrize("solo_lista", [False, True], ids=["historica", "lista"])
def test_chat_y_glosario_preservan_cuerpos_historicos_y_publicos(
    modelo, entrada, historico, forma_pr, json_historico, solo_lista
):
    fuentes = {"document_ids": ["doc_historico"]} if solo_lista else {"document_id": "doc_historico"}
    solicitud = modelo(**entrada, **fuentes)
    campos = solicitud.model_fields_set.copy()
    publico = solicitud.model_dump(mode="json")

    assert solicitud.parametros_persistidos() == historico
    assert solicitud.parametros_idempotentes() == (historico, forma_pr)
    assert json.dumps(solicitud.parametros_persistidos(), sort_keys=True, ensure_ascii=False) == json_historico
    assert solicitud.model_fields_set == campos
    assert solicitud.model_dump(mode="json") == publico
    assert publico == forma_pr


@pytest.mark.parametrize(
    "modelo,entrada,esperado",
    [
        (
            ChatRequest,
            {"pregunta": "Que es una VCN?", "perfil_destinatario": "principiante", "idioma_salida": "es"},
            {
                "document_id": "doc_a",
                "document_ids": ["doc_a", "doc_b"],
                "pregunta": "Que es una VCN?",
                "perfil_destinatario": "principiante",
                "idioma_salida": "es",
            },
        ),
        (
            GlossaryRequest,
            {"perfil_destinatario": "principiante", "idioma_salida": "es"},
            {
                "document_id": "doc_a",
                "document_ids": ["doc_a", "doc_b"],
                "perfil_destinatario": "principiante",
                "idioma_salida": "es",
            },
        ),
    ],
    ids=["chat", "glosario"],
)
def test_chat_y_glosario_multidocumentales_no_omiten_fuentes(modelo, entrada, esperado):
    ab = modelo(**entrada, document_ids=["doc_a", "doc_b"])
    ba = modelo(**entrada, document_ids=["doc_b", "doc_a", "doc_b"])
    ac = modelo(**entrada, document_ids=["doc_a", "doc_c"])

    assert ab.parametros_idempotentes() == ba.parametros_idempotentes() == (esperado,)
    assert ab.parametros_persistidos() == esperado
    assert ac.parametros_idempotentes() != ab.parametros_idempotentes()
