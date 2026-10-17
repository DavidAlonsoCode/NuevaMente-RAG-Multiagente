import React from 'react';
import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { APIClient, APIError } from '../api/client';
import { QuizVistaEstudiante } from '../api/types';
import { QuizInteractiveRenderer } from '../components/FormatRenderers';
import fixtures from './fixtures.test-data.json';
import { t } from '../i18n';

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it('reintenta una respuesta incierta con la misma identidad y distingue un nuevo intento', async () => {
  const pregunta = fixtures.quiz.contenido_adaptado.preguntas[0];
  const correcta = pregunta.opciones[0].option_id;
  const answer = vi.spyOn(APIClient.prototype, 'answerQuiz')
    .mockRejectedValueOnce(new APIError('SERVICE_UNAVAILABLE', 'Respuesta perdida', 503))
    .mockImplementation(async (_token, _id, datos) => ({
      correcta: datos.option_id === correcta, correct_option_id: correcta,
      justificacion: 'Explicación sintética respaldada.', referencias: [{ chunk_id: 'chunk-1' }],
    }));
  render(<QuizInteractiveRenderer contenido={fixtures.quiz.contenido_adaptado as QuizVistaEstudiante}
    idioma="es" generationId="synthetic-gen" documentId="synthetic-doc"
    apiClient={new APIClient()} token="synthetic-token" renderSources={() => null} onSessionInvalid={vi.fn()} />);
  expect(screen.queryByText('Explicación sintética respaldada.')).toBeNull();
  fireEvent.click(screen.getAllByRole('radio')[1]);
  fireEvent.click(screen.getByRole('button', { name: t('quiz.responder', 'es') }));
  await screen.findByRole('alert');
  expect(screen.getAllByRole('radio').every(radio => radio.matches(':disabled'))).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: t('quiz.reenviar', 'es') }));
  await screen.findByText('Explicación sintética respaldada.');
  expect(answer.mock.calls[0]).toEqual(answer.mock.calls[1]);
  fireEvent.click(screen.getByRole('button', { name: t('quiz.reintentar', 'es') }));
  fireEvent.click(screen.getAllByRole('radio')[0]);
  fireEvent.click(screen.getByRole('button', { name: t('quiz.responder', 'es') }));
  await waitFor(() => expect(answer).toHaveBeenCalledTimes(3));
  expect(answer.mock.calls[2][2].event_id).not.toBe(answer.mock.calls[1][2].event_id);
  await screen.findByText(t('quiz.correcto', 'es'));
});
