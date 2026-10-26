import {
  ESTADOS_TRABAJO,
  ExportFormat,
  FORMATOS,
  GenerateRequest,
  IDIOMAS_SALIDA,
  NICHOS,
  NIVELES_DETALLE,
  PedagogicalStudentOutput,
  PERFILES,
} from '../api/types';
import { APIError } from '../api/client';

export const MAX_PDF_BYTES = 20 * 1024 * 1024;
export const MAX_TEXT_BYTES = 5 * 1024 * 1024;
export const FORMATOS_PERMITIDOS = ['.pdf', '.md', '.txt'] as const;

export const DOCUMENTOS_DEMO = [
  { id: 'demo-redes-vcn', clave: 'upload.demo_vcn', formato: 'PDF' },
  { id: 'demo-pagos-fintech', clave: 'upload.demo_pagos', formato: 'Markdown' },
  { id: 'demo-salud-gobernanza', clave: 'upload.demo_salud', formato: 'Markdown' },
] as const;

export async function sha256Hex(input: string | Uint8Array): Promise<string> {
  const bytes = typeof input === 'string' ? new TextEncoder().encode(input) : input;
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('');
}

export async function generarEventIdProgreso(
  workspaceId: string,
  datos: Record<string, any>
): Promise<string> {
  const huella = await sha256Hex(jsonCanonico([workspaceId, datos]));
  return `ui41-${huella}`;
}

// Compatible con json.dumps(sort_keys=True, ensure_ascii=False) del frontend
// anterior: una lectura no se duplica al alternar entre ambas interfaces.
function jsonCanonico(value: any): string {
  if (Array.isArray(value)) return `[${value.map(jsonCanonico).join(', ')}]`;
  if (value !== null && typeof value === 'object') {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}: ${jsonCanonico(value[key])}`).join(', ')}}`;
  }
  return JSON.stringify(value);
}

export async function claveCarga(titulo: string, contenido: string | Uint8Array): Promise<string> {
  return `upload-${await sha256Hex(JSON.stringify([titulo, await sha256Hex(contenido)]))}`;
}

export function validarArchivo(
  nombreArchivo: string,
  tamanoBytes: number
): [boolean, string | null] {
  const lower = nombreArchivo.toLowerCase();
  const dotIdx = lower.lastIndexOf('.');
  const ext = dotIdx !== -1 ? lower.slice(dotIdx) : '';
  if (!FORMATOS_PERMITIDOS.includes(ext as any)) {
    return [false, 'upload.formato_invalido'];
  }
  if (tamanoBytes <= 0) {
    return [false, 'upload.vacio'];
  }
  const limite = ext === '.pdf' ? MAX_PDF_BYTES : MAX_TEXT_BYTES;
  return tamanoBytes > limite ? [false, 'errors.DOCUMENT_TOO_LARGE'] : [true, null];
}

export function validarTextoPegado(
  titulo: string,
  contenido: string
): [boolean, string | null] {
  const t = titulo.trim();
  if (!t || t.length > 300) {
    return [false, 'upload.titulo_invalido'];
  }
  if (!contenido.trim()) {
    return [false, 'upload.vacio'];
  }
  const byteLen = new TextEncoder().encode(contenido).byteLength;
  return byteLen > MAX_TEXT_BYTES ? [false, 'errors.DOCUMENT_TOO_LARGE'] : [true, null];
}

export function formatearTamano(tamanoBytes: number): string {
  if (tamanoBytes < 1024) return `${tamanoBytes} B`;
  if (tamanoBytes < 1024 * 1024) return `${(tamanoBytes / 1024).toFixed(1)} KiB`;
  return `${(tamanoBytes / (1024 * 1024)).toFixed(1)} MiB`;
}

function esTexto(v: unknown): v is string {
  return typeof v === 'string' && v.trim().length > 0;
}

