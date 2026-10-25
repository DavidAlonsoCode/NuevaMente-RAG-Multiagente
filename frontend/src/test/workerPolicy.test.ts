import { expect, it } from 'vitest';
import { frontendTestWorkers } from './workerPolicy';

it('respeta la capacidad disponible sin perder el paralelismo de hosts pequeños', () => {
  for (const available of [1, 2, 3, 4]) {
    expect(frontendTestWorkers(available)).toBe(available);
  }
});

it('acota la presión de los entornos DOM en hosts con más procesadores', () => {
  for (const available of [5, 8, 12, 32, 128]) {
    expect(frontendTestWorkers(available)).toBe(4);
  }
});
