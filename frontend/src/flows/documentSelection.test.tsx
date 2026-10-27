import { AsyncTestScope } from '../test/asyncScope';
import React from 'react';
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { App } from '../App';
import { APIClient } from '../api/client';
import { OutputLanguage } from '../api/types';
import { recoverActiveGeneration } from './generationProgress';
import { clearStudyState } from './studyState';
import { t } from '../i18n';

const doc = (id: string) => ({ document_id: id, titulo: `Fuente ${id}`, status: 'ready', metadata: null, error: null,
  cobertura: { paginas_procesadas: [], paginas_omitidas: [], paginas_visuales_pendientes: [],
    secciones_procesadas: [{ seccion_id: `${id}-sec`, titulo: `Sección ${id}`, linea_inicio: 1, linea_fin: 3 }] } });
const documentos = ['a', 'b', 'c', 'd', 'e', 'f'].map(doc);
const params = { document_id: 'a', document_ids: ['a', 'b'], perfil_destinatario: 'principiante',
  formato_salida: 'flashcards', nicho_sector: 'general', nivel_detalle: 'didactico', idioma_salida: 'es',
  alcance: { tipo: 'multi_documento', document_ids: ['a', 'b'] } };
const deferred = () => {
  let resolve!: (value: any) => void;
  return { promise: new Promise<any>(r => { resolve = r; }), resolve: (value: any) => resolve(value) };
};

let scope: AsyncTestScope;
beforeEach(({ signal }) => {
  scope = new AsyncTestScope(signal);
  sessionStorage.clear(); clearStudyState();
  vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('Network disabled')));
  vi.spyOn(APIClient.prototype, 'createWorkspace').mockResolvedValue({ workspace_id: 'ws', token: 'synthetic', recovery_code: 'code' });
  vi.spyOn(APIClient.prototype, 'getDocuments').mockResolvedValue({ items: documentos });
  vi.spyOn(APIClient.prototype, 'getGenerations').mockResolvedValue({ items: [] });
  vi.spyOn(APIClient.prototype, 'getDocument').mockImplementation(async (_token, id) => doc(id));
  vi.spyOn(APIClient.prototype, 'workspaceStatus').mockResolvedValue(null);
  vi.spyOn(APIClient.prototype, 'getProgress').mockResolvedValue({});
  vi.spyOn(APIClient.prototype, 'getGeneration').mockImplementation(async (_token, id) => ({ generation_id: id, status: 'running', contenido: null }));
  vi.spyOn(APIClient.prototype, 'streamGenerationEvents').mockImplementation(async function* (_token, _id, options) {
    yield { kind: 'open' };
    await new Promise(resolve => options.signal?.addEventListener('abort', resolve, { once: true }));
  });
  vi.spyOn(APIClient.prototype, 'createGeneration').mockResolvedValue({ generation_id: 'gen-new', status: 'queued',
    status_url: '/api/generations/gen-new', events_url: '/api/generations/gen-new/events' });
});
afterEach(async () => { await scope.close(); cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

const selected = () => screen.getAllByRole('checkbox').filter(box => (box as HTMLInputElement).checked)
  .map(box => box.closest('label')!.textContent);
async function generar(idioma: OutputLanguage = 'es') {
  fireEvent.click(within(screen.getByRole('complementary')).getByRole('button', { name: t('sidebar.generar', idioma) }));
  fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: t('generar.confirmar', idioma) }));
  await waitFor(() => expect(APIClient.prototype.createGeneration).toHaveBeenCalledTimes(1));
  return vi.mocked(APIClient.prototype.createGeneration).mock.calls[0][1];
}

