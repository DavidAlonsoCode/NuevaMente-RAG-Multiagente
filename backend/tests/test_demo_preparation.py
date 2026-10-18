"""Preparación administrativa: fuentes inmutables y selección acotada, sin proveedores."""

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from app.demo.prepare import clave_revision, main, solicitud_rechazo, validar_fuentes_checkpoint

pytestmark = pytest.mark.unit


def test_checkpoint_historico_no_se_reutiliza_con_fuente_distinta():
    fuente = SimpleNamespace(document_id="demo-vcn")
    estado = {"workspace_id": "ws", "fuentes": {"demo-vcn": {"document_id": "doc"}}}
    with pytest.raises(ValueError, match="checkpoint histórico"):
        validar_fuentes_checkpoint(
            lambda did: {"workspace_id": "ws", "hash": hashlib.sha256(b"v1").hexdigest()},
            [(fuente, b"v2")],
            estado,
        )


@pytest.mark.parametrize("ajuste", [None, {"workspace_id": "otro"}, {"hash": "0" * 64}])
def test_checkpoint_inexistente_ajeno_o_alterado_se_bloquea(ajuste):
    huella = hashlib.sha256(b"v2").hexdigest()
    fuente = SimpleNamespace(document_id="demo-vcn")
    estado = {"workspace_id": "ws", "fuentes": {"demo-vcn": {"document_id": "doc", "hash": huella}}}
    doc = {"workspace_id": "ws", "hash": huella, **ajuste} if ajuste is not None else None
    with pytest.raises(ValueError):
        validar_fuentes_checkpoint(lambda did: doc, [(fuente, b"v2")], estado)


def test_checkpoint_valido_y_fuentes_nuevas():
    huella = hashlib.sha256(b"v2").hexdigest()
    fuente = SimpleNamespace(document_id="demo-vcn")
    doc = {"workspace_id": "ws", "hash": huella}
    for hash_guardado in ({}, {"hash": huella}):
        estado = {"workspace_id": "ws", "fuentes": {"demo-vcn": {"document_id": "doc", **hash_guardado}}}
        validar_fuentes_checkpoint(lambda did: doc, [(fuente, b"v2")], estado)
    validar_fuentes_checkpoint(lambda did: None, [(fuente, b"v2")], {"fuentes": {}})


def test_rechazo_apunta_a_costos_vcn_no_al_glosario_de_pagos():
    version = json.loads(Path("documents/redes_vcn_oci.version.json").read_text(encoding="utf-8"))
    solicitud = solicitud_rechazo("vcn-v2", [{"seccion_id": version["seccion_insuficiente"]["seccion_id"]}])
    assert solicitud.document_id == "vcn-v2"
    assert solicitud.alcance.seccion_id == "pagina:9"
    assert solicitud.perfil_destinatario.value == "lider_tecnico"
    assert solicitud.nivel_detalle.value == "tecnico_profundo"
    with pytest.raises(ValueError, match="sección débil"):
        solicitud_rechazo("pagos", [{"seccion_id": "seccion:glosario"}])


def test_cli_no_acepta_generar_y_publicar_como_acciones_excluyentes():
    with pytest.raises(SystemExit) as salida:
        main(["--generate-only", "--publish-only"])
    assert salida.value.code == 2


def test_correccion_explicita_conserva_intento_y_reanuda_misma_clave():
    previo = {"generation_id": "gen-viejo", "status": "failed"}
    estado = {"escenarios": {"vcn-b": previo}}
    assert clave_revision(estado, "escenarios", "vcn-b", None) == "55:vcn-b"
    assert "historial" not in estado
    clave = clave_revision(estado, "escenarios", "vcn-b", "citas-1-1-2")
    assert clave == "55:vcn-b:correccion:citas-1-1-2"
    assert estado["historial"] == [{"grupo": "escenarios", "nombre": "vcn-b", **previo}]
    estado["escenarios"]["vcn-b"] = {"revision": "citas-1-1-2", "idempotency_key": clave}
    assert clave_revision(estado, "escenarios", "vcn-b", "citas-1-1-2") == clave
    assert clave_revision(estado, "escenarios", "vcn-b", None) == clave
    assert len(estado["historial"]) == 1


@pytest.mark.parametrize(
    "argumentos", [["--revision-corregida", "citas"], ["--solo", "vcn-b", "--revision-corregida", "otra/clave"]]
)
def test_correccion_no_se_aplica_implicitamente_a_todo(argumentos):
    with pytest.raises(SystemExit) as salida:
        main(argumentos)
    assert salida.value.code == 2
