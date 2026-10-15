"""Vista de revisión manual con fixtures; sin servicios ni credenciales reales."""

import sys
from pathlib import Path


def main():
    ROOT = Path(__file__).resolve().parents[2]
    for folder in ("frontend", "backend", "backend/tests"):
        sys.path.insert(0, str(ROOT / folder))
    from copy import deepcopy

    import streamlit as st
    from app.schemas.responses import PedagogicalStudentOutput
    from components.result import render_generacion
    from components.result_header import render_cabecera_resultado
    from components.theme import aplicar_tema
    from i18n import t
    from test_schemas import TODOS_LOS_FORMATOS, salida_valida

    st.set_page_config(page_title="NuevaMente · Revisión visual", layout="wide")
    aplicar_tema()
    st.session_state.workspace_id = "visual_fixture"
    st.session_state.session_token = "sesion_de_prueba"
    with st.sidebar:
        st.title("NuevaMente")
        st.caption("Vista de prueba · fixtures del contrato")
        idioma = st.selectbox(
            "Idioma de interfaz", ["es", "en", "pt"], format_func=lambda x: t("idioma." + x), key="idioma_visual"
        )
        st.session_state.formato_visual = st.session_state.get("formato_guardado", "flashcards")
        formato = st.selectbox(
            "Formato",
            list(TODOS_LOS_FORMATOS),
            format_func=lambda x: t("formato." + x, idioma),
            key="formato_visual",
            on_change=lambda: st.session_state.update(formato_guardado=st.session_state.formato_visual),
        )
        estado = st.selectbox(
            "Estado",
            ["completed", "queued", "running", "rejected_quality", "failed", "cancelled"],
            format_func=lambda x: t("estados.trabajo." + x, idioma),
        )
        diagrama = st.checkbox("Fuente con diagrama")
        st.caption("No hay llamadas a Gemini ni OCI en esta vista de prueba.")
    p = PedagogicalStudentOutput.desde_canonico(salida_valida(deepcopy(TODOS_LOS_FORMATOS[formato][0]))).model_dump(
        mode="json"
    )
    p["generation_id"] = "gen_visual_" + formato
    if formato == "tutorial":
        p["contenido_adaptado"]["pasos"][0]["codigo"] = (
            "oci network vcn list --compartment-id <compartment>\n# Ejemplo de código para revisar el diseño"
        )
    if diagrama:
        p["referencias"][0]["pagina"] = 3
        for campo in ("items", "pasos", "escenas"):
            for item in p["contenido_adaptado"].get(campo, []):
                item["referencias"][0]["pagina"] = 3
        if formato == "resumen_ejecutivo":
            p["contenido_adaptado"]["referencias"][0]["pagina"] = 3

    class VisualAPI:
        def get_generation(self, token, gid):
            return {
                "generation_id": gid,
                "status": estado,
                "contenido": p,
                "persistencia": {"status_upload": "completado", "provider": "mock"},
                "posicion_cola": 2,
                "error": {"code": "INTERNAL"} if estado == "failed" else None,
            }

        def get_document_source(self, token, document_id, chunk_id, **kwargs):
            return {
                "document_id": document_id,
                "chunk_id": chunk_id,
                "pagina": 3 if diagrama else 2,
                "seccion": "Conceptos",
                "texto": "Una VCN es una red virtual privada y configurable en OCI. Las subredes agrupan recursos dentro de la VCN.",
                "source_type": "image_description" if diagrama else "text",
                "interpretacion_modelo": diagrama,
            }

        def get_document_source_image(self, token, document_id, chunk_id):
            import pymupdf

            with pymupdf.open(ROOT / "documents/redes_vcn_oci.pdf") as doc:
                return doc.load_page(2).get_pixmap().tobytes("png")

        def cancel_generation(self, *args):
            pass

        def record_progress(self, token, payload):
            eventos = st.session_state.setdefault("eventos_visuales", {})
            eventos.setdefault(payload["event_id"], payload)
            return {"event_id": payload["event_id"], "version": len(eventos)}

        def answer_quiz(self, token, gid, payload, **kwargs):
            pregunta = TODOS_LOS_FORMATOS["quiz"][0]["preguntas"][0]
            return {
                "correcta": payload["option_id"] == pregunta["correct_option_id"],
                "correct_option_id": pregunta["correct_option_id"],
                "justificacion": pregunta["justificacion"],
                "referencias": [{**ref, "pagina": 3 if diagrama else 2} for ref in pregunta["referencias"]],
            }

    activa = {
        "generation_id": p["generation_id"],
        "documento_titulo": p["documento_fuente"]["titulo"],
        "seccion_titulo": None,
        "parametros": {
            "document_id": p["documento_fuente"]["document_id"],
            "perfil_destinatario": "principiante",
            "formato_salida": formato,
            "nicho_sector": "general",
            "nivel_detalle": "didactico",
            "idioma_salida": "es",
            "alcance": {"tipo": "documento_completo"},
        },
    }
    render_cabecera_resultado(activa, idioma)
    render_generacion(VisualAPI(), activa, idioma)


if __name__ == "__main__":
    main()
