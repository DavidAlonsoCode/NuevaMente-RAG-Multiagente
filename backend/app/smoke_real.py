"""Smoke manual #54: worker normal, OCI/Gemini reales y evidencia sin credenciales."""

import argparse
import hashlib
import io
import json
import re
import secrets
import sqlite3
import subprocess
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from pypdf import PdfReader

from app.config import Configuracion
from app.documents.preload import leer_fuentes
from app.jobs.manager import ContextoEjecucion
from app.jobs.runtime_lock import exclusividad_runtime
from app.schemas.documents import EntradaArchivoDocumento
from app.schemas.requests import GenerateRequest
from app.schemas.responses import PedagogicalOutput
from app.storage.object_keys import clave_original


def sha256(datos):
    return hashlib.sha256(datos).hexdigest()


def esperar(db, job_id, deadline):
    limite = time.monotonic() + deadline + 30
    while time.monotonic() < limite:
        job = db.obtener_job(job_id)
        if job and job["status"] not in ("running", "queued"):
            if job["status"] != "completed":
                raise RuntimeError("El trabajo real no terminó aprobado: " + (job["error_code"] or job["status"]))
            return job
        time.sleep(0.25)
    raise TimeoutError("El trabajo no confirmó su finalización")


def validar_exportaciones(paquete, archivos):
    """Abrir los formatos, no confiar sólo en extensión o cabecera HTTP."""
    canonico = PedagogicalOutput.model_validate_json(archivos["json"])
    if canonico != paquete or paquete.contenido_adaptado.tipo != "flashcards":
        raise ValueError("El smoke exige el mismo paquete aprobado de flashcards")
    md = archivos["md"].decode("utf-8")
    md_visible = re.sub(r"\\([_-])", r"\1", md)
    if not md.strip() or not all(r.chunk_id in md_visible for r in paquete.referencias):
        raise ValueError("Markdown incompleto o sin sus fuentes")
    pdf = PdfReader(io.BytesIO(archivos["pdf"]))
    if not pdf.pages or not all((p.extract_text() or "").strip() for p in pdf.pages):
        raise ValueError("El PDF no tiene páginas legibles")
    with zipfile.ZipFile(io.BytesIO(archivos["apkg"])) as archivo:
        miembro = archivo.getinfo("collection.anki2")
        if miembro.file_size > 20_000_000 or archivo.testzip() is not None:
            raise ValueError("El mazo está dañado o excede el límite")
        with sqlite3.connect(":memory:") as db:
            db.deserialize(archivo.read(miembro))
            notas = db.execute("SELECT flds FROM notes").fetchall()
            cartas = db.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
            esperadas = len(paquete.contenido_adaptado.items)
            if len(notas) != esperadas or cartas != esperadas or any(not all(n[0].split("\x1f")) for n in notas):
                raise ValueError("El mazo no conserva todas las tarjetas")
    return {"paginas_pdf": len(pdf.pages), "notas_anki": len(notas), "tarjetas_anki": cartas}


