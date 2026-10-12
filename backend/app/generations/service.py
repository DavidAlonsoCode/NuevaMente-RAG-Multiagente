"""Admisión idempotente y escritura confirmada; no posee otra cola ni retries."""

import hashlib
import json
import time
import uuid
from datetime import datetime, timedelta, timezone

from app.core.agents.critic import DependenciasCritic
from app.core.agents.graph import DependenciasOrquestacion, ejecutar_en_worker
from app.core.agents.graph_state import DependenciasGrafo, EstadoGrafo
from app.core.agents.researcher import DependenciasResearcher, researcher
from app.core.agents.writer import DependenciasWriter
from app.core.rag.rebuild import _workspace_vigente
from app.core.rag.retriever import RetrieverMMR
from app.documents.cursor import CursorDocumental
from app.generations.catalog import CatalogoGeneraciones
from app.jobs.manager import ContextoEjecucion, TrabajoCanceladoError
from app.schemas.enums import JobStatus
from app.schemas.errors import ErrorAplicacion, ErrorBody, ErrorCode
from app.schemas.requests import GenerateRequest
from app.schemas.responses import GenerationJobResponse, PedagogicalOutput, PersistenciaInfo
from app.storage.object_keys import clave_output
from app.storage.provider import StorageConflict, StorageUnavailable


