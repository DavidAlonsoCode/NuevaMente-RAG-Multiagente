"""#25: cliente/Streamlit contra servidor TCP, con OCI/Gemini mock explícitos."""

import time
from io import BytesIO

import pytest
import test_session_ui as sesiones
from api_client import APIError
from i18n import t
from reportlab.pdfgen import canvas
from streamlit.testing.v1 import AppTest

pytestmark = pytest.mark.integration_mock
backend_http = sesiones.backend_http
APP = sesiones.APP


def esperar_documento(api, token, cid):
    limite = time.monotonic() + 15
    while time.monotonic() < limite:
        documento = api.get_document(token, cid)
        if documento["status"] != "processing":
            return documento
        time.sleep(0.05)
    pytest.fail("El documento no alcanzó un estado terminal")


@pytest.mark.parametrize("extension,mime", [("txt", "text/plain"), ("md", "text/markdown"), ("pdf", "application/pdf")])
def test_archivo_pdf_md_txt_con_titulo_ready_y_rechazo_forzado(backend_http, extension, mime):
    api = backend_http
    token = api.create_workspace()["token"]
    texto = "Una VCN conecta subredes privadas y mantiene recursos aislados. " * 20
    datos = texto.encode("utf-8")
    if extension == "pdf":
        salida = BytesIO()
        pdf = canvas.Canvas(salida)
        pdf.drawString(40, 700, texto)
        pdf.save()
        datos = salida.getvalue()
    recibo = api.upload_file(
        token, "fuente." + extension, datos, mime, title="Fuente de la UI", idempotency_key="archivo"
    )
    documento = esperar_documento(api, token, recibo["document_id"])
    assert documento["status"] == "ready" and documento["titulo"] == "Fuente de la UI"
    assert not documento["cobertura"]["paginas_visuales_pendientes"]
    if extension == "pdf":
        assert documento["cobertura"]["paginas_procesadas"]
    assert documento["metadata"]["cantidad_tokens"] > 0
    progreso = api.read_job_progress(token, recibo["job_id"])
    assert progreso["data"]["status"] == "completed" and progreso["data"]["step"] == "ingestion"
    with pytest.raises(APIError) as error:
        api.upload_file(token, "grande.pdf", b"%PDF-1.7\n" + b"x" * (25 * 1024 * 1024), "application/pdf")
    assert error.value.status_code == 413 and error.value.code == "DOCUMENT_TOO_LARGE"


def test_pegar_texto_visible_listo_seleccion_y_borrado(backend_http):
    app = AppTest.from_file(str(APP), default_timeout=15).run()
    assert not app.exception
    next(b for b in app.button if b.label == t("onboarding.codigo_guardado")).click().run()
    next(b for b in app.button if b.label == t("upload.enviar_texto")).click().run()
    app.text_input(key="input_texto_titulo").set_value("Texto desde Streamlit")
    app.text_area(key="input_texto_contenido").set_value(
        "Una VCN mantiene las subredes aisladas y conecta recursos. " * 25
    )
    app.checkbox(key="permiso_texto").set_value(True).run()
    app.button(key="btn_guardar_texto").click().run()
    assert not app.exception
    token = app.session_state.session_token
    documentos = backend_http.get_documents(token)["items"]
    assert len(documentos) == 1
    cid = documentos[0]["document_id"]
    assert esperar_documento(backend_http, token, cid)["status"] == "ready"
    app.run()
    assert not app.exception
    assert any(t("almacenamiento.local_dev") == c.value for c in app.caption)
    assert all(t("upload.guardado_oci") != c.value for c in app.caption)
    app.button(key="seleccionar_" + cid).click().run()
    assert app.session_state.documento_activo_id == cid
    app.button(key="borrar_" + cid).click().run()
    assert not app.exception
    assert app.session_state.documento_activo_id is None
    assert backend_http.get_documents(token)["items"] == []
