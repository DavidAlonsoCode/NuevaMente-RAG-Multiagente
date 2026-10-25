"""Procedencia persistida y declaraciones originales, antes de normalizar modelos."""

import json

from app.schemas.errors import ErrorAplicacion, ErrorCode
from app.schemas.responses import DocumentoFuente


def _pares_unicos(pares):
    datos = {}
    for clave, valor in pares:
        if clave in datos:
            raise ValueError("El canónico contiene claves duplicadas")
        datos[clave] = valor
    return datos


def _constante_invalida(valor):
    raise ValueError("El canónico contiene una constante ajena a JSON")


def leer_declaracion(texto):
    datos = json.loads(texto, object_pairs_hook=_pares_unicos, parse_constant=_constante_invalida)
    if not isinstance(datos, dict):
        raise ValueError("El canónico no es un objeto")
    return datos


def _ids(valor):
    if not isinstance(valor, list) or any(not isinstance(item, str) or not item for item in valor):
        raise ValueError("Identidades de fuentes inválidas")
    if len(set(valor)) != len(valor):
        raise ValueError("Identidades de fuentes duplicadas")
    return set(valor)


def _referencias(valor):
    if isinstance(valor, dict):
        for clave, item in valor.items():
            if clave == "referencias":
                yield from item
            else:
                yield from _referencias(item)
    elif isinstance(valor, list):
        for item in valor:
            yield from _referencias(item)


def validar_procedencia(db, fila, paquete):
    """Devuelve versiones esperadas; nunca las reconstruye desde fuentes actuales.

    El único fallback es el canónico unidocumental histórico verificable: sus
    vínculos fijan la identidad y su fuente completa se contrasta con la vigente.
    No realiza I/O de almacenamiento ni proveedores; sirve también antes de enqueue.
    """
    try:
        if isinstance(paquete, dict):
            datos = paquete
        else:
            datos = paquete.model_dump(mode="json")
            # El serializer histórico omite esta lista cuando tiene un elemento.
            datos["documentos_fuente"] = [f.model_dump(mode="json") for f in paquete.documentos_fuente]
        ids = _ids(db.documentos_generacion(fila["generation_id"]))
        primario = DocumentoFuente.model_validate(datos["documento_fuente"])
        if not ids or fila["document_id"] not in ids or primario.document_id != fila["document_id"]:
            raise ValueError("El primario no corresponde a los vínculos")
        if datos["generation_id"] != fila["generation_id"]:
            raise ValueError("El paquete corresponde a otra generación")
        snapshot = db.snapshot_generacion(fila["generation_id"])
        if snapshot is None and len(ids) == 1:
            fuentes = [primario]
        elif isinstance(snapshot, list) and snapshot:
            fuentes = [DocumentoFuente.model_validate(f) for f in snapshot]
        else:
            raise ValueError("No hay snapshot verificable")
        esperadas = {f.document_id: f for f in fuentes}
        if len(esperadas) != len(fuentes) or set(esperadas) != ids or esperadas[primario.document_id] != primario:
            raise ValueError("El snapshot no corresponde a los vínculos o al primario")
        for vinculo in db.fuentes_vinculadas_generacion(fila["generation_id"]):
            fuente = esperadas[vinculo["document_id"]]
            if any(
                vinculo[campo] is not None and vinculo[campo] != getattr(fuente, campo) for campo in ("hash", "titulo")
            ):
                raise ValueError("La procedencia contradice la metadata guardada en los vínculos")
        declaradas = datos.get("documentos_fuente", [])
        if not isinstance(declaradas, list):
            raise ValueError("Fuentes declaradas inválidas")
        fuentes_paquete = [DocumentoFuente.model_validate(f) for f in declaradas]
        if not fuentes_paquete and len(ids) == 1:
            fuentes_paquete = [primario]
        por_id = {f.document_id: f for f in fuentes_paquete}
        if len(por_id) != len(fuentes_paquete) or por_id != esperadas:
            raise ValueError("El paquete omite, duplica o contradice fuentes persistidas")
        parametros = json.loads(fila["parametros_json"] or "{}")
        if parametros.get("document_id", fila["document_id"]) != fila["document_id"]:
            raise ValueError("Primario persistido discordante")
        for seleccion in (parametros, parametros.get("alcance", {})):
            if "document_ids" in seleccion and _ids(seleccion["document_ids"]) != ids:
                raise ValueError("Selección persistida discordante")
        alcance = datos["metadatos"]["alcance"]
        alcance_ids = _ids(alcance.get("document_ids", []))
        if (alcance_ids and alcance_ids != ids) or (len(ids) > 1 and not alcance_ids):
            raise ValueError("El alcance no corresponde al conjunto persistido")
        if len(ids) > 1 and alcance["tipo"] not in {"multi_documento", "espacio_completo"}:
            raise ValueError("Alcance unidocumental para múltiples fuentes")
        if len(ids) == 1 and alcance["tipo"] == "multi_documento":
            raise ValueError("Alcance multidocumental para una sola fuente")
        for ref in _referencias(datos):
            documento = ref.get("document_id")
            if (documento is not None and documento not in ids) or (len(ids) > 1 and documento is None):
                raise ValueError("Referencia sin fuente verificable del conjunto")
        return esperadas
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
        raise ErrorAplicacion(
            ErrorCode.INVALID_STATE, "El material no coincide con su procedencia persistida."
        ) from None
