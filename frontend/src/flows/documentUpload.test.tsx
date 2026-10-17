import React from 'react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { App } from '../App';
import { APIClient, APIError } from '../api/client';
import { t } from '../i18n';

beforeEach(() => {
  sessionStorage.clear();
  vi.spyOn(APIClient.prototype, 'createWorkspace').mockResolvedValue({
    workspace_id: 'synthetic-ws', token: 'synthetic-token', recovery_code: 'synthetic-recovery',
  });
  vi.spyOn(APIClient.prototype, 'getDocuments').mockResolvedValue({ items: [], next_cursor: null });
  vi.spyOn(APIClient.prototype, 'workspaceStatus').mockResolvedValue(null);
  vi.spyOn(APIClient.prototype, 'getGenerations').mockResolvedValue({ items: [], next_cursor: null });
  vi.spyOn(APIClient.prototype, 'getProgress').mockResolvedValue({});
  vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('Network disabled')));
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it.each(['es', 'en', 'pt'] as const)('ofrece carga accesible en el área principal en %s', async (idioma) => {
  sessionStorage.setItem('nm_idioma_ui', idioma);
  render(<App />);
  const input = await screen.findByLabelText(t('upload.titulo', idioma), { selector: 'input' });
  expect(input.closest('main')).not.toBeNull();
  expect(document.querySelector('aside input[type="file"]')).toBeNull();
  expect(input.getAttribute('accept')).toBe('.pdf,.md,.txt');
  expect(screen.getByRole('button', { name: t('upload.procesar', idioma) }).hasAttribute('disabled')).toBe(true);
  const main = within(screen.getByRole('main'));
  fireEvent.click(main.getByRole('button', { name: t('upload.enviar_texto', idioma) }));
  expect(main.getByLabelText(t('upload.texto_titulo', idioma))).toBeTruthy();
  fireEvent.click(main.getByRole('button', { name: t('upload.titulo', idioma) }));
  expect(main.getByLabelText(t('upload.titulo', idioma), { selector: 'input' })).toBeTruthy();
});

it('conserva permisos e intención al reintentar un upload fallido desde el panel principal', async () => {
  const upload = vi.spyOn(APIClient.prototype, 'uploadFile')
    .mockRejectedValueOnce(new APIError('STORAGE_UNAVAILABLE', 'Fallo sintético', 503))
    .mockResolvedValueOnce({ document_id: 'synthetic-doc', job_id: 'synthetic-job', status: 'processing', status_url: '/api/jobs/synthetic-job', events_url: '/api/jobs/synthetic-job/events', cancel_url: '/api/jobs/synthetic-job/cancel' });
  render(<App />);
  const input = await screen.findByLabelText(t('upload.titulo', 'es'), { selector: 'input' });
  const file = new File(['Una red privada conecta subredes.'], 'fuente.txt', { type: 'text/plain' });
  Object.defineProperty(file, 'arrayBuffer', { value: async () => new TextEncoder().encode('Una red privada conecta subredes.').buffer });
  fireEvent.change(input, { target: { files: [file] } });
  const button = screen.getByRole('button', { name: t('upload.procesar', 'es') });
  expect(button.hasAttribute('disabled')).toBe(true);
  fireEvent.click(screen.getByLabelText(t('upload.confirmar_publico', 'es')));
  fireEvent.click(button);
  await screen.findByRole('alert');
  await waitFor(() => expect(button.hasAttribute('disabled')).toBe(false));
  fireEvent.click(button);
  await waitFor(() => expect(upload).toHaveBeenCalledTimes(2));
  expect(upload.mock.calls[0]).toEqual(upload.mock.calls[1]);
  expect(upload.mock.calls[0][4]?.idempotencyKey).toMatch(/^upload-/);
  await waitFor(() => expect((screen.getByLabelText(t('upload.confirmar_publico', 'es')) as HTMLInputElement).checked).toBe(false));
});

it('rechaza archivos no admitidos sin enviar al backend', async () => {
  const upload = vi.spyOn(APIClient.prototype, 'uploadFile');
  render(<App />);
  const input = await screen.findByLabelText(t('upload.titulo', 'es'), { selector: 'input' });
  fireEvent.change(input, { target: { files: [new File(['x'], 'archivo.exe')] } });
  fireEvent.click(screen.getByLabelText(t('upload.confirmar_publico', 'es')));
  fireEvent.click(screen.getByRole('button', { name: t('upload.procesar', 'es') }));
  expect(upload).not.toHaveBeenCalled();
});

it.each([['pdf', 'application/pdf'], ['md', 'text/markdown'], ['txt', 'text/plain']])(
  'envía multipart con título, sesión e idempotencia para %s', async (extension, mime) => {
    const recibo = { document_id: 'synthetic-doc', job_id: 'synthetic-job', status: 'processing',
      status_url: '/api/jobs/synthetic-job', events_url: '/api/jobs/synthetic-job/events', cancel_url: '/api/jobs/synthetic-job/cancel' };
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify(recibo), { status: 202 }));
    vi.stubGlobal('fetch', fetch);
    const result = await new APIClient().uploadFile('synthetic-token', `fuente.${extension}`,
      new Uint8Array([1, 2, 3]), mime, { title: 'Fuente técnica', idempotencyKey: 'synthetic-upload' });
    expect(result).toEqual(recibo);
    const [, options] = fetch.mock.calls[0];
    const headers = new Headers(options.headers);
    expect(headers.get('Authorization')).toBe('Bearer synthetic-token');
    expect(headers.get('Idempotency-Key')).toBe('synthetic-upload');
    expect(headers.has('Content-Type')).toBe(false);
    const body = options.body as FormData;
    expect(body.get('titulo')).toBe('Fuente técnica');
    const file = body.get('file') as File;
    expect(file.name).toBe(`fuente.${extension}`);
    expect(file.type).toBe(mime);
  }
);
