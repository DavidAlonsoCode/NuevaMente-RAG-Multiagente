"""El mock de la aplicación reproduce los presupuestos de producción (#50)."""

import uuid

from app.storage.provider import StorageConflict, StorageInvalidName, StorageNotFound, StorageProvider


class StoragePresupuestado(StorageProvider):
    def __init__(self, proveedor, presupuesto):
        self.proveedor, self.presupuesto = proveedor, presupuesto
        # Inventario local: no consume solicitudes OCI reales.
        cursor = None
        while True:
            pagina = proveedor.list(limit=1000, cursor=cursor)
            presupuesto.incorporar_inventario((o.object_name, o.size) for o in pagina.items)
            cursor = pagina.next_cursor
            if cursor is None:
                break

    def upload(self, object_name, content, **opciones):
        cuerpo = content.encode("utf-8") if isinstance(content, str) else content
        token = uuid.uuid4().hex
        self.presupuesto.reservar_upload(token, object_name, len(cuerpo))
        incierta = False
        try:
            self.presupuesto.reservar_solicitud("put_object")
            incierta = True
            resultado = self.proveedor.upload(object_name, cuerpo, **opciones)
            self.presupuesto.confirmar_upload(token, object_name, len(cuerpo))
            return resultado
        except (StorageConflict, StorageNotFound, StorageInvalidName, ValueError):
            incierta = False
            raise
        finally:
            if not incierta:
                self.presupuesto.liberar_reserva(token)

    def get(self, object_name, *, if_match=None):
        self.presupuesto.reservar_solicitud("get_object")
        return self.proveedor.get(object_name, if_match=if_match)

    def get_as_text(self, object_name, *, if_match=None):
        return self.get(object_name, if_match=if_match).decode("utf-8")

    def list(self, prefix="", *, limit=100, cursor=None):
        self.presupuesto.reservar_solicitud("list_objects")
        return self.proveedor.list(prefix, limit=limit, cursor=cursor)

    def delete(self, object_name):
        self.presupuesto.reservar_solicitud("delete_object")
        self.proveedor.delete(object_name)
        self.presupuesto.confirmar_borrado(object_name)

    def cerrar(self):
        cerrar = getattr(self.proveedor, "cerrar", None)
        if cerrar:
            cerrar()
