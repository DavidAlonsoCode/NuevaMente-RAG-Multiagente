"""Servidor de verificación local de React; proveedores dobles, datos descartables."""

import argparse
import os
import sys
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--port", type=int, default=18080)
    parser.add_argument("--delay-seconds", type=float, default=0)
    parser.add_argument("--reject-quality", action="store_true")
    parser.add_argument("--reject-unsupported", action="store_true", help="El juez no respalda ninguna afirmación.")
    args = parser.parse_args()
    if not 0 <= args.delay_seconds <= 10:
        parser.error("La demora simulada debe estar entre 0 y 10 segundos")
    root = Path(__file__).resolve().parents[3]
    directory = Path(args.data_dir).resolve()
    if not directory.is_relative_to(root / ".data") or directory == root / ".data":
        parser.error("Usar una carpeta de prueba nueva dentro de .data, nunca .data directamente")
    if directory.exists():
        parser.error("La carpeta de prueba debe ser nueva para no abrir datos existentes")
    directory.mkdir(parents=True)
    os.chdir(directory)
    os.environ.update(APP_ENV="test", MOCK_OCI="1", MOCK_GEMINI="1", DATA_DIR=str(directory))

    def solo_loopback(event, values):
        if event == "socket.getaddrinfo" and values[0] not in {"localhost", "127.0.0.1", "::1", None}:
            raise PermissionError("La verificación no permite proveedores externos")

    sys.addaudithook(solo_loopback)
    sys.path[:0] = [str(root / "backend"), str(root / "backend/tests")]
    import uvicorn
    from app.config import Configuracion

    # app.main también construye la app ASGI global al importarse. Aislarla
    # antes del import evita cargar .env o crear clientes reales en esa app.
    Configuracion.model_config["env_file"] = None
    from app.jobs.manager import CuotasModelo, CuotasProveedor
    from app.main import crear_app
    from test_api_generate import DobleEducativo

    cfg = Configuracion(
        _env_file=None,
        app_env="test",
        mock_oci=True,
        mock_gemini=True,
        data_dir=str(directory),
        embedding_dimensions=16,
        google_api_key="synthetic-test-only",
    )

    class DobleDemo(DobleEducativo):
        def generar_sync(self, prompt, **kwargs):
            time.sleep(args.delay_seconds)
            return super().generar_sync(prompt, **kwargs)

    provider = DobleDemo(16)
    provider.rechazar = args.reject_quality
    provider.sin_respaldo = args.reject_unsupported
    quotas = CuotasProveedor(
        {
            model: CuotasModelo(1000, 10_000_000, 1000)
            for model in {
                cfg.gemini_embedding_model,
                cfg.gemini_generation_model,
                cfg.gemini_verification_model,
            }
        }
    )
    app = crear_app(
        cfg,
        proveedor_embeddings=provider,
        proveedor_generacion=provider,
        proveedor_verificacion=provider,
        cuotas_proveedor=quotas,
    )
    uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
