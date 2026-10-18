import { ESTADOS_TRABAJO, JobStatus } from './types';

export interface SseFrame { event: string; id: string | null; data: string }
export interface GenerationProgress {
  generation_id: string;
  step: string;
  status: JobStatus;
  iteration: number | null;
}
export type GenerationEvent =
  | { kind: 'open' }
  | { kind: 'heartbeat' }
  | { kind: 'progress'; id: number; data: GenerationProgress };

/** Encuadre SSE incremental: UTF-8, CR/LF, comentarios y data multilínea. */
export class SseDecoder {
  private decoder = new TextDecoder('utf-8', { fatal: true });
  private buffer = '';
  private fields = { event: '', id: null as string | null, data: [] as string[], heartbeat: false };
  private size = 0;

  push(bytes: Uint8Array, end = false): SseFrame[] {
    this.buffer += this.decoder.decode(bytes, { stream: !end });
    const frames: SseFrame[] = [];
    while (true) {
      const index = this.buffer.search(/[\r\n]/);
      if (index < 0 || (!end && index === this.buffer.length - 1 && this.buffer[index] === '\r')) break;
      const line = this.buffer.slice(0, index);
      const length = this.buffer[index] === '\r' && this.buffer[index + 1] === '\n' ? 2 : 1;
      this.buffer = this.buffer.slice(index + length);
      this.size += line.length;
      if (this.size > 64000) throw new Error('SSE frame exceeds limit');
      if (!line) {
        if (this.fields.data.length || this.fields.heartbeat) {
          frames.push({
            event: this.fields.data.length ? this.fields.event || 'message' : 'heartbeat',
            id: this.fields.id,
            data: this.fields.data.join('\n'),
          });
        }
        this.fields = { event: '', id: null, data: [], heartbeat: false };
        this.size = 0;
      } else if (line.startsWith(':')) {
        this.fields.heartbeat = true;
      } else {
        const colon = line.indexOf(':');
        const field = colon < 0 ? line : line.slice(0, colon);
        let value = colon < 0 ? '' : line.slice(colon + 1);
        if (value.startsWith(' ')) value = value.slice(1);
        if (field === 'data') this.fields.data.push(value);
        if (field === 'event') this.fields.event = value;
        if (field === 'id' && !value.includes('\0')) this.fields.id = value;
      }
    }
    if (this.buffer.length + this.size > 64000) throw new Error('SSE frame exceeds limit');
    // Un evento sin línea vacía final se descarta al cerrar la conexión.
    return frames;
  }
}

export function generationEvent(frame: SseFrame, generationId: string): GenerationEvent | null {
  if (frame.event === 'heartbeat') return { kind: 'heartbeat' };
  if (frame.event !== 'progress') return null;
  const id = Number(frame.id);
  const data = JSON.parse(frame.data);
  if (!frame.id || !/^[1-9]\d*$/.test(frame.id) || !Number.isSafeInteger(id) ||
      !data || Array.isArray(data) || data.generation_id !== generationId ||
      typeof data.step !== 'string' || !/^[a-zA-Z_][a-zA-Z_0-9-]{0,79}$/.test(data.step) ||
      !ESTADOS_TRABAJO.includes(data.status) ||
      !(data.iteration === null || (Number.isInteger(data.iteration) && data.iteration >= 0 && data.iteration <= 3)) ||
      Object.keys(data).some(key => !['generation_id', 'step', 'status', 'iteration'].includes(key))) {
    throw new Error('Invalid generation progress');
  }
  return { kind: 'progress', id, data };
}
