"""Genera cinco muestras VCN de maquetación y las abre en PDFium y Poppler, sin APIs."""

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "backend/tests")]

import pypdfium2 as pdfium  # noqa: E402
from app.exports.pdf import renderizar_pdf  # noqa: E402
from app.schemas.responses import PedagogicalOutput  # noqa: E402
from pypdf import PdfReader  # noqa: E402
from test_schemas import TODOS_LOS_FORMATOS, salida_valida  # noqa: E402


def muestras():
    """Fixtures de presentación: no acreditan generación Gemini ni evaluación factual real."""
    for tipo, (original, _) in TODOS_LOS_FORMATOS.items():
        contenido = deepcopy(original)
        if tipo == "flashcards":
            for i, (frente, dorso) in enumerate(
                [
                    ("¿Qué define el tamaño de una VCN?", "Su bloque CIDR."),
                    ("¿Cuántas direcciones contiene 10.0.0.0/16?", "65.536 direcciones IPv4 en total."),
                ],
                2,
            ):
                contenido["items"].append(
                    {
                        **deepcopy(contenido["items"][0]),
                        "id": f"fc_{i:03}",
                        "frente": frente,
                        "dorso": dorso,
                        "pista_didactica": None,
                    }
                )
        elif tipo == "quiz":
            contenido["preguntas"].append(
                {
                    **deepcopy(contenido["preguntas"][0]),
                    "id": "q_002",
                    "enunciado": "¿Cuántas direcciones IPv4 contiene un bloque /24 en total?",
                    "opciones": [
                        {"option_id": letra, "texto": texto}
                        for letra, texto in zip("ABCD", ["256", "24", "65.536", "Un número definido por los usuarios"])
                    ],
                    "justificacion": "Quedan ocho bits para las direcciones: 2 elevado a 8 es 256.",
                }
            )
        elif tipo == "tutorial":
            contenido["titulo"] = "VCN: confira o bloco CIDR"
            contenido["audiencia"] = "Desenvolvedores em transição para cloud"
            contenido["pasos"][0].update(
                instruccion="Exemplo ilustrativo: calcule o tamanho do bloco sem criar recursos na nuvem.",
                codigo='from ipaddress import ip_network\n\nrede = ip_network("10.0.0.0/16")\n'
                'print("Conexão privada:", rede.num_addresses)\n# ação, informação, São Paulo',
                resultado_esperado="A saída informa 65536 endereços no bloco completo.",
                verificacion="Compare o total com a tabela de CIDR da fonte.",
            )
        elif tipo == "resumen_ejecutivo":
            contenido["titulo"] = "VCN para decisões de negócio"
            contenido["puntos_clave"] = ["Uma VCN isola e organiza os recursos de rede."]
            contenido["impacto_cualitativo"] = "Separa recursos e permite planejar comunicações controladas."
            contenido["implicaciones"] = ["Planejar a comunicação entre os níveis da aplicação."]
            contenido["acciones"] = ["Definir uma convenção de blocos CIDR por ambiente."]
        paquete = salida_valida(contenido).model_dump(mode="json")
        idioma = "pt" if tipo in ("tutorial", "resumen_ejecutivo") else "es"
        paquete["metadatos"]["idioma_salida"] = idioma
        if idioma == "pt":
            paquete["metadatos"]["objetivos_aprendizaje"] = ["Explicar o que é uma VCN e para que serve."]
            if tipo == "resumen_ejecutivo":
                paquete["metadatos"]["perfil_aplicado"] = "ejecutivo"
            else:
                paquete["metadatos"]["perfil_aplicado"] = "junior_ssr"
                paquete["contenido_adaptado"]["prerrequisitos"] = ["Python 3.11 ou superior"]
        paquete["documento_fuente"]["titulo"] = "Redes VCN en Oracle Cloud Infrastructure"
        paquete["documento_fuente"]["hash"] = (
            "sha256:" + hashlib.sha256((ROOT / "documents/redes_vcn_oci.pdf").read_bytes()).hexdigest()
        )
        paquete["documento_fuente"]["procedencia"] = "Documento demo VCN; fixture tipado de maquetación #44"
        yield tipo + "-" + idioma, PedagogicalOutput.model_validate(paquete)


def verificar(destino):
    destino.mkdir(parents=True, exist_ok=True)
    poppler = shutil.which("pdftoppm")
    if not poppler:
        raise RuntimeError("La verificación con dos motores requiere pdftoppm de Poppler")
    reporte = {}
    for nombre, paquete in muestras():
        for previo in destino.glob(nombre + "-*.png"):
            previo.unlink()  # Sólo páginas de estas muestras, para permitir regeneración.
        datos = renderizar_pdf(paquete)
        assert datos == renderizar_pdf(paquete)
        archivo = destino / (nombre + ".pdf")
        archivo.write_bytes(datos)
        lector = PdfReader(archivo, strict=True)
        doc = pdfium.PdfDocument(datos)
        try:
            assert len(doc) == len(lector.pages)
            for i in range(len(doc)):
                pagina = doc[i]
                bitmap = pagina.render(scale=1.5)
                try:
                    bitmap.to_pil().save(destino / f"{nombre}-pdfium-{i + 1}.png")
                    texto = pagina.get_textpage()
                    try:
                        # Verifica que el texto visible queda dentro del papel.
                        for n in range(texto.count_rects()):
                            x0, y0, x1, y1 = texto.get_rect(n)
                            assert 0 <= x0 <= x1 <= pagina.get_width() + 1
                            assert 0 <= y0 <= y1 <= pagina.get_height() + 1
                    finally:
                        texto.close()
                finally:
                    bitmap.close()
                    pagina.close()
        finally:
            doc.close()
        subprocess.run(
            [poppler, "-r", "108", "-png", str(archivo), str(destino / (nombre + "-poppler"))],
            check=True,
            timeout=30,
            capture_output=True,
        )
        assert len(list(destino.glob(nombre + "-poppler-*.png"))) == len(lector.pages)
        reporte[nombre] = {
            "paginas": len(lector.pages),
            "sha256": hashlib.sha256(datos).hexdigest(),
            "motores": ["PDFium", "Poppler"],
            "fuente": "fixture tipado VCN",
        }
    (destino / "reporte.json").write_text(json.dumps(reporte, ensure_ascii=False, indent=2), encoding="utf-8")
    print("PDF: cinco formatos VCN, texto dentro del papel, PDFium y Poppler, bytes deterministas")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / ".data/pdf-evidence")
    verificar(parser.parse_args().output)
