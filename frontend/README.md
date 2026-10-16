# Frontend React de NuevaMente

`frontend/` contiene React 18, TypeScript y Vite. La interfaz anterior se conserva
en `frontend_streamlit_viejo/`, junto con sus pruebas históricas. Esas pruebas
no acreditan por sí solas la nueva interfaz.

## Cómo funciona

El navegador muestra React y solicita `/api/...` al mismo origen. En desarrollo,
Vite redirige esas solicitudes a FastAPI (`http://localhost:8000` por defecto).
FastAPI sigue gestionando sesiones, documentos, el worker, RAG, Gemini y OCI.
No hay claves de proveedores en React ni es necesario cambiar el backend a JavaScript.

En Docker, una etapa Node compila la interfaz. El contenedor final sirve los
archivos estáticos con el pequeño servidor Python de `entrypoint.py` y redirige
`/api` a `API_URL`. La receta existente de Caddy conserva el enrutamiento directo
de `/api` al backend y su protección de cabeceras. Las claves de origen nunca
se agregan al JavaScript ni se envían como variables `VITE_*`.
Las tipografías Inter y JetBrains Mono se incluyen en el build y se sirven
localmente, sin solicitudes del navegador a Google Fonts.

## Uso local

Con el backend ya iniciado en el puerto 8000, abrir otra terminal PowerShell:

```powershell
cd frontend
npm ci
npm run dev
```

Abrir http://127.0.0.1:8501. Los proveedores utilizados dependen de la
configuración efectiva del backend existente; este comando no cambia `.env`.
`API_URL` se puede configurar en el entorno de esa terminal para elegir otra API.

El token y el código de recuperación se mantienen en memoria. Guardar el código:
al recargar completamente o cerrar la pestaña se debe recuperar el espacio con él.
Sólo la preferencia de idioma se guarda en `sessionStorage`.

## Verificación sin proveedores reales

Desde la raíz, en una terminal, elegir una carpeta de prueba que todavía no exista:

```powershell
& .venv\Scripts\python.exe -m backend.tests.integration.react_backend --data-dir .data/react-prueba-nueva --port 18080
```

En otra terminal:

```powershell
cd frontend
$env:API_URL = 'http://127.0.0.1:18080'
npm run dev -- --port 18501
```

Abrir http://127.0.0.1:18501. Esta API usa SQLite/Chroma/worker/grafo reales con
Gemini y almacenamiento explícitamente simulados. No abre el estado operativo,
no carga `.env` y bloquea resolución DNS externa. Sus materiales son sintéticos
y no constituyen evidencia de calidad pedagógica real. Detener ambos procesos
con Ctrl+C al terminar.

```powershell
npm test
npm run build
npm audit
```

`npm run preview` sirve una vista local del build y conserva el proxy de API;
no es el servidor de despliegue. Las pruebas Python ordinarias aíslan `.env`.

Resultados y límites de la revisión: [verificación de React](../docs/verificacion-frontend-react.md).

## Alcance

Se conservan los contratos HTTP y los cinco formatos, el quiz, chat, glosario,
progreso, historial, comparación y los ejemplos publicados. El seguimiento de
generación todavía usa consultas de estado: esto no acredita el cierre completo
de SSE/reconexión del #33. Tampoco cierra automáticamente #46 o #53.
La generación directa desde fuentes compartidas demo sigue pendiente. La VM y
el HTTPS público se verifican en #133. La CI conserva las pruebas históricas
Caddy/Streamlit y añade una prueba del build React en navegador detrás de
Caddy/TLS locales con proveedores simulados; no equivale a aceptación pública.
