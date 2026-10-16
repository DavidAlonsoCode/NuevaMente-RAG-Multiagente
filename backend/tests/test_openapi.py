"""Etapa 1 de #59: procesos reales, artefacto versionado y negativos en copias temporales."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from openapi_spec_validator import OpenAPIV31SpecValidator

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]
EXPORTADOR = ROOT / ".github/scripts/export_openapi.py"
SNAPSHOT = ROOT / "docs/openapi.json"


def ejecutar(*argumentos, script=EXPORTADOR, cwd=ROOT, env=None):
    return subprocess.run(
        [sys.executable, "-B", str(script), *map(str, argumentos)],
        cwd=cwd,
        env=env,
        capture_output=True,
        timeout=120,
    )


def exitoso(resultado):
    assert resultado.returncode == 0, (resultado.stdout + resultado.stderr).decode("utf-8", errors="replace")


@pytest.fixture(scope="module")
def exportaciones(tmp_path_factory):
    carpeta = tmp_path_factory.mktemp("openapi-exportaciones")
    salidas = [carpeta / nombre for nombre in ("primera.json", "segunda.json")]
    for salida in salidas:
        exitoso(ejecutar("--output", salida))
    return [salida.read_bytes() for salida in salidas]


def test_reproducible_entre_procesos_independientes_y_serializacion(exportaciones):
    primera, segunda = exportaciones
    assert primera == segunda
    assert primera.endswith(b"\n") and not primera.endswith(b"\n\n") and b"\r" not in primera
    documento = json.loads(primera.decode("utf-8"))
    assert primera == (json.dumps(documento, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    assert documento["openapi"] == "3.1.0"
    assert "servers" not in documento


def test_snapshot_coincide_con_generacion_nueva(exportaciones):
    assert SNAPSHOT.read_bytes() == exportaciones[0]


def test_snapshot_igual_a_app_real_sin_usar_exportador(tmp_path):
    # Comprobación independiente: ni importa el exportador ni usa sus helpers/env.
    # La instancia global y sus recursos son reales; no se entra al lifespan.
    codigo = """
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from app.main import app
from app.jobs.store import RegistroOperativo
from app.storage.budgeted import StoragePresupuestado
from app.storage.oci_storage import LocalMockStorageProvider
try:
    assert isinstance(app.state.db, RegistroOperativo)
    assert isinstance(app.state.storage_provider, StoragePresupuestado)
    assert isinstance(app.state.storage_provider.proveedor, LocalMockStorageProvider)
    assert app.state.db.ruta_db == Path.cwd() / "operativo.db"
    assert app.state.db.ruta_db.is_file()
    assert app.state.storage_provider.base_dir.is_dir()
    assert app.state.vectorstore is None and app.state.generations is None
    sys.stdout.buffer.write(json.dumps(app.openapi(), ensure_ascii=False).encode("utf-8"))
finally:
    try:
        app.state.storage_provider.cerrar()
    finally:
        app.state.db.cerrar()
