"""Ciclo de vida del cliente adquirido por VectorStoreChroma, sin proveedores."""

import sqlite3
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from app.core.rag import vectorstore
from app.core.rag.vectorstore import IndiceInconsistenteError, VectorStoreChroma
from chromadb.api.shared_system_client import SharedSystemClient
from test_vectorstore import chunks, ctx

pytestmark = pytest.mark.integration_mock


def embeddings(preparacion="actual"):
    return SimpleNamespace(
        modelo="vectores-locales",
        dimensiones=2,
        preparation_version=preparacion,
        collection_name="vectores-ciclo-de-vida",
        embed_documents=Mock(side_effect=lambda textos, **kw: [[1.0, 0.0] for _ in textos]),
    )


def referencias(ruta):
    with SharedSystemClient._refcount_lock:
        return SharedSystemClient._identifier_to_refcount.get(str(ruta.resolve()), 0)


def chunks_con_secciones():
    return [chunk.model_copy(update={"seccion_id": f"seccion:{i}"}) for i, chunk in enumerate(chunks(), 1)]


@pytest.mark.parametrize(
    "fase",
    ["lote", "metadata", "privada", "demo", "catalogo-conectar", "catalogo-schema", "interrupcion"],
)
def test_cierra_cliente_si_falla_cualquier_etapa_de_construccion(tmp_path, monkeypatch, fase):
    error = KeyboardInterrupt("interrupción simulada") if fase == "interrupcion" else RuntimeError(fase)
    cliente = Mock()
    cliente.get_max_batch_size.return_value = 64
    llamadas = []

    def coleccion(nombre, **kwargs):
        llamadas.append(nombre)
        if fase == ("demo" if nombre.startswith("demo-") else "privada"):
            raise error
        return SimpleNamespace(metadata=kwargs["metadata"], configuration={"hnsw": {"space": "cosine"}})

    cliente.get_or_create_collection.side_effect = coleccion
    if fase in ("lote", "interrupcion"):
        cliente.get_max_batch_size.side_effect = error
    datos = embeddings()
    if fase == "metadata":

        class MetadataRota:
            @property
            def modelo(self):
                raise error

        datos = MetadataRota()

    @contextmanager
    def catalogo(self):
        if fase == "catalogo-conectar":
            raise error
        db = Mock()
        db.executescript.side_effect = error if fase == "catalogo-schema" else None
        yield db

    monkeypatch.setattr(vectorstore.chromadb, "PersistentClient", Mock(return_value=cliente))
    monkeypatch.setattr(VectorStoreChroma, "_db", catalogo)
    with pytest.raises(type(error)) as capturada:
        VectorStoreChroma(tmp_path / "chroma", datos)
    assert capturada.value is error
    cliente.close.assert_called_once_with()
    assert len(llamadas) == {"lote": 0, "interrupcion": 0, "metadata": 0, "privada": 1}.get(fase, 2)


def test_fallo_del_cierre_conserva_error_original_y_diagnostico(tmp_path, monkeypatch):
    original = IndiceInconsistenteError("preparación incompatible")
    cierre = OSError("cierre simulado fallido")
    cliente = Mock()
    cliente.get_max_batch_size.side_effect = original
    cliente.close.side_effect = cierre
    monkeypatch.setattr(vectorstore.chromadb, "PersistentClient", Mock(return_value=cliente))
    with pytest.raises(IndiceInconsistenteError) as capturada:
        VectorStoreChroma(tmp_path / "chroma", embeddings())
    assert capturada.value is original
    assert str(capturada.value) == "preparación incompatible"
    assert any("OSError" in nota and "cierre simulado fallido" in nota for nota in original.__notes__)
    cliente.close.assert_called_once_with()


def adquirir_clientes(monkeypatch):
    fabricar = vectorstore.chromadb.PersistentClient
    clientes = []

    def abrir(*args, **kwargs):
        cliente = fabricar(*args, **kwargs)
        clientes.append(cliente)
        return cliente

    monkeypatch.setattr(vectorstore.chromadb, "PersistentClient", abrir)
    return clientes


