"""Supervisor de procesos para el despliegue en Render (FastAPI + React)."""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path


def configurar_oci_si_aplica():
    key_content = os.environ.get("OCI_KEY_CONTENT", "").strip()
    user_ocid = os.environ.get("OCI_USER_OCID", "").strip()
    tenancy_ocid = os.environ.get("OCI_TENANCY_OCID", "").strip()
    fingerprint = os.environ.get("OCI_FINGERPRINT", "").strip()
    region = os.environ.get("OCI_REGION", "sa-vinhedo-1").strip()

    if key_content and user_ocid and tenancy_ocid and fingerprint:
        oci_dir = Path("/tmp/oci")
        oci_dir.mkdir(parents=True, exist_ok=True)
        key_file = oci_dir / "oci_api_key.pem"
        # Asegurar formato PEM limpio
        key_file.write_text(key_content + "\n", encoding="utf-8")
        try:
            key_file.chmod(0o600)
        except OSError:
            pass

        config_file = oci_dir / "config"
        config_text = (
            "[DEFAULT]\n"
            f"user={user_ocid}\n"
            f"fingerprint={fingerprint}\n"
            f"tenancy={tenancy_ocid}\n"
            f"region={region}\n"
            f"key_file={key_file.as_posix()}\n"
        )
        config_file.write_text(config_text, encoding="utf-8")
        os.environ["OCI_CONFIG_FILE"] = str(config_file)
        os.environ["OCI_REGION"] = region
        print("[Runner] Configuración OCI generada dinámicamente en", config_file, flush=True)


def main():
    print("[Runner] Iniciando orquestador de producción...", flush=True)
    configurar_oci_si_aplica()

    port = os.environ.get("PORT", "10000")
    base_dir = Path(__file__).resolve().parent

    backend_dir = base_dir / "backend"
    frontend_dir = base_dir / "frontend"

    # Lanzar FastAPI en 127.0.0.1:8000
    env_backend = os.environ.copy()
    env_backend["PYTHONPATH"] = str(backend_dir)
    cmd_backend = [
        sys.executable,
        "-m",
        "uvicorn",
        "app.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        "8000",
        "--workers",
        "1",
        "--no-access-log",
    ]
    print("[Runner] Lanzando backend FastAPI en 127.0.0.1:8000...", flush=True)
    proc_backend = subprocess.Popen(cmd_backend, cwd=str(backend_dir), env=env_backend)

    # Lanzar servidor y proxy frontend en 0.0.0.0:$PORT
    env_frontend = os.environ.copy()
    env_frontend["PORT"] = port
    env_frontend["FRONTEND_HOST"] = "0.0.0.0"
    env_frontend["API_URL"] = "http://127.0.0.1:8000"
    env_frontend["API_HEALTH_WAIT_SECONDS"] = "60"

    entrypoint_script = frontend_dir / "entrypoint.py"
    cmd_frontend = [sys.executable, str(entrypoint_script)]
    print(f"[Runner] Lanzando servidor frontend en 0.0.0.0:{port}...", flush=True)
    proc_frontend = subprocess.Popen(cmd_frontend, cwd=str(frontend_dir), env=env_frontend)

    def terminar_procesos(*args):
        print("[Runner] Señal de terminación recibida. Cerrando procesos...", flush=True)
        for p in (proc_backend, proc_frontend):
            if p.poll() is None:
                p.terminate()
        time.sleep(1)
        for p in (proc_backend, proc_frontend):
            if p.poll() is None:
                p.kill()
        sys.exit(0)

    signal.signal(signal.SIGINT, terminar_procesos)
    signal.signal(signal.SIGTERM, terminar_procesos)

    while True:
        b_ret = proc_backend.poll()
        f_ret = proc_frontend.poll()

        if b_ret is not None:
            print(f"[Runner] Backend terminó inesperadamente con código {b_ret}. Deteniendo frontend...", flush=True)
            terminar_procesos()
            sys.exit(b_ret)

        if f_ret is not None:
            print(f"[Runner] Frontend terminó inesperadamente con código {f_ret}. Deteniendo backend...", flush=True)
            terminar_procesos()
            sys.exit(f_ret)

        time.sleep(2)


if __name__ == "__main__":
    main()
