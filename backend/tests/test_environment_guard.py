"""El primer import no hereda credenciales reales en una suite ordinaria (#52)."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "seleccion,esperado",
    [
        ("integration_mock", True),
        ("unit or integration_mock", True),
        ("not integration_real", True),
        ("integration_real", False),
    ],
)
def test_configuracion_se_importa_con_modo_correcto_antes_de_configure(seleccion, esperado):
    raiz = Path(__file__).resolve().parents[2]
    programa = """
import json, runpy, sys
sys.argv=['pytest','-m',sys.argv[1]]
runpy.run_path('conftest.py')
from app.config import config
print(json.dumps({'oci':config.mock_oci,'gemini':config.mock_gemini}))
"""
    env = {
        **os.environ,
        "MOCK_OCI": "0",
        "MOCK_GEMINI": "0",
        "APP_ENV": "development",
        "PYTHONPATH": str(raiz / "backend"),
        "PYTEST_ADDOPTS": "",
    }
    resultado = subprocess.run(
        [sys.executable, "-c", programa, seleccion], cwd=raiz, env=env, capture_output=True, text=True, timeout=15
    )
    assert resultado.returncode == 0
    assert json.loads(resultado.stdout) == {"oci": esperado, "gemini": esperado}
