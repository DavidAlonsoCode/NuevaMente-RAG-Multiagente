import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { APIClient, APIError } from '../api/client';
import { ExportFormat, FORMATOS_EXPORTACION, PedagogicalStudentOutput } from '../api/types';
import fixtures from '../flows/fixtures.test-data.json';
import { t } from '../i18n';
import { ExportDownloads } from './ExportDownloads';
import { validarPaquete } from '../flows/validators';

const persistenciaOci = { status_upload: 'completado' as const, provider: 'oci' as const };
const boton = (fmt: ExportFormat) =>
  screen.getByRole('button', { name: t(`exportar.${fmt}`, 'es') }) as HTMLButtonElement;

function montar(paquete: unknown, overrides: Partial<React.ComponentProps<typeof ExportDownloads>> = {}) {
  const api = { downloadExport: vi.fn().mockResolvedValue(new Uint8Array([1, 2, 3])) };
  const props = {
    api: api as unknown as APIClient,
    token: 'synthetic',
    paquete: paquete as PedagogicalStudentOutput,
    persistencia: persistenciaOci,
    idioma: 'es' as const,
    onError: vi.fn(),
    onSessionInvalid: vi.fn(),
    ...overrides,
  };
  render(<ExportDownloads {...props} />);
  return { api, props };
}

beforeEach(() => {
  URL.createObjectURL = vi.fn().mockReturnValue('blob:synthetic');
  URL.revokeObjectURL = vi.fn();
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe('descargas multiformato', () => {
  it.each(['multiagente', 'basico'] as const)('descarga los seis formatos del paquete 1.1 %s', async (modo) => {
    const paquete: any = { ...structuredClone(fixtures.flashcards), schema_version: '1.1', modo_generacion: modo };
    if (modo === 'basico') {
      paquete.status = 'no_evaluado';
      paquete.evaluacion_calidad = {
        ...Object.fromEntries(Object.keys(paquete.evaluacion_calidad).map((key) => [key, null])),
        estado_evaluacion: 'no_realizada',
      };
      paquete.trazabilidad = { ...paquete.trazabilidad, modelo_verificacion: null };
    }
    validarPaquete(paquete);
    const { api, props } = montar(paquete);
    for (const fmt of FORMATOS_EXPORTACION) {
      expect(boton(fmt).disabled).toBe(false);
      fireEvent.click(boton(fmt));
      await waitFor(() => expect(api.downloadExport).toHaveBeenLastCalledWith('synthetic', paquete.generation_id, fmt));
      await waitFor(() => expect(boton(fmt).disabled).toBe(false));
    }
    expect(api.downloadExport).toHaveBeenCalledTimes(6);
    expect(props.onError).not.toHaveBeenCalled();
    expect(paquete.status).toBe(modo === 'basico' ? 'no_evaluado' : 'aprobado');
  });

  it('habilita los seis formatos para flashcards y descarga el pedido', async () => {
    const { api } = montar(fixtures.flashcards);
    for (const fmt of FORMATOS_EXPORTACION) expect(boton(fmt).disabled).toBe(false);
    fireEvent.click(boton('apkg'));
    await waitFor(() => expect(api.downloadExport).toHaveBeenCalledWith('synthetic', 'gen_01HX', 'apkg'));
  });

  it.each(['tutorial', 'quiz', 'resumen_ejecutivo', 'guion_clase'] as const)(
    'deshabilita CSV/TSV/APKG con explicación para %s',
    (formato) => {
      montar(fixtures[formato]);
      for (const fmt of ['json', 'md', 'pdf'] as const) expect(boton(fmt).disabled).toBe(false);
      for (const fmt of ['csv', 'tsv', 'apkg'] as const) {
        const b = boton(fmt);
        expect(b.disabled).toBe(true);
        expect(b.parentElement?.getAttribute('title')).toBe(t('exportar.solo_flashcards', 'es'));
        expect(b.getAttribute('aria-describedby')).toBeTruthy();
      }
    },
  );

  it('muestra «Guardado» solo con OCI confirmado y bucket/objeto solo en detalles técnicos', () => {
    montar(fixtures.flashcards);
    expect(screen.getByText(t('material.guardado', 'es'))).toBeTruthy();
    expect(screen.queryByText(/nuevamente-contenidos-educativos/)).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: t('comun.detalles_tecnicos', 'es') }));
    expect(screen.getByText(/outputs\/ws\/gen_01HX\/content\.json/)).toBeTruthy();
  });

  it('no afirma OCI con almacenamiento mock', () => {
    montar(fixtures.flashcards, { persistencia: { status_upload: 'completado', provider: 'mock' } });
    expect(screen.queryByText(t('material.guardado', 'es'))).toBeNull();
    expect(screen.getByText(t('almacenamiento.local_dev', 'es'))).toBeTruthy();
  });

  it('no descarga si la verificación previa indica que ya no está completado', async () => {
    const verificarAntes = vi.fn().mockResolvedValue(false);
    const { api } = montar(fixtures.tutorial, { verificarAntes });
    fireEvent.click(boton('pdf'));
    await waitFor(() => expect(verificarAntes).toHaveBeenCalled());
    expect(api.downloadExport).not.toHaveBeenCalled();
  });

  it('una sesión inválida no se muestra como error de descarga', async () => {
    const { api, props } = montar(fixtures.flashcards);
    api.downloadExport.mockRejectedValue(new APIError('SESSION_INVALID', 'Sesión inválida.', 401));
    fireEvent.click(boton('json'));
    await waitFor(() => expect(props.onSessionInvalid).toHaveBeenCalled());
    expect(props.onError).not.toHaveBeenCalled();
  });
});
