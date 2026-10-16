"""#59: cobertura de referencia y ejecución literal de curl por TCP local."""

import importlib.util
import json
import os
import shutil
import socket
import subprocess
import threading
import time
import zipfile
from pathlib import Path

import pytest
import test_api_generate as generaciones
import uvicorn

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("verify_reference", ROOT / ".github/scripts/verify_api_reference.py")
reference = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reference)
entorno = generaciones.entorno
pytestmark = pytest.mark.integration_mock


def test_cobertura_completa_y_bloques_literales():
    text = reference.REFERENCE.read_text(encoding="utf-8")
    assert reference.check_reference(text) == (37, 7)
    script = reference.published_script(text)
    assert all(body in text and body in script for body, _ in reference.example_blocks(text))
    assert script.count("# api-example:") == 7


@pytest.mark.parametrize("mutation", ["operation", "mime", "parameter", "body", "block", "link"])
def test_detecta_referencia_incompleta_o_divergente(mutation):
    text = reference.REFERENCE.read_text(encoding="utf-8")
    if mutation == "operation":
        row = next(line for line in text.splitlines() if line.startswith("| `GET /api/jobs/{job_id}`"))
        changed = text.replace(row, "")
    elif mutation == "mime":
        changed = text.replace("`image/png`", "`application/json`", 1)
    elif mutation == "parameter":
        changed = text.replace("header `Last-Event-ID`", "header `Cursor-Inexistente`", 1)
    elif mutation == "body":
        changed = text.replace("minLength `1`", "minLength `0`", 1)
    elif mutation == "block":
        changed = text.replace("# api-example: multipart", "# api-example: omitido")
    else:
        changed = text + "\n[Modelo inexistente](openapi.json#/components/schemas/ModeloInexistente)\n"
    assert changed != text
    with pytest.raises(ValueError):
        reference.check_reference(changed)


def test_curl_publicado_por_tcp_local(entorno, txt_ejemplo, tmp_path):
    e = entorno
    bash = os.environ.get("NM_API_REFERENCE_BASH") or shutil.which("bash")
    if not bash and os.name == "nt":
        candidate = Path(r"C:\Program Files\Git\bin\bash.exe")
        bash = str(candidate) if candidate.is_file() else None
    assert bash, "Instalar Bash; ver prerrequisitos de docs/api_reference.md"
    script = tmp_path / "published-examples.sh"
    text = reference.REFERENCE.read_text(encoding="utf-8")
    script.write_text(reference.published_script(text), encoding="utf-8", newline="\n")
    output = tmp_path / "responses"
    output.mkdir()
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    # Igual que #52: la fixture ya abrió lifespan, worker y recursos reales.
    server = uvicorn.Server(
        uvicorn.Config(e.app, host="127.0.0.1", port=port, lifespan="off", access_log=False, log_level="error")
    )
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started
        env = dict(os.environ)
        env.update(
            BASE_URL=f"http://127.0.0.1:{port}",
            NM_REFERENCE_INPUT=str(txt_ejemplo),
            NM_REFERENCE_OUTPUT=str(output),
            NM_REFERENCE_SCRIPT=str(script),
        )
        # Sólo adapta paths Windows de archivos de entrada/salida; no modifica bloques ni curl.
        bootstrap = """
if command -v cygpath >/dev/null; then
  export INPUT_FILE="$(cygpath -u "$NM_REFERENCE_INPUT")"
  export RUN_DIR="$(cygpath -u "$NM_REFERENCE_OUTPUT")"
  SCRIPT="$(cygpath -u "$NM_REFERENCE_SCRIPT")"
else
  export INPUT_FILE="$NM_REFERENCE_INPUT" RUN_DIR="$NM_REFERENCE_OUTPUT"
  SCRIPT="$NM_REFERENCE_SCRIPT"
fi
exec bash --noprofile --norc "$SCRIPT"
"""
        result = subprocess.run(
            [bash, "--noprofile", "--norc", "-c", bootstrap],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=180,
            check=False,
        )
        print(result.stdout)
        assert result.returncode == 0, result.stderr + "\n" + result.stdout
        assert "OK quiz-reconnect SSE" in result.stdout
        for name in ("document", "multipart-document"):
            assert json.loads((output / (name + ".json")).read_bytes())["status"] == "ready"
        student = json.loads((output / "quiz-status.json").read_bytes())["contenido"]["contenido_adaptado"]
        assert "correct_option_id" not in student["preguntas"][0]
        canonical = json.loads((output / "quiz-canonical.json").read_bytes())
        assert "correct_option_id" in canonical["contenido_adaptado"]["preguntas"][0]
        assert (output / "quiz-pdf.pdf").read_bytes().startswith(b"%PDF-")
        with zipfile.ZipFile(output / "flashcards-apkg.apkg") as archive:
            assert archive.testzip() is None
            assert archive.read("collection.anki2").startswith(b"SQLite format 3\x00")
        assert json.loads((output / "progress.json").read_bytes())["quiz_intentos"] == 1
    finally:
        server.should_exit = True
        thread.join(5)
        sock.close()
        assert not thread.is_alive(), "El servidor local no cerró"