function esListaTextos(v: unknown): v is string[] {
  return Array.isArray(v) && v.every(esTexto);
}

function esReferencias(v: unknown): boolean {
  return (
    Array.isArray(v) &&
    v.length > 0 &&
    v.every(
      (r) =>
        typeof r === 'object' &&
        r !== null &&
        esTexto(r.chunk_id) &&
        (r.pagina === undefined ||
          r.pagina === null ||
          (Number.isInteger(r.pagina) && r.pagina > 0)) &&
        (r.seccion === undefined || r.seccion === null || esTexto(r.seccion))
    )
  );
}

function exigir(condicion: boolean, msg = 'Contrato incompatible'): void {
  if (!condicion) throw new Error(msg);
}

function validarCalidadAprobada(calidad: any): boolean {
  if (typeof calidad !== 'object' || calidad === null) return false;
  const score = calidad.anclaje_fuente_score;
  const total = calidad.cantidad_afirmaciones;
  const respaldadas = calidad.cantidad_respaldadas;
  if (typeof score !== 'number' || !Number.isFinite(score) || score < 0.85 || score > 1) {
    return false;
  }
  if (!Number.isInteger(total) || !Number.isInteger(respaldadas) || total <= 0) {
    return false;
  }
  if (respaldadas !== total) {
    return false;
  }
  if (Math.abs(score - respaldadas / total) > 1e-6) return false;
  if (calidad.estado_evaluacion !== 'aprobada') return false;
  if (calidad.razones_bloqueo && calidad.razones_bloqueo.length > 0) return false;
  if (calidad.cobertura_objetivos !== 'completa') return false;
  if (!['no_aplica', 'aprobada'].includes(calidad.verificacion_visual)) return false;
  if (
    !['claridad_pedagogica', 'adecuacion_perfil', 'coherencia_didactica'].every((k) =>
      ['alta', 'media'].includes(calidad[k])
    )
  ) {
    return false;
  }
  return true;
}

