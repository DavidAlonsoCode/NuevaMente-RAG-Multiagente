# Issue 58 — Ensayo reproducible de copia y restauración sintética

Registro documental del **16/10/2026**. Implementación leída en `develop`
`f2924151302001141bec65aadd081d3247e485c4`; las funciones de copia/restauración
son instrucciones locales del [runbook, sección 16](../../despliegue-oci.md#16-backup-físico-consistente-del-volumen-completo),
todavía sin commit. No son una CLI del backend ni evidencia de una VM.
La [matriz de verificación](../../verificacion-issue-58.md) conserva los resultados
históricos de etapa 3 y registra separadamente esta consolidación de etapa 4.

## Entorno y límites

PC Windows AMD64, Python 3.11.9, SQLite 3.45.1. El ensayo usa únicamente la
biblioteca estándar; no instala paquetes, importa la aplicación, lee `.env`,
abre datos del proyecto ni llama a proveedores. Todos los destinos son nuevos
bajo `tempfile.mkdtemp`, fuera del repo; no hay borrados ni restauración encima
de un destino ocupado. No requiere el `resultado.json` temporal de etapa 3.

Se extraen por AST **sólo tres funciones** del runbook: no se ejecutan Docker,
`fcntl`, UID/GID, rutas `/etc`, `/var` ni las llamadas operativas que rodean esas
funciones. Los checks root/POSIX del procedimiento real quedan fuera del ensayo.
El archivo llamado Chroma es una SQLite y un binario **ficticios**: no se abre el
motor Chroma. El esquema 11 de la fixture es una etiqueta de prueba, no una base
operativa creada por `RegistroOperativo` ni una prueba de migraciones.

## Procedimiento exacto

**Máquina/shell:** PC/PowerShell, raíz del repo, `.venv/Scripts/python.exe`
existente. **Previo:** revisar estas instrucciones y las tres funciones de sección
16; no usar Python `-O` (las guardas utilizan `assert`). **Resultado esperado:**
11 casos `PASS`; se imprime la ruta temporal y versiones. **Consumo:** CPU/disco
temporal local, sin red. **Parar:** excepción, caso no rechazado o hash diferente;
conservar el temporal e informar, sin adaptar el test para esconder el fallo.

La fixture confirma una fila en la base principal y otra en WAL. Su subproceso
termina con `os._exit(0)` después de `commit`: no hay escritor vivo al copiar,
y no se ejecuta el checkpoint habitual de cierre. Se incluyen ledger artificial,
WAL/SHM, catálogo, mantenimiento, lock y temporal. Las funciones físicas son las
mismas de la receta; se verifican hashes **antes** de abrir SQLite restaurada.

```powershell
@'
import ast, hashlib, io, json, os, platform, re, shutil, sqlite3, subprocess, sys, tarfile, tempfile
import stat
from pathlib import Path, PurePosixPath

guia = Path('docs/despliegue-oci.md').read_text(encoding='utf-8')
nombres = {'crear_copia', 'restaurar_copia', 'verificar_sqlite_restaurado'}
funciones = []
for bloque in re.findall(r'```bash\n(.*?)\n```', guia, re.S):
    for cuerpo in re.findall(r"<<'PY'\n(.*?)\nPY(?:\n|$)", bloque, re.S):
        funciones.extend(n for n in ast.parse(cuerpo).body
                         if isinstance(n, ast.FunctionDef) and n.name in nombres)
assert len(funciones) == 3 and {f.name for f in funciones} == nombres
exec(compile(ast.Module(body=funciones, type_ignores=[]), '<funciones-documentadas>', 'exec'), globals())
raiz = Path(tempfile.mkdtemp(prefix='nuevamente-issue58-sintetico-auditoria-')).resolve()
(raiz / '.synthetic-only').write_text('Datos artificiales exclusivamente\n')
origen = raiz / 'origen'
origen.mkdir()
(origen / 'chroma').mkdir()
(origen / 'chroma/segmento.bin').write_bytes(b'Chroma ficticio\x00' * 80)
for nombre, contenido in {'catalogo.json': '{}', 'mantenimiento.json': '{}',
                          'runtime.lock': '', 'temporal.tmp': 'incluir'}.items():
    (origen / nombre).write_text(contenido)
fixture = r'''
import os, sqlite3, sys
from pathlib import Path
p = Path(sys.argv[1])
c = sqlite3.connect(p / 'operativo.db')
c.execute('PRAGMA journal_mode=WAL')
c.execute('PRAGMA wal_autocheckpoint=0')
c.executescript('CREATE TABLE schema_migrations(version INTEGER); INSERT INTO schema_migrations VALUES(11);'
               'CREATE TABLE registros(id INTEGER PRIMARY KEY, nombre TEXT); INSERT INTO registros VALUES(1,"base");'
               'CREATE TABLE revocaciones(id INTEGER PRIMARY KEY);')
c.commit()
c.execute('PRAGMA wal_checkpoint(TRUNCATE)')
c.execute('INSERT INTO registros VALUES(2,"solo_WAL")')
c.commit()
l = sqlite3.connect(p / 'oci_budget.sqlite3')
l.execute('CREATE TABLE solicitudes(mes TEXT PRIMARY KEY, usadas INTEGER)')
l.execute('INSERT INTO solicitudes VALUES("2026-10",1234)')
l.commit()
l.close()
z = sqlite3.connect(p / 'chroma/chroma.sqlite3')
z.execute('CREATE TABLE ficticia(id INTEGER)')
z.execute('INSERT INTO ficticia VALUES(1)')
z.commit()
z.close()
os._exit(0)
'''
subprocess.run([sys.executable, '-B', '-c', fixture, str(origen)], check=True)
assert (origen / 'operativo.db-wal').stat().st_size > 0
assert (origen / 'operativo.db-shm').exists()

def hashes(directorio):
    return {p.relative_to(directorio).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in directorio.rglob('*') if p.is_file()}

def consultar(ruta, sql):
    db = sqlite3.connect(ruta.resolve().as_uri() + '?mode=ro', uri=True)
    try:
        return db.execute(sql).fetchall()
    finally:
        db.close()

resultados = []
def aprobado(nombre, detalle):
    resultados.append({'caso': nombre, 'resultado': 'PASS', 'detalle': detalle})

antes = hashes(origen)
copia = raiz / 'snapshot'
m = crear_copia(origen, copia, {'commit': 'SINTETICO_NO_RELEASE', 'images': 'FICTICIAS'})
assert antes == hashes(origen) and len(antes) == 10
assert {x['path'] for x in m['files'] if x['type'] == 'file'} == set(antes)
aprobado('copia_completa_origen_inmutable', {'archivos': 10, 'wal_bytes': (origen / 'operativo.db-wal').stat().st_size})
restaurado = raiz / 'restaurado'
restaurar_copia(copia, restaurado)
assert hashes(restaurado) == antes
aprobado('restauracion_fisica', '10 hashes iguales antes de abrir SQLite')
bases = verificar_sqlite_restaurado(restaurado)
assert len(bases) == 3
assert consultar(restaurado / 'operativo.db', 'SELECT COUNT(*) FROM registros') == [(2,)]
aprobado('sqlite_con_wal', bases)
principal = raiz / 'solo-principal'
principal.mkdir()
shutil.copyfile(origen / 'operativo.db', principal / 'operativo.db')
assert verificar_sqlite_restaurado(principal)[0]['integridad'] == 'ok'
assert consultar(principal / 'operativo.db', 'SELECT COUNT(*) FROM registros') == [(1,)]
aprobado('integridad_no_demuestra_completitud', 'Integridad ok, pero solo 1 de 2 registros sin WAL')

def clonar(nombre):
    destino = raiz / nombre
    shutil.copytree(copia, destino)
    return destino

def guardar_manifiesto(directorio, datos):
    (directorio / 'manifest.json').write_text(json.dumps(datos))

def rechazar(nombre, archivo):
    destino = raiz / ('rechazo-' + nombre)
    try:
        restaurar_copia(archivo, destino)
    except (AssertionError, KeyError, ValueError, tarfile.TarError):
        pass
    else:
        raise AssertionError('No rechazo: ' + nombre)
    assert not destino.exists() or not list(destino.iterdir())
    aprobado(nombre, 'Rechazo antes de extraer archivos')

x = clonar('checksum')
b = bytearray((x / 'volume.tar.gz').read_bytes())
b[-15] ^= 1
(x / 'volume.tar.gz').write_bytes(b)
rechazar('archivo_corrupto', x)
x = clonar('hash-inventario')
d = json.loads((x / 'manifest.json').read_text())
next(f for f in d['files'] if f['type'] == 'file')['sha256'] = '0' * 64
guardar_manifiesto(x, d)
rechazar('hash_de_archivo_incorrecto', x)
x = clonar('sin-wal')
d = json.loads((x / 'manifest.json').read_text())
with tarfile.open(copia / 'volume.tar.gz', 'r:gz') as entrada, tarfile.open(x / 'volume.tar.gz', 'w:gz') as salida:
    for miembro in entrada.getmembers():
        if miembro.name != 'operativo.db-wal':
            salida.addfile(miembro, entrada.extractfile(miembro) if miembro.isfile() else None)
d['archive_sha256'] = hashlib.sha256((x / 'volume.tar.gz').read_bytes()).hexdigest()
guardar_manifiesto(x, d)
rechazar('wal_omitido_del_tar', x)
for nombre, tipo in [('traversal', 'path'), ('enlace', 'symlink')]:
    x = raiz / nombre
    x.mkdir()
    d = json.loads(json.dumps(m))
    d['files'] = [f for f in d['files'] if f['path'] == '.']
    miembro = tarfile.TarInfo('../fuera.txt' if tipo == 'path' else 'enlace')
    miembro.mode = 0o600
    d['files'].append({'path': miembro.name, 'type': 'file', 'uid': 0, 'gid': 0, 'mode': 0o600,
                       'mtime_ns': 0, 'size': 1, 'sha256': hashlib.sha256(b'x').hexdigest()})
    with tarfile.open(x / 'volume.tar.gz', 'w:gz') as salida:
        directorio = tarfile.TarInfo('.')
        directorio.type = tarfile.DIRTYPE
        directorio.uid, directorio.gid, directorio.mode = (d['files'][0][k] for k in ('uid', 'gid', 'mode'))
        salida.addfile(directorio)
        if tipo == 'symlink':
            miembro.type, miembro.linkname = tarfile.SYMTYPE, '../fuera.txt'
            salida.addfile(miembro)
        else:
            miembro.size = 1
            salida.addfile(miembro, io.BytesIO(b'x'))
    d['archive_sha256'] = hashlib.sha256((x / 'volume.tar.gz').read_bytes()).hexdigest()
    guardar_manifiesto(x, d)
    rechazar('rechazo_' + nombre, x)
assert not (raiz / 'fuera.txt').exists()
ocupado = raiz / 'ocupado'
ocupado.mkdir()
(ocupado / 'sentinela').write_text('conservar')
try:
    restaurar_copia(copia, ocupado)
except AssertionError:
    pass
else:
    raise AssertionError('Acepto destino ocupado')
assert (ocupado / 'sentinela').read_text() == 'conservar' and len(list(ocupado.iterdir())) == 1
aprobado('destino_ocupado', 'Sentinela intacta')
db = sqlite3.connect(origen / 'operativo.db')
db.execute('INSERT INTO registros VALUES(3,"posterior")')
db.execute('INSERT INTO revocaciones VALUES(88)')
db.commit()
db.close()
db = sqlite3.connect(origen / 'oci_budget.sqlite3')
db.execute('UPDATE solicitudes SET usadas=1434')
db.commit()
db.close()
antiguo = raiz / 'restaurado-antiguo'
restaurar_copia(copia, antiguo)
verificar_sqlite_restaurado(antiguo)
assert consultar(antiguo / 'operativo.db', 'SELECT COUNT(*) FROM registros') == [(2,)]
assert consultar(antiguo / 'operativo.db', 'SELECT COUNT(*) FROM revocaciones') == [(0,)]
assert consultar(antiguo / 'oci_budget.sqlite3', 'SELECT usadas FROM solicitudes') == [(1234,)]
assert consultar(origen / 'oci_budget.sqlite3', 'SELECT usadas FROM solicitudes') == [(1434,)]
assert hashlib.sha256((copia / 'volume.tar.gz').read_bytes()).hexdigest() == m['archive_sha256']
aprobado('backup_antiguo', 'Faltan registro/revocacion posteriores; ledger 1234 frente a 1434')
assert len(resultados) == 11
print(json.dumps({'python': sys.version, 'sqlite': sqlite3.sqlite_version, 'plataforma': platform.platform(),
                  'directorio_temporal': str(raiz), 'resultados': resultados}, ensure_ascii=True, indent=2))
'@ | .venv/Scripts/python.exe -B -
if ($LASTEXITCODE -ne 0) { throw 'Fallo del ensayo sintetico; no adoptar ninguna copia' }
```

## Resultados y alcance

La etapa 3 registró 11 casos satisfactorios, con 10 archivos, WAL de 4.152 bytes,
tres bases SQLite íntegras y dos registros recuperados. La base principal aislada
pasó integridad pero perdió el registro de WAL; el backup antiguo omitió una
revocación y consumo posteriores. Esos resultados conservan su fecha y alcance;
el procedimiento de arriba explicita ahora la construcción y las comprobaciones.
La **repetición de consolidación de etapa 4, 16/10/2026**, ejecutó el cuerpo
Python exactamente como aparece arriba: **11 PASS, cero fallos**, diez hashes
físicos conservados, WAL de 4.152 bytes y tres bases verificadas. Recuperó ambos
registros con WAL y sólo uno sin él; rechazó checksum/hash incorrectos, WAL
omitido, traversal, symlink y destino ocupado. El snapshot antiguo conservó
1.234 solicitudes frente a 1.434 posteriores y omitió la revocación posterior.
El resultado se imprime al ejecutar y queda resumido aquí; no depende de que
sobrevivan fixtures o JSON temporales. Versiones y límites son los de arriba.

No demuestra compatibilidad de versiones/esquema real, Chroma, propietarios/modos
POSIX, ACL/xattrs, `flock`, apagado de productores reales, custodia externa,
resistencia a fallos de energía ni restauración funcional del servicio. SHA256
detecta alteraciones accidentales; no autentica un manifiesto también comprometido.
No acredita recuperación de cambios no respaldados/sincronizados ni permite
interpretar el ledger restaurado/nuevo como consumo mensual cero. La revisión
humana de #58 y la reproducción real de #133 siguen pendientes.
