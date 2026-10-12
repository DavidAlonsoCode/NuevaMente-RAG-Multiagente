"""Cursores firmados, ligados al workspace y a un ancla independiente del recurso."""

import base64
import binascii
import hashlib
import hmac
import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path

from app.schemas.errors import ErrorAplicacion, ErrorCode
from app.storage.provider import StorageUnavailable


class CursorDocumental:
    def __init__(self, ruta: Path):
        # Una clave privada independiente de tokens/códigos de recuperación.
        # Se conserva en el mismo volumen local que el registro, sin migrarlo.
        # Publicar de forma exclusiva después de fsync: ningún proceso ve un
        # archivo de destino parcial, ni se reemplaza una clave existente.
        temporal = None
        try:
            if not ruta.exists():
                fd, nombre = tempfile.mkstemp(prefix=".document-cursor-", dir=ruta.parent)
                temporal = Path(nombre)
                with os.fdopen(fd, "wb") as archivo:
                    os.chmod(temporal, 0o600)
                    archivo.write(os.urandom(32))
                    archivo.flush()
                    os.fsync(archivo.fileno())
                try:
                    if os.name == "nt":
                        # En Windows rename falla si el destino existe; no
                        # requiere permisos para crear enlaces físicos.
                        os.rename(temporal, ruta)
                    else:
                        os.link(temporal, ruta)
                except FileExistsError:
                    pass
        except OSError as error:
            raise StorageUnavailable("No se pudo publicar la clave de paginación") from error
        finally:
            if temporal is not None:
                temporal.unlink(missing_ok=True)
        try:
            self._clave = ruta.read_bytes()
        except OSError as error:
            raise StorageUnavailable("No se pudo cargar la clave de paginación") from error
        if len(self._clave) != 32:
            raise StorageUnavailable("La clave de paginación no es verificable")

    def codificar(self, workspace_id: str, fecha: str, document_id: str) -> str:
        cuerpo = json.dumps([1, workspace_id, fecha, document_id], separators=(",", ":")).encode()
        firma = hmac.digest(self._clave, cuerpo, hashlib.sha256)
        return base64.urlsafe_b64encode(cuerpo + firma).decode().rstrip("=")

    def decodificar(self, cursor: str, workspace_id: str) -> tuple[str, str]:
        try:
            if not cursor or len(cursor) > 1024:
                raise ValueError()
            datos = base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True)
            cuerpo, firma = datos[:-32], datos[-32:]
            if not hmac.compare_digest(firma, hmac.digest(self._clave, cuerpo, hashlib.sha256)):
                raise ValueError()
            valores = json.loads(cuerpo)
            if not isinstance(valores, list) or len(valores) != 4:
                raise ValueError()
            version, ws, fecha, doc = valores
            if type(version) is not int or version != 1 or ws != workspace_id:
                raise ValueError()
            if not isinstance(fecha, str) or not isinstance(doc, str) or not 1 <= len(doc) <= 255:
                raise ValueError()
            if datetime.fromisoformat(fecha).utcoffset() is None:
                raise ValueError()
            return fecha, doc
        except (ValueError, TypeError, binascii.Error, UnicodeError):
            raise ErrorAplicacion(ErrorCode.INVALID_REQUEST, "Cursor documental inválido para este espacio.") from None


class CursorDemo(CursorDocumental):
    """Dominio firmado distinto; ordena identidades, sin fabricar fechas."""

    def codificar(self, document_id: str) -> str:
        cuerpo = json.dumps([1, "biblioteca-demo", document_id], separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(cuerpo + hmac.digest(self._clave, cuerpo, hashlib.sha256)).decode().rstrip("=")

    def decodificar(self, cursor: str) -> str:
        try:
            if not cursor or len(cursor) > 1024:
                raise ValueError()
            datos = base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True)
            cuerpo, firma = datos[:-32], datos[-32:]
            if not hmac.compare_digest(firma, hmac.digest(self._clave, cuerpo, hashlib.sha256)):
                raise ValueError()
            valores = json.loads(cuerpo)
            if not isinstance(valores, list) or len(valores) != 3:
                raise ValueError()
            version, ambito, documento = valores
            if (
                type(version) is not int
                or version != 1
                or ambito != "biblioteca-demo"
                or not isinstance(documento, str)
                or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", documento) is None
            ):
                raise ValueError()
            return documento
        except (ValueError, TypeError, binascii.Error, UnicodeError):
            raise ErrorAplicacion(ErrorCode.INVALID_REQUEST, "Cursor demo inválido.") from None