export function validarPaquete(paquete: any): asserts paquete is PedagogicalStudentOutput {
  exigir(
    typeof paquete === 'object' &&
    paquete !== null &&
    ((paquete.schema_version === '1.0' && paquete.status === 'aprobado' && paquete.modo_generacion === undefined) ||
      (paquete.schema_version === '1.1' &&
        ((paquete.modo_generacion === 'multiagente' && paquete.status === 'aprobado') ||
          (paquete.modo_generacion === 'basico' && paquete.status === 'no_evaluado'))))
  );
  exigir(esTexto(paquete.generation_id));

  const {
    metadatos: meta,
    documento_fuente: fuente,
    contenido_adaptado: contenido,
    evaluacion_calidad: calidad,
  } = paquete;
  exigir(
    [meta, fuente, contenido, calidad].every((v) => typeof v === 'object' && v !== null)
  );
  exigir(
    FORMATOS.includes(contenido.tipo) && contenido.tipo === meta.formato_generado
  );
  exigir(
    esTexto(contenido.titulo) &&
    ['document_id', 'titulo', 'hash', 'version'].every((k) => esTexto(fuente[k]))
  );
  exigir(PERFILES.includes(meta.perfil_aplicado));
  exigir(NICHOS.includes(meta.nicho_sector));
  exigir(NIVELES_DETALLE.includes(meta.nivel_detalle));
  exigir(IDIOMAS_SALIDA.includes(meta.idioma_salida));

  exigir(
    esListaTextos(meta.conceptos_clave) &&
    esListaTextos(meta.prerrequisitos) &&
    esListaTextos(meta.objetivos_aprendizaje)
  );
  exigir(meta.conceptos_clave.length > 0 && meta.objetivos_aprendizaje.length > 0);
  exigir(
    Number.isInteger(meta.tiempo_estimado_estudio_minutos) &&
    meta.tiempo_estimado_estudio_minutos > 0
  );
  exigir(
    meta.alcance &&
    (meta.alcance.tipo === 'documento_completo' || meta.alcance.tipo === 'seccion')
  );
  if (meta.alcance.tipo === 'seccion') {
    exigir(esTexto(meta.alcance.seccion_id));
  }
  exigir(
    meta.alcance.secciones_cubiertas === undefined ||
    meta.alcance.secciones_cubiertas === null ||
    esListaTextos(meta.alcance.secciones_cubiertas)
  );
  exigir(esReferencias(paquete.referencias));
  if (paquete.status === 'no_evaluado') {
    exigir(calidad.estado_evaluacion === 'no_realizada');
    exigir(['anclaje_fuente_score', 'cantidad_afirmaciones', 'cantidad_respaldadas',
      'claridad_pedagogica', 'adecuacion_perfil', 'cobertura_objetivos', 'coherencia_didactica',
      'verificacion_visual', 'observaciones', 'razones_bloqueo'].every((key) => calidad[key] === null));
    exigir(paquete.trazabilidad?.modelo_verificacion === null);
  } else {
    // El contrato vigente aprueba con score >= 0.85 sin exigir respaldo total.
    exigir(validarCalidadAprobada(calidad));
  }
  exigir(calidad.observaciones === undefined || calidad.observaciones === null || esTexto(calidad.observaciones));
  exigir(
    contenido.introduccion_contextualizada === undefined ||
    contenido.introduccion_contextualizada === null ||
    esTexto(contenido.introduccion_contextualizada)
  );

  const tipo = contenido.tipo;
  if (tipo === 'resumen_ejecutivo') {
    exigir(esTexto(contenido.impacto_cualitativo) && esReferencias(contenido.referencias));
    exigir(
      ['puntos_clave', 'implicaciones', 'acciones'].every(
        (k) => esListaTextos(contenido[k]) && contenido[k].length > 0
      )
    );
    return;
  }

  const campo = {
    flashcards: 'items',
    quiz: 'preguntas',
    tutorial: 'pasos',
    guion_clase: 'escenas',
  }[tipo as 'flashcards' | 'quiz' | 'tutorial' | 'guion_clase'];
  const items = contenido[campo];
  exigir(
    Array.isArray(items) &&
    items.length > 0 &&
    items.every((item) => typeof item === 'object' && item !== null && esTexto(item.id))
  );
  const uniqueIds = new Set(items.map((i: any) => i.id));
  exigir(uniqueIds.size === items.length);

  if (tipo === 'tutorial') {
    exigir(esTexto(contenido.audiencia) && esListaTextos(contenido.prerrequisitos));
  }
  if (tipo === 'guion_clase') {
    exigir(esListaTextos(contenido.objetivos) && contenido.objetivos.length > 0);
  }

  for (const item of items) {
    if (tipo === 'quiz') {
      exigir(
        !('correct_option_id' in item) &&
        !('justificacion' in item) &&
        !('referencias' in item)
      );
      exigir(esTexto(item.enunciado));
      exigir(Array.isArray(item.opciones) && item.opciones.length >= 2);
      exigir(item.opciones.every((o: any) => esTexto(o?.option_id) && esTexto(o?.texto)));
      const optIds = new Set(item.opciones.map((o: any) => o.option_id));
      exigir(optIds.size === item.opciones.length);
    } else {
      exigir(esReferencias(item.referencias));
      if (tipo === 'flashcards') {
        exigir(esTexto(item.frente) && esTexto(item.dorso) && esListaTextos(item.etiquetas));
        exigir(item.pista_didactica === undefined || item.pista_didactica === null || esTexto(item.pista_didactica));
      } else if (tipo === 'tutorial') {
        exigir(
          esTexto(item.instruccion) &&
          esTexto(item.resultado_esperado) &&
          esTexto(item.verificacion)
        );
        exigir(item.codigo === undefined || item.codigo === null || esTexto(item.codigo));
      } else if (tipo === 'guion_clase') {
        exigir(
          esTexto(item.narracion) &&
          typeof item.duracion_min === 'number' &&
          Number.isFinite(item.duracion_min) &&
          item.duracion_min > 0 &&
          esListaTextos(item.puntos_diapositiva) &&
          item.puntos_diapositiva.length > 0
        );
        exigir(
          item.pregunta_interactiva === undefined ||
          item.pregunta_interactiva === null ||
          esTexto(item.pregunta_interactiva)
        );
      }
    }
  }
}

