import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { HistoryFullView, ProgressView } from '../components/StudyAndHistoryViews';
import { PackageViewer } from '../components/FormatRenderers';
import { RecentGenerations } from '../components/RecentGenerations';
import { diaLocal, validarProgreso } from './validators';
import fixtures from './fixtures.test-data.json';
import { t } from '../i18n';
import type { APIClient } from '../api/client';

afterEach(() => cleanup());

const progreso = (extra: Record<string, unknown> = {}) => ({
  version: 1, conceptos_revisados: 1, conceptos_repasos: 2, conceptos: [], flashcards_vistas: 0, quiz_intentos: 0,
  quiz_aciertos_primer_intento: 0, quiz_total_primer_intento: 0, quiz_aciertos_posteriores: 0,
  tiempo_estimado_restante_minutos: 0, ...extra,
});

const fila = (id: string, extra: Record<string, unknown> = {}) => ({
  generation_id: id, document_id: 'doc-a', status: 'completed', created_at: '2026-10-27T12:00:00Z',
  parametros: { perfil_destinatario: 'principiante', formato_salida: 'flashcards', idioma_salida: 'es' }, ...extra,
});

const apiHistorial = (items: any[]) => ({
  workspaceStatus: vi.fn().mockResolvedValue(null),
  getDocuments: vi.fn().mockResolvedValue({ items: [
    { document_id: 'doc-a', titulo: 'Parte 1' }, { document_id: 'doc-b', titulo: 'Parte 2' },
  ] }),
  getGenerations: vi.fn().mockResolvedValue({ items, next_cursor: null }),
  getGeneration: vi.fn(),
});

const historial = (api: any, extra: Record<string, unknown> = {}) =>
  render(<HistoryFullView apiClient={api as unknown as APIClient} token="synthetic" workspaceId="ws" idiomaUi="es"
    onClose={vi.fn()} onSessionInvalid={vi.fn()} {...extra} />);

describe('Tu avance', () => {
  it('explica cada contador y cómo medir aciertos cuando todavía no hay quiz', async () => {
    const api = { getProgress: vi.fn().mockResolvedValue(progreso()) } as unknown as APIClient;
    render(<ProgressView apiClient={api} token="t" idiomaUi="es" pendingEvents={[]} onRetryPending={vi.fn()}
      onSessionInvalid={vi.fn()} />);
    expect(await screen.findByText(t('progreso.sin_quiz', 'es'))).toBeTruthy();
    for (const ayuda of ['ayuda_conceptos', 'ayuda_repasos', 'ayuda_flashcards', 'ayuda_primer_intento', 'ayuda_intentos']) {
      expect(screen.getByText(t(`progreso.${ayuda}`, 'es'))).toBeTruthy();
    }
    expect(screen.getByText(t('progreso.repasos', 'es'))).toBeTruthy();
  });

  it('no muestra el aviso de quiz cuando ya hubo respuestas', async () => {
    const api = { getProgress: vi.fn().mockResolvedValue(progreso({
      quiz_intentos: 2, quiz_total_primer_intento: 2, quiz_aciertos_primer_intento: 1,
    })) } as unknown as APIClient;
    render(<ProgressView apiClient={api} token="t" idiomaUi="es" pendingEvents={[]} onRetryPending={vi.fn()}
      onSessionInvalid={vi.fn()} />);
    expect(await screen.findByText('1 / 2')).toBeTruthy();
    expect(screen.queryByText(t('progreso.sin_quiz', 'es'))).toBeNull();
  });

  it('rechaza un estado de concepto mal formado', () => {
    expect(() => validarProgreso(progreso({ conceptos: [{ document_id: 'd', concepto: 'X', veces: 1, ultima_fecha: '2026-10-27' }] }))).not.toThrow();
    expect(() => validarProgreso(progreso({ conceptos: [{ document_id: 'd', concepto: 'X', veces: 0 }] }))).toThrow();
    expect(() => validarProgreso(progreso({ conceptos: [{ document_id: 'd', concepto: 'X', veces: 1, ultima_fecha: '27/10' }] }))).toThrow();
    expect(() => validarProgreso(progreso({ conceptos_repasos: -1 }))).toThrow();
  });
});

describe('conceptos clave del material', () => {
  const paquete = fixtures.flashcards as any;
  const clave = `${paquete.generation_id}::VCN`;
  const visor = (conceptosMarcados: Record<string, string | null>, onMarkConcept = vi.fn()) =>
    <PackageViewer paquete={paquete} idioma="es" apiClient={{} as APIClient} token="t"
      conceptosMarcados={conceptosMarcados} onMarkConcept={onMarkConcept} onSessionInvalid={vi.fn()} />;

  it('el botón de la ruta es visible y sin marcar ofrece marcar el concepto', () => {
    const onMarkConcept = vi.fn();
    render(visor({}, onMarkConcept));
    const ruta = screen.getByRole('button', { name: t('material.preparar', 'es') });
    expect(ruta.className).toContain('nm-btn-primary');
    fireEvent.click(ruta);
    fireEvent.click(screen.getByRole('button', { name: t('progreso.marcar_concepto', 'es', { concepto: 'VCN' }) }));
    expect(onMarkConcept).toHaveBeenCalledWith(paquete.documento_fuente.document_id, 'VCN', paquete.generation_id);
  });

  it('marcado otro día se puede repasar; marcado hoy queda deshabilitado', () => {
    const view = render(visor({ [clave]: '2020-01-01' }));
    fireEvent.click(screen.getByRole('button', { name: t('material.preparar', 'es') }));
    expect((screen.getByRole('button', { name: t('progreso.repasar_concepto', 'es', { concepto: 'VCN' }) }) as HTMLButtonElement).disabled).toBe(false);
    view.rerender(visor({ [clave]: diaLocal() }));
    expect((screen.getByRole('button', { name: t('progreso.revisado_hoy', 'es', { concepto: 'VCN' }) }) as HTMLButtonElement).disabled).toBe(true);
  });
});

