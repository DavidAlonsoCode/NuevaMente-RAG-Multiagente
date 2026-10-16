"""Sólo selección manual explícita; nunca usa la fixture API simulada."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration_real


def test_smoke_oci_gemini_real(tmp_path):
    if os.environ.get("NUEVAMENTE_REAL_SMOKE") != "1":
        pytest.skip("Autorizar explícitamente la corrida con NUEVAMENTE_REAL_SMOKE=1")
    if any(os.environ.get(k) != "0" for k in ("MOCK_OCI", "MOCK_GEMINI")):
        pytest.fail("La prueba real rechaza mocks")
    directorio = Path(os.environ.get("NUEVAMENTE_REAL_SMOKE_OUTPUT", str(tmp_path / "evidencia")))
    comando = [
        sys.executable,
        "-X",
        "utf8",
        "-m",
        "app.smoke_real",
        "--output-dir",
        str(directorio),
        "--data-dir",
        os.environ.get("NUEVAMENTE_REAL_SMOKE_DATA", ".data/smoke-real"),
    ]
    if reutilizar := os.environ.get("NUEVAMENTE_REAL_SMOKE_REUSE"):
        comando.extend(["--reuse", reutilizar])
    entorno = {**os.environ, "PYTHONPATH": "backend"}
    resultado = subprocess.run(comando, env=entorno, capture_output=True, timeout=780)
    # No volcar stdout/stderr de SDK o credenciales en los reportes de pytest.
    assert resultado.returncode == 0, "La corrida real falló; revisar el informe saneado local"
    informe = json.loads((directorio / "informe.json").read_text(encoding="utf-8"))
    assert informe["estado"] == "aprobado"
    assert informe["providers"] == {"oci": "real", "gemini": "real"}
    assert informe["sdk_attempts"] == 1 and informe["storage"]["solicitudes_sdk"] > 0
    assert all((directorio / ("resultado." + f)).is_file() for f in ("json", "md", "pdf", "apkg"))
