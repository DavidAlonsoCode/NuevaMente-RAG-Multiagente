import { AsyncTestScope } from '../test/asyncScope';
import React from 'react';
import { cleanup, fireEvent, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { APIClient, APIError } from '../api/client';
import { ChatTutorView } from '../components/StudyAndHistoryViews';
import { clearStudyState } from './studyState';
import fixtures from './fixtures.test-data.json';
import { t } from '../i18n';

const pregunta = '¿Qué aportan las fuentes?';
const respuesta = (texto = 'Respuesta de A+B', ids = ['a', 'b']) => ({ document_id: 'a', document_ids: ids,
  document_hash: 'a'.repeat(64), pregunta, perfil_destinatario: 'principiante', idioma_salida: 'es',
  estado: 'respondida', respuesta: texto, referencias: [{ chunk_id: 'chunk-a' }], evaluacion_calidad: fixtures.flashcards.evaluacion_calidad });
const completo = (texto?: string, ids?: string[]) => ({ job_id: 'job-ab', status: 'completed', result: respuesta(texto, ids) });
const base = { token: 'synthetic', workspaceId: 'ws', perfil: 'principiante' as const, idiomaSalida: 'es' as const,
  idiomaUi: 'es' as const, onSessionInvalid: vi.fn(), documentId: 'b', documentIds: ['b', 'a'] };
const cliente = () => ({ getDocument: vi.fn().mockResolvedValue({ status: 'ready' }),
  createChat: vi.fn().mockResolvedValue(completo()), getJob: vi.fn().mockResolvedValue(completo()),
  getDocumentSource: vi.fn().mockResolvedValue({ document_id: 'a', chunk_id: 'chunk-a', texto: 'Fuente A', pagina: null, seccion: null }) });
type Cliente = ReturnType<typeof cliente>;
const props = (api: Cliente) => ({ ...base, apiClient: api as unknown as APIClient });
const tick = async (ms = 0) => { await scope.act(async () => { await vi.advanceTimersByTimeAsync(ms); }); };
async function enviar() {
  const input = screen.getByRole('textbox');
  fireEvent.change(input, { target: { value: pregunta } });
  const form = input.closest('form')!;
  expect(within(form).getByRole('button', { name: t('chat.enviar') }).hasAttribute('disabled')).toBe(false);
  await scope.act(() => { fireEvent.submit(form); });
}
let scope: AsyncTestScope;
beforeEach(({ signal }) => {
  scope = new AsyncTestScope(signal);
  clearStudyState(); base.onSessionInvalid.mockClear();
  vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('Network disabled')));
});
afterEach(async () => { await scope.close(); cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers(); });

it('usa A en admisión inmediata y citas aunque el foco sea B y la selección venga B+A', async () => {
  const api = cliente();
  await scope.render(<ChatTutorView {...props(api)} />);
  await enviar();
  await screen.findByText('Respuesta de A+B');
  expect(api.createChat).toHaveBeenCalledWith('synthetic', { document_id: 'a', document_ids: ['a', 'b'],
    pregunta, perfil_destinatario: 'principiante', idioma_salida: 'es' }, expect.any(String));
  fireEvent.click(screen.getByRole('button', { name: t('fuentes.cargar') }));
  await screen.findByText('Fuente A');
  expect(api.getDocumentSource).toHaveBeenCalledWith('synthetic', 'a', 'chunk-a', { demo: false });
});

it.each(['manual', 'automatico'])('valida el primario canónico al consultar un resultado: %s', async modo => {
  vi.useFakeTimers();
  const api = cliente(); api.createChat.mockResolvedValue({ job_id: 'job-ab', status: 'queued' } as any);
  await scope.render(<ChatTutorView {...props(api)} />);
  await tick();
  fireEvent.change(screen.getByRole('textbox'), { target: { value: pregunta } });
  fireEvent.submit(screen.getByRole('textbox').closest('form')!);
  await tick();
  if (modo === 'manual') { fireEvent.click(screen.getByRole('button', { name: t('chat.actualizar') })); await tick(); }
  else await tick(5000);
  expect(api.getJob).toHaveBeenCalledWith('synthetic', 'job-ab');
  expect(screen.getByText('Respuesta de A+B')).toBeTruthy();
});

it('reintenta exactamente la misma intención y acepta el resultado inmediato de A', async () => {
  const api = cliente();
  api.createChat.mockRejectedValueOnce(new APIError('SERVICE_UNAVAILABLE', 'Fallo simulado', 503));
  await scope.render(<ChatTutorView {...props(api)} />);
  await enviar();
  await screen.findByRole('alert');
  fireEvent.click(screen.getByRole('button', { name: t('chat.reenviar') }));
  await screen.findByText('Respuesta de A+B');
  expect(api.createChat.mock.calls[1]).toEqual(api.createChat.mock.calls[0]);
});

