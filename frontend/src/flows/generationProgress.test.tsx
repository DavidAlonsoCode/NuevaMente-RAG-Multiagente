import { AsyncTestScope } from '../test/asyncScope';
import React from 'react';
import { act, cleanup, fireEvent, render, renderHook, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { APIClient, APIError } from '../api/client';
import { GenerationEvent } from '../api/sse';
import { recoverActiveGeneration, retryDelay, useGenerationProgress } from './generationProgress';
import { ActiveGenerationPanel, App } from '../App';
import { t } from '../i18n';
import fixtures from './fixtures.test-data.json';

const running = { generation_id: 'gen-one', status: 'running', contenido: null };
const complete = { generation_id: 'gen-one', status: 'completed' };
const progress: GenerationEvent = { kind: 'progress', id: 7,
  data: { generation_id: 'gen-one', status: 'running', step: 'writer', iteration: 2 } };
const params = { document_id: 'doc-one', perfil_destinatario: 'principiante' as const, formato_salida: 'flashcards' as const,
  nicho_sector: 'general' as const, nivel_detalle: 'didactico' as const, idioma_salida: 'es' as const,
  alcance: { tipo: 'documento_completo' as const } };
let scope: AsyncTestScope;
beforeEach(({ signal }) => { scope = new AsyncTestScope(signal); vi.useFakeTimers(); });
afterEach(async () => { await scope.close(); cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); });
const tick = async (ms = 0) => { await scope.act(async () => { await vi.advanceTimersByTimeAsync(ms); }); };

it('reconecta con el último cursor y reconcilia el resultado sin otro POST', async () => {
  const stream = vi.fn().mockImplementationOnce(async function* () { yield progress; throw new APIError('SERVICE_UNAVAILABLE', '', 503); })
    .mockImplementationOnce(async function* () { yield { ...progress, id: 8, data: { ...progress.data, status: 'completed' } }; });
  const api = { getGeneration: vi.fn().mockResolvedValueOnce(running).mockResolvedValueOnce(running).mockResolvedValue(complete),
    streamGenerationEvents: stream, createGeneration: vi.fn() } as unknown as APIClient;
  const onSnapshot = vi.fn();
  const hook = renderHook(() => useGenerationProgress({ api, token: 'synthetic', generationId: 'gen-one',
    enabled: true, onSnapshot, onSessionInvalid: vi.fn() }));
  await tick();
  expect(hook.result.current.phase).toBe('reconnecting');
  await tick(1000);
  expect(stream.mock.calls[1][2].lastEventId).toBe(7);
  expect(onSnapshot).toHaveBeenLastCalledWith(complete);
  expect(api.createGeneration).not.toHaveBeenCalled();
});

it('reconcilia el estado al perder el evento terminal', async () => {
  const api = { getGeneration: vi.fn().mockResolvedValueOnce(running).mockResolvedValue(complete),
    streamGenerationEvents: vi.fn().mockImplementation(async function* () {}) } as unknown as APIClient;
  const callback = vi.fn();
  renderHook(() => useGenerationProgress({ api, token: 'synthetic', generationId: 'gen-one', enabled: true,
    onSnapshot: callback, onSessionInvalid: vi.fn() }));
  await tick();
  expect(callback).toHaveBeenLastCalledWith(complete);
  expect(api.streamGenerationEvents).toHaveBeenCalledOnce();
});

it('respeta Retry-After incluso si se pide reconexión manual', async () => {
  const api = { getGeneration: vi.fn().mockResolvedValue(running), streamGenerationEvents: vi.fn()
    .mockImplementation(async function* () { throw new APIError('RATE_LIMITED', '', 429, null, null, '5'); }) } as unknown as APIClient;
  const hook = renderHook(() => useGenerationProgress({ api, token: 'synthetic', generationId: 'gen-one', enabled: true,
    onSnapshot: vi.fn(), onSessionInvalid: vi.fn() }));
  await tick();
  act(() => hook.result.current.reconnect());
  await tick(4999);
  expect(api.streamGenerationEvents).toHaveBeenCalledOnce();
  await tick(1);
  expect(api.streamGenerationEvents).toHaveBeenCalledTimes(2);
  expect(retryDelay(new Date(Date.now() + 8000).toUTCString(), 0)).toBeGreaterThanOrEqual(7000);
});

