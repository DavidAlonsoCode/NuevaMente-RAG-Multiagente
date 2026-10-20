"""Proveedores de almacenamiento: mock local (este issue) y factory (issue #04).

Aquí viven DOS cosas, y es fácil confundirlas:

1. `LocalMockStorageProvider`: la implementación de `StorageProvider` que
   guarda archivos locales bajo `.data/oci_mock_storage/`. Imita la semántica
   de OCI Object Storage (ETag, creación y actualización condicionales,
   paginación) usando solo la biblioteca
   estándar de Python, para que desarrollo y CI corran sin red, sin
   credenciales y sin costo.

2. `get_storage_provider()`: la FÁBRICA que decide qué proveedor usar
   leyendo la variable de entorno MOCK_OCI. Regla de oro de §8.2:

       MOCK_OCI=1  -> mock (solo desarrollo/CI).
       MOCK_OCI=0  -> proveedor real; si faltan credenciales, ERROR DE
                      ARRANQUE con mensaje accionable. NUNCA fallback al
                      mock: una falla real convertida en "éxito local" es
                      exactamente el falso positivo que §12.2 (criterio 10)
                      prohíbe demostrar.

   El proveedor OCI real (#14) valida credenciales, home region y el bucket
   preexistente; errores de configuración/red son visibles, sin fallback.

Cómo el mock imita a OCI, para quien lo lea después:

- Un `object_name` como "outputs/ws_1/gen_1/content.json" es un ARCHIVO en
  esa misma ruta relativa dentro del directorio base del mock. Los
  prefijos de §8.3 funcionan idéntico que en producción.
- El ETag es el hash SHA-256 del contenido: determinista (mismo contenido
  => misma huella) y suficiente para detectar escrituras concurrentes, que
  es lo que el control de versión de §7.2 necesita.
- Los metadatos (content_type, última modificación) viven en un índice
  `_meta.json` dentro del directorio base, aparte de los contenidos: así
  el "listado" no depende de estadísticas del sistema de archivos, que
  difieren entre Windows y Linux.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from tempfile import NamedTemporaryFile

from app.storage.object_keys import BUCKET_PRODUCCION as BUCKET_PRODUCCION
from app.storage.object_keys import validar_nombre as _validar_object_name
from app.storage.oci_real import OCIObjectStorageProvider as OCIObjectStorageProvider
from app.storage.provider import (
    ListedObject,
    StorageConfigError,
    StorageConflict,
    StorageInvalidName,
    StorageNotFound,
    StoragePage,
    StorageProvider,
    StoredObject,
)

# Variables del proveedor real; el mock conserva el contrato de nombres de §8.3.
VARIABLES_REQUERIDAS_REAL = ("OCI_BUCKET_NAME", "OCI_COMPARTMENT_ID", "OCI_REGION", "OCI_CONFIG_FILE")

# Un proceso escritor; instancias que apuntan al mismo bucket comparten exclusión.
_LOCKS: dict[Path, object] = {}
_LOCKS_GUARD = threading.Lock()


# con este decorador sincronizo las operaciones sobre el almacenamiento para garantizar exclusion mutua entre hilos
# utilizo un cerrojo reentrante (threading.RLock) para proteger el archivo de metadatos contra accesos concurrentes
def _serializado(metodo):
    @wraps(metodo)
    def ejecutar(self, *args, **kwargs):
        with self._lock:
            return metodo(self, *args, **kwargs)

    return ejecutar


# con esta funcion realizo la escritura de archivos en disco de forma completamente atomica y segura
# escribo primero en un archivo temporal (NamedTemporaryFile) y luego utilizo os.replace para evitar lecturas parciales
def _escribir_atomico(ruta: Path, contenido: bytes) -> None:
    temporal = None
    try:
        with NamedTemporaryFile(dir=ruta.parent, delete=False) as archivo:
            temporal = Path(archivo.name)
            archivo.write(contenido)
        os.replace(temporal, ruta)
    finally:
        if temporal is not None:
            temporal.unlink(missing_ok=True)


# con esta clase emulo localmente el comportamiento de oracle cloud infrastructure object storage (oci)
# guardo los archivos y un indice de metadatos json en el disco local para permitir desarrollo y pruebas sin conexion a la nube
class LocalMockStorageProvider(StorageProvider):
    """Proveedor de desarrollo: archivos locales con semántica idéntica a OCI.

    Constructor: `base_dir` permite apuntar el mock a un directorio
    específico (los tests usan un directorio temporal); por defecto usa
    `{DATA_DIR:-.data}/oci_mock_storage/`, la ubicación que §8.2 fija y que
    el .gitignore mantiene fuera de Git.
    """

    def __init__(self, base_dir: Path | str | None = None) -> None:
        if base_dir is None:
            # os.getenv lee variables de entorno; el segundo argumento es el
            # valor por defecto si no está definida (DATA_DIR del Apéndice A).
            data_dir = os.getenv("DATA_DIR", ".data")
            base_dir = Path(data_dir) / "oci_mock_storage"
        self.base_dir = Path(base_dir).resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)
        with _LOCKS_GUARD:
            self._lock = _LOCKS.setdefault(self.base_dir, threading.RLock())

    # ------------------------- helpers internos -------------------------

    def _ruta(self, object_name: str) -> Path:
        """Traduce object_name a ruta de archivo DENTRO del directorio base."""
        _validar_object_name(object_name)
        ruta = (self.base_dir / object_name).resolve()
        if not ruta.is_relative_to(self.base_dir):
            raise StorageInvalidName("La ruta escapa del directorio de almacenamiento")
        return ruta

    def _leer_meta(self) -> dict:
        """Carga el índice de metadatos (vacío si nunca se escribió nada)."""
        archivo = self.base_dir / "_meta.json"
        if not archivo.exists():
            return {}
        return json.loads(archivo.read_text(encoding="utf-8"))

    def _escribir_meta(self, meta: dict) -> None:
        _escribir_atomico(self.base_dir / "_meta.json", json.dumps(meta, ensure_ascii=False, indent=2).encode("utf-8"))

    @staticmethod
    def _etag_de(contenido: bytes) -> str:
        """Huella del contenido: SHA-256 truncado a 32 caracteres."""
        return hashlib.sha256(contenido).hexdigest()[:32]

    # ------------------------- operaciones -------------------------

    @_serializado
    # aqui almaceno un objeto binario o de texto calculando su huella sha256 como identificador de version (ETag)
    # aplico control de concurrencia optimista mediante el parametro if_match para impedir que se pisen cambios simultaneos
    def upload(
        self,
        object_name: str,
        content: bytes | str,
        *,
        content_type: str = "application/octet-stream",
        crear_solo: bool = False,
        if_match: str | None = None,
    ) -> StoredObject:
        # str se codifica a bytes una sola vez: de aquí en adelante todo es
        # binario, igual que en OCI (que no distingue texto de binario).
        contenido = content.encode("utf-8") if isinstance(content, str) else content

        if crear_solo and if_match is not None:
            raise StorageInvalidName("crear_solo e if_match son mutuamente excluyentes")

        ruta = self._ruta(object_name)
        existe = ruta.exists()

        if crear_solo and existe:
            raise StorageConflict(f"el objeto ya existe (crear_solo): {object_name}")
        if if_match is not None:
            if not existe:
                raise StorageNotFound(f"no existe el objeto a actualizar: {object_name}")
            etag_actual = self._etag_de(ruta.read_bytes())
            if etag_actual != if_match:
                # Alguien escribió entre nuestra lectura y esta escritura:
                # perder su cambio silenciosamente es lo que §7.2 prohíbe.
                raise StorageConflict(f"if_match={if_match} != etag actual {etag_actual}: {object_name}")

        ruta.parent.mkdir(parents=True, exist_ok=True)
        _escribir_atomico(ruta, contenido)

        etag = self._etag_de(contenido)
        meta = self._leer_meta()
        meta[object_name] = {
            "etag": etag,
            "size": len(contenido),
            "content_type": content_type,
            "last_modified": datetime.now(timezone.utc).isoformat(),
        }
        self._escribir_meta(meta)

        return StoredObject(
            object_name=object_name,
            etag=etag,
            # El prefijo mock:// es el "rótulo" que la UI detecta para
            # avisar «Almacenamiento local de desarrollo» (§8.2).
            uri=f"mock://{self.base_dir.name}/{object_name}",
            proveedor="mock",
        )

    @_serializado
    # con este metodo leo y devuelvo el contenido binario del objeto solicitado desde el almacenamiento
    # verifico la existencia del archivo en disco y compruebo la coincidencia del etag si el llamador lo requiere
    def get(self, object_name: str, *, if_match: str | None = None) -> bytes:
        ruta = self._ruta(object_name)
        if not ruta.exists():
            raise StorageNotFound(f"no existe el objeto: {object_name}")
        contenido = ruta.read_bytes()
        if if_match is not None and self._etag_de(contenido) != if_match:
            raise StorageConflict(f"if_match={if_match} != etag actual: {object_name}")
        return contenido

    def get_as_text(self, object_name: str, *, if_match: str | None = None) -> str:
        return self.get(object_name, if_match=if_match).decode("utf-8")

    @_serializado
    # aqui listen los objetos almacenados filtrando por prefijo de ruta y ordenandolos alfabeticamente
    # implemento navegacion paginada mediante un cursor opaco para no saturar la memoria con catalogos extensos
    def list(
        self,
        prefix: str = "",
        *,
        limit: int = 100,
        cursor: str | None = None,
    ) -> StoragePage:
        if limit < 1:
            raise ValueError(f"limit debe ser >= 1 (recibido: {limit})")

        nombres = sorted(
            nombre for nombre in self._leer_meta() if nombre.startswith(prefix) and (cursor is None or nombre > cursor)
        )

        pagina = nombres[:limit]
        resto = nombres[limit:]
        meta = self._leer_meta()

        items = [
            ListedObject(
                object_name=nombre,
                etag=meta[nombre]["etag"],
                size=meta[nombre]["size"],
                last_modified=datetime.fromisoformat(meta[nombre]["last_modified"]),
            )
            for nombre in pagina
        ]
        # El cursor es la última clave de la página; None cuando ya se
        # entregó todo.
        return StoragePage(items=items, next_cursor=pagina[-1] if resto else None)

    @_serializado
    # con esta funcion elimino un objeto tanto del sistema de archivos local como del registro de metadatos
    # aseguro la liberacion limpia del espacio cumpliendo las directivas de retirada de artefactos
    def delete(self, object_name: str) -> None:
        ruta = self._ruta(object_name)
        if not ruta.exists():
            raise StorageNotFound(f"no existe el objeto a borrar: {object_name}")
        ruta.unlink()
        meta = self._leer_meta()
        meta.pop(object_name, None)
        self._escribir_meta(meta)


# aqui implemento la fabrica principal (factory pattern) que selecciona el proveedor de almacenamiento adecuado
# si la variable de entorno mock_oci esta activada uso el almacenamiento local con control presupuestario
# en caso contrario instancio el cliente real de oci validando credenciales y region sin tolerar caidas silenciosas
def get_storage_provider(base_dir: Path | str | None = None, *, configuracion=None) -> StorageProvider:
    """Fábrica del proveedor de almacenamiento según MOCK_OCI (§8.2).

    Recibe Configuracion validada; sin inyección la construye al llamar,
    leyendo entorno y .env con las mismas reglas que el backend.

    - MOCK_OCI=1 -> LocalMockStorageProvider. Sin red, sin credenciales.
    - MOCK_OCI=0 -> OCI real: región y bucket verificados. Credenciales
      inválidas/faltantes y fallos del servicio son errores visibles.
      Nunca se devuelve un mock por un fallo real.
    """
    try:
        from app.config import Configuracion

        ajustes = configuracion or Configuracion()
        ajustes.validar_critico()
    except (ValueError, RuntimeError) as error:
        raise StorageConfigError(
            "Configuración inválida: production no admite mocks; revisar APP_ENV y credenciales OCI"
        ) from error
    if ajustes.mock_oci:
        from app.storage.budgeted import StoragePresupuestado
        from app.storage.oci_budget import PresupuestoOCI

        local = LocalMockStorageProvider(
            base_dir=base_dir if base_dir is not None else Path(ajustes.data_dir) / "oci_mock_storage"
        )
        return StoragePresupuestado(
            local,
            PresupuestoOCI(
                local.base_dir.parent / "oci_budget.sqlite3",
                solicitudes=ajustes.oci_max_requests_per_month,
                bytes_maximos=ajustes.oci_max_storage_bytes,
            ),
        )
    return OCIObjectStorageProvider(ajustes)