it('restaura A+B sin mezclar C ni perder B ante páginas documentales tardías o incompletas', async () => {
  const primera = deferred(), segunda = deferred();
  vi.mocked(APIClient.prototype.getDocuments).mockReturnValueOnce(primera.promise).mockReturnValueOnce(segunda.promise);
  vi.mocked(APIClient.prototype.getGenerations).mockResolvedValue({ items: [{ generation_id: 'gen-old', status: 'running', document_id: 'a', parametros: params }] });
  await scope.render(<App />);
  await screen.findByLabelText('b', { selector: 'input' });
  expect(selected()).toEqual(['a', 'b']);
  await scope.act(async () => primera.resolve({ items: [doc('c'), doc('a')] }));
  expect((screen.getByLabelText('b') as HTMLInputElement).checked).toBe(true);
  expect(screen.getByRole('button', { name: t('sidebar.generar') }).hasAttribute('disabled')).toBe(true);
  expect(screen.getByText(t('sidebar.fuentes_no_disponibles'))).toBeTruthy();
  await scope.act(async () => segunda.resolve({ items: [doc('c'), doc('a'), doc('b')] }));
  await waitFor(() => expect(selected()).toEqual(['Fuente a', 'Fuente b']));
  fireEvent.change(screen.getByLabelText(t('sidebar.documento')), { target: { value: 'c' } });
  expect(selected()).toEqual(['Fuente a', 'Fuente b']);
  expect(await generar()).toMatchObject({ document_id: 'a', document_ids: ['a', 'b'],
    alcance: { tipo: 'multi_documento', document_ids: ['a', 'b'] } });
});

it.each([undefined, ['a']])('restaura el unidocumental histórico %j y su sección sin usar el foco B', async (ids) => {
  const pagina = deferred();
  vi.mocked(APIClient.prototype.getDocuments).mockReturnValue(pagina.promise);
  vi.mocked(APIClient.prototype.getGenerations).mockResolvedValue({ items: [{ generation_id: 'gen-old', status: 'running', document_id: 'a',
    parametros: { ...params, document_ids: ids, alcance: { tipo: 'seccion', seccion_id: 'a-sec' } } }] });
  await scope.render(<App />);
  await screen.findByLabelText('a', { selector: 'input' });
  await scope.act(async () => pagina.resolve({ items: documentos }));
  await waitFor(() => expect(selected()).toEqual(['Fuente a']));
  fireEvent.change(screen.getByLabelText(t('sidebar.documento')), { target: { value: 'b' } });
  expect(selected()).toEqual(['Fuente a']);
  const payload = await generar();
  expect(payload).toMatchObject({ document_id: 'a', alcance: { tipo: 'seccion', seccion_id: 'a-sec' } });
  expect(payload.document_ids).toBeUndefined();
});

it.each(['es', 'en', 'pt'] as const)('enfocar la sexta conserva las cinco fuentes y su payload en %s', async idioma => {
  sessionStorage.setItem('nm_idioma_ui', idioma);
  await scope.render(<App />);
  await screen.findByLabelText('Fuente a', { selector: 'input' });
  for (const id of ['b', 'c', 'd', 'e']) fireEvent.click(screen.getByLabelText(`Fuente ${id}`, { selector: 'input' }));
  fireEvent.change(screen.getByLabelText(t('sidebar.documento', idioma)), { target: { value: 'f' } });
  expect((screen.getByLabelText(t('sidebar.documento', idioma)) as HTMLSelectElement).value).toBe('f');
  expect(selected()).toEqual(['Fuente a', 'Fuente b', 'Fuente c', 'Fuente d', 'Fuente e']);
  expect(screen.getByLabelText('Fuente f', { selector: 'input' }).hasAttribute('disabled')).toBe(true);
  expect(screen.getByText(t('sidebar.foco_fuera_seleccion', idioma))).toBeTruthy();
  expect(await generar(idioma)).toMatchObject({ document_id: 'a', document_ids: ['a', 'b', 'c', 'd', 'e'] });
});

