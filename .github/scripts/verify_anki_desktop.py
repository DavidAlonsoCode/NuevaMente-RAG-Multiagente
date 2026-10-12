"""Importación real en Anki Desktop aislado y evidencia Qt (sin perfil personal)."""

import json
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path[:0] = [str(Path("backend").resolve()), str(Path("backend/tests").resolve())]

import aqt  # noqa: E402
from app.exports.anki import exportar_apkg, exportar_texto, nombre_mazo  # noqa: E402
from aqt.profiles import ProfileManager  # noqa: E402
from test_exports_anki import _importar, paquete  # noqa: E402


def eventos(app, segundos=2):
    limite = time.monotonic() + segundos
    while time.monotonic() < limite:
        app.processEvents()
        time.sleep(0.02)


def verificar():
    salida = Path(".data/anki-desktop-evidence")
    salida.mkdir(parents=True, exist_ok=True)
    datos = paquete.__wrapped__()
    with TemporaryDirectory(prefix="nm-desktop-") as base:
        # Prepara preferencias propias para que el primer inicio no pida idioma.
        pm = ProfileManager(Path(base))
        pm.setupMeta()
        pm.meta.update(firstRun=False, defaultLang="en_US", updates=False)
        pm.create("NuevaMente test")
        pm.load("NuevaMente test")
        pm.profile.update(autoSync=False)
        pm.save()
        pm.db.close()
        app = aqt._run(["anki", "-b", base, "-p", "NuevaMente test", "-l", "en", "--safemode"], exec=False)
        assert app is not None
        eventos(app)
        mw = aqt.mw
        assert mw.col is not None
        resultados = []
        for formato in ("csv", "tsv", "apkg"):
            ruta = Path(base) / ("tarjetas." + formato)
            ruta.write_bytes(exportar_apkg(datos) if formato == "apkg" else exportar_texto(datos, formato))
            for _ in range(2):
                # Es el importador oficial de la colección abierta por Desktop.
                # Las cabeceras se detectan sin modificar separador/campos/tags.
                _importar(mw.col, ruta, formato)
                assert mw.col.note_count() == mw.col.card_count() == 3
            mw.reset()
            eventos(app)
            assert mw.grab().save(str(salida / (formato + ".png")))
            resultados.append(
                {"formato": formato, "importaciones": 2, "notas": mw.col.note_count(), "tarjetas": mw.col.card_count()}
            )
        browser = aqt.dialogs.open("Browser", mw)
        browser.search_for('deck:"' + nombre_mazo(datos) + '"')
        eventos(app)
        assert browser.grab().save(str(salida / "tarjetas-etiquetas.png"))
        (salida / "resultado.json").write_text(
            json.dumps({"anki": aqt.appVersion, "resultados": resultados}, indent=2), encoding="utf-8"
        )
        browser.close()
        mw.col.close()
        app.quit()
    print("Anki Desktop: tres formatos, seis importaciones, tres notas sin duplicados")


if __name__ == "__main__":
    verificar()
