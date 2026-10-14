import streamlit as st


def aplicar_tema():
    """Inyecta CSS para fuentes Inter/JetBrains, bordes hairline y accesibilidad."""
    css = """
    <style>
    /* Importar fuentes si no se auto-hospedan inicialmente */
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600&family=JetBrains+Mono:wght@400;600&display=swap');

    html, body, [class*="css"] {
        font-family: 'Inter', sans-serif;
    }
    
    code {
        font-family: 'JetBrains Mono', monospace;
    }

    /* Bordes hairline y tarjetas carbón */
    .stCard, div[data-testid="stExpander"] {
        background-color: #0f1011 !important;
        border: 1px solid #23252a !important;
        border-radius: 8px;
    }

    /* Foco visible para navegación por teclado (Accesibilidad) */
    *:focus-visible {
        outline: 2px solid #5e6ad2 !important;
        outline-offset: 2px;
    }

    /* Material de estudio: superficies, jerarquía y microinteracciones. */
    .nm-badge {
        display: inline-block;
        max-width: 100%;
        padding: 0.3rem 0.65rem;
        margin-bottom: 0.65rem;
        border: 1px solid #34343a;
        border-radius: 6px;
        background: #141516;
        color: #abb4ff;
        font-size: 0.8rem;
        font-weight: 600;
        overflow-wrap: anywhere;
    }
    [class*="st-key-nm_flashcard_"], [class*="st-key-nm_step_"],
    [class*="st-key-nm_quiz_"], [class*="st-key-nm_scene_"] {
        background: #0f1011;
        border-radius: 12px;
        transition: box-shadow 180ms ease, transform 180ms ease;
        animation: nm-enter 200ms ease-out;
    }
    [class*="st-key-nm_flashcard_"]:hover, [class*="st-key-nm_step_"]:hover,
    [class*="st-key-nm_scene_"]:hover {
        box-shadow: 0 8px 24px rgba(94, 106, 210, 0.10);
        transform: translateY(-2px);
    }
    .nm-reveal {
        padding: 1rem;
        border-left: 3px solid #828fff;
        border-radius: 6px;
        background: #141516;
        color: #f7f8f8;
        line-height: 1.65;
        overflow-wrap: anywhere;
        animation: nm-enter 180ms ease-out;
    }
    [data-testid="stMarkdownContainer"], [data-testid="stCaptionContainer"] {
        overflow-wrap: anywhere;
    }
    [data-testid="stCode"] pre {
        font-family: 'JetBrains Mono', Menlo, monospace;
    }
    @keyframes nm-enter {
        from { opacity: 0; transform: translateY(4px); }
        to { opacity: 1; transform: translateY(0); }
    }
    @media (max-width: 640px) {
        [class*="st-key-nm_summary_"] [data-testid="stHorizontalBlock"] {
            flex-direction: column;
        }
        [class*="st-key-nm_summary_"] [data-testid="stColumn"] {
            width: 100% !important;
            flex: 1 1 100% !important;
            min-width: 0;
        }
        [data-testid="stBaseButton-secondary"], [data-testid="stBaseButton-primary"] {
            white-space: normal;
            overflow-wrap: anywhere;
        }
    }
    /* Respeto por preferencias de movimiento del usuario */
    @media (prefers-reduced-motion: reduce) {
        * {
            animation: none !important;
            transition: none !important;
            transform: none !important;
        }
    }
    </style>
    """
    st.markdown(css, unsafe_allow_html=True)
