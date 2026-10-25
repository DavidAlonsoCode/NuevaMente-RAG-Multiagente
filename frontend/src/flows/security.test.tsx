import React from 'react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { APIClient } from '../api/client';
import { PackageViewer } from '../components/FormatRenderers';
import { SourcesDrawer } from '../components/SourcesDrawer';
import { validarPaquete } from './validators';
import fixtures from './fixtures.test-data.json';
import { t } from '../i18n';

const ataque = '<script>alert(1)</script><img src="https://externo.invalid/privado" onerror="alert(2)">[abrir](javascript:alert(3))';

beforeEach(() => { vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('Network disabled'))); });
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it.each(Object.entries(fixtures))('presenta texto hostil como texto literal en %s', (_formato, fixture) => {
  const paquete = structuredClone(fixture);
  paquete.contenido_adaptado.titulo = ataque;
  validarPaquete(paquete);
  const { container } = render(<PackageViewer paquete={paquete} idioma="es" apiClient={new APIClient()}
    token="synthetic-token" onSessionInvalid={vi.fn()} />);
  expect(screen.getByRole('heading', { name: ataque, level: 2 })).toBeTruthy();
  expect(container.querySelector('script, img, iframe, a[href^="javascript:"]')).toBeNull();
  expect(fetch).not.toHaveBeenCalled();
});

it('escapa la evidencia recuperada y no resuelve imágenes ni enlaces remotos', async () => {
  const api = { getDocumentSource: vi.fn().mockResolvedValue({ document_id: 'synthetic-doc', chunk_id: 'chunk',
    texto: ataque, pagina: null, seccion: null }) } as unknown as APIClient;
  const { container } = render(<SourcesDrawer documentId="synthetic-doc" referencias={[{ chunk_id: 'chunk' }]}
    idioma="es" apiClient={api} token="synthetic-token" onSessionInvalid={vi.fn()} />);
  fireEvent.click(screen.getByRole('button', { name: t('fuentes.cargar', 'es') }));
  await screen.findByText(ataque);
  expect(container.querySelector('script, img, iframe, a[href^="javascript:"]')).toBeNull();
  expect(fetch).not.toHaveBeenCalled();
});
