"""Comprobaciones de frontera para presentar sólo el contrato aprobado de estudiante."""

import math

from api_client import FORMATOS, IDIOMAS_SALIDA, NICHOS, NIVELES_DETALLE, PERFILES


def _exigir(condicion) -> None:
    if not condicion:
        raise ValueError("Contrato incompatible")


def _texto(valor) -> bool:
    return isinstance(valor, str) and bool(valor.strip())


def _textos(valores) -> bool:
    return isinstance(valores, list) and all(_texto(v) for v in valores)


def _referencias(valores) -> bool:
    return (
        isinstance(valores, list)
        and bool(valores)
        and all(
            isinstance(r, dict)
            and _texto(r.get("chunk_id"))
            and (r.get("pagina") is None or type(r["pagina"]) is int and r["pagina"] > 0)
            and (r.get("seccion") is None or _texto(r["seccion"]))
            for r in valores
        )
    )


def validar_paquete(paquete: dict) -> None:
    """No reemplaza al juez; evita mostrar respuestas incompatibles o borradores."""
    try:
        _exigir(isinstance(paquete, dict) and paquete["status"] == "aprobado" and paquete["schema_version"] == "1.0")
        _exigir(_texto(paquete["generation_id"]))
        meta, fuente, contenido, calidad = (
            paquete[k] for k in ("metadatos", "documento_fuente", "contenido_adaptado", "evaluacion_calidad")
        )
        _exigir(all(isinstance(v, dict) for v in (meta, fuente, contenido, calidad)))
        _exigir(contenido["tipo"] in FORMATOS and contenido["tipo"] == meta["formato_generado"])
        _exigir(
            _texto(contenido["titulo"]) and all(_texto(fuente[k]) for k in ("document_id", "titulo", "hash", "version"))
        )
        for campo, opciones in (
            ("perfil_aplicado", PERFILES),
            ("nicho_sector", NICHOS),
            ("nivel_detalle", NIVELES_DETALLE),
            ("idioma_salida", IDIOMAS_SALIDA),
        ):
            _exigir(meta[campo] in opciones)
        _exigir(all(_textos(meta[k]) for k in ("conceptos_clave", "prerrequisitos", "objetivos_aprendizaje")))
        _exigir(bool(meta["conceptos_clave"]) and bool(meta["objetivos_aprendizaje"]))
        _exigir(type(meta["tiempo_estimado_estudio_minutos"]) is int and meta["tiempo_estimado_estudio_minutos"] > 0)
        _exigir(meta["alcance"]["tipo"] in ("documento_completo", "seccion"))
        if meta["alcance"]["tipo"] == "seccion":
            _exigir(_texto(meta["alcance"].get("seccion_id")))
        _exigir(meta["alcance"].get("secciones_cubiertas") is None or _textos(meta["alcance"]["secciones_cubiertas"]))
        _exigir(_referencias(paquete["referencias"]))
        score, total, respaldadas = (
            calidad[k] for k in ("anclaje_fuente_score", "cantidad_afirmaciones", "cantidad_respaldadas")
        )
        _exigir(type(score) in (int, float) and math.isfinite(score) and 0.85 <= score <= 1)
        _exigir(type(total) is int and type(respaldadas) is int and 0 < total and 0 <= respaldadas <= total)
        _exigir(math.isclose(score, respaldadas / total, abs_tol=1e-6))
        _exigir(calidad["estado_evaluacion"] == "aprobada" and not calidad.get("razones_bloqueo"))
        _exigir(
            calidad["cobertura_objetivos"] == "completa" and calidad["verificacion_visual"] in ("no_aplica", "aprobada")
        )
        _exigir(
            all(
                calidad[k] in ("alta", "media")
                for k in ("claridad_pedagogica", "adecuacion_perfil", "coherencia_didactica")
            )
        )
        _exigir(calidad.get("observaciones") is None or _texto(calidad["observaciones"]))
        _exigir(
            contenido.get("introduccion_contextualizada") is None or _texto(contenido["introduccion_contextualizada"])
        )
        _validar_contenido(contenido)
    except (AttributeError, KeyError, TypeError, ValueError):
        raise ValueError("Paquete de estudiante incompatible con el contrato.") from None


def _validar_contenido(c: dict) -> None:
    tipo = c["tipo"]
    if tipo == "resumen_ejecutivo":
        _exigir(_texto(c["impacto_cualitativo"]) and _referencias(c["referencias"]))
        _exigir(all(_textos(c[k]) and c[k] for k in ("puntos_clave", "implicaciones", "acciones")))
        return
    campo = {"flashcards": "items", "quiz": "preguntas", "tutorial": "pasos", "guion_clase": "escenas"}[tipo]
    items = c[campo]
    _exigir(
        isinstance(items, list) and items and all(isinstance(item, dict) and _texto(item.get("id")) for item in items)
    )
    _exigir(len({item["id"] for item in items}) == len(items))
    if tipo == "tutorial":
        _exigir(_texto(c["audiencia"]) and _textos(c["prerrequisitos"]))
    if tipo == "guion_clase":
        _exigir(_textos(c["objetivos"]) and c["objetivos"])
    for item in items:
        if tipo == "quiz":
            _exigir(not {"correct_option_id", "justificacion", "referencias"}.intersection(item))
            _exigir(_texto(item["enunciado"]))
            opciones = item["opciones"]
            _exigir(isinstance(opciones, list) and len(opciones) >= 2)
            _exigir(all(_texto(o["option_id"]) and _texto(o["texto"]) for o in opciones))
            _exigir(len({o["option_id"] for o in opciones}) == len(opciones))
        else:
            _exigir(_referencias(item["referencias"]))
            campos = {
                "flashcards": ("frente", "dorso"),
                "tutorial": ("instruccion", "resultado_esperado", "verificacion"),
                "guion_clase": ("narracion",),
            }[tipo]
            _exigir(all(_texto(item[k]) for k in campos))
            if tipo == "flashcards":
                _exigir(_textos(item["etiquetas"]))
                _exigir(item.get("pista_didactica") is None or _texto(item["pista_didactica"]))
            elif tipo == "tutorial":
                _exigir(item.get("codigo") is None or _texto(item["codigo"]))
            else:
                _exigir(
                    type(item["duracion_min"]) in (int, float)
                    and math.isfinite(item["duracion_min"])
                    and item["duracion_min"] > 0
                )
                _exigir(_textos(item["puntos_diapositiva"]) and item["puntos_diapositiva"])
                _exigir(item.get("pregunta_interactiva") is None or _texto(item["pregunta_interactiva"]))
