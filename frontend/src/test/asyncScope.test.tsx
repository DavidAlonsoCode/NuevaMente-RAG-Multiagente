import React, { useEffect, useState } from 'react';
import { cleanup, fireEvent, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { AsyncTestScope } from './asyncScope';

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it('drena act antes de limpiar y no deja que la continuación alcance el siguiente montaje', async () => {
  const controller = new AbortController();
  const first = new AsyncTestScope(controller.signal);
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  const warning = vi.spyOn(console, 'error');
  const nextAction = vi.fn();
  await first.render(<button>anterior</button>);
  const oldTask = (async () => {
    await first.act(() => gate);
    fireEvent.click(screen.getByRole('button', { name: 'siguiente' }));
  })();
  const outcome = oldTask.then(() => ({ error: null }), error => ({ error }));
  controller.abort(new Error('fin simulado de la prueba'));
  let closed = false;
  const closing = first.close().then(() => { closed = true; });
  await Promise.resolve();
  expect(closed).toBe(false);
  release();
  await closing;
  expect((await outcome).error).toBe(controller.signal.reason);
  cleanup();
  const next = new AsyncTestScope(new AbortController().signal);
  await next.render(<button onClick={nextAction}>siguiente</button>);
  expect(nextAction).not.toHaveBeenCalled();
  expect(warning).not.toHaveBeenCalled();
  await next.close();
});

it('cierra incluso sin aborto e impide acciones posteriores sin ocultar errores', async () => {
  const scope = new AsyncTestScope(new AbortController().signal);
  const error = new Error('fallo de la acción');
  await expect(scope.act(() => { throw error; })).rejects.toBe(error);
  await scope.close();
  const action = vi.fn();
  await expect(scope.act(action)).rejects.toThrow('Test scope already closed');
  expect(action).not.toHaveBeenCalled();
});

it('monta y completa los efectos con promesas resueltas antes de consultar el DOM', async () => {
  function Initializing() {
    const [ready, setReady] = useState(false);
    useEffect(() => { void Promise.resolve().then(() => setReady(true)); }, []);
    return <button disabled={!ready}>documentos</button>;
  }
  const scope = new AsyncTestScope(new AbortController().signal);
  await scope.render(<Initializing />);
  expect((screen.getByRole('button', { name: 'documentos' }) as HTMLButtonElement).disabled).toBe(false);
  await scope.close();
});

it('termina el avance del reloj propio antes de restaurar temporizadores para otra prueba', async () => {
  vi.useFakeTimers();
  try {
    const controller = new AbortController();
    const scope = new AsyncTestScope(controller.signal);
    const fired = vi.fn();
    setTimeout(fired, 10);
    const advance = scope.act(async () => { await vi.advanceTimersByTimeAsync(10); });
    const outcome = advance.then(() => null, error => error);
    controller.abort(new Error('prueba terminada'));
    await scope.close();
    expect(await outcome).toBe(controller.signal.reason);
    expect(fired).toHaveBeenCalledOnce();
    expect(vi.getTimerCount()).toBe(0);
  } finally {
    vi.useRealTimers();
  }
});