@pytest.mark.parametrize("mantener_abierto", [False, True], ids=["ultimo-cliente", "cliente-compartido"])
def test_chroma_real_intentos_incompatibles_no_acumulan_referencias(tmp_path, monkeypatch, mantener_abierto):
    ruta = tmp_path / "chroma"
    datos = embeddings()
    clientes = adquirir_clientes(monkeypatch)
    vigente = None
    try:
        vigente = VectorStoreChroma(ruta, datos)
        originales = chunks_con_secciones()
        vigente.indexar("espacio-a", "doc-a", originales, contexto=ctx())
        sistema = vigente._cliente._system
        if not mantener_abierto:
            vigente.cerrar()
        base = referencias(ruta)
        assert (base > 0) == mantener_abierto
        incompatibles = embeddings("anterior")
        conteos = []
        for _ in range(2):
            with pytest.raises(IndiceInconsistenteError, match="preparación"):
                VectorStoreChroma(ruta, incompatibles)
            conteos.append(referencias(ruta))
            if mantener_abierto:
                assert vigente._cliente._system is sistema
                assert not vigente._cliente._closed
                assert (
                    vigente.listar_chunks("espacio-a", "doc-a", contexto=ctx(), source_hash=originales[0].document_hash)
                    == originales
                )
        assert conteos == [base, base]
        assert all(cliente._closed for cliente in clientes[1:])
        incompatibles.embed_documents.assert_not_called()
        if not mantener_abierto:
            assert str(ruta.resolve()) not in SharedSystemClient._identifier_to_system
        vigente.cerrar()
        vigente.cerrar()
        assert referencias(ruta) == 0

        reabierto = VectorStoreChroma(ruta, datos)
        try:
            assert reabierto.contar("espacio-a", "doc-a", contexto=ctx()) == len(originales)
            assert (
                reabierto.listar_chunks("espacio-a", "doc-a", contexto=ctx(), source_hash=originales[0].document_hash)
                == originales
            )
            assert datos.embed_documents.call_count == 1
            reabierto.cerrar()
            reabierto.cerrar()
            assert referencias(ruta) == 0
        finally:
            reabierto.cerrar()
    finally:
        for cliente in clientes:
            cliente.close()
    assert referencias(ruta) == 0
    assert str(ruta.resolve()) not in SharedSystemClient._identifier_to_system


@pytest.mark.parametrize("fase", ["conectar", "schema"])
def test_chroma_real_fallo_catalogo_libera_solo_cliente_del_intento(tmp_path, monkeypatch, fase):
    ruta = tmp_path / "chroma"
    clientes = adquirir_clientes(monkeypatch)
    datos = embeddings()
    conexiones = []
    error = sqlite3.OperationalError(f"fallo simulado del catálogo: {fase}")
    try:
        vigente = VectorStoreChroma(ruta, datos)
        originales = chunks_con_secciones()
        vigente.indexar("espacio-a", "doc-a", originales, contexto=ctx())
        base = referencias(ruta)
        sistema = vigente._cliente._system
        conectar = sqlite3.connect

        class ConexionFallida(sqlite3.Connection):
            def executescript(self, sql):
                raise error

        def conectar_catalogo(archivo, *args, **kwargs):
            if str(archivo).endswith("catalogo.sqlite3"):
                if fase == "conectar":
                    raise error
                kwargs["factory"] = ConexionFallida
                db = conectar(archivo, *args, **kwargs)
                conexiones.append(db)
                return db
            return conectar(archivo, *args, **kwargs)

        with monkeypatch.context() as parche:
            parche.setattr(vectorstore.sqlite3, "connect", conectar_catalogo)
            for _ in range(2):
                with pytest.raises(sqlite3.OperationalError) as capturada:
                    VectorStoreChroma(ruta, datos)
                assert capturada.value is error
            assert referencias(ruta) == base
            assert all(cliente._closed for cliente in clientes[1:])
            for db in conexiones:
                with pytest.raises(sqlite3.ProgrammingError, match="closed"):
                    db.execute("SELECT 1")
        assert vigente._cliente._system is sistema
        assert (
            vigente.listar_chunks("espacio-a", "doc-a", contexto=ctx(), source_hash=originales[0].document_hash)
            == originales
        )
        assert datos.embed_documents.call_count == 1
    finally:
        for cliente in clientes:
            cliente.close()
        for db in conexiones:
            db.close()
    assert referencias(ruta) == 0