export function validarGeneracionCompletada(
  job: any,
  expectedGenId: string,
  expectedParams?: GenerateRequest
): PedagogicalStudentOutput {
  if (
    !job ||
    typeof job !== 'object' ||
    job.generation_id !== expectedGenId ||
    job.status !== 'completed'
  ) {
    throw new APIError('INVALID_RESPONSE', 'Estado de generación inválido.', 502);
  }
  const persistencia = job.persistencia;
  if (
    !persistencia ||
    typeof persistencia !== 'object' ||
    persistencia.status_upload !== 'completado' ||
    !['oci', 'mock'].includes(persistencia.provider)
  ) {
    throw new APIError('INVALID_RESPONSE', 'Persistencia no confirmada.', 502);
  }
  const paquete = job.contenido;
  try {
    validarPaquete(paquete);
    exigir(paquete.generation_id === expectedGenId);
    if (expectedParams) {
      exigir((paquete.modo_generacion ?? 'multiagente') === (expectedParams.modo_generacion ?? 'multiagente'));
      exigir(paquete.documento_fuente.document_id === expectedParams.document_id);
      exigir(paquete.metadatos.formato_generado === expectedParams.formato_salida);
      exigir(paquete.metadatos.perfil_aplicado === expectedParams.perfil_destinatario);
      exigir(paquete.metadatos.nicho_sector === expectedParams.nicho_sector);
      exigir(paquete.metadatos.nivel_detalle === expectedParams.nivel_detalle);
      exigir(paquete.metadatos.idioma_salida === expectedParams.idioma_salida);
      exigir(paquete.metadatos.alcance.tipo === expectedParams.alcance.tipo);
      if (expectedParams.alcance.tipo === 'seccion') {
        exigir(paquete.metadatos.alcance.seccion_id === expectedParams.alcance.seccion_id);
      }
    }
  } catch {
    throw new APIError('INVALID_RESPONSE', 'Resultado incompatible con la solicitud.', 502);
  }
  return paquete;
}

export function validarTrabajoChat(datos: any, expectedJobId?: string | null): any {
  if (typeof datos !== 'object' || datos === null) {
    throw new APIError('INVALID_RESPONSE', 'Respuesta de chat inválida.', 502);
  }
  const id = datos.job_id;
  if (typeof id !== 'string' || !/^[A-Za-z0-9_.-]{1,128}$/.test(id)) {
    throw new APIError('INVALID_RESPONSE', 'Respuesta de chat inválida.', 502);
  }
  if (expectedJobId && id !== expectedJobId) {
    throw new APIError('INVALID_RESPONSE', 'Respuesta de chat inválida.', 502);
  }
  if (!ESTADOS_TRABAJO.includes(datos.status)) {
    throw new APIError('INVALID_RESPONSE', 'Respuesta de chat inválida.', 502);
  }
  return datos;
}

