"""#59: cobertura de la referencia y ejecución local de sus bloques Bash literales."""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[2]
REFERENCE = ROOT / "docs/api_reference.md"
BLOCKS = ("setup", "workspace", "upload", "quiz", "exports", "flashcards", "multipart")
START, END = "<!-- operations:start -->", "<!-- operations:end -->"


def schema_name(schema):
    if "$ref" in schema:
        name = schema["$ref"].rsplit("/", 1)[-1]
        return f"[{name}](openapi.json#/components/schemas/{name})"
    if "anyOf" in schema:
        return " / ".join(schema_name(s) for s in schema["anyOf"])
    name = schema.get("type", "objeto")
    return f"{name} ({schema['format']})" if "format" in schema else name


def operation_rows(spec):
    """Proyección pequeña y contrastable; los schemas completos siguen en el snapshot."""
    rows = []
    for path, methods in spec["paths"].items():
        for method, op in methods.items():
            parameters = []
            for p in op.get("parameters", []):
                if p["name"] in {"X-Request-ID", "Accept-Language"}:
                    continue
                s = p["schema"]
                value = f"{p['in']} `{p['name']}`: {schema_name(s)}"
                if p.get("required"):
                    value += " obligatorio"
                if "default" in s:
                    value += f"; default `{json.dumps(s['default'], ensure_ascii=False)}`"
                for key in ("minimum", "maximum"):
                    if key in s:
                        value += f"; {key} `{s[key]}`"
                parameters.append(value)
            for mime, body in op.get("requestBody", {}).get("content", {}).items():
                value = schema_name(body["schema"])
                if mime == "multipart/form-data":
                    fields = []
                    for name, field in body["schema"]["properties"].items():
                        required = name in body["schema"].get("required", [])
                        item = f"`{name}`: {schema_name(field)} {'obligatorio' if required else 'opcional'}"
                        for key in ("minLength", "maxLength"):
                            if key in field:
                                item += f"; {key} `{field[key]}`"
                        fields.append(item)
                    value = "; ".join(fields)
                required = " obligatorio" if op["requestBody"].get("required") else " opcional"
                parameters.append(f"body `{mime}`{required}: {value}")
            success = []
            errors = []
            for status, response in op["responses"].items():
                if int(status) >= 400:
                    errors.append(status)
                    continue
                content = response.get("content", {})
                description = "; ".join(f"`{mime}` {schema_name(v.get('schema', {}))}" for mime, v in content.items())
                success.append(f"**{status}** {description or 'sin cuerpo'}")
            auth = "Bearer" if op.get("security") else "Pública"
            rows.append(
                f"| `{method.upper()} {path}` | {auth} | {'<br>'.join(parameters) or 'Sin body/parámetros específicos'} "
                f"| {'<br>'.join(success)} | {', '.join(errors)} |"
            )
    return rows


def example_blocks(text):
    return re.findall(r"```bash\n(# api-example: ([a-z-]+)\n.*?)\n```", text, re.DOTALL)


def published_script(text):
    return "\n\n".join(body for body, _ in example_blocks(text)) + "\n"


def check_reference(text=None):
    text = REFERENCE.read_text(encoding="utf-8") if text is None else text
    spec = json.loads((ROOT / "docs/openapi.json").read_bytes())
    if text.count(START) != 1 or text.count(END) != 1:
        raise ValueError("Faltan los delimitadores del índice de operaciones.")
    actual = text.split(START, 1)[1].split(END, 1)[0]
    rows = [line for line in actual.splitlines() if line.startswith("| `")]
    if rows != operation_rows(spec):
        raise ValueError(
            "El índice no coincide con métodos, parámetros, modelos, tipos de contenido o HTTP del snapshot."
        )
    blocks = example_blocks(text)
    if tuple(name for _, name in blocks) != BLOCKS or text.count("```bash\n") != len(blocks):
        raise ValueError("Los siete bloques Bash deben estar completos, identificados y en orden.")
    for ref in re.findall(r"openapi.json#/components/schemas/([A-Za-z0-9_-]+)", text):
        if ref not in spec["components"]["schemas"]:
            raise ValueError(f"Schema enlazado inexistente: {ref}")
    return len(rows), len(blocks)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="Contrastar tablas, enlaces y bloques con el snapshot")
    mode.add_argument("--local", action="store_true", help="Ejecutar los tests y curl contra TCP loopback con dobles")
    args = parser.parse_args()
    count, blocks = check_reference()
    print(f"Referencia: {count} operaciones y {blocks} bloques Bash identificados.", flush=True)
    if args.local:
        # Cwd vacío: Settings no carga el .env personal. Sin credenciales, proxies ni BASH_ENV heredados.
        with TemporaryDirectory(prefix="nm-api-reference-") as temp:
            env = {
                k: v
                for k, v in os.environ.items()
                if k.upper() in {"SYSTEMROOT", "WINDIR", "COMSPEC", "PATH", "PATHEXT", "NM_API_REFERENCE_BASH"}
            }
            env.update(
                APP_ENV="test",
                MOCK_OCI="1",
                MOCK_GEMINI="1",
                DATA_DIR=str(Path(temp) / "global-data"),
                HOME=temp,
                USERPROFILE=temp,
                APPDATA=temp,
                LOCALAPPDATA=temp,
                TMP=temp,
                TEMP=temp,
                TMPDIR=temp,
                ANONYMIZED_TELEMETRY="False",
                LANGSMITH_TRACING="false",
                LANGCHAIN_TRACING_V2="false",
            )
            result = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-B",
                    "-c",
                    "import sys, pytest; sys.exit(pytest.main(sys.argv[1:]))",
                    "-c",
                    str(ROOT / "pytest.ini"),
                    "--rootdir=" + str(ROOT),
                    "-p",
                    "no:cacheprovider",
                    "--basetemp=" + str(Path(temp) / "tests"),
                    str(ROOT / "backend/tests/test_api_reference.py"),
                    "-q",
                    "-s",
                    "--tb=short",
                ],
                cwd=temp,
                env=env,
                timeout=240,
                check=False,
            )
            return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
