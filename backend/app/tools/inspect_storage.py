"""Inspección OCI de sólo lectura y diagnóstico administrativo de limpieza (#50)."""

import argparse
import json
import sqlite3
from pathlib import Path

from app.config import Configuracion
from app.storage.oci_real import OCIObjectStorageProvider


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--local", action="store_true", help="Sólo diagnóstico local de limpieza; no acredita OCI")
    args = parser.parse_args(argv)
    proveedor = None
    try:
        cfg = Configuracion(_env_file=args.env_file)
        if args.local:
            ruta = (Path(cfg.data_dir) / "operativo.db").resolve()
            with sqlite3.connect(ruta.as_uri() + "?mode=ro", uri=True) as db:
                db.row_factory = sqlite3.Row
                resultado = {
                    "limpieza": [dict(f) for f in db.execute("SELECT * FROM workspace_cleanup ORDER BY workspace_id")]
                }
        else:
            if cfg.mock_oci:
                raise ValueError("La inspección real requiere MOCK_OCI=0")
            proveedor = OCIObjectStorageProvider(cfg)
            resultado = {
                "verificacion": "OCI real (lectura)",
                "bucket": cfg.oci_bucket_name,
                "controles": proveedor.inspeccion_bucket,
                "presupuesto": proveedor.presupuesto.resumen(),
            }
        print(json.dumps(resultado, ensure_ascii=False, indent=2))
        return 0
    except Exception:
        print(
            "No se confirmó la inspección. Revisar configuración/credenciales OCI, home region, permisos y presupuesto. Sin cambios remotos ni fallback."
        )
        return 1
    finally:
        if proveedor is not None:
            proveedor.cerrar()


if __name__ == "__main__":
    raise SystemExit(main())