export function validarRespuestaChat(
  datos: any,
  payloadEsperado: {
    document_id: string;
    document_ids?: string[];
    pregunta: string;
    modo_generacion?: 'multiagente' | 'basico';
    perfil_destinatario: string;
    idioma_salida: string;
  }
): any {
  const invalida = () => new APIError('INVALID_RESPONSE', 'Respuesta de chat inválida.', 502);
  if (
    typeof datos !== 'object' ||
    datos === null ||
    datos.document_id !== payloadEsperado.document_id ||
    datos.pregunta !== payloadEsperado.pregunta ||
    datos.perfil_destinatario !== payloadEsperado.perfil_destinatario ||
    datos.idioma_salida !== payloadEsperado.idioma_salida
  ) {
    throw invalida();
  }
  const esperados = payloadEsperado.document_ids ?? [payloadEsperado.document_id];
  const declarados = datos.document_ids ?? [datos.document_id];
  if (!Array.isArray(declarados) || declarados.length !== esperados.length ||
    new Set(declarados).size !== declarados.length ||
    declarados.some((id: unknown) => typeof id !== 'string' || !esperados.includes(id))) {
    throw invalida();
  }
  if (typeof datos.document_hash !== 'string' || !/^[0-9a-f]{64}$/.test(datos.document_hash)) {
    throw invalida();
  }
  if (typeof datos.respuesta !== 'string' || datos.respuesta.length > 5000) {
    throw invalida();
  }
  const refs = datos.referencias;
  const calidad = datos.evaluacion_calidad;
  if (datos.estado === 'fuera_de_alcance') {
    if (!Array.isArray(refs) || refs.length !== 0 || calidad !== null) {
      throw invalida();
    }
  } else if (datos.estado === 'respondida' || datos.estado === 'no_evaluado') {
    if (
      !datos.respuesta.trim() ||
      !Array.isArray(refs) ||
      refs.length === 0 ||
      refs.length > 100 ||
      refs.some((r) => typeof r !== 'object' || r === null || !esTexto(r.chunk_id)) ||
      !(datos.estado === 'no_evaluado'
        ? payloadEsperado.modo_generacion === 'basico' && calidad === null
        : payloadEsperado.modo_generacion !== 'basico' && validarCalidadAprobada(calidad))
    ) {
      throw invalida();
    }
  } else {
    throw invalida();
  }
  return datos;
}

export function validarGlosario(
  salida: any,
  expectedJobId: string,
  payloadEsperado: { document_id: string; perfil_destinatario: string; idioma_salida: string; modo_generacion?: 'multiagente' | 'basico' }
): void {
  try {
    exigir(typeof salida === 'object' && salida !== null);
    exigir(salida.glossary_id === expectedJobId);
    exigir(salida.documento_fuente?.document_id === payloadEsperado.document_id);
    exigir(salida.perfil_destinatario === payloadEsperado.perfil_destinatario);
    exigir(salida.idioma_salida === payloadEsperado.idioma_salida);
    const terminos = salida.terminos;
    exigir(Array.isArray(terminos) && terminos.length <= 64);
    exigir(new Set(terminos.map((t: any) => t.id)).size === terminos.length);
    exigir(Number.isInteger(salida.candidatos_omitidos) && salida.candidatos_omitidos >= 0);

    const definidos = terminos.filter((t: any) => t.estado === 'definido');
    exigir(['verificado', 'no_evaluado', 'sin_definiciones', 'sin_terminos'].includes(salida.estado));
    exigir((salida.estado === 'sin_terminos') === (terminos.length === 0));
    exigir(['verificado', 'no_evaluado'].includes(salida.estado) === (definidos.length > 0));
    if (salida.estado === 'no_evaluado') exigir(payloadEsperado.modo_generacion === 'basico');
    if (salida.estado === 'verificado') exigir(payloadEsperado.modo_generacion !== 'basico');

    for (const term of terminos) {
      exigir(esTexto(term.termino_original));
      exigir(typeof term.es_identificador === 'boolean');
      exigir(!term.es_identificador || term.traduccion === null || term.traduccion === undefined);
      exigir(['definido', 'sin_definicion_suficiente'].includes(term.estado));
      exigir(
        Array.isArray(term.referencias) &&
        (term.estado !== 'definido' || term.referencias.length > 0) &&
        term.referencias.every((r: any) => esTexto(r?.chunk_id))
      );
      if (term.estado === 'definido') {
        exigir(esTexto(term.definicion) && !term.limitacion);
      } else {
        exigir(esTexto(term.limitacion) && !term.definicion && !term.traduccion);
      }
    }

    if (salida.estado === 'no_evaluado') {
      exigir(salida.evaluacion_calidad === null && salida.trazabilidad === null);
    } else if (definidos.length > 0) {
      exigir(validarCalidadAprobada(salida.evaluacion_calidad));
      exigir(typeof salida.trazabilidad === 'object' && salida.trazabilidad !== null);
    } else {
      exigir(salida.evaluacion_calidad === null);
    }
  } catch {
    throw new APIError('INVALID_RESPONSE', 'Glosario inválido', 502);
  }
}