def _json(ruta, datos):
    ruta.write_text(json.dumps(datos, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def ejecutar(app, cfg, contenido, nombre, destino, *, checkpoint=None):
    storage, servicio, db = app.state.storage_provider, app.state.generations, app.state.db
    if cfg.mock_oci or cfg.mock_gemini or servicio.generador.es_mock or servicio.verificador.es_mock:
        raise ValueError("El smoke de entrega rechaza proveedores simulados")
    destino.mkdir(parents=True, exist_ok=True)
    presupuesto_antes = storage.presupuesto.resumen()
    solicitudes_antes = storage.solicitudes
    modelos = {cfg.gemini_embedding_model, cfg.gemini_generation_model, cfg.gemini_verification_model}
    cuotas = app.state.jobs_manager.cuotas
    restantes = {m: cuotas.disponibles_hoy(m) for m in modelos}
    sdk_attempts = {
        p._cliente._api_client._http_options.retry_options.attempts for p in (servicio.generador, servicio.verificador)
    }
    if sdk_attempts != {1}:
        raise ValueError("El SDK no tiene desactivados sus reintentos adicionales")
    informe = {
        "schema_version": "1.0",
        "estado": "iniciado",
        "fecha_utc": datetime.now(timezone.utc).isoformat(),
        "modo": "reutilizacion" if checkpoint else "generacion_nueva",
        "providers": {"oci": "real", "gemini": "real"},
        "timeout_llamada_segundos": ContextoEjecucion.timeout_por_llamada,
        "deadline_trabajo_segundos": cfg.generation_deadline_seconds,
        "sdk_attempts": next(iter(sdk_attempts)),
        "intentos_tecnicos_maximos": ContextoEjecucion.reintentos_transitorios + 1,
        "modelos": {
            "generacion": cfg.gemini_generation_model,
            "verificacion": cfg.gemini_verification_model,
            "embeddings": cfg.gemini_embedding_model,
        },
    }
    try:
        if checkpoint:
            workspace_id, gid = checkpoint["workspace_id"], checkpoint["generation_id"]
            if db.obtener_workspace(workspace_id) is None:
                raise ValueError("El espacio reutilizado ya no está vigente")
            token = secrets.token_urlsafe(32)
            db.crear_sesion(token, workspace_id, cfg.session_max_hours)
            fila = db.obtener_generacion(gid)
            if not fila or fila["workspace_id"] != workspace_id or fila["status"] != "completed":
                raise ValueError("El resultado reutilizado no está aprobado en este espacio")
            document_id = fila["document_id"]
        else:
            from app.session.manager import SessionManager

            sesion = SessionManager(db, storage, cfg.workspace_retention_days, cfg.session_max_hours).crear_espacio()
            token, workspace_id = sesion.token, sesion.workspace_id
            subida = app.state.documents_admission.admitir_archivo(
                token,
                contenido,
                EntradaArchivoDocumento(source_name=nombre, titulo="Smoke real VCN"),
                clave="smoke54:original",
            )
            esperar(db, subida.job_id, cfg.generation_deadline_seconds)
            document_id = subida.document_id
            solicitud = GenerateRequest(
                document_id=document_id,
                perfil_destinatario="principiante",
                formato_salida="flashcards",
                nicho_sector="general",
                nivel_detalle="didactico",
                idioma_salida="es",
            )
            fila = servicio.generar(token, solicitud, "smoke54:generacion")
            gid = fila["generation_id"]
            _json(destino / "reanudacion.json", {"workspace_id": workspace_id, "generation_id": gid})
            esperar(db, db.job_generacion(gid)["job_id"], cfg.generation_deadline_seconds)
        # Comprueba el original de OCI contra el archivo autorizado, no sólo contra metadata.
        original = clave_original(workspace_id, document_id)
        if storage.get(original) != contenido:
            raise ValueError("El original OCI no coincide con el documento autorizado")
        recibo = servicio.estado(token, gid)
        if recibo.status.value != "completed" or recibo.persistencia.provider != "oci":
            raise ValueError("La API no confirma persistencia OCI real")
        paquete, canonico = servicio.catalogo.cargar(token, gid)
        if paquete.documento_fuente.hash != sha256(contenido):
            raise ValueError("La generación no conserva el hash original")
        archivos = {f: servicio.exports.descargar(token, gid, f).contenido for f in ("json", "md", "pdf", "apkg")}
        if archivos["json"] != canonico:
            raise ValueError("La descarga JSON difiere del objeto canónico OCI")
        validaciones = validar_exportaciones(paquete, archivos)
        for formato, datos in archivos.items():
            (destino / ("resultado." + formato)).write_bytes(datos)
        _json(destino / "respuesta.json", recibo.model_dump(mode="json"))
        trabajo = db.job_generacion(gid)
        informe.update(
            estado="aprobado",
            generation_id=gid,
            document_id=document_id,
            original={"objeto": original, "sha256": sha256(contenido)},
            paquete={
                "objeto": paquete.almacenamiento_oci.objeto_id,
                "sha256": sha256(canonico),
                "evaluacion": paquete.evaluacion_calidad.model_dump(mode="json"),
            },
            duracion_trabajo_segundos=round(
                (
                    datetime.fromisoformat(trabajo["actualizado_en"]) - datetime.fromisoformat(trabajo["creado_en"])
                ).total_seconds(),
                3,
            ),
            exportaciones={f: {"bytes": len(d), "sha256": sha256(d)} for f, d in archivos.items()},
            apertura_estructural=validaciones,
        )
    except Exception as error:
        informe.update(estado="fallido", error_tipo=type(error).__name__)
        raise
    finally:
        despues = storage.presupuesto.resumen()
        informe["storage"] = {
            "solicitudes_sdk": storage.solicitudes - solicitudes_antes,
            "reservas_solicitudes": despues["solicitudes"] - presupuesto_antes["solicitudes"],
            "operaciones": {
                k: v - presupuesto_antes["operaciones"].get(k, 0) for k, v in despues["operaciones"].items()
            },
            "acumulado_mes": despues["solicitudes"],
            "limite_operaciones_usuario": despues["limite_operaciones_usuario"],
        }
        informe["solicitudes_gemini_nuevas"] = {m: restantes[m] - cuotas.disponibles_hoy(m) for m in modelos}
        _json(destino / "informe.json", informe)
    return informe


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--documents-dir", type=Path, default=Path("documents"))
    parser.add_argument("--data-dir", type=Path, default=Path(".data/smoke-real"))
    parser.add_argument("--output-dir", type=Path, default=Path(".data/smoke-real-evidencia"))
    parser.add_argument("--reuse", type=Path, help="JSON local con workspace_id y generation_id; nunca tokens")
    args = parser.parse_args(argv)
    fuente, contenido = leer_fuentes(args.documents_dir)[0]
    original = Configuracion()
    if original.mock_oci or original.mock_gemini:
        raise ValueError("Configurar MOCK_OCI=0 y MOCK_GEMINI=0 para la corrida real")
    original.validar_critico()
    if args.data_dir.resolve() == Path(original.data_dir).resolve():
        raise ValueError("Reservar un directorio distinto del registro habitual")
    cfg = original.model_copy(update={"data_dir": str(args.data_dir)})
    checkpoint = json.loads(args.reuse.read_text(encoding="utf-8")) if args.reuse else None
    from fastapi.testclient import TestClient

    with exclusividad_runtime(Path(original.data_dir)):
        from app.main import app as principal
        from app.main import crear_app

        storage = principal.state.storage_provider
        principal.state.db.cerrar()
        app = crear_app(cfg, proveedor_storage=storage)
        with TestClient(app):
            informe = ejecutar(app, cfg, contenido, fuente.archivo, args.output_dir, checkpoint=checkpoint)
            revision = subprocess.run(
                ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
            ).stdout.strip()
            informe["revision"] = revision
            informe["codigo_smoke_sha256"] = sha256(Path(__file__).read_bytes())
            informe["arbol_modificado"] = (
                subprocess.run(["git", "diff", "--quiet"], capture_output=True).returncode != 0
            )
            _json(args.output_dir / "informe.json", informe)
            print(
                json.dumps(
                    {
                        "estado": informe["estado"],
                        "generation_id": informe["generation_id"],
                        "storage": informe["storage"],
                    }
                ),
                flush=True,
            )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps({"smoke": "fallido", "error_tipo": type(error).__name__}), flush=True)
        raise SystemExit(1) from None
