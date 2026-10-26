"""Candidatos, contrato y caché operativa #39; sin SDK, red ni Chroma."""

from types import SimpleNamespace

import pytest
from app.glossary.candidates import detectar_candidatos
from app.jobs.store import RegistroOperativo
from app.schemas.enums import DocumentStatus, JobStatus
from app.schemas.glossary import BorradorGlosario, TerminoGlosario
from pydantic import ValidationError

pytestmark = pytest.mark.unit


def fragmento(texto, id="chunk-1", pagina=3):
    return SimpleNamespace(texto=texto, chunk_id=id, pagina=pagina, seccion="Redes VCN")


def test_detector_ubicaciones_estables_y_sin_definiciones():
    chunks = [fragmento("Una VCN es una red virtual. Una **subred** es un rango de direcciones. Usar `CreateVcn`.")]
    candidatos, omitidos = detectar_candidatos(chunks)
    terminos = {t.termino_original: t for t in candidatos}
    assert {"VCN", "subred", "CreateVcn"} <= set(terminos)
    assert terminos["VCN"].es_identificador and terminos["CreateVcn"].es_identificador
    assert not terminos["subred"].es_identificador
    assert terminos["VCN"].ubicaciones[0].model_dump() == {"chunk_id": "chunk-1", "pagina": 3, "seccion": "Redes VCN"}
    assert all("definicion" not in t.model_dump() for t in candidatos) and omitidos == 0
    repetidos, _ = detectar_candidatos([*chunks, fragmento("VCN", "chunk-2", 4)])
    vcn = next(t for t in repetidos if t.termino_original == "VCN")
    assert vcn.id == terminos["VCN"].id and len(vcn.ubicaciones) == 2


def test_productos_comandos_apis_ipv_no_se_traducen():
    candidatos, _ = detectar_candidatos(
        [fragmento("Oracle Cloud Infrastructure, IPv4 e IPv6. `oci network vcn list` y `get_vcn()`. HTTP y API.")]
    )
    t = {c.termino_original: c for c in candidatos}
    assert {"Oracle Cloud Infrastructure", "IPv4", "IPv6", "oci network vcn list", "get_vcn()", "HTTP", "API"} <= set(t)
    assert all(t[n].es_identificador for n in t)


def test_limite_visible_no_oculta_candidatos_omitidos():
    candidatos, omitidos = detectar_candidatos([fragmento(" ".join(f"TERM{i}" for i in range(70)))])
    assert len(candidatos) == 64 and omitidos == 6


def test_limite_ubicaciones_y_cancelacion():
    candidatos, _ = detectar_candidatos([fragmento("VCN", f"chunk-{i}", i + 1) for i in range(12)])
    assert len(candidatos[0].ubicaciones) == 8

    def cancelar():
        raise RuntimeError("cancelado")

    with pytest.raises(RuntimeError):
        detectar_candidatos([fragmento("VCN")], chequear=cancelar)


@pytest.mark.parametrize(
    "extra", [{"definicion": "Inventada"}, {"traduccion": "Inventada"}, {"referencias": [{"chunk_id": "x"}]}]
)
def test_limitacion_del_llm_no_introduce_contenido(extra):
    with pytest.raises(ValidationError):
        BorradorGlosario.model_validate({"terminos": [{"id": "term", "estado": "sin_definicion_suficiente", **extra}]})


def test_limitacion_publica_cita_aparicion_y_protege_identificadores():
    datos = dict(
        id="term",
        estado="sin_definicion_suficiente",
        termino_original="VCN",
        es_identificador=True,
        referencias=[{"chunk_id": "original", "pagina": 3}],
        limitacion="Sin definición suficiente en la fuente.",
    )
    termino = TerminoGlosario.model_validate(datos)
    assert termino.definicion is None and termino.referencias[0].pagina == 3
    with pytest.raises(ValidationError):
        TerminoGlosario.model_validate({**datos, "traduccion": "Nombre inventado"})


def test_cache_operativa_sobrevive_reinicio_y_omite_fallos_y_borrados(tmp_path):
    ruta = tmp_path / "registro.db"
    db = RegistroOperativo(ruta)
    db.crear_workspace("ws", "codigo", dias_validez=30)
    db.crear_sesion("sesion", "ws", horas_validez=24)
    db.registrar_documento("doc", "ws", "VCN", DocumentStatus.READY, "a" * 64)
    db.encolar_job("job", "ws", "glosario", document_id="doc")
    db.vincular_cache_job("job", "cache")
    assert db.job_cache_glosario("ws", "cache")["job_id"] == "job"
    db.iniciar_job("job")
    db.finalizar_job("job", JobStatus.COMPLETED, resultado='{"verificado":true}')
    db.cerrar()
    db = RegistroOperativo(ruta)
    try:
        assert db.job_cache_glosario("ws", "cache")["status"] == "completed"
        assert db.job_cache_glosario("otro", "cache") is None
        db.encolar_job("fallo", "ws", "glosario", document_id="doc")
        db.vincular_cache_job("fallo", "clave-fallo")
        db.finalizar_job("fallo", JobStatus.REJECTED_QUALITY)
        assert db.job_cache_glosario("ws", "clave-fallo") is None
        db.retirar_documento("sesion", "doc")
        assert db.job_cache_glosario("ws", "cache") is None
    finally:
        db.cerrar()


def test_cache_no_reutiliza_glosario_con_reservas(tmp_path):
    db = RegistroOperativo(tmp_path / "registro.db")
    db.crear_workspace("ws", "codigo", dias_validez=30)
    db.registrar_documento("doc", "ws", "VCN", DocumentStatus.READY, "a" * 64)
    try:
        for job, estado in (("aprobado", "aprobada"), ("reservas", "requiere_revision")):
            db.encolar_job(job, "ws", "glosario", document_id="doc")
            db.vincular_cache_job(job, job)
            db.iniciar_job(job)
            resultado = '{"evaluacion_calidad": {"estado_evaluacion": "%s"}}' % estado
            db.finalizar_job(job, JobStatus.COMPLETED, resultado=resultado)
        assert db.job_cache_glosario("ws", "aprobado")["job_id"] == "aprobado"
        assert db.job_cache_glosario("ws", "reservas") is None
    finally:
        db.cerrar()