export function validarFuente(
  fuente: any,
  referencia: { chunk_id: string; pagina?: number | null; seccion?: string | null },
  documentId: string
): void {
  if (
    typeof fuente !== 'object' ||
    fuente === null ||
    fuente.document_id !== documentId ||
    fuente.chunk_id !== referencia.chunk_id ||
    !esTexto(fuente.texto) ||
    (fuente.pagina !== undefined &&
      fuente.pagina !== null &&
      (!Number.isInteger(fuente.pagina) || fuente.pagina < 1)) ||
    (referencia.pagina !== undefined &&
      referencia.pagina !== null &&
      fuente.pagina !== referencia.pagina) ||
    (referencia.seccion !== undefined &&
      referencia.seccion !== null &&
      fuente.seccion !== referencia.seccion)
  ) {
    throw new APIError('INVALID_RESPONSE', 'Fuente incompatible con la cita.', 502);
  }
}

export function validarProgreso(salida: any): void {
  const contadores = [
    'version',
    'conceptos_revisados',
    'flashcards_vistas',
    'quiz_intentos',
    'quiz_aciertos_primer_intento',
    'quiz_total_primer_intento',
    'quiz_aciertos_posteriores',
  ];
  const valido =
    typeof salida === 'object' &&
    salida !== null &&
    contadores.every((k) => Number.isInteger(salida[k]) && salida[k] >= 0) &&
    typeof salida.tiempo_estimado_restante_minutos === 'number' &&
    Number.isFinite(salida.tiempo_estimado_restante_minutos) &&
    salida.tiempo_estimado_restante_minutos >= 0 &&
    salida.quiz_aciertos_primer_intento <= salida.quiz_total_primer_intento &&
    salida.quiz_total_primer_intento <= salida.quiz_intentos &&
    salida.quiz_aciertos_posteriores <=
    salida.quiz_intentos - salida.quiz_total_primer_intento;

  if (!valido) {
    throw new APIError('INVALID_RESPONSE', 'Progreso inválido', 502);
  }
}

export function formatosExportables(paquete: PedagogicalStudentOutput): ExportFormat[] {
  const formatos: ExportFormat[] = ['json', 'md', 'pdf'];
  if (paquete.metadatos.formato_generado === 'flashcards') {
    formatos.push('csv', 'tsv', 'apkg');
  }
  return formatos;
}

export function filtrarTerminosGlosario(terminos: any[], consulta: string): any[] {
  const q = consulta.trim().toLocaleLowerCase();
  const norm = (s: string) =>
    s
      .normalize('NFD')
      .replace(/[\u0300-\u036f]/g, '')
      .toLocaleLowerCase();

  return terminos
    .filter((t) => {
      if (!q) return true;
      const haystack = [t.termino_original, t.traduccion, t.definicion]
        .filter(Boolean)
        .join(' ')
        .toLocaleLowerCase();
      return haystack.includes(q);
    })
    .sort((a, b) => {
      const na = norm(a.termino_original);
      const nb = norm(b.termino_original);
      if (na !== nb) return na.localeCompare(nb);
      return String(a.id).localeCompare(String(b.id));
    });
}
