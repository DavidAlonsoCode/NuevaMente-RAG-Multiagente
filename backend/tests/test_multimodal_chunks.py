"""Las interpretaciones extensas conservan límites y procedencia al segmentarse."""

from types import SimpleNamespace

import pytest
from app.core.rag.chunker import ConfigChunker
from app.core.rag.multimodal import chunks_visuales
from app.schemas.multimodal import InterpretacionVisual

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("con_limitaciones", [False, True])
def test_descripcion_extensa_no_excede_chunker_y_conserva_pagina(con_limitaciones):
    interpretacion = InterpretacionVisual(
        estado="ambigua" if con_limitaciones else "interpretada",
        descripcion="La subred privada conecta recursos. " * 150 + "Etiqueta final: FIN_DESCRIPCION.",
        cobertura_completa=True,
        limitaciones=[f"Limitación {i}: " + "relación sin dirección confirmada. " * 12 for i in range(20)]
        if con_limitaciones
        else [],
        document_id="doc1",
        hash_sha256="a" * 64,
        pagina=3,
        imagen_referencia="pagina:3",
        imagen_sha256="b" * 64,
        modelo="gemini-modelo",
        version_prompt="prompt-v1",
        version_raster="raster-v1",
    )
    manifest = SimpleNamespace(
        document_id="doc1",
        workspace_id="ws1",
        hash_sha256="a" * 64,
        source_name="redes.pdf",
        language="es",
        chunker=ConfigChunker(),
        interpretaciones_visuales=[interpretacion],
    )
    chunks = chunks_visuales(manifest, indice_inicial=4)
    assert len(chunks) > 1
    assert all(0 < c.cantidad_tokens <= manifest.chunker.tamano_maximo for c in chunks)
    assert all(
        (c.source_type, c.es_diagrama, c.pagina, c.seccion_id, c.start_index)
        == ("image_description", True, 3, "pagina:3", None)
        for c in chunks
    )
    assert [c.indice for c in chunks] == list(range(4, 4 + len(chunks)))
    assert len({c.chunk_id for c in chunks}) == len(chunks)
    assert any("FIN_DESCRIPCION" in c.texto for c in chunks)
    if con_limitaciones:
        assert any("Limitación 19:" in c.texto for c in chunks)
    assert chunks_visuales(manifest, indice_inicial=4) == chunks
    assert manifest.interpretaciones_visuales == [interpretacion]
