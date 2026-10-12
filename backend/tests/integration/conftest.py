"""Proveedores explícitos y fallos sembrados sólo en el harness de pruebas (#52)."""

import os
from dataclasses import replace

import pytest
import test_api_generate as generaciones
import test_chat as tutor
import test_glossary as glosarios

entorno_base = generaciones.entorno
entorno_generacion = generaciones.entorno
entorno_chat = tutor.entorno
entorno_glosario = glosarios.entorno


@pytest.fixture
def entorno(entorno_base, monkeypatch):
    e = entorno_base
    fallo = os.environ.get("NM_INTEGRATION_FAULT")
    if fallo == "request-id":
        solicitar = e.cliente.request

        def sin_request_id(*args, **kwargs):
            respuesta = solicitar(*args, **kwargs)
            respuesta.headers.pop("x-request-id", None)
            return respuesta

        monkeypatch.setattr(e.cliente, "request", sin_request_id)
        print("NM_FAULT=request-id")
    elif fallo == "retries":
        # Fallo acotado: cinco intentos en vez de tres, nunca un bucle infinito.
        e.gestor.controles = replace(e.gestor.controles, reintentos_transitorios=4)
        print("NM_FAULT=retries")
    elif fallo:
        pytest.fail("Fallo sembrado desconocido")
    return e
