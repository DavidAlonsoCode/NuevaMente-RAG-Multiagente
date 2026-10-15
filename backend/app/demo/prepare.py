"""Preparación administrativa con el worker existente, nunca JSON pedagógico manual."""

import argparse
import json
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path

from app.config import Configuracion
from app.demo.scenarios import ESCENARIOS, PREGUNTA_DIAGRAMA
from app.documents.preload import leer_fuentes
from app.schemas.documents import EntradaArchivoDocumento
from app.schemas.requests import GenerateRequest, QuizAnswerRequest


def guardar_estado(ruta, estado):
    temporal = ruta.with_suffix(".tmp")
    temporal.write_text(json.dumps(estado, ensure_ascii=False, indent=2), encoding="utf-8")
    temporal.replace(ruta)


def esperar(db, job_id, plazo):
    inicio = time.monotonic()
    while time.monotonic() - inicio < plazo + 30:
        job = db.obtener_job(job_id)
        if job and job["status"] not in ("queued", "running"):
            return job
        time.sleep(0.25)
    raise TimeoutError("El trabajo de preparación no confirmó su estado terminal")


def duracion(job):
    return round(
        (datetime.fromisoformat(job["actualizado_en"]) - datetime.fromisoformat(job["creado_en"])).total_seconds(), 3
    )


