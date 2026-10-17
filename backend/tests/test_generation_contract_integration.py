"""API -> worker/grafo/Chroma/storage; proveedores explícitamente simulados."""

import pytest
import test_api_generate as generaciones
from app.schemas.enums import PedagogicalFormat, RecipientProfile

entorno_generacion = generaciones.entorno
pytestmark = pytest.mark.integration_mock


@pytest.mark.parametrize("perfil", list(RecipientProfile))
@pytest.mark.parametrize("formato", list(PedagogicalFormat))
def test_formatos_perfiles_completan_el_pipeline(entorno_generacion, perfil, formato):
    e = entorno_generacion
    indice = list(RecipientProfile).index(perfil) * len(PedagogicalFormat) + list(PedagogicalFormat).index(formato)
    idioma = ("es", "en", "pt")[indice % 3]
    alcance = {"tipo": "documento_completo"}
    if indice % 2:
        detalle = e.cliente.get(f"/api/documents/{e.doc}", headers=e.headers).json()
        alcance = {"tipo": "seccion", "seccion_id": detalle["cobertura"]["secciones_procesadas"][0]["seccion_id"]}
    respuesta = generaciones.generar(
        e,
        "combinacion-panel",
        perfil_destinatario=perfil.value,
        formato_salida=formato.value,
        idioma_salida=idioma,
        alcance=alcance,
    )
    assert respuesta.status_code == 202, respuesta.text
    resultado = generaciones.esperar(e, respuesta.json(), timeout=30)
    metadata = resultado["contenido"]["metadatos"]
    assert metadata["perfil_aplicado"] == perfil.value
    assert metadata["formato_generado"] == formato.value
    assert metadata["idioma_salida"] == idioma
    assert all(metadata["alcance"][k] == v for k, v in alcance.items())
    assert resultado["persistencia"]["status_upload"] == "completado"
    llamadas = (e.doble.llamadas_generacion, e.doble.llamadas_verificacion)
    repetida = generaciones.generar(
        e,
        "combinacion-panel",
        perfil_destinatario=perfil.value,
        formato_salida=formato.value,
        idioma_salida=idioma,
        alcance=alcance,
    )
    assert repetida.json()["generation_id"] == respuesta.json()["generation_id"]
    assert llamadas == (e.doble.llamadas_generacion, e.doble.llamadas_verificacion)
