"""La suite debe fallar por la aserción prevista ante dos mutaciones acotadas (#52)."""

import os
import subprocess
import sys

CASOS = {
    "request-id": "test_envoltorios_http_con_request_id[401-SESSION_INVALID]",
    "retries": "test_proveedor_sync_timeout_cuota_por_intento_y_retries_limitados[99-failed]",
}


def main():
    for fallo, caso in CASOS.items():
        env = {**os.environ, "NM_INTEGRATION_FAULT": fallo, "MOCK_OCI": "1", "MOCK_GEMINI": "1", "APP_ENV": "ci"}
        resultado = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "--tb=short",
                "-m",
                "integration_mock",
                "backend/tests/integration/test_contract.py::" + caso,
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        salida = resultado.stdout + resultado.stderr
        if (
            resultado.returncode != 1
            or "AssertionError" not in salida
            or "FAILED" not in salida
            or "NM_FAULT=" + fallo not in salida
        ):
            print(salida[-6000:])
            raise RuntimeError("La suite no detectó el fallo esperado: " + fallo)
        print("Fallo sembrado detectado: " + fallo)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
