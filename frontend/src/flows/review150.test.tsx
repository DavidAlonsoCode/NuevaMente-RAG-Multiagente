import React from 'react';
import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { APIClient } from '../api/client';
import { ChatTutorView, GlossaryView } from '../components/StudyAndHistoryViews';
import { AsyncTestScope } from '../test/asyncScope';
import { clearStudyState } from './studyState';
import { validarGlosario, validarPaquete, validarRespuestaChat } from './validators';
import fixtures from './fixtures.test-data.json';
import { t } from '../i18n';

let scope: AsyncTestScope;
beforeEach(({ signal }) => { scope = new AsyncTestScope(signal); clearStudyState(); });
afterEach(async () => { await scope.close(); cleanup(); vi.restoreAllMocks(); });
const payload = { document_id: 'a', pregunta: '¿De qué trata el documento?',
  perfil_destinatario: 'principiante', idioma_salida: 'es', modo_generacion: 'basico' as const };
const chat = () => ({ ...payload, document_hash: 'a'.repeat(64), estado: 'no_evaluado',
  respuesta: 'El documento presenta redes privadas.', referencias: [{ chunk_id: 'ch' }], evaluacion_calidad: null });
const glosario = () => ({ glossary_id: 'glos', documento_fuente: { document_id: 'a' },
  perfil_destinatario: 'principiante', idioma_salida: 'es', estado: 'no_evaluado', candidatos_omitidos: 0,
  evaluacion_calidad: null, trazabilidad: null,
  terminos: [{ id: 'term', termino_original: 'VCN', es_identificador: true, traduccion: null,
    estado: 'definido', definicion: 'Una red privada.', referencias: [{ chunk_id: 'ch' }] }] });
const props = { token: 'synthetic', documentId: 'a', perfil: 'principiante' as const,
  idiomaSalida: 'es' as const, idiomaUi: 'es' as const, onSessionInvalid: vi.fn() };

it('el tutor envía el modo básico y presenta la respuesta con aviso', async () => {
  const api = { getDocument: vi.fn().mockResolvedValue({ status: 'ready' }),
    createChat: vi.fn().mockResolvedValue({ job_id: 'chat', status: 'completed', result: chat() }) };
  await scope.render(<ChatTutorView {...props} workspaceId="ws" apiClient={api as unknown as APIClient} />);
  fireEvent.change(screen.getByLabelText(t('modo.selector')), { target: { value: 'basico' } });
  fireEvent.change(screen.getByRole('textbox'), { target: { value: payload.pregunta } });
  fireEvent.click(screen.getByRole('button', { name: t('chat.enviar') }));
  await screen.findByText(chat().respuesta);
  expect(api.createChat.mock.calls[0][1].modo_generacion).toBe('basico');
  expect(screen.getAllByText(t('modo.aviso')).length).toBeGreaterThan(0);
});

it('el glosario básico presenta definiciones sin etiqueta de verificado', async () => {
  const api = { createGlossary: vi.fn().mockResolvedValue({ job_id: 'glos', glossary_id: 'glos', status: 'completed' }),
    getGlossary: vi.fn().mockResolvedValue(glosario()) };
  await scope.render(<GlossaryView {...props} apiClient={api as unknown as APIClient} />);
  fireEvent.change(screen.getByLabelText(t('modo.selector')), { target: { value: 'basico' } });
  fireEvent.click(screen.getByRole('button', { name: t('glosario.generar') }));
  await screen.findByText('Una red privada.');
  await waitFor(() => expect(api.createGlossary.mock.calls[0][1].modo_generacion).toBe('basico'));
  expect(screen.getAllByText(t('modo.aviso')).length).toBeGreaterThan(0);
});

it('un resultado básico solo es válido si se pidió explícitamente ese modo', () => {
  expect(() => validarRespuestaChat(chat(), payload)).not.toThrow();
  expect(() => validarRespuestaChat(chat(), { ...payload, modo_generacion: 'multiagente' })).toThrow();
  expect(() => validarGlosario(glosario(), 'glos', payload)).not.toThrow();
  expect(() => validarGlosario(glosario(), 'glos', { ...payload, modo_generacion: 'multiagente' })).toThrow();
  expect(() => validarRespuestaChat({ ...chat(), evaluacion_calidad: fixtures.flashcards.evaluacion_calidad }, payload)).toThrow();
  expect(() => validarGlosario({ ...glosario(), evaluacion_calidad: fixtures.flashcards.evaluacion_calidad }, 'glos', payload)).toThrow();
});

it('no acepta un paquete aprobado que conserva hechos sin respaldo', () => {
  const paquete = structuredClone(fixtures.flashcards);
  Object.assign(paquete.evaluacion_calidad, { cantidad_afirmaciones: 20, cantidad_respaldadas: 19, anclaje_fuente_score: 0.95 });
  expect(() => validarPaquete(paquete)).toThrow();
});
