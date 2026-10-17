"""Publicación administrativa de evidencia real, sin redactar contenido educativo."""

import hashlib
import json
from datetime import datetime, timezone

from app.demo.catalog import CLAVE_CATALOGO, guardar_blob
from app.demo.models import CatalogoEjemplos, DiagramaDemo, EjemploDemo, FuenteDemoGuardada
from app.demo.scenarios import ESCENARIOS
from app.schemas.chat import ChatAnswer
from app.schemas.documents_http import FuenteDocumentoResponse
from app.schemas.requests import GenerateRequest
from app.storage.provider import StorageNotFound, StorageUnavailable


def publicar(app, configuracion, fuentes, token, estado):
    """Copiar sólo originales autorizados y canónicos aprobados a snapshots públicos."""
    if (
        configuracion.mock_oci
        or configuracion.mock_gemini
        or getattr(app.state.generations.generador, "es_mock", True)
        or getattr(app.state.generations.verificador, "es_mock", True)
    ):
        raise ValueError("La publicación administrativa exige proveedores reales")
    storage, consulta, servicio = app.state.storage_provider, app.state.documents_query, app.state.generations
    snapshots, originales = {}, {}
    for fuente, contenido in fuentes:
        document_id = estado["fuentes"][fuente.document_id]["document_id"]
        aprobada = consulta.fuente_aprobada(token, document_id)
        huella = hashlib.sha256(contenido).hexdigest()
        if aprobada.hash != huella:
            raise ValueError("El original ya no coincide con el autorizado")
        chunks = consulta.chunks(token, document_id)
        imagenes = {}
        for chunk in chunks:
            if chunk.source_type == "image_description":
                imagen = consulta.imagen(token, document_id, chunk.chunk_id)
                imagenes[chunk.chunk_id] = guardar_blob(storage, imagen.contenido, "image/png")
        original_sha = guardar_blob(storage, contenido, "application/octet-stream")
        snapshot = FuenteDemoGuardada(
            document_id=document_id,
            document_hash=huella,
            original_sha256=original_sha,
            chunks=[
                FuenteDocumentoResponse(
                    document_id=document_id,
                    chunk_id=c.chunk_id,
                    texto=c.texto,
                    pagina=c.pagina,
                    seccion=c.seccion,
                    seccion_id=c.seccion_id,
                    source_type=c.source_type,
                    interpretacion_modelo=c.source_type == "image_description",
                )
                for c in chunks
            ],
            imagenes=imagenes,
        )
        snapshots[document_id] = guardar_blob(storage, snapshot.model_dump_json().encode())
        originales[document_id] = (fuente.archivo, huella)
    items = []
    for escenario in ESCENARIOS:
        registro = estado["escenarios"].get(escenario.id)
        if not registro or registro["status"] not in ("completed", "rejected_quality"):
            continue
        gid = registro["generation_id"]
        recibo = servicio.estado(token, gid)
        if registro["status"] == "rejected_quality":
            fila = app.state.db.obtener_generacion(gid)
            if recibo.status.value != "rejected_quality":
                raise ValueError("El diagnóstico no conserva su estado")
            document_id = fila["document_id"]
            nombre, huella = originales[document_id]
            items.append(
                EjemploDemo(
                    escenario=escenario.id,
                    generation_id=gid,
                    fuente=nombre,
                    document_hash=huella,
                    parametros=GenerateRequest.model_validate(escenario.solicitud(document_id)),
                    created_at=fila["creado_en"],
                    segundos=registro["segundos"],
                    status="rejected_quality",
                    diagnostico="La adaptación no superó la revisión de calidad. No se publica su borrador.",
                )
            )
            continue
        if recibo.status.value != "completed" or recibo.persistencia.provider != "oci":
            raise ValueError("El resultado no confirma persistencia real")
        paquete, contenido = servicio.catalogo.cargar(token, gid)
        document_id = paquete.documento_fuente.document_id
        nombre, huella = originales[document_id]
        solicitud = GenerateRequest.model_validate(escenario.solicitud(document_id))
        if (
            paquete.metadatos.perfil_aplicado != solicitud.perfil_destinatario
            or paquete.metadatos.formato_generado != solicitud.formato_salida
            or paquete.metadatos.nicho_sector != solicitud.nicho_sector
            or paquete.metadatos.idioma_salida != solicitud.idioma_salida
        ):
            raise ValueError("El paquete no conserva los parámetros del escenario")
        items.append(
            EjemploDemo(
                escenario=escenario.id,
                generation_id=gid,
                fuente=nombre,
                document_hash=huella,
                parametros=solicitud,
                created_at=paquete.created_at,
                segundos=registro["segundos"],
                status="completed",
                paquete_sha256=guardar_blob(storage, contenido),
                fuente_sha256=snapshots[document_id],
            )
        )
    rechazo = estado["complementos"].get("rechazo", {})
    if rechazo.get("status") == "rejected_quality":
        fila = app.state.db.obtener_generacion(rechazo["generation_id"])
        if fila["status"] != "rejected_quality":
            raise ValueError("El rechazo no está confirmado")
        document_id = fila["document_id"]
        nombre, huella = originales[document_id]
        solicitud = GenerateRequest.model_validate_json(fila["parametros_json"])
        items.append(
            EjemploDemo(
                escenario="evidencia-insuficiente",
                generation_id=fila["generation_id"],
                fuente=nombre,
                document_hash=huella,
                parametros=solicitud,
                created_at=fila["creado_en"],
                segundos=rechazo["segundos"],
                status="rejected_quality",
                diagnostico="La evidencia disponible no permitió aprobar esta adaptación. Acotá el alcance o aportá una fuente más completa.",
            )
        )
    diagrama = None
    registro = estado["complementos"].get("diagrama", {})
    if registro.get("status") == "completed":
        fila = app.state.db.obtener_job(registro["job_id"])
        respuesta = ChatAnswer.model_validate_json(fila["resultado"])
        if respuesta.estado == "respondida" and respuesta.evaluacion_calidad.verificacion_visual == "aprobada":
            chunks = {c.chunk_id: c for c in consulta.chunks(token, respuesta.document_id)}
            if any(chunks[r.chunk_id].source_type == "image_description" for r in respuesta.referencias):
                diagrama = DiagramaDemo(
                    respuesta=respuesta, fuente_sha256=snapshots[respuesta.document_id], segundos=registro["segundos"]
                )
    quiz = estado["complementos"].get("quiz") == [{"correcta": False}, {"correcta": True}]
    completo = (
        sum(i.status == "completed" for i in items) == len(ESCENARIOS)
        and rechazo.get("status") == "rejected_quality"
        and diagrama is not None
        and quiz
    )
    catalogo = CatalogoEjemplos(
        provider="oci",
        publicado_en=datetime.now(timezone.utc),
        completo=completo,
        items=items,
        diagrama=diagrama,
        quiz_verificado=quiz,
    )
    # No publicar un inventario vacío ni sustituir un catálogo completo por uno parcial.
    if not items:
        raise ValueError("No hay paquetes realmente aprobados para publicar")
    etag = None
    try:
        objetos = storage.list(CLAVE_CATALOGO, limit=1).items
        if not objetos or objetos[0].object_name != CLAVE_CATALOGO:
            raise StorageNotFound("Catálogo todavía no publicado")
        etag = objetos[0].etag
        previo = CatalogoEjemplos.model_validate_json(storage.get(CLAVE_CATALOGO, if_match=etag))
        if previo.completo and not completo:
            raise ValueError("La preparación parcial no reemplaza el catálogo completo")
    except StorageNotFound:
        pass
    contenido = catalogo.model_dump_json().encode()
    storage.upload(CLAVE_CATALOGO, contenido, content_type="application/json", crear_solo=etag is None, if_match=etag)
    if storage.get(CLAVE_CATALOGO) != contenido:
        raise StorageUnavailable("El catálogo publicado no fue confirmado")
    print(json.dumps({"publicado": True, "completo": completo, "ejemplos": len(items), "provider": "oci"}), flush=True)
    return catalogo
