import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { HistoryFullView } from '../components/StudyAndHistoryViews';
import { RejectionDiagnosis } from '../components/RejectionDiagnosis';
import { t } from '../i18n';
import type { APIClient } from '../api/client';

afterEach(() => cleanup());

describe('diagnóstico de un rechazo en el historial', () => {
  it('muestra el motivo aunque la causa del servidor no tenga traducción', async () => {
    const fila = {
      generation_id: 'gen-r', document_id: 'doc-a', status: 'rejected_quality', created_at: '2026-10-27T12:46:00Z',
      parametros: { perfil_destinatario: 'principiante', formato_salida: 'guion_clase', idioma_salida: 'es' },
    };
    const motivo = 'El contenido no superó la revisión de calidad. Falta cubrir parte del alcance solicitado.';
    const api = {
      workspaceStatus: vi.fn().mockResolvedValue(null),
      getDocuments: vi.fn().mockResolvedValue({ items: [] }),
      getGenerations: vi.fn().mockResolvedValue({ items: [fila], next_cursor: null }),
      // Forma real de GET /api/generations/{id} para un rechazo: la causa REJECTED_QUALITY no está en el catálogo.
      getGeneration: vi.fn().mockResolvedValue({
        generation_id: 'gen-r', status: 'rejected_quality', status_url: '/api/generations/gen-r',
        events_url: '/api/generations/gen-r/events', cancel_url: null, posicion_cola: null, contenido: null,
        persistencia: null,
        error: { code: 'INVALID_STATE', message: motivo, details: { causa: 'REJECTED_QUALITY', razones_bloqueo: ['cobertura_incompleta'] } },
      }),
    } as unknown as APIClient;
    render(<HistoryFullView apiClient={api} token="synthetic" workspaceId="ws" idiomaUi="es" onClose={vi.fn()} onSessionInvalid={vi.fn()} />);
    fireEvent.click(await screen.findByRole('button', { name: t('historial.abrir_resultado', 'es') }));
    expect(await screen.findByText(t('estados.trabajo.rejected_quality', 'es'))).toBeTruthy();
    expect(screen.getByText(t('rechazo.motivo.cobertura', 'es'))).toBeTruthy();
    expect(screen.queryByText(motivo)).toBeNull();
    expect(screen.getByText(t('material.rechazo_ayuda', 'es'))).toBeTruthy();
  });
});

describe('explicación del rechazo', () => {
  const error = (details: Record<string, unknown>) => ({ message: 'Mensaje del servidor.', details });

  it('explica una sola vez los motivos que describen lo mismo y muestra cifras y secciones citadas', () => {
    render(<RejectionDiagnosis idioma="es" error={error({
      razones_bloqueo: ['afirmacion_sin_respaldo', 'evidencia_insuficiente', 'fidelidad_requiere_revision'],
      revision: { afirmaciones: 22, sin_respaldo: 3, citadas: [
        { documento: 'Parte 1', seccion: 'Imágenes y capas', pagina: null },
        { documento: 'Parte 2', seccion: null, pagina: 2 },
      ] },
    })} />);
    expect(screen.getAllByRole('listitem')[0].textContent).toBe(t('rechazo.motivo.respaldo', 'es'));
    expect(screen.getByText(t('rechazo.cifras', 'es', { sin_respaldo: 3, afirmaciones: 22 }))).toBeTruthy();
    expect(screen.getByText('Parte 1 › Imágenes y capas')).toBeTruthy();
    expect(screen.getByText(`Parte 2 › ${t('rechazo.pagina', 'es', { pagina: 2 })}`)).toBeTruthy();
    expect(screen.queryByText('Mensaje del servidor.')).toBeNull();
    expect(screen.getAllByRole('listitem')).toHaveLength(3);
  });

  it('sin motivos conocidos muestra el mensaje y no inventa cifras incoherentes', () => {
    render(<RejectionDiagnosis idioma="es" error={error({
      razones_bloqueo: ['motivo_nuevo'], revision: { afirmaciones: 2, sin_respaldo: 5, citadas: [] },
    })} />);
    expect(screen.getByText('Mensaje del servidor.')).toBeTruthy();
    expect(screen.queryByText(/afirmaciones no se pudieron comprobar/)).toBeNull();
  });
});
