import { act, render, RenderResult } from '@testing-library/react';
import { ReactElement } from 'react';

/** Owns React work until it settles, including when Vitest aborts the test. */
export class AsyncTestScope {
  private closed = false;
  private pending = new Set<Promise<void>>();

  constructor(private readonly signal: AbortSignal) {}

  check(): void {
    this.signal.throwIfAborted();
    if (this.closed) throw new Error('Test scope already closed');
  }

  async act(action: () => void | Promise<void>): Promise<void> {
    this.check();
    const work = Promise.resolve(act(async () => { this.check(); await action(); }));
    this.pending.add(work);
    try {
      await work;
      this.check();
    } finally {
      this.pending.delete(work);
    }
  }

  async render(ui: ReactElement): Promise<RenderResult> {
    let view!: RenderResult;
    await this.act(() => { view = render(ui); });
    return view;
  }

  async close(): Promise<void> {
    this.closed = true;
    while (this.pending.size) await Promise.allSettled([...this.pending]);
  }
}
