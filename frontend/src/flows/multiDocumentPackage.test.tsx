import React from 'react';
import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import fixtures from './fixtures.test-data.json';
import { validarGeneracionCompletada, validarPaquete } from './validators';
import { PackageViewer } from '../components/FormatRenderers';
import { APIClient } from '../api/client';
import { t } from '../i18n';

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

const FORMATOS = ['flashcards', 'quiz', 'tutorial', 'resumen_ejecutivo', 'guion_clase'] as const;

// Paquete multidocumental tal como lo emite el backend (draft_validation.validar_borrador).
const multi = (fmt: typeof FORMATOS[number], ids = ['a', 'b']): any => {
  const p: any = structuredClone((fixtures as any)[fmt]);
  p.documento_fuente.document_id = ids[0];
  p.documentos_fuente = ids.map((id) => ({ ...p.documento_fuente, document_id: id, titulo: `Fuente ${id}` }));
  p.metadatos.alcance = { tipo: 'multi_documento', seccion_id: null, document_ids: ids,
    secciones_cubiertas: p.metadatos.alcance.secciones_cubiertas };
  return p;
};

const trabajo = (p: any) => ({ generation_id: p.generation_id, status: 'completed', contenido: p,
  persistencia: { provider: 'mock', status_upload: 'completado' } });

const parametros = (p: any, ids = ['a', 'b']): any => ({ document_id: ids[0], document_ids: ids,
  perfil_destinatario: p.metadatos.perfil_aplicado, formato_salida: p.metadatos.formato_generado,
  nicho_sector: p.metadatos.nicho_sector, nivel_detalle: p.metadatos.nivel_detalle,
  idioma_salida: p.metadatos.idioma_salida, alcance: { tipo: 'multi_documento', document_ids: ids } });

it.each(FORMATOS)('acepta el alcance multidocumental en %s', (fmt) => {
  expect(() => validarPaquete(multi(fmt))).not.toThrow();
});

it('el guion multidocumental completa la solicitud que lo pidió', () => {
  const p = multi('guion_clase');
  expect(validarGeneracionCompletada(trabajo(p), p.generation_id, parametros(p))).toBe(p);
});

it.each([
  ['una sola fuente', (p: any) => { p.metadatos.alcance.document_ids = ['a']; }],
  ['fuentes repetidas', (p: any) => { p.metadatos.alcance.document_ids = ['a', 'a']; }],
  ['más de cinco fuentes', (p: any) => { p.metadatos.alcance.document_ids = ['a', 'b', 'c', 'd', 'e', 'f']; }],
  ['primario ajeno a las fuentes', (p: any) => { p.documento_fuente.document_id = 'z'; }],
  ['tipo inventado', (p: any) => { p.metadatos.alcance.tipo = 'inventado'; }],
  ['varias fuentes con documento_completo', (p: any) => { p.metadatos.alcance.tipo = 'documento_completo'; }],
])('rechaza un alcance con %s', (_caso, mutar) => {
  const p = multi('guion_clase');
  mutar(p);
  expect(() => validarPaquete(p)).toThrow();
});

it('rechaza un resultado de otro conjunto de fuentes', () => {
  vi.spyOn(console, 'warn').mockImplementation(() => {});
  const p = multi('guion_clase', ['a', 'c']);
  expect(() => validarGeneracionCompletada(trabajo(p), p.generation_id, { ...parametros(p), document_ids: ['a', 'b'] }))
    .toThrow();
  expect(console.warn).toHaveBeenCalled();
});

it('muestra el guion multidocumental con todas sus fuentes', () => {
  render(<PackageViewer paquete={multi('guion_clase')} idioma="es" token={null} registrarEstudio={false}
    apiClient={{} as APIClient} onSessionInvalid={vi.fn()} />);
  expect(screen.queryByText(t('errors.INVALID_RESPONSE', 'es'))).toBeNull();
  expect(screen.getByText('Fuente a · Fuente b')).toBeTruthy();
});

