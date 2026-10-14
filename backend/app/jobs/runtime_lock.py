"""Exclusividad entre Uvicorn y la precarga administrativa sobre el mismo volumen."""

import os
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def exclusividad_runtime(data_dir: Path):
    data_dir.mkdir(parents=True, exist_ok=True)
    archivo = (data_dir / "runtime.lock").open("a+b")
    try:
        if os.name == "nt":
            import msvcrt

            archivo.seek(0, 2)
            if archivo.tell() == 0:
                archivo.write(b"0")
                archivo.flush()
            archivo.seek(0)
            msvcrt.locking(archivo.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(archivo.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        archivo.close()
        raise RuntimeError("El volumen está en uso: detener el backend antes del mantenimiento demo.") from None
    try:
        yield
    finally:
        # El archivo permanece: borrarlo permitiría bloquear otro inode en Linux.
        archivo.close()
