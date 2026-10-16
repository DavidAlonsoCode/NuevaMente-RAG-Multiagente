import { useEffect, useRef, useState } from 'react';
import { APIClient, APIError } from '../api/client';
import { GenerationProgress } from '../api/sse';
import { ESTADOS_TRABAJO, FORMATOS, IDIOMAS_SALIDA, NICHOS, NIVELES_DETALLE, PERFILES, GenerateRequest } from '../api/types';

export const isActive = (status: string) => status === 'queued' || status === 'running';
export type ConnectionPhase = 'connecting' | 'connected' | 'reconnecting' | 'error' | 'closed';

export function validateSnapshot(value: any, id: string): void {
  if (value?.generation_id !== id || !ESTADOS_TRABAJO.includes(value?.status) ||
      (value.status !== 'completed' && value.contenido != null)) {
    throw new APIError('INVALID_RESPONSE', 'Estado de generación incompatible.', 502);
  }
}

export function retryDelay(value: string | null, failures: number, now = Date.now()): number {
  const requested = value && /^\d+$/.test(value) ? Number(value) * 1000 : value ? Date.parse(value) - now : 0;
  return Math.max(Math.min(30000, 1000 * 2 ** Math.min(failures, 5)),
    Number.isFinite(requested) ? Math.min(requested, 2147483647) : 0);
}

function pause(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise(resolve => {
    const done = () => { clearTimeout(timer); signal.removeEventListener('abort', done); resolve(); };
    const timer = setTimeout(done, ms);
    signal.addEventListener('abort', done, { once: true });
    if (signal.aborted) done();
  });
}

export function useGenerationProgress({ api, token, generationId, enabled, onSnapshot, onSessionInvalid }: {
  api: APIClient; token: string | null; generationId: string | null; enabled: boolean;
  onSnapshot: (job: any) => void; onSessionInvalid: () => void;
}) {
  const key = `${token ?? ''}:${generationId ?? ''}`;
  const callbacks = useRef({ onSnapshot, onSessionInvalid });
  callbacks.current = { onSnapshot, onSessionInvalid };
  const cursor = useRef({ key, id: 0, notBefore: 0 });
  const [revision, setRevision] = useState(0);
  const [state, setState] = useState<{ key: string; phase: ConnectionPhase;
    progress: GenerationProgress | null; error: APIError | null }>({ key, phase: 'connecting', progress: null, error: null });

  useEffect(() => {
    if (!enabled || !token || !generationId) return;
    const controller = new AbortController();
    const signal = controller.signal;
    if (cursor.current.key !== key) cursor.current = { key, id: 0, notBefore: 0 };
    setState(previous => ({ key, phase: 'connecting',
      progress: previous.key === key && previous.progress && isActive(previous.progress.status) ? previous.progress : null, error: null }));
    const update = (value: Partial<typeof state>) => {
      if (!signal.aborted) setState(previous => ({ ...previous, ...value, key }));
    };
    const snapshot = async () => {
      const job = await api.getGeneration(token, generationId);
      if (signal.aborted) return false;
      validateSnapshot(job, generationId);
      callbacks.current.onSnapshot(job);
      return isActive(job.status);
    };
    void (async () => {
      let failures = 0;
      while (!signal.aborted) {
        let delay = 1000;
        try {
          await pause(Math.max(0, cursor.current.notBefore - Date.now()), signal);
          if (signal.aborted) return;
          if (!await snapshot()) { update({ phase: 'closed' }); return; }
          for await (const event of api.streamGenerationEvents(token, generationId,
            { signal, lastEventId: cursor.current.id })) {
            if (signal.aborted) return;
            if (event.kind === 'open') { update({ phase: 'connected', error: null }); continue; }
            if (event.kind === 'heartbeat') {
              if (!await snapshot()) { update({ phase: 'closed' }); return; }
              continue;
            }
            cursor.current.id = event.id;
            failures = 0;
            update({ progress: event.data });
            if (!isActive(event.data.status) && !await snapshot()) {
              update({ phase: 'closed' }); return;
            }
          }
          if (!signal.aborted && !await snapshot()) { update({ phase: 'closed' }); return; }
        } catch (error) {
          if (signal.aborted) return;
          const failure = error instanceof APIError ? error : new APIError('SERVICE_UNAVAILABLE', 'Conexión interrumpida.', 503);
          if (failure.statusCode === 401) { callbacks.current.onSessionInvalid(); return; }
          if ([400, 404, 409, 422].includes(failure.statusCode) || failure.code === 'INVALID_RESPONSE') {
            update({ phase: 'error', error: failure }); return;
          }
          delay = retryDelay(failure.retryAfter, failures++);
        }
        cursor.current.notBefore = Date.now() + delay;
        update({ phase: 'reconnecting' });
      }
    })();
    return () => controller.abort();
  }, [api, token, generationId, enabled, key, revision]);

  const visible = state.key === key ? state : { phase: 'connecting' as const, progress: null, error: null };
  return { phase: visible.phase, progress: visible.progress, error: visible.error,
    reconnect: () => setRevision(value => value + 1) };
}

/** Descubre una solicitud existente; nunca admite ni regenera contenido. */
export async function recoverActiveGeneration(api: APIClient, token: string) {
  const page = await api.getGenerations(token, { limit: 100 });
  if (!Array.isArray(page?.items)) throw new APIError('INVALID_RESPONSE', 'Historial incompatible.', 502);
  const active = page.items.filter((entry: any) => isActive(entry?.status));
  if (active.length > 1) throw new APIError('INVALID_RESPONSE', 'Más de un trabajo intensivo en el espacio.', 502);
  const item = active[0];
  if (!item) return null;
  const params = item.parametros;
  const scope = params?.alcance;
  if (typeof item.generation_id !== 'string' || !item.generation_id ||
      typeof item.document_id !== 'string' || params?.document_id !== item.document_id ||
      !PERFILES.includes(params?.perfil_destinatario) || !FORMATOS.includes(params?.formato_salida) ||
      !NICHOS.includes(params?.nicho_sector) || !NIVELES_DETALLE.includes(params?.nivel_detalle) ||
      !IDIOMAS_SALIDA.includes(params?.idioma_salida) || !scope ||
      !['documento_completo', 'seccion'].includes(scope.tipo) ||
      (scope.tipo === 'seccion' && (typeof scope.seccion_id !== 'string' || !scope.seccion_id))) {
    throw new APIError('INVALID_RESPONSE', 'Solicitud recuperada incompatible.', 502);
  }
  const doc = await api.getDocument(token, item.document_id);
  if (doc?.document_id !== item.document_id) throw new APIError('INVALID_RESPONSE', 'Fuente incompatible.', 502);
  const route = `/api/generations/${encodeURIComponent(item.generation_id)}`;
  return { generation_id: item.generation_id, status: item.status, status_url: route, events_url: route + '/events',
    parametros: params as GenerateRequest, documento_titulo: typeof doc.titulo === 'string' ? doc.titulo : item.document_id,
    seccion_titulo: scope.tipo === 'seccion' ? doc.cobertura?.secciones_procesadas?.find((s: any) => s.seccion_id === scope.seccion_id)?.titulo ?? scope.seccion_id : null };
}