it('ignora un estado pendiente del espacio anterior al cambiar token', async () => {
  let resolve!: (job: unknown) => void;
  const api = { getGeneration: vi.fn().mockReturnValueOnce(new Promise(r => { resolve = r; })).mockResolvedValue(complete) } as unknown as APIClient;
  const callback = vi.fn();
  const hook = renderHook(({ token }) => useGenerationProgress({ api, token, generationId: 'gen-one', enabled: true,
    onSnapshot: callback, onSessionInvalid: vi.fn() }), { initialProps: { token: 'old' } });
  await tick();
  hook.rerender({ token: 'new' });
  await tick();
  await scope.act(async () => resolve(running));
  expect(callback).toHaveBeenCalledTimes(1);
  expect(callback).toHaveBeenLastCalledWith(complete);
});

it.each([401, 404])('detiene la reconexión ante %i sin crear otra generación', async status => {
  const api = { getGeneration: vi.fn().mockRejectedValue(new APIError(status === 401 ? 'SESSION_INVALID' : 'NOT_FOUND', '', status)) } as unknown as APIClient;
  const invalid = vi.fn();
  const hook = renderHook(() => useGenerationProgress({ api, token: 'synthetic', generationId: 'gen-one', enabled: true,
    onSnapshot: vi.fn(), onSessionInvalid: invalid }));
  await tick(20000);
  expect(api.getGeneration).toHaveBeenCalledOnce();
  expect(status === 401 ? invalid.mock.calls.length : hook.result.current.phase).toBe(status === 401 ? 1 : 'error');
});

it('un 401 tardío del espacio anterior no cierra la sesión recuperada', async () => {
  let reject!: (error: unknown) => void;
  const api = { getGeneration: vi.fn().mockReturnValueOnce(new Promise((_resolve, fail) => { reject = fail; }))
    .mockResolvedValue(complete) } as unknown as APIClient;
  const invalid = vi.fn();
  const hook = renderHook(({ token }) => useGenerationProgress({ api, token, generationId: 'gen-one', enabled: true,
    onSnapshot: vi.fn(), onSessionInvalid: invalid }), { initialProps: { token: 'old' } });
  await tick();
  hook.rerender({ token: 'recovered' });
  await tick();
  await scope.act(async () => reject(new APIError('SESSION_INVALID', '', 401)));
  expect(invalid).not.toHaveBeenCalled();
});

it('recupera el trabajo propio desde el historial y conserva los parámetros', async () => {
  const api = { getGenerations: vi.fn().mockResolvedValue({ items: [{ ...running, document_id: 'doc-one', parametros: params }] }),
    getDocument: vi.fn().mockResolvedValue({ document_id: 'doc-one', titulo: 'Fuente de prueba' }), createGeneration: vi.fn() } as unknown as APIClient;
  const result = await recoverActiveGeneration(api, 'synthetic');
  expect(result).toMatchObject({ generation_id: 'gen-one', parametros: params, documento_titulo: 'Fuente de prueba' });
  expect(api.createGeneration).not.toHaveBeenCalled();
});

it('actualiza la posición de cola con un heartbeat confirmado', async () => {
  const api = { getGeneration: vi.fn().mockResolvedValueOnce({ ...running, status: 'queued', posicion_cola: 3 })
    .mockResolvedValue({ ...running, status: 'queued', posicion_cola: 2 }),
    streamGenerationEvents: vi.fn().mockImplementation(async function* (_t, _id, options) {
      yield { kind: 'heartbeat' };
      await new Promise(resolve => options.signal.addEventListener('abort', resolve, { once: true }));
    }) } as unknown as APIClient;
  const callback = vi.fn();
  renderHook(() => useGenerationProgress({ api, token: 'synthetic', generationId: 'gen-one', enabled: true,
    onSnapshot: callback, onSessionInvalid: vi.fn() }));
  await tick();
  expect(callback).toHaveBeenLastCalledWith(expect.objectContaining({ posicion_cola: 2 }));
});