it.each(['primario', 'conjunto', 'job'])('rechaza un resultado consultado ajeno por %s', async caso => {
  const api = cliente(); api.createChat.mockResolvedValue({ job_id: 'job-ab', status: 'queued' } as any);
  const ajena = completo('Contenido ajeno');
  if (caso === 'primario') ajena.result.document_id = 'b';
  if (caso === 'conjunto') ajena.result.document_ids = ['a', 'c'];
  if (caso === 'job') ajena.job_id = 'job-otro';
  api.getJob.mockResolvedValue(ajena);
  await scope.render(<ChatTutorView {...props(api)} />);
  await enviar();
  fireEvent.click(await screen.findByRole('button', { name: t('chat.actualizar') }));
  await screen.findByRole('alert');
  expect(screen.queryByText('Contenido ajeno')).toBeNull();
});

it('rechaza también una admisión inmediata de A+C cuando se solicitó A+B', async () => {
  const api = cliente(); api.createChat.mockResolvedValue(completo('Contenido ajeno', ['a', 'c']));
  await scope.render(<ChatTutorView {...props(api)} />);
  await enviar(); await screen.findByRole('alert');
  expect(screen.queryByText('Contenido ajeno')).toBeNull();
});

it('separa A+B de A+C y conserva A+B al volver en orden B+A', async () => {
  const api = cliente();
  api.createChat.mockResolvedValueOnce(completo()).mockResolvedValueOnce(completo('Respuesta de A+C', ['a', 'c']));
  const view = await scope.render(<ChatTutorView {...props(api)} />);
  await enviar(); await screen.findByText('Respuesta de A+B');
  await scope.act(() => { view.rerender(<ChatTutorView {...props(api)} documentId="c" documentIds={['c', 'a']} />); });
  expect(screen.queryByText('Respuesta de A+B')).toBeNull();
  await enviar(); await screen.findByText('Respuesta de A+C');
  expect(api.createChat.mock.calls[1][1]).toMatchObject({ document_id: 'a', document_ids: ['a', 'c'] });
  view.rerender(<ChatTutorView {...props(api)} documentId="a" documentIds={['a', 'b']} />);
  expect(screen.getByText('Respuesta de A+B')).toBeTruthy();
  expect(screen.queryByText('Respuesta de A+C')).toBeNull();
});

it.each(['admision', 'sondeo'])('descarta respuestas tardías tras A+B → A+C → A+B: %s', async fase => {
  const api = cliente(); let resolve!: (datos: any) => void;
  const tardia = new Promise<any>(r => { resolve = r; });
  if (fase === 'admision') api.createChat.mockReturnValueOnce(tardia);
  else { api.createChat.mockResolvedValue({ job_id: 'job-ab', status: 'queued' } as any); api.getJob.mockReturnValueOnce(tardia); }
  const view = await scope.render(<ChatTutorView {...props(api)} />);
  await enviar();
  if (fase === 'sondeo') fireEvent.click(await screen.findByRole('button', { name: t('chat.actualizar') }));
  view.rerender(<ChatTutorView {...props(api)} documentId="c" documentIds={['a', 'c']} />);
  expect(screen.queryByText(pregunta)).toBeNull();
  view.rerender(<ChatTutorView {...props(api)} />);
  await scope.act(async () => resolve(completo('Respuesta tardía descartada')));
  expect(screen.queryByText('Respuesta tardía descartada')).toBeNull();
  if (fase === 'admision') fireEvent.click(screen.getByRole('button', { name: t('chat.reenviar') }));
  else fireEvent.click(screen.getByRole('button', { name: t('chat.actualizar') }));
  await screen.findByText('Respuesta de A+B');
  expect(api.createChat).toHaveBeenCalledTimes(fase === 'admision' ? 2 : 1);
  if (fase === 'admision') expect(api.createChat.mock.calls[1]).toEqual(api.createChat.mock.calls[0]);
});

it('un 401 tardío no invalida otro workspace ni mezcla conversaciones', async () => {
  const api = cliente(); let reject!: (error: APIError) => void;
  api.createChat.mockReturnValueOnce(new Promise((_resolve, fail) => { reject = fail; }));
  const view = await scope.render(<ChatTutorView {...props(api)} />);
  await enviar();
  view.rerender(<ChatTutorView {...props(api)} workspaceId="ws-nuevo" token="token-nuevo" />);
  await scope.act(async () => reject(new APIError('SESSION_INVALID', 'Anterior', 401)));
  expect(base.onSessionInvalid).not.toHaveBeenCalled();
  expect(screen.queryByText(pregunta)).toBeNull();
  expect(screen.queryByRole('alert')).toBeNull();
});