describe('historial', () => {
  it('lista todas las fuentes de una generación de varios documentos', async () => {
    historial(apiHistorial([fila('g1', { document_ids: ['doc-a', 'doc-b'] }), fila('g2', { document_ids: ['doc-a'] })]));
    expect(await screen.findByText(new RegExp(`${t('historial.documentos', 'es')}: Parte 1 · Parte 2`))).toBeTruthy();
    expect(await screen.findByText(new RegExp(`${t('historial.documento', 'es')}: Parte 1 ·`))).toBeTruthy();
  });

  it('los filtros consultan al tocar Aplicar y Limpiar vuelve a la lista completa', async () => {
    const api = apiHistorial([fila('g1')]);
    historial(api);
    await screen.findByText(t('historial.mostrando', 'es', { cantidad: 1 }));
    expect(api.getGenerations).toHaveBeenCalledTimes(1);
    fireEvent.change(screen.getByLabelText(t('historial.perfil', 'es')), { target: { value: 'ejecutivo' } });
    expect(screen.getByText(t('historial.filtros_pendientes', 'es'))).toBeTruthy();
    expect(api.getGenerations).toHaveBeenCalledTimes(1);
    api.getGenerations.mockResolvedValueOnce({ items: [], next_cursor: null });
    fireEvent.click(screen.getByRole('button', { name: t('historial.aplicar_filtros', 'es') }));
    expect(await screen.findByText(t('historial.sin_resultados', 'es'))).toBeTruthy();
    expect(api.getGenerations).toHaveBeenLastCalledWith('synthetic', expect.objectContaining({ perfil: 'ejecutivo' }));
    fireEvent.click(screen.getByRole('button', { name: t('historial.limpiar_filtros', 'es') }));
    await screen.findByText(t('historial.mostrando', 'es', { cantidad: 1 }));
    expect(api.getGenerations.mock.calls.at(-1)?.[1]).not.toHaveProperty('perfil');
  });

  it('comparar el mismo resultado dos veces explica qué elegir, sin hablar de hash', async () => {
    historial(apiHistorial([fila('g1'), fila('g2')]));
    await screen.findByText(t('historial.mostrando', 'es', { cantidad: 2 }));
    fireEvent.change(screen.getByLabelText(t('historial.adaptacion_0', 'es')), { target: { value: 'g1' } });
    fireEvent.change(screen.getByLabelText(t('historial.adaptacion_1', 'es')), { target: { value: 'g1' } });
    expect(await screen.findByText(t('historial.comparar_mismo', 'es'))).toBeTruthy();
    expect(t('historial.comparacion_incompatible', 'es')).not.toMatch(/hash/i);
  });

  it('abre directamente la generación pedida y resalta los botones de navegación', async () => {
    const api = apiHistorial([fila('g1')]);
    api.getGeneration.mockResolvedValue({
      generation_id: 'g1', status: 'cancelled', status_url: '/api/generations/g1', events_url: '/api/generations/g1/events',
      cancel_url: null, posicion_cola: null, contenido: null, persistencia: null, error: null,
    });
    historial(api, { abrirDetalle: { id: 'g1', pedido: 1 } });
    expect(await screen.findByText(t('estados.trabajo.cancelled', 'es'))).toBeTruthy();
    expect(api.getGeneration).toHaveBeenCalledWith('synthetic', 'g1');
    expect(screen.getByRole('button', { name: t('historial.volver', 'es') }).className).toContain('nm-btn-primary');
    expect(screen.getByRole('button', { name: t('historial.cerrar', 'es') }).className).toContain('nm-btn-primary');
  });
});

describe('generaciones recientes', () => {
  it('se actualizan al terminar una generación y abren la elegida', async () => {
    const getGenerations = vi.fn()
      .mockResolvedValueOnce({ items: [], next_cursor: null })
      .mockResolvedValue({ items: [fila('g9')], next_cursor: null });
    const onOpen = vi.fn();
    const props = { api: { getGenerations } as unknown as APIClient, token: 't', idioma: 'es' as const, onOpen };
    const view = render(<RecentGenerations {...props} actualizar="gen-1:running" />);
    const details = view.container.querySelector('details')!;
    details.open = true;
    fireEvent(details, new Event('toggle'));
    expect(await screen.findByText(t('historial.recientes_vacio', 'es'))).toBeTruthy();
    view.rerender(<RecentGenerations {...props} actualizar="gen-1:completed" />);
    fireEvent.click(await screen.findByRole('button', { name: new RegExp(t('formato.flashcards', 'es')) }));
    expect(onOpen).toHaveBeenCalledWith('g9');
  });
});
