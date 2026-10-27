import {
  ESTADOS_TRABAJO,
  ExportFormat,
  OutputLanguage,
  RotatedCodeResponse,
  SessionResponse,
  TextDocumentRequest,
  UploadResponse,
  WorkspaceResponse,
} from './types';
import { generationEvent, GenerationEvent, SseDecoder } from './sse';

const SAFE_ID_REGEX = /^[A-Za-z0-9_.-]{1,128}$/;
const ERROR_CODE_REGEX = /^[A-Z_]{1,64}$/;
const PNG_MAGIC = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a];
// En Render (Oregón) casi cada operación consulta OCI (Brasil) y una descarga espera mientras otra
// generación sube sus exportaciones: con 30 s se cortaban respuestas que el servidor sí completaba.
export const ESPERA_MS = 90000;
export const ESPERA_TRANSFERENCIA_MS = 180000;

export function identificadorSeguro(value: unknown): string | null {
  return typeof value === 'string' && SAFE_ID_REGEX.test(value) ? value : null;
}

export class APIError extends Error {
  public readonly code: string;
  public readonly remoteMessage: string;
  public readonly statusCode: number;
  public readonly requestId: string | null;
  public readonly details: Record<string, any>;
  public readonly retryAfter: string | null;

  constructor(
    code: string,
    message: string,
    statusCode: number,
    requestId?: string | null,
    details?: Record<string, any> | null,
    retryAfter?: string | null
  ) {
    const safeCode = ERROR_CODE_REGEX.test(code) ? code : 'INTERNAL';
    const safeReqId = identificadorSeguro(requestId);
    super(`[${safeCode}] HTTP ${statusCode} (request_id: ${safeReqId})`);
    this.name = 'APIError';
    this.code = safeCode;
    this.remoteMessage = message;
    this.statusCode = statusCode;
    this.requestId = safeReqId;
    this.details = details && typeof details === 'object' ? details : {};
    this.retryAfter = retryAfter ?? null;
  }
}

export class APIClient {
  public readonly baseUrl: string;
  public idiomaUi: OutputLanguage;

  constructor(options?: { baseUrl?: string; idiomaUi?: OutputLanguage }) {
    const rawBase = options?.baseUrl ?? '';
    this.baseUrl = rawBase.replace(/\/+$/, '');
    this.idiomaUi =
      options?.idiomaUi && ['es', 'en', 'pt'].includes(options.idiomaUi)
        ? options.idiomaUi
        : 'es';
  }

  private headers(token?: string | null, includeJson = true): Record<string, string> {
    const h: Record<string, string> = {
      'Accept-Language': this.idiomaUi,
    };
    if (includeJson) {
      h['Content-Type'] = 'application/json';
    }
    if (token) {
      h['Authorization'] = `Bearer ${token}`;
    }
    return h;
  }

