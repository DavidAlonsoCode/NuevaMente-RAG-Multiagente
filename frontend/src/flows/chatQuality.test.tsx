import React from 'react';
import { afterEach, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import fixtures from './fixtures.test-data.json';
import { nivelRespaldo, validarGlosario, validarRespuestaChat } from './validators';
import { RespaldoBadge } from '../components/StudyAndHistoryViews';
import { t } from '../i18n';

afterEach(cleanup);

const pregunta = '¿De qué se trata este documento?';
const esperado = { document_id: 'a', pregunta, perfil_destinatario: 'principiante', idioma_salida: 'es' };
const conReservas = { ...fixtures.flashcards.evaluacion_calidad, anclaje_fuente_score: 0.7, cantidad_afirmaciones: 10,
  cantidad_respaldadas: 7, estado_evaluacion: 'requiere_revision', razones_bloqueo: ['fidelidad_requiere_revision'] };
const respuesta = (evaluacion_calidad: any) => ({ document_id: 'a', document_hash: 'a'.repeat(64), pregunta,
  perfil_destinatario: 'principiante', idioma_salida: 'es', estado: 'respondida', respuesta: 'Trata sobre VCN.',
  referencias: [{ chunk_id: 'chunk-a' }], evaluacion_calidad });

it.each([
  ['aprobada', fixtures.flashcards.evaluacion_calidad],
  ['con reservas', conReservas],
])('el chat entrega una respuesta %s', (_caso, calidad) => {
  expect(() => validarRespuestaChat(respuesta(calidad), esperado)).not.toThrow();
});

it.each([
  ['score que no corresponde a los conteos', { ...conReservas, anclaje_fuente_score: 0.9 }],
  ['evaluación no evaluable', { ...conReservas, estado_evaluacion: 'no_evaluable' }],
  ['aprobada por debajo de 0.85', { ...conReservas, estado_evaluacion: 'aprobada', razones_bloqueo: null }],
  ['respondida sin evaluación', null],
])('rechaza una respuesta con %s', (_caso, calidad) => {
  expect(() => validarRespuestaChat(respuesta(calidad), esperado)).toThrow();
});

it.each([
  [fixtures.flashcards.evaluacion_calidad, 'verificada', {}],
  [conReservas, 'parcial', { porcentaje: 70 }],
] as const)('el aviso de respaldo informa el nivel real', (calidad, nivel, vars) => {
  expect(nivelRespaldo(calidad)).toBe(nivel);
  render(<RespaldoBadge calidad={calidad} idioma="es" />);
  expect(screen.getByText(t(`respaldo.${nivel}`, 'es', vars))).toBeTruthy();
});

const glosario = (evaluacion_calidad: any) => ({ glossary_id: 'job-g', documento_fuente: { document_id: 'a' },
  perfil_destinatario: 'principiante', idioma_salida: 'es', candidatos_omitidos: 0, estado: 'verificado',
  terminos: [{ id: 't1', termino_original: 'VCN', es_identificador: false, estado: 'definido',
    definicion: 'Red virtual.', traduccion: null, limitacion: null, referencias: [{ chunk_id: 'chunk-a' }] }],
  evaluacion_calidad, trazabilidad: evaluacion_calidad ? { modelo_verificacion: 'juez' } : null });
const esperadoGlosario = { document_id: 'a', perfil_destinatario: 'principiante', idioma_salida: 'es' };

it.each([
  ['aprobado', fixtures.flashcards.evaluacion_calidad],
  ['con reservas', conReservas],
])('el glosario verificado se acepta %s', (_caso, calidad) => {
  expect(() => validarGlosario(glosario(calidad), 'job-g', esperadoGlosario)).not.toThrow();
});

it('el glosario con reservas debe ser trazable', () => {
  expect(() => validarGlosario({ ...glosario(conReservas), trazabilidad: null }, 'job-g', esperadoGlosario)).toThrow();
});
