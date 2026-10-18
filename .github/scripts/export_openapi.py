"""Snapshot OpenAPI del runtime (#59, etapa 1; no acredita fidelidad contractual).

Desde la raíz: python .github/scripts/export_openapi.py [--check | --validate].
--output permite comprobar copias sin tocar docs/openapi.json. El proceso hijo
importa la instancia global real, sin lifespan, con SQLite/storage temporales.
La validación es estructural y offline; conserva las divergencias conocidas de
metadatos del runtime. El viewer y los recorridos locales se comprueban aparte; la aceptación pública corresponde a #133.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT = ROOT / "docs" / "openapi.json"


def serializar(documento: dict) -> bytes:
    return (json.dumps(documento, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def _exportar_app() -> None:
    """Sólo se invoca en el hijo aislado; no construye una segunda aplicación."""
    sys.path.insert(0, str(ROOT / "backend"))
    from app.main import app

    try:
        sys.stdout.buffer.write(serializar(app.openapi()))
    finally:
        try:
            app.state.storage_provider.cerrar()
        finally:
            app.state.db.cerrar()


def generar() -> bytes:
    # No heredar configuración de app, credenciales, PYTHONPATH, proxies ni HOME.
    # -I también excluye user-site; cwd vacío evita que Settings lea el .env del checkout.
    sistema = {
        k: v for k, v in os.environ.items() if k.upper() in {"SYSTEMROOT", "WINDIR", "COMSPEC", "PATH", "PATHEXT"}
    }
    with TemporaryDirectory(prefix="nuevamente-openapi-") as temporal:
        entorno = {
            **sistema,
            "APP_ENV": "ci",
            "MOCK_OCI": "1",
            "MOCK_GEMINI": "1",
            "DATA_DIR": str(Path(temporal) / "data"),
            "HOME": temporal,
            "USERPROFILE": temporal,
            "APPDATA": temporal,
            "LOCALAPPDATA": temporal,
            "TMP": temporal,
            "TEMP": temporal,
            "TMPDIR": temporal,
        }
        resultado = subprocess.run(
            [
                sys.executable,
                "-I",
                "-B",
                "-c",
                'import runpy, sys; runpy.run_path(sys.argv[1])["_exportar_app"]()',
                str(Path(__file__).resolve()),
            ],
            cwd=temporal,
            env=entorno,
            capture_output=True,
            timeout=90,
            check=True,
        )
    return resultado.stdout


def validar(documento: dict) -> None:
    """Validador estándar 3.1, sin handlers de red/archivos y con todos los $ref comprobados."""
    from jsonschema.exceptions import ValidationError
    from jsonschema_path import SchemaPath
    from openapi_spec_validator import OpenAPIV31SpecValidator
    from referencing import Registry, Resource
    from referencing.exceptions import Unresolvable
    from referencing.jsonschema import DRAFT202012

    if not isinstance(documento, dict) or documento.get("openapi") != "3.1.0":
        raise ValueError("Se esperaba un documento OpenAPI 3.1.0.")

    resolutor = (
        Registry().with_resource("", Resource.from_contents(documento, default_specification=DRAFT202012)).resolver()
    )

    def referencias(valor):
        if isinstance(valor, dict):
            for clave, item in valor.items():
                if clave in {"$ref", "$dynamicRef"}:
                    if not isinstance(item, str) or not item.startswith("#"):
                        raise ValueError("El snapshot debe ser autocontenido: se requieren referencias locales.")
                    try:
                        resolutor.lookup(item)
                    except Unresolvable as error:
                        raise ValueError(f"Referencia local no resuelta: {item}") from error
                referencias(item)
        elif isinstance(valor, list):
            for item in valor:
                referencias(item)

    referencias(documento)
    try:
        # Los metaschemas del validador están incluidos en el paquete instalado.
        OpenAPIV31SpecValidator(SchemaPath.from_dict(documento, handlers={})).validate()
    except (ValidationError, Unresolvable) as error:
        raise ValueError(f"OpenAPI inválido: {getattr(error, 'message', str(error))}") from error


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modos = parser.add_mutually_exclusive_group()
    modos.add_argument("--check", action="store_true", help="Detectar drift sin escribir el snapshot.")
    modos.add_argument("--validate", action="store_true", help="Validar el archivo y sus referencias, sin generar.")
    parser.add_argument("--output", type=Path, default=SNAPSHOT, help="Snapshot a exportar/comprobar/validar.")
    args = parser.parse_args()
    destino = args.output.resolve()
    comando = [sys.executable, str(Path(__file__).resolve()), "--output", str(destino)]
    regenerar = subprocess.list2cmdline(comando) if os.name == "nt" else shlex.join(comando)
    try:
        if args.check or args.validate:
            existente = destino.read_bytes()
            validar(json.loads(existente))
            if args.validate:
                print("OpenAPI 3.1.0 y referencias locales válidos (validación estructural offline).")
                return 0
        nuevo = generar()
        validar(json.loads(nuevo))
        if args.check:
            if existente != nuevo:
                raise ValueError("Drift: el snapshot difiere del OpenAPI generado por la aplicación.")
            print("Snapshot vigente: coincide con una generación nueva del runtime.")
        else:
            destino.write_bytes(nuevo)
            print(f"OpenAPI exportado: {destino}")
        return 0
    except (OSError, ValueError, ImportError, subprocess.SubprocessError) as error:
        print(f"No se pudo completar OpenAPI: {error}", file=sys.stderr)
        if isinstance(error, subprocess.CalledProcessError):
            print(error.stderr.decode("utf-8", errors="replace")[-6000:], file=sys.stderr)
        print(f"Regenerar: {regenerar}", file=sys.stderr)
        print("Requiere las dependencias de backend/requirements.txt y requirements-dev.txt.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