it.each(['es', 'en', 'pt'] as const)('seleccionar todos con seis disponibles es explícito y no trunca en %s', async idioma => {
  sessionStorage.setItem('nm_idioma_ui', idioma);
  await scope.render(<App />);
  await screen.findByLabelText('Fuente a', { selector: 'input' });
  const todos = screen.getByRole('button', { name: t('sidebar.seleccionar_todos', idioma) });
  expect(todos.hasAttribute('disabled')).toBe(true);
  expect(screen.getByText(t('sidebar.todos_supera_limite', idioma, { n: 6 }))).toBeTruthy();
  fireEvent.click(todos);
  expect(selected()).toEqual(['Fuente a']);
  const payload = await generar(idioma);
  expect(payload.document_id).toBe('a');
  expect(payload.document_ids).toBeUndefined();
});

it('seleccionar todos incluye exactamente las cinco disponibles', async () => {
  vi.mocked(APIClient.prototype.getDocuments).mockResolvedValue({ items: documentos.slice(0, 5) });
  await scope.render(<App />);
  await screen.findByLabelText('Fuente a', { selector: 'input' });
  fireEvent.click(screen.getByRole('button', { name: t('sidebar.seleccionar_todos') }));
  expect(selected()).toHaveLength(5);
  expect(await generar()).toMatchObject({ document_ids: ['a', 'b', 'c', 'd', 'e'] });
});

it('con varias fuentes genera en Básico y al volver a una recupera el modo elegido', async () => {
  vi.mocked(APIClient.prototype.getDocuments).mockResolvedValue({ items: documentos.slice(0, 2) });
  await scope.render(<App />);
  await screen.findByLabelText('Fuente a', { selector: 'input' });
  const modo = () => document.getElementById('app-generation-mode') as HTMLSelectElement;
  const multiagente = () => within(modo()).getByRole('option', { name: t('modo.multiagente') }) as HTMLOptionElement;
  expect(modo().value).toBe('multiagente');
  fireEvent.click(screen.getByRole('button', { name: t('sidebar.seleccionar_todos') }));
  expect(selected()).toHaveLength(2);
  expect(modo().value).toBe('basico');
  expect(multiagente().disabled).toBe(true);
  expect(screen.getByText(new RegExp(t('modo.varias_fuentes').slice(0, 40)))).toBeTruthy();
  fireEvent.click(screen.getByLabelText('Fuente b', { selector: 'input' }));
  expect(modo().value).toBe('multiagente');
  expect(multiagente().disabled).toBe(false);
  fireEvent.click(screen.getByLabelText('Fuente b', { selector: 'input' }));
  expect(await generar()).toMatchObject({ document_ids: ['a', 'b'], modo_generacion: 'basico' });
});

it('permite vaciar y volver a elegir explícitamente la única fuente disponible', async () => {
  vi.mocked(APIClient.prototype.getDocuments).mockResolvedValue({ items: [doc('a')] });
  await scope.render(<App />);
  const fuente = await screen.findByLabelText('Fuente a', { selector: 'input' });
  expect(selected()).toEqual(['Fuente a']);
  fireEvent.click(fuente);
  expect(selected()).toEqual([]);
  expect(screen.getByRole('button', { name: t('sidebar.generar') }).hasAttribute('disabled')).toBe(true);
  fireEvent.click(screen.getByLabelText('Fuente a', { selector: 'input' }));
  expect(selected()).toEqual(['Fuente a']);
  const payload = await generar();
  expect(payload.document_id).toBe('a');
  expect(payload.document_ids).toBeUndefined();
});

it.each([
  { ...params, document_id: 'b' },
  { ...params, document_ids: ['a', 'a'] },
  { ...params, alcance: { tipo: 'multi_documento', document_ids: ['a', 'c'] } },
  { ...params, document_ids: ['a', 'b', 'c', 'd', 'e', 'f'] },
])('rechaza una restauración sin conjunto y primario coherentes %#', async parametros => {
  vi.mocked(APIClient.prototype.getGenerations).mockResolvedValue({ items: [{ generation_id: 'gen-old', status: 'running', document_id: parametros.document_id, parametros }] });
  await expect(recoverActiveGeneration(new APIClient(), 'synthetic')).rejects.toMatchObject({ code: 'INVALID_RESPONSE' });
});
