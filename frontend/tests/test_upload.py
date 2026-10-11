"""Pruebas unitarias para el componente de carga e ingestión (Issue #25).

Verifica los criterios de aceptación:
1. Validación inmediata de extensiones (.pdf, .md, .txt) y rechazo de no permitidas.
2. Rechazo de archivos que superen los límites (§4.1): PDF > 20 MB y MD/TXT > 5 MB.
3. Validación de entrada de texto pegado (campos requeridos y tamaño).
4. Integridad de los 3 documentos demo oficiales.
"""

import pytest
from components.upload import (
    DOCUMENTOS_DEMO,
    MAX_PDF_BYTES,
    MAX_TEXT_BYTES,
    formatear_tamano,
    validar_archivo,
    validar_texto_pegado,
)

pytestmark = pytest.mark.unit


def test_validar_archivo_pdf_valido():
    """Un archivo PDF dentro del límite de 20 MB es aceptado."""
    valido, error = validar_archivo("documento_arquitectura.pdf", 5 * 1024 * 1024)
    assert valido is True
    assert error is None


def test_validar_archivo_markdown_y_txt_validos():
    """Archivos MD y TXT dentro del límite de 5 MB son aceptados."""
    valido_md, error_md = validar_archivo("guia_api.md", 2 * 1024 * 1024)
    assert valido_md is True
    assert error_md is None

    valido_txt, error_txt = validar_archivo("notas.txt", 1 * 1024 * 1024)
    assert valido_txt is True
    assert error_txt is None


def test_rechazo_pdf_de_25_mb():
    """Criterio de aceptación explícito: un PDF de 25 MB es rechazado por la UI."""
    pdf_25mb = 25 * 1024 * 1024
    valido, error = validar_archivo("manual_pesado.pdf", pdf_25mb)
    assert valido is False
    assert error == "errors.DOCUMENT_TOO_LARGE"


def test_rechazo_pdf_limite_exacto():
    """Un PDF que excede MAX_PDF_BYTES por 1 byte es rechazado."""
    valido, error = validar_archivo("limite.pdf", MAX_PDF_BYTES + 1)
    assert valido is False
    assert error == "errors.DOCUMENT_TOO_LARGE"


def test_rechazo_texto_que_supera_5_mb():
    """Un archivo MD o TXT que supera 5 MB es rechazado."""
    texto_grande = 6 * 1024 * 1024
    valido_md, error_md = validar_archivo("grande.md", texto_grande)
    assert valido_md is False
    assert error_md == "errors.DOCUMENT_TOO_LARGE"

    valido_txt, error_txt = validar_archivo("grande.txt", texto_grande)
    assert valido_txt is False
    assert error_txt == "errors.DOCUMENT_TOO_LARGE"


def test_rechazo_extension_no_permitida():
    """Formatos no soportados (.docx, .png, .exe, .zip) son rechazados."""
    for archivo_invalido in ["doc.docx", "imagen.png", "script.exe", "bundle.zip"]:
        valido, error = validar_archivo(archivo_invalido, 1024)
        assert valido is False
        assert "Formato no permitido" in error


def test_validar_texto_pegado_valido():
    """Texto con título y contenido razonable es válido."""
    valido, error = validar_texto_pegado("Título Demo", "Contenido técnico sobre APIs.")
    assert valido is True
    assert error is None


def test_validar_texto_pegado_vacio():
    """Texto o título vacíos son rechazados con mensaje descriptivo."""
    valido_sin_titulo, err_titulo = validar_texto_pegado("", "Contenido técnico.")
    assert valido_sin_titulo is False
    assert "título" in err_titulo

    valido_sin_contenido, err_contenido = validar_texto_pegado("Título", "   ")
    assert valido_sin_contenido is False
    assert "contenido" in err_contenido


def test_validar_texto_pegado_excede_tamano():
    """Texto pegado que excede 5 MB es rechazado."""
    contenido_gigante = "A" * (MAX_TEXT_BYTES + 10)
    valido, error = validar_texto_pegado("Texto Gigante", contenido_gigante)
    assert valido is False
    assert error == "errors.DOCUMENT_TOO_LARGE"


def test_formatear_tamano():
    """Convierte bytes a formato humano legible."""
    assert formatear_tamano(500) == "500 B"
    assert formatear_tamano(1024) == "1.0 KB"
    assert formatear_tamano(2048 * 1024) == "2.0 MB"


def test_integridad_documentos_demo():
    """Verifica que los 3 documentos demo contengan todos los campos obligatorios."""
    assert len(DOCUMENTOS_DEMO) == 3
    ids = {d["id"] for d in DOCUMENTOS_DEMO}
    assert ids == {"demo-redes-vcn", "demo-pagos-fintech", "demo-salud-gobernanza"}

    for demo in DOCUMENTOS_DEMO:
        assert demo["titulo"]
        assert demo["archivo"]
        assert demo["formato"] in ["PDF", "Markdown"]
        assert demo["paginas_procesadas"] >= 1
        assert demo["chunks"] >= 1
        assert demo["cobertura_porcentaje"] == 100