it('conserva el cursor al cambiar el cliente por idioma y cierra la suscripción anterior', async () => {
  const stream = vi.fn().mockImplementation(async function* (_t, _id, options) {
    yield progress;
    await new Promise(resolve => options.signal.addEventListener('abort', resolve, { once: true }));
  });
  const first = { getGeneration: vi.fn().mockResolvedValue(running), streamGenerationEvents: stream } as unknown as APIClient;
  const second = { getGeneration: vi.fn().mockResolvedValue(running), streamGenerationEvents: vi.fn()
    .mockImplementation(async function* (_t, _id, options) {
      yield { kind: 'open' };
      await new Promise(resolve => options.signal.addEventListener('abort', resolve, { once: true }));
    }) } as unknown as APIClient;
  const hook = renderHook(({ api }) => useGenerationProgress({ api, token: 'synthetic', generationId: 'gen-one', enabled: true,
    onSnapshot: vi.fn(), onSessionInvalid: vi.fn() }), { initialProps: { api: first } });
  await tick();
  hook.rerender({ api: second });
  await tick();
  expect(stream.mock.calls[0][2].signal.aborted).toBe(true);
  expect(vi.mocked(second.streamGenerationEvents).mock.calls[0][2].lastEventId).toBe(7);
  expect(hook.result.current.progress?.iteration).toBe(2);
});

it('una lista de documentos tardía no cambia la fuente del trabajo recuperado', async () => {
  sessionStorage.clear();
  const doc = { document_id: 'doc-one', titulo: 'Fuente seleccionada', status: 'ready', metadata: null, cobertura: null, error: null };
  const docs = { items: [{ ...doc, document_id: 'doc-first', titulo: 'Otra fuente' }, doc] };
  let resolve!: (value: unknown) => void;
  vi.spyOn(APIClient.prototype, 'createWorkspace').mockResolvedValue({ workspace_id: 'workspace', token: 'synthetic', recovery_code: 'recovery' });
  vi.spyOn(APIClient.prototype, 'getDocuments').mockReturnValueOnce(new Promise(r => { resolve = r; })).mockResolvedValue(docs);
  vi.spyOn(APIClient.prototype, 'workspaceStatus').mockResolvedValue({});
  vi.spyOn(APIClient.prototype, 'getGenerations').mockResolvedValue({ items: [{ ...running, document_id: 'doc-one', parametros: params }] });
  vi.spyOn(APIClient.prototype, 'getDocument').mockResolvedValue(doc);
  vi.spyOn(APIClient.prototype, 'getGeneration').mockResolvedValue(running);
  vi.spyOn(APIClient.prototype, 'streamGenerationEvents').mockImplementation(async function* (_t, _id, options) {
    yield progress;
    await new Promise(resolve => options.signal.addEventListener('abort', resolve, { once: true }));
  });
  await scope.render(<App />);
  await tick();
  await scope.act(async () => resolve(docs));
  await tick();
  expect((screen.getByDisplayValue('Fuente seleccionada') as HTMLSelectElement).value).toBe('doc-one');
});

it('no recupera una solicitud cuyos parámetros apuntan a otro documento', async () => {
  const api = { getGenerations: vi.fn().mockResolvedValue({ items: [{ ...running, document_id: 'doc-other', parametros: params }] }) } as unknown as APIClient;
  await expect(recoverActiveGeneration(api, 'synthetic')).rejects.toMatchObject({ code: 'INVALID_RESPONSE' });
});

const base = () => ({ api: { cancelGeneration: vi.fn().mockResolvedValue(undefined), persistGeneration: vi.fn().mockResolvedValue(undefined) } as unknown as APIClient,
  token: 'synthetic', generacion: { generation_id: 'gen-one', status: 'queued', status_url: '/api/generations/gen-one',
    events_url: '/api/generations/gen-one/events', parametros: params, documento_titulo: 'Fuente de prueba', seccion_titulo: null },
  idioma: 'es' as const, conceptosMarcados: {}, onRefresh: vi.fn(), onMarkConcept: vi.fn(), onReviewFlashcard: vi.fn(),
  onSessionInvalid: vi.fn(), onChangeSource: vi.fn(), onNarrowScope: vi.fn(),
  stream: { phase: 'connected' as const, progress: progress.data, error: null, reconnect: vi.fn() } });