class ServicioGeneraciones:
    def __init__(self, db, storage, consulta, gestor, configuracion, generador=None, verificador=None):
        self.db, self.storage, self.consulta, self.gestor = db, storage, consulta, gestor
        self.cfg, self.generador, self.verificador = configuracion, generador, verificador
        self.retriever = (
            RetrieverMMR(consulta.indice, configuracion=configuracion) if consulta.indice is not None else None
        )
        self.cursor = CursorDocumental(db.ruta_db.parent / "generation-cursor.key")
        self.catalogo = CatalogoGeneraciones(db, storage, consulta, configuracion.oci_bucket_name)
        self.db.purgar_aprobados_expirados()

    def _sesion(self, token):
        sesion = self.db.obtener_sesion(token)
        if sesion is None:
            raise ErrorAplicacion(ErrorCode.SESSION_INVALID, "La sesión no está disponible.")
        return sesion

    def _lectura(self, token):
        return ContextoEjecucion(
            "consulta_generacion",
            self._sesion(token)["workspace_id"],
            "consulta",
            time.monotonic() + 30,
            espacio_vigente=lambda: self.db.obtener_sesion(token) is not None,
        )

    def _propia_db(self, token, generation_id):
        ws = self._sesion(token)["workspace_id"]
        fila = self.db.obtener_generacion(generation_id)
        if fila is None or fila["workspace_id"] != ws:
            raise ErrorAplicacion(ErrorCode.NOT_FOUND, "Generación no encontrada.")
        return fila

    def _propia(self, token, generation_id):
        fila = self._propia_db(token, generation_id)
        self.consulta.detalle(token, fila["document_id"])
        try:
            manifest = _workspace_vigente(self.storage, self._lectura(token), fila["document_id"])
            if any(
                r.get("recurso_tipo") == "generation" and r.get("recurso_id") == generation_id
                for r in manifest.get("recursos_borrados", [])
            ):
                raise TrabajoCanceladoError()
        except TrabajoCanceladoError:
            self._sesion(token)
            raise ErrorAplicacion(ErrorCode.NOT_FOUND, "Generación no encontrada.") from None
        return self._propia_db(token, generation_id)

    def canonico(self, token, generation_id):
        return self.catalogo.canonico(token, generation_id)

    def estado(self, token, generation_id):
        fila = self._propia(token, generation_id)
        job = self.db.job_generacion(generation_id)
        ruta = f"/api/generations/{generation_id}"
        error = None
        if fila["status"] in {"failed", "cancelled", "rejected_quality"}:
            causa = fila["error_code"] or fila["status"].upper()
            codigo = ErrorCode.INVALID_STATE
            if causa == "STORAGE_UNAVAILABLE":
                codigo = ErrorCode.STORAGE_UNAVAILABLE
            elif causa in {"CUOTA_AGOTADA", "RATE_LIMITED", "PRESUPUESTO_AGOTADO"}:
                codigo = ErrorCode.RATE_LIMITED
            elif causa == "PROVIDER_UNAVAILABLE":
                codigo = ErrorCode.PROVIDER_UNAVAILABLE
            error = ErrorBody(code=codigo, message="El trabajo no se completó.", details={"causa": causa})
        activo = fila["status"] in {"queued", "running"}
        posicion = self.gestor.estado(job["job_id"]).get("posicion_cola") if activo and job else None
        return GenerationJobResponse(
            generation_id=generation_id,
            status=fila["status"],
            status_url=ruta,
            events_url=ruta + "/events",
            cancel_url=ruta + "/cancel" if activo else None,
            posicion_cola=posicion,
            error=error,
            contenido=self.canonico(token, generation_id) if fila["status"] == "completed" else None,
            persistencia=PersistenciaInfo(status_upload="completado", provider="mock" if self.cfg.mock_oci else "oci")
            if fila["status"] == "completed"
            else None,
        )

    def cancelar(self, token, generation_id):
        fila = self._propia(token, generation_id)
        if fila["status"] == "cancelled":
            return self.estado(token, generation_id)
        job = self.db.job_generacion(generation_id)
        if job is None or not self.gestor.cancelar(job["job_id"]):
            raise ErrorAplicacion(ErrorCode.INVALID_STATE, "El trabajo ya terminó.")
        return self.estado(token, generation_id)

    def pagina(self, token, *, cursor=None, limite=20, **filtros):
        ws = self._sesion(token)["workspace_id"]
        filtros = {k: v for k, v in filtros.items() if v is not None}
        scope = ws + ":generaciones:" + hashlib.sha256(json.dumps(filtros, sort_keys=True).encode()).hexdigest()
        despues = self.cursor.decodificar(cursor, scope) if cursor else None
        filas = []
        while len(filas) <= limite:
            lote = self.db.pagina_generaciones(ws, despues=despues, limit=limite + 1, **filtros)
            if not lote:
                break
            for fila in lote:
                despues = (fila["creado_en"], fila["generation_id"])
                try:
                    filas.append(self._propia(token, fila["generation_id"]))
                except ErrorAplicacion as exc:
                    if exc.error.code != ErrorCode.NOT_FOUND:
                        raise
                if len(filas) > limite:
                    break
            if len(lote) < limite + 1:
                break
        siguiente = None
        if len(filas) > limite:
            ultimo = filas[limite - 1]
            siguiente = self.cursor.codificar(scope, ultimo["creado_en"], ultimo["generation_id"])
        items = []
        for fila in filas[:limite]:
            propia = self._propia(token, fila["generation_id"])
            items.append(
                {
                    "generation_id": propia["generation_id"],
                    "document_id": propia["document_id"],
                    "status": propia["status"],
                    "created_at": propia["creado_en"],
                    "parametros": json.loads(propia["parametros_json"]) if propia["parametros_json"] else None,
                }
            )
        return {"items": items, "next_cursor": siguiente}

    def _vigente(self, contexto, generation_id, document_id, paquete=None):
        contexto.chequear()
        fila = self.db.obtener_generacion(generation_id)
        if fila is None or (fila["workspace_id"], fila["document_id"]) != (contexto.workspace_id, document_id):
            raise TrabajoCanceladoError()
        ws = _workspace_vigente(self.storage, contexto, document_id)
        if any(
            r.get("recurso_tipo") == "generation" and r.get("recurso_id") == generation_id
            for r in ws.get("recursos_borrados", [])
        ):
            raise TrabajoCanceladoError()
        documento = self.consulta.documento_grafo(contexto, document_id)
        if documento is None:
            raise TrabajoCanceladoError()
        if paquete is not None and documento.fuente != paquete.documento_fuente:
            raise ErrorAplicacion(ErrorCode.INVALID_STATE, "La fuente del aprobado cambió.")
        return documento

    def _persistir(self, paquete, contexto, *, retener=True):
        self._vigente(contexto, paquete.generation_id, paquete.documento_fuente.document_id, paquete)
        texto = paquete.model_dump_json()
        if retener:
            self.db.guardar_aprobado(
                paquete.generation_id, texto, (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
            )
        clave = clave_output(contexto.workspace_id, paquete.generation_id)
        try:
            self.storage.upload(clave, texto, crear_solo=True)
        except StorageConflict:
            if self.storage.get(clave) != texto.encode():
                raise StorageUnavailable("El objeto no corresponde al aprobado retenido") from None
        if self.storage.get(clave) != texto.encode():
            raise StorageUnavailable("La escritura no pudo confirmarse")
        self._vigente(contexto, paquete.generation_id, paquete.documento_fuente.document_id, paquete)
        return PersistenciaInfo(status_upload="completado", provider="mock" if self.cfg.mock_oci else "oci")

    def _trabajo(self, solicitud, generation_id, creado_en):
        def ejecutar(contexto):
            grafo = DependenciasGrafo(
                contexto,
                lambda ws, doc: self.consulta.documento_grafo(contexto, doc) if ws == contexto.workspace_id else None,
            )
            investigador = DependenciasResearcher(
                grafo, self.retriever, self.consulta.version_parser(contexto, solicitud.document_id)
            )
            deps = DependenciasOrquestacion(
                grafo,
                lambda estado: researcher(estado, investigador),
                DependenciasWriter(grafo, self.generador, self.cfg),
                DependenciasCritic(grafo, self.verificador, self.cfg, self.consulta.loader_critic(contexto)),
                self._persistir,
                bucket=self.cfg.oci_bucket_name,
                created_at=datetime.fromisoformat(creado_en),
            )
            return ejecutar_en_worker(
                EstadoGrafo(
                    workspace_id=contexto.workspace_id,
                    generation_id=generation_id,
                    document_id=solicitud.document_id,
                    parametros=solicitud,
                    deadline=contexto.deadline,
                ),
                deps,
            )

        return ejecutar

    def _terminal(self, generation_id, status):
        if status != JobStatus.FAILED:
            self.db.descartar_aprobado(generation_id)

    @staticmethod
    def _clave(clave):
        if clave is not None and (not clave.strip() or len(clave) > 200):
            raise ErrorAplicacion(ErrorCode.INVALID_REQUEST, "Idempotency-Key inválida.")
        return clave.strip() if clave is not None else None

    def generar(self, token, solicitud: GenerateRequest, clave=None):
        clave = self._clave(clave)
        self.consulta._listo(token, solicitud.document_id)
        cuerpo = json.dumps(solicitud.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
        if clave is not None:
            with self.gestor.admision_serializada():
                previo = self.db.idempotencia_consultar(self._sesion(token)["workspace_id"], "generate", clave, cuerpo)
            if previo is not None:
                if previo[0] == "conflicto":
                    raise ErrorAplicacion(ErrorCode.IDEMPOTENCY_CONFLICT, "La clave ya corresponde a otro cuerpo.")
                return self._propia(token, previo[1])
        if self.generador is None or self.verificador is None or self.retriever is None:
            raise ErrorAplicacion(
                ErrorCode.PROVIDER_UNAVAILABLE, "Generación requiere proveedores y cuotas explícitos."
            )
        ctx = self._lectura(token)
        documento = self.consulta.documento_grafo(ctx, solicitud.document_id)
        if documento is None:
            raise ErrorAplicacion(ErrorCode.NOT_FOUND, "Documento no encontrado.")
        if solicitud.alcance.tipo == "seccion" and solicitud.alcance.seccion_id not in documento.secciones:
            raise ErrorAplicacion(ErrorCode.VALIDATION_ERROR, "La sección no pertenece al documento.")
        generation_id = "gen_" + uuid.uuid4().hex
        with self.gestor.admision_serializada():
            try:
                with self.db.transaccion():
                    if clave is not None:
                        resultado, anterior = self.db.idempotencia_iniciar(
                            ctx.workspace_id, "generate", clave, cuerpo, recurso_id=generation_id
                        )
                        if resultado == "conflicto":
                            raise ErrorAplicacion(
                                ErrorCode.IDEMPOTENCY_CONFLICT, "La clave ya corresponde a otro cuerpo."
                            )
                        if resultado == "duplicada":
                            return self._propia_db(token, anterior)
                    self.db.registrar_generacion(generation_id, ctx.workspace_id, solicitud.document_id)
                    self.db.guardar_parametros_generacion(generation_id, solicitud.model_dump(mode="json"))
                fila = self.db.obtener_generacion(generation_id)
                self.gestor.enqueue(
                    ctx.workspace_id,
                    "generation",
                    self._trabajo(solicitud, generation_id, fila["creado_en"]),
                    generation_id=generation_id,
                    document_id=solicitud.document_id,
                    al_terminar=lambda status: self._terminal(generation_id, status),
                )
                if clave is not None:
                    self.db.idempotencia_completar(ctx.workspace_id, "generate", clave, generation_id)
            except Exception:
                self.db.abandonar_reserva_generacion(ctx.workspace_id, generation_id, "generate", clave, eliminar=True)
                raise
        return self._propia_db(token, generation_id)

    def persistir(self, token, generation_id, clave=None):
        self._propia(token, generation_id)
        clave = self._clave(clave)
        with self.gestor.admision_serializada():
            fila = self._propia_db(token, generation_id)
            if fila["status"] == "completed":
                return fila
            job = self.db.job_generacion(generation_id)
            if fila["status"] in ("queued", "running"):
                if job is not None and job["tipo"] == "persist":
                    return fila
                raise ErrorAplicacion(ErrorCode.INVALID_STATE, "El trabajo todavía está en ejecución.")
            texto = self.db.aprobado_retenido(generation_id)
            if fila["status"] != "failed" or texto is None:
                raise ErrorAplicacion(
                    ErrorCode.INVALID_STATE, "No hay un aprobado retenido vigente; iniciar otra generación."
                )
            paquete = PedagogicalOutput.model_validate_json(texto)
            operacion = "persist:" + generation_id
            if clave is not None:
                resultado, _ = self.db.idempotencia_iniciar(
                    fila["workspace_id"], operacion, clave, generation_id, recurso_id=generation_id
                )
                if resultado == "duplicada":
                    return fila
                if resultado == "conflicto":
                    raise ErrorAplicacion(ErrorCode.IDEMPOTENCY_CONFLICT, "Clave de persistencia incompatible.")

            def ejecutar(contexto):
                contexto.registrar_paso("persist", 1)
                self._persistir(paquete, contexto, retener=False)
                return paquete

            try:
                nuevo_job = None
                self.db.actualizar_generacion(generation_id, JobStatus.QUEUED)
                nuevo_job = self.gestor.enqueue(
                    fila["workspace_id"],
                    "persist",
                    ejecutar,
                    generation_id=generation_id,
                    document_id=fila["document_id"],
                    al_terminar=lambda status: self._terminal(generation_id, status),
                )
                if clave is not None:
                    self.db.idempotencia_completar(fila["workspace_id"], operacion, clave, generation_id)
            except Exception:
                if nuevo_job is None:
                    self.db.actualizar_generacion(
                        generation_id, JobStatus.FAILED, fila["error_code"], fila["error_message"]
                    )
                    self.db.abandonar_reserva_generacion(fila["workspace_id"], generation_id, operacion, clave)
                raise
        return self._propia_db(token, generation_id)
