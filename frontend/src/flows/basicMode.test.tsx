import { AsyncTestScope } from '../test/asyncScope';
import React from 'react';
import { beforeEach, afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import fixtures from './fixtures.test-data.json';
import { validarGeneracionCompletada, validarPaquete } from './validators';
import { PackageViewer } from '../components/FormatRenderers';
import { APIClient } from '../api/client';
import { t } from '../i18n';
import { App } from '../App';
import { GenerationLiveStatus } from '../components/GenerationLiveStatus';

let scope: AsyncTestScope;
beforeEach(({ signal }) => { scope = new AsyncTestScope(signal); });
afterEach(async () => { await scope.close(); cleanup(); vi.restoreAllMocks(); });

it('el progreso básico informa una sola redacción sin prometer revisiones', () => {
  const stream: any = { progress: { step: 'writer', iteration: 1 }, phase: 'connected', error: null, reconnect: vi.fn() };
  render(<GenerationLiveStatus stream={stream} idioma="es" modo="basico" />);
  expect(screen.getByText(t('modo.redaccion_unica'))).toBeTruthy();
  expect(screen.queryByText(t('live.draft', 'es', { numero: 1 }))).toBeNull();
});

const basic = (): any => ({
  ...structuredClone(fixtures.flashcards), schema_version: '1.1', modo_generacion: 'basico', status: 'no_evaluado',
  evaluacion_calidad: { estado_evaluacion: 'no_realizada', anclaje_fuente_score: null, cantidad_afirmaciones: null,
    cantidad_respaldadas: null, claridad_pedagogica: null, adecuacion_perfil: null, cobertura_objetivos: null,
    coherencia_didactica: null, verificacion_visual: null, observaciones: null, razones_bloqueo: null },
  trazabilidad: { modelo_verificacion: null },
});

it('acepta el básico explícito y conserva el contrato revisado histórico', () => {
  expect(() => validarPaquete(basic())).not.toThrow();
  expect(() => validarPaquete(fixtures.flashcards)).not.toThrow();
  const reviewed = { ...fixtures.flashcards, schema_version: '1.1', modo_generacion: 'multiagente' };
  expect(() => validarPaquete(reviewed)).not.toThrow();
});

it.each([
  (p: any) => { p.status = 'aprobado'; },
  (p: any) => { p.modo_generacion = 'multiagente'; },
  (p: any) => { p.evaluacion_calidad.anclaje_fuente_score = 1; },
  (p: any) => { p.evaluacion_calidad.cantidad_afirmaciones = 0; },
  (p: any) => { p.trazabilidad.modelo_verificacion = 'juez-inventado'; },
  (p: any) => { p.schema_version = '1.0'; },
])('rechaza un básico con revisión, métricas o versión falsificadas', (mutate) => {
  const p = basic(); mutate(p);
  expect(() => validarPaquete(p)).toThrow();
});

it.each(['es', 'en', 'pt'] as const)('presenta el básico sin sello ni score en %s', (idioma) => {
  const p = basic();
  render(<PackageViewer paquete={p} idioma={idioma} token={null} registrarEstudio={false}
    apiClient={{} as APIClient} onSessionInvalid={vi.fn()} />);
  expect(screen.getAllByText(t('modo.basico', idioma)).length).toBeGreaterThan(0);
  expect(screen.getByText(t('modo.aviso', idioma))).toBeTruthy();
  expect(screen.queryByText(t('material.revisado', idioma))).toBeNull();
  expect(screen.queryByText(t('calidad.anclaje', idioma))).toBeNull();
});

it('el resultado debe conservar el modo de su solicitud y la persistencia confirmada', () => {
  const p = basic();
  const job = { generation_id: p.generation_id, status: 'completed', contenido: p,
    persistencia: { provider: 'mock', status_upload: 'completado' } };
  const params: any = { document_id: p.documento_fuente.document_id,
    perfil_destinatario: p.metadatos.perfil_aplicado, formato_salida: p.metadatos.formato_generado,
    nicho_sector: p.metadatos.nicho_sector, nivel_detalle: p.metadatos.nivel_detalle,
    idioma_salida: p.metadatos.idioma_salida, alcance: p.metadatos.alcance, modo_generacion: 'basico' };
  expect(validarGeneracionCompletada(job, p.generation_id, params)).toBe(p);
  expect(() => validarGeneracionCompletada(job, p.generation_id, { ...params, modo_generacion: 'multiagente' })).toThrow();
  expect(() => validarGeneracionCompletada({ ...job, persistencia: null }, p.generation_id, params)).toThrow();
});

it('seleccionar otro modo no genera ni vuelve a cargar el documento', async () => {
  sessionStorage.clear();
  vi.spyOn(APIClient.prototype, 'createWorkspace').mockResolvedValue({ workspace_id: 'ws', token: 'synthetic', recovery_code: 'synthetic-code' });
  const docs = vi.spyOn(APIClient.prototype, 'getDocuments').mockResolvedValue({ items: [] });
  vi.spyOn(APIClient.prototype, 'getGenerations').mockResolvedValue({ items: [] });
  vi.spyOn(APIClient.prototype, 'workspaceStatus').mockResolvedValue({});
  const generate = vi.spyOn(APIClient.prototype, 'createGeneration');
  await scope.render(<App />);
  await waitFor(() => expect(docs).toHaveBeenCalled());
  const before = docs.mock.calls.length;
  const select = screen.getByLabelText(t('modo.selector'));
  expect((select as HTMLSelectElement).value).toBe('multiagente');
  fireEvent.change(select, { target: { value: 'basico' } });
  expect((select as HTMLSelectElement).value).toBe('basico');
  expect(generate).not.toHaveBeenCalled();
  expect(docs).toHaveBeenCalledTimes(before);
});