def preparar(app, configuracion, fuentes, ruta_estado, *, solo=None, recuperar_interrumpidos=False):
    """El checkpoint guarda IDs/mediciones, nunca tokens o códigos de recuperación."""
    db = app.state.db
    estado = (
        json.loads(ruta_estado.read_text(encoding="utf-8"))
        if ruta_estado.is_file()
        else {
            "fuentes": {},
            "escenarios": {},
            "complementos": {},
        }
    )
    if estado.get("workspace_id"):
        ws = db.obtener_workspace(estado["workspace_id"])
        if not ws or datetime.fromisoformat(ws["expira_en"]) <= datetime.now(timezone.utc):
            raise ValueError("El espacio de preparación ya no está vigente")
        token = secrets.token_urlsafe(32)
        # Mantenimiento local exclusivo del directorio reservado de preparación.
        db.crear_sesion(token, estado["workspace_id"], configuracion.session_max_hours)
    else:
        from app.session.manager import SessionManager

        sesion = SessionManager(
            db, app.state.storage_provider, configuracion.workspace_retention_days, configuracion.session_max_hours
        ).crear_espacio()
        token, estado["workspace_id"] = sesion.token, sesion.workspace_id
        guardar_estado(ruta_estado, estado)
    for fuente, contenido in fuentes:
        if fuente.document_id not in estado["fuentes"]:
            inicio = time.monotonic()
            admision = app.state.documents_admission.admitir_archivo(
                token,
                contenido,
                EntradaArchivoDocumento(source_name=fuente.archivo, titulo=fuente.titulo),
                clave="55:fuente:" + fuente.document_id,
            )
            estado["fuentes"][fuente.document_id] = {"document_id": admision.document_id, "job_id": admision.job_id}
            guardar_estado(ruta_estado, estado)
            esperar(db, admision.job_id, configuracion.generation_deadline_seconds)
            estado["fuentes"][fuente.document_id]["segundos_ingesta"] = round(time.monotonic() - inicio, 3)
            guardar_estado(ruta_estado, estado)
        document_id = estado["fuentes"][fuente.document_id]["document_id"]
        if app.state.documents_query.detalle(token, document_id)["estado"] != "ready":
            raise ValueError("La fuente requiere preparación completa antes de generar")
    for escenario in ESCENARIOS:
        if solo and escenario.id not in solo:
            continue
        document_id = estado["fuentes"][fuentes[escenario.fuente][0].document_id]["document_id"]
        entrada = GenerateRequest.model_validate(escenario.solicitud(document_id))
        clave = "55:" + escenario.id
        previo = estado["escenarios"].get(escenario.id, {})
        if recuperar_interrumpidos and previo:
            anterior = db.job_generacion(previo["generation_id"])
            if anterior["error_code"] == "INTERRUPTED":
                clave += ":recuperacion"
                estado.setdefault("intentos_interrumpidos", {})[escenario.id] = {
                    **previo,
                    "segundos": duracion(anterior),
                    "error_code": "INTERRUPTED",
                }
        if previo.get("recuperado"):
            clave += ":recuperacion"
        fila = app.state.generations.generar(token, entrada, clave)
        gid = fila["generation_id"]
        job = db.job_generacion(gid)
        job = esperar(db, job["job_id"], configuracion.generation_deadline_seconds)
        resultado = app.state.generations.estado(token, gid)
        estado["escenarios"][escenario.id] = {
            "generation_id": gid,
            "status": resultado.status.value,
            "segundos": duracion(job),
            "recuperado": clave.endswith(":recuperacion"),
        }
        guardar_estado(ruta_estado, estado)
        print(json.dumps({"escenario": escenario.id, **estado["escenarios"][escenario.id]}), flush=True)
    if solo:
        return token, estado
    if estado["escenarios"]["vcn-quiz"]["status"] == "completed":
        gid = estado["escenarios"]["vcn-quiz"]["generation_id"]
        pregunta = app.state.generations.canonico(token, gid).contenido_adaptado.preguntas[0]
        incorrecta = next(o.option_id for o in pregunta.opciones if o.option_id != pregunta.correct_option_id)
        pruebas = []
        for etiqueta, opcion in (("incorrecta", incorrecta), ("correcta", pregunta.correct_option_id)):
            feedback = app.state.progress.responder(
                token, gid, QuizAnswerRequest(question_id=pregunta.id, option_id=opcion, event_id="55-quiz-" + etiqueta)
            )
            pruebas.append({"correcta": feedback.correcta})
        estado["complementos"]["quiz"] = pruebas
    if "diagrama" not in estado["complementos"]:
        from app.schemas.chat import ChatRequest

        document_id = estado["fuentes"][fuentes[0][0].document_id]["document_id"]
        inicio = time.monotonic()
        job_id = app.state.chat.encolar(
            token,
            ChatRequest(
                document_id=document_id,
                pregunta=PREGUNTA_DIAGRAMA,
                perfil_destinatario="lider_tecnico",
                idioma_salida="es",
            ),
            "55:diagrama",
        )
        job = esperar(db, job_id, configuracion.generation_deadline_seconds)
        estado["complementos"]["diagrama"] = {
            "job_id": job_id,
            "status": job["status"],
            "segundos": round(time.monotonic() - inicio, 3),
        }
    if "rechazo" not in estado["complementos"]:
        document_id = estado["fuentes"][fuentes[1][0].document_id]["document_id"]
        secciones = app.state.documents_query.secciones(token, document_id)
        debil = next(s for s in secciones if "glosario" in s["titulo"].lower())
        solicitud = ESCENARIOS[6].solicitud(document_id)
        solicitud["alcance"] = {"tipo": "seccion", "seccion_id": debil["seccion_id"]}
        solicitud["nivel_detalle"] = "tecnico_profundo"
        inicio = time.monotonic()
        fila = app.state.generations.generar(token, GenerateRequest.model_validate(solicitud), "55:rechazo")
        esperar(db, db.job_generacion(fila["generation_id"])["job_id"], configuracion.generation_deadline_seconds)
        estado["complementos"]["rechazo"] = {
            "generation_id": fila["generation_id"],
            "status": db.obtener_generacion(fila["generation_id"])["status"],
            "segundos": round(time.monotonic() - inicio, 3),
        }
    guardar_estado(ruta_estado, estado)
    return token, estado


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--documents-dir", type=Path, default=Path("documents"))
    parser.add_argument("--data-dir", type=Path, default=Path(".data/issue55-real"))
    parser.add_argument("--generate-only", action="store_true")
    parser.add_argument("--publish-only", action="store_true")
    parser.add_argument("--solo", action="append", choices=[s.id for s in ESCENARIOS])
    parser.add_argument("--recuperar-interrumpidos", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    fuentes = leer_fuentes(args.documents_dir)
    if args.check:
        print(json.dumps({"fuentes": len(fuentes), "escenarios": len(ESCENARIOS), "publicado": False}))
        return 0
    original = Configuracion()
    configuracion = original.model_copy(update={"data_dir": str(args.data_dir)})
    if configuracion.mock_oci or configuracion.mock_gemini:
        raise ValueError("La preparación de entrega exige OCI y Gemini reales")
    configuracion.validar_critico()
    args.data_dir.mkdir(parents=True, exist_ok=True)
    from fastapi.testclient import TestClient

    from app.jobs.runtime_lock import exclusividad_runtime

    if args.data_dir.resolve() == Path(original.data_dir).resolve():
        raise ValueError("Usar un directorio reservado para la preparación")
    # Compartir el presupuesto OCI vigente; nunca reiniciar el contador de costos.
    with exclusividad_runtime(Path(original.data_dir)):
        from app.main import app as principal
        from app.main import crear_app

        storage = principal.state.storage_provider
        principal.state.db.cerrar()
        app = crear_app(configuracion=configuracion, proveedor_storage=storage)
        with TestClient(app):
            ruta = args.data_dir / "preparacion.json"
            if args.publish_only:
                estado = json.loads(ruta.read_text(encoding="utf-8"))
                token = secrets.token_urlsafe(32)
                app.state.db.crear_sesion(token, estado["workspace_id"], configuracion.session_max_hours)
            else:
                token, estado = preparar(
                    app,
                    configuracion,
                    fuentes,
                    ruta,
                    solo=args.solo,
                    recuperar_interrumpidos=args.recuperar_interrumpidos,
                )
            if not args.generate_only:
                from app.demo.publish import publicar

                catalogo = publicar(app, configuracion, fuentes, token, estado)
                if not catalogo.completo:
                    return 2
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps({"preparacion": "fallida", "error_tipo": type(error).__name__}), flush=True)
        # Ubicación del fallo, sin mensajes del SDK ni variables/credenciales.
        import traceback

        for marco in traceback.extract_tb(error.__traceback__):
            print(json.dumps({"archivo": Path(marco.filename).name, "linea": marco.lineno, "funcion": marco.name}))
        raise SystemExit(1) from None