"""
    entorno = {
        k: v for k, v in os.environ.items() if k.upper() in {"SYSTEMROOT", "WINDIR", "COMSPEC", "PATH", "PATHEXT"}
    }
    entorno.update(
        APP_ENV="ci",
        MOCK_OCI="1",
        MOCK_GEMINI="1",
        DATA_DIR=str(tmp_path),
        HOME=str(tmp_path),
        USERPROFILE=str(tmp_path),
    )
    resultado = subprocess.run(
        [sys.executable, "-I", "-B", "-c", codigo, str(ROOT / "backend")],
        cwd=tmp_path,
        env=entorno,
        capture_output=True,
        timeout=90,
    )
    exitoso(resultado)
    assert json.loads(resultado.stdout) == json.loads(SNAPSHOT.read_bytes())


def test_check_vigente_no_escribe(tmp_path):
    copia = tmp_path / "vigente.json"
    original = SNAPSHOT.read_bytes()
    copia.write_bytes(original)
    antes = copia.stat().st_mtime_ns
    resultado = ejecutar("--check", "--output", copia)
    exitoso(resultado)
    assert b"Snapshot vigente" in resultado.stdout
    assert copia.read_bytes() == original and copia.stat().st_mtime_ns == antes


@pytest.mark.parametrize("caso", ["drift", "ausente", "json_invalido"])
def test_check_falla_y_conserva_archivo_comprobado(tmp_path, caso):
    copia = tmp_path / "comprobar.json"
    original_repo = SNAPSHOT.read_bytes()
    if caso == "drift":
        documento = json.loads(original_repo)
        documento["info"]["title"] = "Otra API válida pero desactualizada"
        OpenAPIV31SpecValidator(documento).validate()
        copia.write_text(json.dumps(documento), encoding="utf-8")
    elif caso == "json_invalido":
        copia.write_bytes(b'{"openapi":')
    antes = copia.read_bytes() if copia.exists() else None
    mtime = copia.stat().st_mtime_ns if copia.exists() else None
    resultado = ejecutar("--check", "--output", copia)
    assert resultado.returncode == 1
    assert b"Regenerar:" in resultado.stderr and b"export_openapi.py" in resultado.stderr
    assert str(copia).encode() in resultado.stderr
    if caso == "drift":
        assert b"Drift:" in resultado.stderr
    if antes is None:
        assert not copia.exists()
    else:
        assert copia.read_bytes() == antes and copia.stat().st_mtime_ns == mtime
    assert SNAPSHOT.read_bytes() == original_repo


def test_aislamiento_de_env_personal_adverso_y_checkout_con_dotenv(tmp_path, exportaciones):
    checkout = tmp_path / "checkout"
    script = checkout / ".github/scripts/export_openapi.py"
    script.parent.mkdir(parents=True)
    shutil.copyfile(EXPORTADOR, script)
    shutil.copytree(ROOT / "backend/app", checkout / "backend/app", ignore=shutil.ignore_patterns("__pycache__"))
    privado = tmp_path / "datos-personales"
    privado.mkdir()
    testigo = privado / "conservar.txt"
    testigo.write_bytes(b"datos previos")
    dotenv = (
        "APP_ENV=production\nMOCK_OCI=0\nMOCK_GEMINI=0\n"
        "EMBEDDING_QUOTA_RPM=no-es-un-entero\nGOOGLE_API_KEY=secreto-dotenv-sintetico\n"
        f"DATA_DIR={privado.as_posix()}\n"
    )
    (checkout / ".env").write_text(dotenv, encoding="utf-8")
    (tmp_path / ".env").write_text(dotenv, encoding="utf-8")
    entorno = {
        **os.environ,
        "APP_ENV": "production",
        "MOCK_OCI": "0",
        "MOCK_GEMINI": "0",
        "DATA_DIR": str(privado),
        "GOOGLE_API_KEY": "secreto-entorno-sintetico",
        "OCI_CONFIG_FILE": str(tmp_path / "credenciales-inexistentes"),
        "EMBEDDING_QUOTA_RPM": "invalida",
        "CORS_ORIGINS": "https://dominio-personal.invalid",
        "PYTHONPATH": str(checkout),
    }
    # Un import app desde PYTHONPATH debe fallar; el hijo sólo debe cargar backend/app.
    (checkout / "app.py").write_text('raise RuntimeError("PYTHONPATH personal cargado")\n', encoding="utf-8")
    salida = tmp_path / "aislado.json"
    exitoso(ejecutar("--output", salida, script=script, cwd=tmp_path, env=entorno))
    assert salida.read_bytes() == exportaciones[0]
    assert list(privado.iterdir()) == [testigo] and testigo.read_bytes() == b"datos previos"
    assert not (checkout / ".data").exists() and not (tmp_path / ".data").exists()
    assert (checkout / ".env").read_text(encoding="utf-8") == dotenv
    assert b"secreto-" not in salida.read_bytes() and b"dominio-personal" not in salida.read_bytes()


def test_validacion_estandar_y_referencias_sin_red(tmp_path):
    # Auditoría de sockets durante la validación real; no sustituye el validador.
    codigo = """
import runpy, sys
def sin_red(evento, argumentos):
    if evento in ("socket.connect", "socket.getaddrinfo"):
        raise AssertionError("La validación intentó usar red")
sys.addaudithook(sin_red)
sys.argv = [sys.argv[1], "--validate", "--output", sys.argv[2]]
runpy.run_path(sys.argv[0], run_name="__main__")
"""
    resultado = subprocess.run(
        [sys.executable, "-I", "-B", "-c", codigo, str(EXPORTADOR), str(SNAPSHOT)],
        cwd=tmp_path,
        capture_output=True,
        timeout=30,
    )
    exitoso(resultado)
    assert b"offline" in resultado.stdout


@pytest.mark.parametrize("caso", ["estructura", "ref_rota", "ref_http", "ref_archivo"])
def test_validacion_rechaza_documento_o_referencias_invalidos(tmp_path, caso):
    documento = json.loads(SNAPSHOT.read_bytes())
    if caso == "estructura":
        del documento["info"]
    else:
        referencias = {
            "ref_rota": "#/components/schemas/NoExiste",
            "ref_http": "https://example.invalid/schema.json",
            "ref_archivo": "file:///no-leer/schema.json",
        }
        # No usado por ninguna ruta: también deben comprobarse refs no alcanzados.
        documento["components"]["schemas"]["ReferenciaNoUsada"] = {"$ref": referencias[caso]}
    copia = tmp_path / "invalido.json"
    copia.write_text(json.dumps(documento), encoding="utf-8")
    antes = copia.read_bytes()
    resultado = ejecutar("--validate", "--output", copia)
    assert resultado.returncode == 1 and b"Regenerar:" in resultado.stderr
    assert copia.read_bytes() == antes