  private async solicitar(
    method: string,
    endpoint: string,
    options?: {
      json?: unknown;
      token?: string | null;
      params?: Record<string, any>;
      formData?: FormData;
      idempotencyKey?: string | null;
      binary?: boolean;
      timeoutMs?: number;
    }
  ): Promise<any> {
    let url = `${this.baseUrl}${endpoint}`;
    if (options?.params) {
      const search = new URLSearchParams();
      for (const [k, v] of Object.entries(options.params)) {
        if (v !== undefined && v !== null && v !== '') {
          search.append(k, String(v));
        }
      }
      const qs = search.toString();
      if (qs) url += `?${qs}`;
    }

    const isMultipart = Boolean(options?.formData);
    const headers = this.headers(options?.token, !isMultipart && options?.json !== undefined);
    if (!isMultipart && options?.json === undefined) {
      headers['Content-Type'] = 'application/json';
    }
    if (options?.idempotencyKey) {
      headers['Idempotency-Key'] = options.idempotencyKey;
    }

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), options?.timeoutMs ?? ESPERA_MS);
    const demorada = () => new APIError('TIMEOUT', 'El servidor tardó demasiado en responder.', 503);

    let res: Response;
    try {
      res = await fetch(url, {
        method,
        headers,
        body: isMultipart
          ? options?.formData
          : options?.json !== undefined
          ? JSON.stringify(options.json)
          : undefined,
        redirect: 'manual',
        signal: controller.signal,
      });
    } catch {
      clearTimeout(timer);
      if (controller.signal.aborted) throw demorada();
      throw new APIError('SERVICE_UNAVAILABLE', 'No se pudo conectar al backend.', 503);
    }

    const requestId = identificadorSeguro(res.headers.get('X-Request-ID'));

    if (res.type === 'opaqueredirect' || (res.status >= 300 && res.status < 400)) {
      clearTimeout(timer);
      throw new APIError('INVALID_RESPONSE', 'Redirección inesperada de la API.', 502, requestId);
    }

    if (res.status === 204) {
      clearTimeout(timer);
      return null;
    }

    if (options?.binary && res.ok) {
      try {
        return new Uint8Array(await res.arrayBuffer());
      } catch {
        if (controller.signal.aborted) throw demorada();
        throw new APIError('SERVICE_UNAVAILABLE', 'Descarga interrumpida.', 503, requestId);
      } finally { clearTimeout(timer); }
    }

    let data: any = {};
    try {
      data = await res.json();
    } catch {
      if (controller.signal.aborted) throw demorada();
      if (res.ok) {
        throw new APIError('INVALID_RESPONSE', 'Respuesta JSON inválida.', 502, requestId);
      }
      data = {};
    } finally { clearTimeout(timer); }

    if (res.ok && (typeof data !== 'object' || data === null || Array.isArray(data))) {
      throw new APIError('INVALID_RESPONSE', 'Se esperaba un objeto JSON.', 502, requestId);
    }

    if (!res.ok) {
      const errorPayload =
        data && typeof data === 'object' && typeof data.error === 'object' && data.error !== null
          ? data.error
          : {};
      const fallback = res.status === 401 ? 'SESSION_INVALID' : 'INTERNAL';
      const errCode = typeof errorPayload.code === 'string' ? errorPayload.code : fallback;
      const errMessage =
        typeof errorPayload.message === 'string' ? errorPayload.message : 'Error de API.';
      const errDetails =
        typeof errorPayload.details === 'object' && errorPayload.details !== null
          ? errorPayload.details
          : {};
      const bodyReqId = identificadorSeguro(data?.request_id);

      throw new APIError(
        errCode,
        errMessage,
        res.status,
        bodyReqId || requestId,
        errDetails,
        res.headers.get('Retry-After')
      );
    }

    return data;
  }

  private static validarSesion(data: any, campos: string[]): any {
    if (
      typeof data !== 'object' ||
      data === null ||
      campos.some((c) => typeof data[c] !== 'string' || !data[c])
    ) {
      throw new APIError('INVALID_RESPONSE', 'Respuesta de sesión incompleta.', 502);
    }
    return data;
  }

  // 1. Workspaces y Sesiones
  async createWorkspace(): Promise<WorkspaceResponse> {
    const data = await this.solicitar('POST', '/api/workspaces');
    return APIClient.validarSesion(data, ['workspace_id', 'token', 'recovery_code']);
  }

  async recoverSession(recoveryCode: string): Promise<SessionResponse> {
    const data = await this.solicitar('POST', '/api/sessions/recover', {
      json: { recovery_code: recoveryCode },
    });
    return APIClient.validarSesion(data, ['workspace_id', 'token']);
  }

  async closeSession(token: string): Promise<void> {
    await this.solicitar('DELETE', '/api/sessions/current', { token });
  }

  async rotateRecoveryCode(token: string): Promise<RotatedCodeResponse> {
    const data = await this.solicitar('POST', '/api/workspaces/current/recovery-code', { token });
    return APIClient.validarSesion(data, ['token', 'recovery_code']);
  }

  async deleteWorkspace(token: string): Promise<void> {
    await this.solicitar('DELETE', '/api/workspaces/current', { token });
  }

  async workspaceStatus(token: string): Promise<any> {
    return this.solicitar('GET', '/api/workspaces/current', { token });
  }

  // 2. Documentos
  private static reciboUpload(data: any): UploadResponse {
    const campos = ['document_id', 'job_id', 'status_url', 'events_url', 'cancel_url'];
    if (
      !data ||
      data.status !== 'processing' ||
      campos.some((c) => typeof data[c] !== 'string' || !data[c])
    ) {
      throw new APIError('INVALID_RESPONSE', 'Admisión incompleta', 502);
    }
    return data as UploadResponse;
  }

  async uploadDocument(
    token: string,
    payload: TextDocumentRequest,
    idempotencyKey?: string | null
  ): Promise<UploadResponse> {
    const data = await this.solicitar('POST', '/api/documents/upload', {
      json: payload,
      token,
      idempotencyKey,
    });
    return APIClient.reciboUpload(data);
  }

  async uploadFile(
    token: string,
    filename: string,
    content: Uint8Array | Blob,
    mediaType: string,
    options?: { title?: string | null; idempotencyKey?: string | null }
  ): Promise<UploadResponse> {
    const formData = new FormData();
    const blob =
      content instanceof Blob ? content : new Blob([content], { type: mediaType });
    formData.append('file', blob, filename);
    if (options?.title !== undefined && options?.title !== null) {
      formData.append('titulo', options.title);
    }
    const data = await this.solicitar('POST', '/api/documents/upload', {
      token,
      formData,
      idempotencyKey: options?.idempotencyKey,
      timeoutMs: ESPERA_TRANSFERENCIA_MS,
    });
    return APIClient.reciboUpload(data);
  }

  async getDocuments(token: string, params?: Record<string, any>): Promise<any> {
    return this.solicitar('GET', '/api/documents', { token, params });
  }

  async getDocument(token: string, documentId: string): Promise<any> {
    return this.solicitar('GET', `/api/documents/${encodeURIComponent(documentId)}`, { token });
  }

  async deleteDocument(token: string, documentId: string): Promise<void> {
    await this.solicitar('DELETE', `/api/documents/${encodeURIComponent(documentId)}`, { token });
  }

  async getDocumentSource(
    token: string,
    documentId: string,
    chunkId: string,
    options?: { demo?: boolean }
  ): Promise<any> {
    const base = options?.demo ? '/api/demo/documents' : '/api/documents';
    const ruta = `${base}/${encodeURIComponent(documentId)}/sources/${encodeURIComponent(chunkId)}`;
    return this.solicitar('GET', ruta, { token });
  }

  async getDocumentSourceImage(
    token: string,
    documentId: string,
    chunkId: string
  ): Promise<Uint8Array> {
    const ruta = `/api/documents/${encodeURIComponent(documentId)}/sources/${encodeURIComponent(chunkId)}/image`;
    const bytes: Uint8Array = await this.solicitar('GET', ruta, { token, binary: true });
    if (
      !(bytes instanceof Uint8Array) ||
      bytes.length < PNG_MAGIC.length ||
      PNG_MAGIC.some((b, i) => bytes[i] !== b)
    ) {
      throw new APIError('INVALID_RESPONSE', 'Imagen de fuente inválida.', 502);
    }
    return bytes;
  }

  // 3. Trabajos Comunes y SSE
  async getJob(token: string, jobId: string): Promise<any> {
    return this.solicitar('GET', `/api/jobs/${encodeURIComponent(jobId)}`, { token });
  }

  async cancelJob(token: string, jobId: string): Promise<any> {
    return this.solicitar('POST', `/api/jobs/${encodeURIComponent(jobId)}/cancel`, { token });
  }

  async readJobProgress(
    token: string,
    jobId: string,
    cursor = 0
  ): Promise<{ id: number; data: any } | null> {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 900);
    let res: Response;
    try {
      res = await fetch(
        `${this.baseUrl}/api/jobs/${encodeURIComponent(jobId)}/events`,
        {
          method: 'GET',
          headers: {
            ...this.headers(token, false),
            'Last-Event-ID': String(cursor),
          },
          redirect: 'manual',
          signal: controller.signal,
        }
      );
    } catch {
      clearTimeout(timeout);
      return null;
    }

    if (res.status !== 200) {
      clearTimeout(timeout);
      const codigo = res.status === 401 ? 'SESSION_INVALID' : 'SERVICE_UNAVAILABLE';
      throw new APIError(codigo, 'Progreso no disponible', res.status);
    }
    if (!res.headers.get('Content-Type')?.startsWith('text/event-stream') || !res.body) {
      clearTimeout(timeout);
      throw new APIError('INVALID_RESPONSE', 'Progreso inválido', 502);
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder('utf-8');
    let buffer = '';
    let ultimo: { id: number; data: any } | null = null;
    let campos: Record<string, string> = {};
    let totalBytes = 0;

    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        totalBytes += value.byteLength;
        if (totalBytes > 64000) {
          throw new APIError('INVALID_RESPONSE', 'Progreso excedido', 502);
        }
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split(/\r?\n/);
        buffer = lines.pop() ?? '';

        for (const line of lines) {
          if (!line) {
            if (campos.event === 'error') {
              throw new APIError('SERVICE_UNAVAILABLE', 'Progreso interrumpido', 503);
            }
            if (campos.event === 'progress') {
              const datos = JSON.parse(campos.data || '{}');
              const numero = parseInt(campos.id || '0', 10);
              if (
                numero > cursor &&
                datos.job_id === jobId &&
                typeof datos.step === 'string'
              ) {
                ultimo = { id: numero, data: datos };
                cursor = numero;
              }
            }
            campos = {};
          } else if (!line.startsWith(':')) {
            const idx = line.indexOf(':');
            if (idx !== -1) {
              const clave = line.slice(0, idx);
              const valor = line.slice(idx + 1).replace(/^\s+/, '');
              campos[clave] = valor;
            }
          }
        }
      }
    } catch (e) {
      if (e instanceof APIError) throw e;
    } finally {
      clearTimeout(timeout);
      try {
        await reader.cancel();
      } catch {
        // Ignorar cierre de stream
      }
    }
    return ultimo;
  }

  // 4. Demo Library y Ejemplos
  async getDemoDocuments(token: string, params?: Record<string, any>): Promise<any> {
    return this.solicitar('GET', '/api/demo/documents', { token, params });
  }

  async getDemoDocument(token: string, documentId: string): Promise<any> {
    return this.solicitar('GET', `/api/demo/documents/${encodeURIComponent(documentId)}`, {
      token,
    });
  }

  async getDemoGenerations(token: string): Promise<any> {
    return this.solicitar('GET', '/api/demo/generations', { token });
  }

  async getDemoGeneration(token: string, generationId: string): Promise<any> {
    return this.solicitar(
      'GET',
      `/api/demo/generations/${encodeURIComponent(generationId)}`,
      { token }
    );
  }

  async getDemoGenerationSource(
    token: string,
    generationId: string,
    chunkId: string,
    options?: { image?: boolean }
  ): Promise<any> {
    const ruta = `/api/demo/generations/${encodeURIComponent(generationId)}/sources/${encodeURIComponent(chunkId)}${
      options?.image ? '/image' : ''
    }`;
    const res = await this.solicitar('GET', ruta, {
      token,
      binary: Boolean(options?.image),
    });
    if (options?.image) {
      if (
        !(res instanceof Uint8Array) ||
        res.length < PNG_MAGIC.length ||
        PNG_MAGIC.some((b, i) => res[i] !== b)
      ) {
        throw new APIError('INVALID_RESPONSE', 'Imagen incompatible.', 502);
      }
    }
    return res;
  }

  async answerDemoQuiz(token: string, generationId: string, payload: any): Promise<any> {
    const ruta = `/api/demo/generations/${encodeURIComponent(generationId)}/answers`;
    return this.solicitar('POST', ruta, { token, json: payload });
  }

  // 5. Generaciones
  async createGeneration(
    token: string,
    payload: Record<string, any>,
    idempotencyKey?: string | null
  ): Promise<any> {
    const data = await this.solicitar('POST', '/api/generate', {
      json: payload,
      token,
      idempotencyKey,
    });
    const generationId = data?.generation_id;
    const encodedId = typeof generationId === 'string' ? encodeURIComponent(generationId) : '';
    if (
      typeof generationId !== 'string' ||
      !generationId ||
      identificadorSeguro(generationId) !== generationId ||
      !ESTADOS_TRABAJO.includes(data?.status) ||
      data?.status_url !== `/api/generations/${encodedId}` ||
      data?.events_url !== `/api/generations/${encodedId}/events`
    ) {
      throw new APIError('INVALID_RESPONSE', 'Admisión de generación incompleta.', 502);
    }
    return data;
  }

  async getGenerations(token: string, params?: Record<string, any>): Promise<any> {
    return this.solicitar('GET', '/api/generations', { token, params });
  }

  async getGeneration(token: string, generationId: string): Promise<any> {
    return this.solicitar('GET', `/api/generations/${encodeURIComponent(generationId)}`, {
      token,
    });
  }

  async *streamGenerationEvents(
    token: string,
    generationId: string,
    options: { signal: AbortSignal; lastEventId: number; idleTimeoutMs?: number }
  ): AsyncGenerator<GenerationEvent> {
    const controller = new AbortController();
    let reader: ReadableStreamDefaultReader<Uint8Array> | undefined;
    const abort = () => { controller.abort(); void reader?.cancel().catch(() => {}); };
    options.signal.addEventListener('abort', abort, { once: true });
    if (options.signal.aborted) abort();
    const timeout = options.idleTimeoutMs ?? 30000;
    let timer = setTimeout(abort, timeout);
    let cursor = options.lastEventId;
    try {
      const response = await fetch(`${this.baseUrl}/api/generations/${encodeURIComponent(generationId)}/events`, {
        headers: { ...this.headers(token, false), Accept: 'text/event-stream',
          ...(cursor > 0 ? { 'Last-Event-ID': String(cursor) } : {}) },
        redirect: 'manual', signal: controller.signal,
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new APIError(body?.error?.code || (response.status === 401 ? 'SESSION_INVALID' : 'SERVICE_UNAVAILABLE'),
          'Stream no disponible.', response.status, response.headers.get('X-Request-ID'),
          body?.error?.details, response.headers.get('Retry-After'));
      }
      if (!/^text\/event-stream(?:\s*;|$)/i.test(response.headers.get('Content-Type') ?? '') || !response.body) {
        throw new APIError('INVALID_RESPONSE', 'Stream incompatible.', 502);
      }
      reader = response.body.getReader();
      const decoder = new SseDecoder();
      yield { kind: 'open' };
      while (!controller.signal.aborted) {
        const { done, value } = await reader.read();
        if (options.signal.aborted) return;
        clearTimeout(timer);
        timer = setTimeout(abort, timeout);
        let frames;
        try { frames = decoder.push(value ?? new Uint8Array(), done); }
        catch { throw new APIError('INVALID_RESPONSE', 'Encuadre SSE incompatible.', 502); }
        for (const frame of frames) {
          if (frame.event === 'error') {
            let error;
            try { error = JSON.parse(frame.data).error; }
            catch { throw new APIError('INVALID_RESPONSE', 'Error SSE incompatible.', 502); }
            throw new APIError(error?.code || 'SERVICE_UNAVAILABLE', 'Stream interrumpido.',
              error?.code === 'SESSION_INVALID' ? 401 : error?.code === 'NOT_FOUND' ? 404 : 503);
          }
          let event;
          try { event = generationEvent(frame, generationId); }
          catch { throw new APIError('INVALID_RESPONSE', 'Progreso SSE incompatible.', 502); }
          if (!event) continue;
          if (event.kind === 'progress') {
            if (event.id <= cursor) continue;
            cursor = event.id;
          }
          yield event;
        }
        if (done) {
          if (controller.signal.aborted && !options.signal.aborted) {
            throw new APIError('SERVICE_UNAVAILABLE', 'Stream sin actividad.', 503);
          }
          return;
        }
      }
      if (!options.signal.aborted) throw new APIError('SERVICE_UNAVAILABLE', 'Stream sin actividad.', 503);
    } catch (error) {
      if (options.signal.aborted || error instanceof APIError) throw error;
      throw new APIError('SERVICE_UNAVAILABLE', 'Progreso interrumpido.', 503);
    } finally {
      clearTimeout(timer);
      controller.abort();
      options.signal.removeEventListener('abort', abort);
      await reader?.cancel().catch(() => {});
    }
  }

  async cancelGeneration(token: string, generationId: string): Promise<void> {
    await this.solicitar(
      'POST',
      `/api/generations/${encodeURIComponent(generationId)}/cancel`,
      { token }
    );
  }

  async persistGeneration(
    token: string,
    generationId: string,
    idempotencyKey: string
  ): Promise<any> {
    return this.solicitar(
      'POST',
      `/api/generations/${encodeURIComponent(generationId)}/persist`,
      { token, idempotencyKey }
    );
  }

  // 6. Quiz, Chat, Glosario, Progreso y Exportaciones
  async answerQuiz(
    token: string,
    generationId: string,
    payload: { question_id: string; option_id: string; event_id: string }
  ): Promise<any> {
    return this.solicitar(
      'POST',
      `/api/quizzes/${encodeURIComponent(generationId)}/answers`,
      {
        json: payload,
        token,
        idempotencyKey: payload.event_id,
      }
    );
  }

  async createChat(
    token: string,
    payload: Record<string, any>,
    idempotencyKey: string
  ): Promise<any> {
    return this.solicitar('POST', '/api/chat', {
      json: payload,
      token,
      idempotencyKey,
    });
  }

  async createGlossary(
    token: string,
    payload: Record<string, any>,
    idempotencyKey?: string | null
  ): Promise<any> {
    return this.solicitar('POST', '/api/glossaries', {
      json: payload,
      token,
      idempotencyKey,
    });
  }

  async getGlossary(token: string, glossaryId: string): Promise<any> {
    return this.solicitar('GET', `/api/glossaries/${encodeURIComponent(glossaryId)}`, {
      token,
    });
  }

  async getProgress(token: string): Promise<any> {
    return this.solicitar('GET', '/api/progress', { token });
  }

  async recordProgress(
    token: string,
    payload: Record<string, any> & { event_id: string }
  ): Promise<any> {
    return this.solicitar('POST', '/api/progress/events', {
      json: payload,
      token,
      idempotencyKey: payload.event_id,
    });
  }

  async downloadExport(
    token: string,
    generationId: string,
    fmt: ExportFormat
  ): Promise<Uint8Array> {
    return this.solicitar(
      'GET',
      `/api/exports/${encodeURIComponent(generationId)}`,
      {
        token,
        params: { format: fmt },
        binary: true,
        timeoutMs: ESPERA_TRANSFERENCIA_MS,
      }
    );
  }
}