it('cancelar no presenta el trabajo como cancelado antes de la confirmación del worker', async () => {
  const props = base();
  const view = render(<ActiveGenerationPanel {...props} job={running} />);
  fireEvent.click(screen.getByRole('button', { name: t('generar.cancelar') }));
  await tick();
  expect(screen.getByText(t('live.cancel_requested'))).toBeTruthy();
  expect((screen.getByRole('button', { name: t('generar.cancelar') }) as HTMLButtonElement).disabled).toBe(true);
  view.rerender(<ActiveGenerationPanel {...props} job={{ ...running, status: 'cancelled', contenido: { titulo: 'private draft' } }} />);
  expect(screen.getByText(t('material.cancelado'))).toBeTruthy();
  expect(screen.queryByText('private draft')).toBeNull();
});

it.each(['rejected_quality', 'failed'])('no expone borradores en %s', status => {
  const props = base();
  render(<ActiveGenerationPanel {...props} job={{ ...running, status, contenido: { titulo: 'private draft' }, error: { code: 'INTERNAL' } }} />);
  expect(screen.queryByText('private draft')).toBeNull();
  if (status === 'rejected_quality') {
    expect(screen.getByText(t('live.rejected_reason'))).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: t('live.narrow') }));
    expect(props.onNarrowScope).toHaveBeenCalledOnce();
  }
});

it('reintenta sólo persistencia con la misma clave tras una respuesta incierta', async () => {
  const props = base();
  vi.mocked(props.api.persistGeneration).mockRejectedValueOnce(new APIError('STORAGE_UNAVAILABLE', '', 503)).mockResolvedValue(undefined);
  render(<ActiveGenerationPanel {...props} job={{ ...running, status: 'failed', error: { code: 'STORAGE_UNAVAILABLE' } }} />);
  const retry = screen.getByRole('button', { name: t('material.persist_reintentar') });
  fireEvent.click(retry); await tick(); fireEvent.click(retry); await tick();
  const calls = vi.mocked(props.api.persistGeneration).mock.calls;
  expect(calls).toHaveLength(2);
  expect(calls[0][2]).toBe(calls[1][2]);
  expect(props.onRefresh).toHaveBeenCalledOnce();
});

it('distingue un deadline técnico de rechazo de calidad y permite consultar estado', () => {
  const props = base();
  render(<ActiveGenerationPanel {...props} job={{ ...running, status: 'failed',
    error: { code: 'INVALID_STATE', details: { causa: 'DEADLINE' } } }} />);
  expect(screen.getByText(t('live.cause.DEADLINE'))).toBeTruthy();
  expect(screen.queryByText(t('live.rejected_reason'))).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: t('material.actualizar') }));
  expect(props.onRefresh).toHaveBeenCalledOnce();
});

it('sólo anuncia revisión cuando el paquete completado supera las validaciones', () => {
  const props = base();
  const packet = fixtures.flashcards;
  const source = packet.documento_fuente.document_id;
  const generationId = packet.generation_id;
  const parameters = { ...params, document_id: source };
  const view = render(<ActiveGenerationPanel {...props} generacion={{ ...props.generacion, generation_id: generationId, parametros: parameters }}
    job={{ generation_id: generationId, status: 'completed', contenido: packet, persistencia: { status_upload: 'pendiente', provider: 'mock' } }} />);
  expect(screen.queryByText(t('material.revisado'))).toBeNull();
  view.rerender(<ActiveGenerationPanel {...props} generacion={{ ...props.generacion, generation_id: generationId, parametros: parameters }}
    job={{ generation_id: generationId, status: 'completed', contenido: packet, persistencia: { status_upload: 'completado', provider: 'mock' } }} />);
  expect(screen.getAllByText(t('material.revisado')).length).toBeGreaterThan(0);
});
