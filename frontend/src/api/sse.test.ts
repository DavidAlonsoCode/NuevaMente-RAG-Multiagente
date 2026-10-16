import { afterEach, describe, expect, it, vi } from 'vitest';
import { APIClient, APIError } from './client';
import { generationEvent, SseDecoder } from './sse';

const data = { generation_id: 'gen-one', step: 'writer', status: 'running', iteration: 2 };
const encode = (text: string) => new TextEncoder().encode(text);
const frame = (id = 7, value: unknown = data) => `id: ${id}\nevent: progress\ndata: ${JSON.stringify(value)}\n\n`;
function response(text: string) {
  return new Response(new ReadableStream({ start(controller) { controller.enqueue(encode(text)); controller.close(); } }),
    { headers: { 'Content-Type': 'text/event-stream; charset=utf-8' } });
}
async function collect(iterator: AsyncIterable<unknown>) {
  const items = [];
  for await (const item of iterator) items.push(item);
  return items;
}
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

describe('encuadre SSE', () => {
  it.each(['\n', '\r', '\r\n'])('lee UTF-8 byte a byte, data multilínea y comentarios (%j)', newline => {
    const decoder = new SseDecoder();
    const bytes = encode(`: heartbeat${newline}${newline}id: 8${newline}event: progress${newline}data: {${newline}data: "texto": "acción"}${newline}${newline}`);
    const events = [...bytes].flatMap(byte => decoder.push(new Uint8Array([byte])));
    events.push(...decoder.push(new Uint8Array(), true));
    expect(events).toEqual([{ event: 'heartbeat', id: null, data: '' },
      { event: 'progress', id: '8', data: '{\n"texto": "acción"}' }]);
  });
  it('descarta un evento sin delimitador final', () => {
    const decoder = new SseDecoder();
    expect(decoder.push(encode(frame().trimEnd()), true)).toEqual([]);
  });
  it('acota un frame sin delimitadores', () => {
    expect(() => new SseDecoder().push(encode('data: ' + 'a'.repeat(64000)))).toThrow();
  });
  it.each([
    { ...data, generation_id: 'gen-other' }, { ...data, contenido: { titulo: 'private draft' } },
    { ...data, iteration: 4 }, { ...data, status: 'approved' },
  ])('rechaza identidad, borradores y metadatos incompatibles', value => {
    expect(() => generationEvent({ id: '7', event: 'progress', data: JSON.stringify(value) }, 'gen-one')).toThrow();
  });
  it('no acepta IDs parcialmente numéricos', () => {
    expect(() => generationEvent({ id: '7abc', event: 'progress', data: JSON.stringify(data) }, 'gen-one')).toThrow();
  });
});

describe('fetch SSE autenticado', () => {
  it('envía Bearer y cursor, ignora repetidos y conserva heartbeats', async () => {
    const fetch = vi.fn().mockResolvedValue(response(frame(6) + ': heartbeat\n\n' + frame(8)));
    vi.stubGlobal('fetch', fetch);
    const items = await collect(new APIClient().streamGenerationEvents('synthetic', 'gen-one',
      { signal: new AbortController().signal, lastEventId: 6 }));
    expect(items).toEqual([{ kind: 'open' }, { kind: 'heartbeat' }, { kind: 'progress', id: 8, data }]);
    expect(fetch.mock.calls[0][1].headers).toMatchObject({ Authorization: 'Bearer synthetic', 'Last-Event-ID': '6' });
    expect(fetch.mock.calls[0][1].redirect).toBe('manual');
  });
  it('conserva Retry-After de un 429', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: { code: 'RATE_LIMITED' } }),
      { status: 429, headers: { 'Retry-After': '5' } })));
    await expect(collect(new APIClient().streamGenerationEvents('synthetic', 'gen-one',
      { signal: new AbortController().signal, lastEventId: 0 }))).rejects.toMatchObject({ statusCode: 429, retryAfter: '5' });
  });
  it('trata una desconexión de red como recuperable', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('network disconnected')));
    await expect(collect(new APIClient().streamGenerationEvents('synthetic', 'gen-one',
      { signal: new AbortController().signal, lastEventId: 0 }))).rejects.toMatchObject({ code: 'SERVICE_UNAVAILABLE' });
  });
  it('revocación dentro del stream conserva el fallo de sesión', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response('event: error\ndata: {"error":{"code":"SESSION_INVALID"}}\n\n')));
    await expect(collect(new APIClient().streamGenerationEvents('synthetic', 'gen-one',
      { signal: new AbortController().signal, lastEventId: 0 }))).rejects.toMatchObject({ statusCode: 401 });
  });
  it.each([new Response('{}', { headers: { 'Content-Type': 'application/json' } }),
    response(frame(7, { ...data, contenido: 'private draft' }))])('no convierte una respuesta inválida en progreso', async res => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(res));
    await expect(collect(new APIClient().streamGenerationEvents('synthetic', 'gen-one',
      { signal: new AbortController().signal, lastEventId: 0 }))).rejects.toBeInstanceOf(APIError);
  });
  it('cierra el lector al agotar inactividad, sin esperar al cierre del servidor', async () => {
    vi.useFakeTimers();
    const cancel = vi.fn();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(new ReadableStream({ cancel }),
      { headers: { 'Content-Type': 'text/event-stream' } })));
    const iterator = new APIClient().streamGenerationEvents('synthetic', 'gen-one',
      { signal: new AbortController().signal, lastEventId: 0, idleTimeoutMs: 1000 });
    await iterator.next();
    const pending = expect(iterator.next()).rejects.toMatchObject({ code: 'SERVICE_UNAVAILABLE' });
    await vi.advanceTimersByTimeAsync(1000);
    await pending;
    expect(cancel).toHaveBeenCalledOnce();
  });
  it('cancela el lector al desmontar la suscripción', async () => {
    const cancel = vi.fn();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(new ReadableStream({ cancel }),
      { headers: { 'Content-Type': 'text/event-stream' } })));
    const controller = new AbortController();
    const iterator = new APIClient().streamGenerationEvents('synthetic', 'gen-one', { signal: controller.signal, lastEventId: 0 });
    await iterator.next();
    const pending = iterator.next();
    controller.abort();
    await pending;
    expect(cancel).toHaveBeenCalledOnce();
  });
});
