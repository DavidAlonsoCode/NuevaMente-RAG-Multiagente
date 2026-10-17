import React, { useState } from 'react';
import { CheckCircle2, HelpCircle, Layers, Sparkles, Terminal, Video } from 'lucide-react';
import { APIClient, APIError } from '../api/client';
import {
  EscenaGuion,
  ExecutiveSummary,
  FlashcardDeck,
  OutputLanguage,
  PasoTutorial,
  PedagogicalStudentOutput,
  PracticalTutorial,
  QuizVistaEstudiante,
  Referencia,
  VideoLessonScript,
} from '../api/types';
import { validarPaquete } from '../flows/validators';
import { t } from '../i18n';
import { ErrorBanner } from './ErrorBanner';
import { SourcesDrawer } from './SourcesDrawer';

type SourceRenderFn = (refs: Referencia[], keySuffix: string) => React.ReactNode;

export const FlashcardsRenderer: React.FC<{
  contenido: FlashcardDeck;
  idioma: OutputLanguage;
  generationId: string;
  renderSources: SourceRenderFn;
  onReviewCard?: (flashcardId: string) => void;
}> = ({ contenido, idioma, generationId, renderSources, onReviewCard }) => {
  const [visibleCards, setVisibleCards] = useState<Record<string, boolean>>({});
  const [hintsOpen, setHintsOpen] = useState<Record<string, boolean>>({});

  const toggleCard = (id: string) => {
    const next = !visibleCards[id];
    setVisibleCards((prev) => ({ ...prev, [id]: next }));
    if (next && onReviewCard) {
      onReviewCard(id);
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
      {contenido.items.map((item, idx) => {
        const isVisible = Boolean(visibleCards[item.id]);
        const isHintOpen = Boolean(hintsOpen[item.id]);
        return (
          <div key={`${generationId}_${item.id}`} className="nm-card nm-card-interactive">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.6rem' }}>
              <span className="nm-badge">
                <Layers size={13} />
                {t('material.tarjeta', idioma, { numero: idx + 1, total: contenido.items.length })}
              </span>
              {item.etiquetas?.length > 0 && (
                <span style={{ fontSize: '0.78rem', color: 'var(--ink-muted)' }}>
                  {item.etiquetas.join(' · ')}
                </span>
              )}
            </div>

            <h3 style={{ margin: '0 0 0.75rem 0', fontSize: '1.1rem' }}>{item.frente}</h3>

            {item.pista_didactica && (
              <div style={{ marginBottom: '0.75rem' }}>
                <button
                  type="button"
                  className="nm-btn"
                  style={{ fontSize: '0.8rem', padding: '0.3rem 0.65rem' }}
                  onClick={() => setHintsOpen((p) => ({ ...p, [item.id]: !isHintOpen }))}
                >
                  <HelpCircle size={14} />
                  {t('material.pista', idioma)}
                </button>
                {isHintOpen && (
                  <div
                    style={{
                      marginTop: '0.45rem',
                      padding: '0.65rem 0.85rem',
                      background: 'var(--surface-2)',
                      borderRadius: '6px',
                      fontSize: '0.88rem',
                      color: 'var(--ink-secondary)',
                    }}
                  >
                    {item.pista_didactica}
                  </div>
                )}
              </div>
            )}

            <button
              type="button"
              className="nm-btn nm-btn-primary"
              onClick={() => toggleCard(item.id)}
            >
              {t(isVisible ? 'material.ocultar_respuesta' : 'material.mostrar_respuesta', idioma)}
            </button>

            {isVisible && (
              <div className="nm-reveal">
                <div style={{ whiteSpace: 'pre-wrap' }}>{item.dorso}</div>
                {renderSources(item.referencias, `fc_${item.id}`)}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
};

export const QuizInteractiveRenderer: React.FC<{
  contenido: QuizVistaEstudiante;
  idioma: OutputLanguage;
  generationId: string;
  documentId: string;
  apiClient: APIClient;
  token: string;
  renderSources: SourceRenderFn;
  onSessionInvalid: () => void;
}> = ({
  contenido,
  idioma,
  generationId,
  apiClient,
  token,
  renderSources,
  onSessionInvalid,
}) => {
  const preguntas = contenido.preguntas;
  const [indice, setIndice] = useState(0);
  const [selecciones, setSelecciones] = useState<Record<string, string>>({});
  const [intentos, setIntentos] = useState<
    Record<string, Array<{ optionId: string; eventId: string; feedback: any | null }>>
  >({});
  const [modoNuevo, setModoNuevo] = useState<Record<string, boolean>>({});
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<APIError | null>(null);

  const pregunta = preguntas[indice];
  const qid = pregunta.id;
  const historial = intentos[qid] || [];
  const ultimo = historial.length > 0 ? historial[historial.length - 1] : null;
  const pendiente = ultimo !== null && ultimo.feedback === null;
  const bloqueada = ultimo !== null && !modoNuevo[qid];
  const opcionActual = bloqueada ? ultimo?.optionId : selecciones[qid];

  const confirmados = Object.values(intentos).map((list) =>
    list.filter((x) => x.feedback !== null)
  );
  const respondidas = confirmados.filter((list) => list.length > 0).length;
  const primerosAciertos = confirmados.filter(
    (list) => list.length > 0 && list[0].feedback?.correcta
  ).length;
  const posterioresAciertos = confirmados.reduce(
    (acc, list) => acc + list.slice(1).filter((x) => x.feedback?.correcta).length,
    0
  );

  const enviarRespuesta = async () => {
    if (!opcionActual) return;
    setSubmitting(true);
    setError(null);

    let intentoActual = ultimo;
    if (!pendiente || !intentoActual) {
      const nuevoEvtId = `quiz-${generationId}-${qid}-${historial.length + 1}-${Date.now().toString(36)}`;
      intentoActual = { optionId: opcionActual, eventId: nuevoEvtId, feedback: null };
      setIntentos((prev) => ({
        ...prev,
        [qid]: [...(prev[qid] || []), intentoActual!],
      }));
      setModoNuevo((prev) => ({ ...prev, [qid]: false }));
    }

    try {
      const datos = await apiClient.answerQuiz(token, generationId, {
        question_id: qid,
        option_id: intentoActual.optionId,
        event_id: intentoActual.eventId,
      });

      const validOptionIds = new Set(pregunta.opciones.map((o) => o.option_id));
      if (
        typeof datos?.correcta !== 'boolean' ||
        !validOptionIds.has(datos?.correct_option_id) ||
        datos.correcta !== (intentoActual.optionId === datos.correct_option_id) ||
        typeof datos?.justificacion !== 'string' ||
        !datos.justificacion.trim() ||
        !Array.isArray(datos?.referencias) ||
        datos.referencias.length === 0
      ) {
        throw new APIError('INVALID_RESPONSE', 'Respuesta de quiz inválida.', 502);
      }

      setIntentos((prev) => {
        const copia = [...(prev[qid] || [])];
        if (copia.length > 0) {
          copia[copia.length - 1] = { ...copia[copia.length - 1], feedback: datos };
        }
        return { ...prev, [qid]: copia };
      });
    } catch (err) {
      if (err instanceof APIError) {
        if (err.statusCode === 401) {
          onSessionInvalid();
          return;
        }
        setError(err);
      } else {
        setError(new APIError('INVALID_RESPONSE', 'Respuesta de quiz inválida.', 502));
      }
    } finally {
      setSubmitting(false);
    }
  };

  const iniciarNuevoIntento = () => {
    setModoNuevo((prev) => ({ ...prev, [qid]: true }));
    setSelecciones((prev) => {
      const next = { ...prev };
      delete next[qid];
      return next;
    });
  };

  const textoOpcionCorrecta =
    ultimo?.feedback &&
    pregunta.opciones.find((o) => o.option_id === ultimo.feedback.correct_option_id)?.texto;

  return (
    <div className="nm-card">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '0.5rem', marginBottom: '0.75rem' }}>
        <h3 style={{ margin: 0 }}>{t('quiz.titulo', idioma)}</h3>
        <span className="nm-badge nm-badge-neutral">
          {t('quiz.respondidas', idioma, { cantidad: respondidas, total: preguntas.length })}
        </span>
      </div>

      {/* Barra de progreso */}
      <div
        role="progressbar"
        aria-label={t('a11y.barra_progreso', idioma)}
        aria-valuenow={respondidas}
        aria-valuemin={0}
        aria-valuemax={preguntas.length}
        style={{
          height: '6px',
          background: 'var(--surface-3)',
          borderRadius: '999px',
          overflow: 'hidden',
          marginBottom: '1.2rem',
        }}
      >
        <div
          style={{
            width: `${(respondidas / preguntas.length) * 100}%`,
            height: '100%',
            background: 'linear-gradient(90deg, var(--oracle-red), var(--oracle-coral))',
            transition: 'width 220ms ease',
          }}
        />
      </div>

      <ErrorBanner error={error} idioma={idioma} />

      <div style={{ marginBottom: '0.5rem', fontSize: '0.84rem', color: 'var(--oracle-coral)', fontWeight: 600 }}>
        {t('quiz.pregunta', idioma, { numero: indice + 1, total: preguntas.length })}
      </div>
      <h4 style={{ margin: '0 0 1rem 0', fontSize: '1.08rem' }}>{pregunta.enunciado}</h4>

      <fieldset
        disabled={bloqueada || submitting}
        style={{ border: 'none', padding: 0, margin: '0 0 1rem 0', display: 'flex', flexDirection: 'column', gap: '0.55rem' }}
      >
        <legend className="nm-label" style={{ marginBottom: '0.5rem' }}>{t('quiz.elegir', idioma)}</legend>
        {pregunta.opciones.map((op) => {
          const checked = opcionActual === op.option_id;
          return (
            <label
              key={op.option_id}
              style={{
                display: 'flex',
                alignItems: 'flex-start',
                gap: '0.65rem',
                padding: '0.75rem 0.9rem',
                borderRadius: 'var(--radius-sm)',
                background: checked ? 'rgba(199, 70, 52, 0.18)' : 'var(--surface-2)',
                border: checked ? '1px solid var(--oracle-coral)' : '1px solid var(--border-hairline)',
                cursor: bloqueada ? 'default' : 'pointer',
              }}
            >
              <input
                type="radio"
                name={`quiz_${generationId}_${qid}`}
                value={op.option_id}
                checked={checked}
                onChange={() => setSelecciones((prev) => ({ ...prev, [qid]: op.option_id }))}
                style={{ marginTop: '0.2rem', accentColor: 'var(--oracle-red)' }}
              />
              <span>
                <strong className="mono">{op.option_id}.</strong> {op.texto}
              </span>
            </label>
          );
        })}
      </fieldset>

      <div style={{ display: 'flex', gap: '0.65rem', flexWrap: 'wrap' }}>
        <button
          type="button"
          className="nm-btn nm-btn-primary"
          disabled={!opcionActual || (bloqueada && !pendiente) || submitting}
          onClick={enviarRespuesta}
        >
          {submitting
            ? t('quiz.enviando', idioma)
            : t(pendiente ? 'quiz.reenviar' : 'quiz.responder', idioma)}
        </button>

        {ultimo?.feedback && (
          <button type="button" className="nm-btn" onClick={iniciarNuevoIntento}>
            {t('quiz.reintentar', idioma)}
          </button>
        )}
      </div>

      {ultimo?.feedback && (
        <div style={{ marginTop: '1rem' }}>
          <div className={`nm-alert ${ultimo.feedback.correcta ? 'nm-alert-success' : 'nm-alert-error'}`}>
            <strong>
              {t(ultimo.feedback.correcta ? 'quiz.correcto' : 'quiz.incorrecto', idioma)}
            </strong>
            <div style={{ marginTop: '0.35rem' }}>
              {t('quiz.respuesta_correcta', idioma, {
                opcion: textoOpcionCorrecta || ultimo.feedback.correct_option_id,
              })}
            </div>
            <div style={{ marginTop: '0.45rem', color: 'var(--ink-primary)' }}>
              {ultimo.feedback.justificacion}
            </div>
          </div>
          {renderSources(ultimo.feedback.referencias, `quiz_fb_${qid}`)}
        </div>
      )}

      <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: '1.25rem', paddingTop: '1rem', borderTop: '1px solid var(--border-hairline)' }}>
        <button
          type="button"
          className="nm-btn"
          disabled={indice === 0}
          onClick={() => setIndice((i) => Math.max(0, i - 1))}
        >
          {t('quiz.anterior', idioma)}
        </button>
        <button
          type="button"
          className="nm-btn"
          disabled={indice === preguntas.length - 1}
          onClick={() => setIndice((i) => Math.min(preguntas.length - 1, i + 1))}
        >
          {t('quiz.siguiente_pregunta', idioma)}
        </button>
      </div>

      {respondidas === preguntas.length && (
        <div className="nm-card nm-card-terracotta" style={{ marginTop: '1.25rem' }}>
          <h4 style={{ margin: '0 0 0.5rem 0' }}>{t('quiz.resumen_sesion', idioma)}</h4>
          <div>{t('quiz.primeros_sesion', idioma, { cantidad: primerosAciertos, total: preguntas.length })}</div>
          <div>{t('quiz.posteriores_sesion', idioma, { cantidad: posterioresAciertos })}</div>
        </div>
      )}
    </div>
  );
};

export const TutorialRenderer: React.FC<{
  contenido: PracticalTutorial;
  idioma: OutputLanguage;
  renderSources: SourceRenderFn;
}> = ({ contenido, idioma, renderSources }) => (
  <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
    <div style={{ fontSize: '0.9rem', color: 'var(--ink-secondary)' }}>
      <strong>{t('material.audiencia', idioma)}:</strong> {contenido.audiencia}
    </div>
    {contenido.prerrequisitos?.length > 0 && (
      <div className="nm-card" style={{ padding: '0.9rem 1.1rem' }}>
        <strong>{t('material.prerrequisitos', idioma)}</strong>
        <ul style={{ margin: '0.4rem 0 0 1.2rem', padding: 0 }}>
          {contenido.prerrequisitos.map((p, i) => (
            <li key={i}>{p}</li>
          ))}
        </ul>
      </div>
    )}

    {contenido.pasos.map((paso: PasoTutorial, idx: number) => (
      <div key={paso.id} className="nm-card nm-card-interactive">
        <span className="nm-badge">
          <Terminal size={13} />
          {t('material.paso', idioma, { numero: idx + 1, total: contenido.pasos.length })}
        </span>
        <h3 style={{ margin: '0.6rem 0' }}>{paso.instruccion}</h3>
        {paso.codigo && (
          <pre
            className="mono"
            style={{
              background: '#080a0e',
              border: '1px solid var(--border-hairline)',
              borderRadius: '8px',
              padding: '0.85rem 1rem',
              overflowX: 'auto',
              fontSize: '0.85rem',
              color: '#ffbfa8',
            }}
          >
            <code>{paso.codigo}</code>
          </pre>
        )}
        <div className="nm-grid-2" style={{ marginTop: '0.75rem' }}>
          <div style={{ background: 'var(--surface-2)', padding: '0.75rem', borderRadius: '6px' }}>
            <div style={{ fontSize: '0.78rem', fontWeight: 600, color: 'var(--oracle-coral)', marginBottom: '0.25rem' }}>
              {t('material.resultado_esperado', idioma)}
            </div>
            <div style={{ fontSize: '0.9rem' }}>{paso.resultado_esperado}</div>
          </div>
          <div style={{ background: 'var(--surface-2)', padding: '0.75rem', borderRadius: '6px' }}>
            <div style={{ fontSize: '0.78rem', fontWeight: 600, color: '#6ee787', marginBottom: '0.25rem' }}>
              {t('material.verificacion', idioma)}
            </div>
            <div style={{ fontSize: '0.9rem' }}>{paso.verificacion}</div>
          </div>
        </div>
        {renderSources(paso.referencias, `paso_${paso.id}`)}
      </div>
    ))}
  </div>
);

export const SummaryRenderer: React.FC<{
  contenido: ExecutiveSummary;
  idioma: OutputLanguage;
  renderSources: SourceRenderFn;
}> = ({ contenido, idioma, renderSources }) => (
  <div>
    <div className="nm-grid-2">
      <div className="nm-card nm-card-terracotta">
        <span className="nm-badge">{t('material.impacto', idioma)}</span>
        <p style={{ marginTop: '0.65rem', lineHeight: 1.65 }}>{contenido.impacto_cualitativo}</p>
        <h3 style={{ fontSize: '1rem', marginTop: '1.1rem' }}>{t('material.puntos_clave', idioma)}</h3>
        <ul style={{ paddingLeft: '1.2rem', margin: 0 }}>
          {contenido.puntos_clave.map((pt, i) => (
            <li key={i} style={{ marginBottom: '0.35rem' }}>{pt}</li>
          ))}
        </ul>
      </div>

      <div className="nm-card">
        <span className="nm-badge">{t('material.implicaciones', idioma)}</span>
        <ul style={{ paddingLeft: '1.2rem', marginTop: '0.65rem' }}>
          {contenido.implicaciones.map((imp, i) => (
            <li key={i} style={{ marginBottom: '0.35rem' }}>{imp}</li>
          ))}
        </ul>
        <h3 style={{ fontSize: '1rem', marginTop: '1.1rem' }}>{t('material.acciones', idioma)}</h3>
        <ul style={{ paddingLeft: '1.2rem', margin: 0 }}>
          {contenido.acciones.map((ac, i) => (
            <li key={i} style={{ marginBottom: '0.35rem' }}>{ac}</li>
          ))}
        </ul>
      </div>
    </div>
    {renderSources(contenido.referencias, 'resumen')}
  </div>
);

export const ScriptRenderer: React.FC<{
  contenido: VideoLessonScript;
  idioma: OutputLanguage;
  renderSources: SourceRenderFn;
}> = ({ contenido, idioma, renderSources }) => (
  <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
    <div className="nm-card">
      <strong>{t('material.objetivos', idioma)}</strong>
      <ul style={{ margin: '0.45rem 0 0 1.2rem', padding: 0 }}>
        {contenido.objetivos.map((obj, i) => (
          <li key={i}>{obj}</li>
        ))}
      </ul>
    </div>

    {contenido.escenas.map((escena: EscenaGuion, idx: number) => (
      <div key={escena.id} className="nm-card nm-card-interactive">
        <span className="nm-badge">
          <Video size={13} />
          {t('material.escena', idioma, { numero: idx + 1, minutos: escena.duracion_min })}
        </span>
        <p style={{ fontStyle: 'italic', color: 'var(--ink-primary)', margin: '0.75rem 0', lineHeight: 1.65 }}>
          “{escena.narracion}”
        </p>
        <div style={{ background: 'var(--surface-2)', padding: '0.75rem 1rem', borderRadius: '6px' }}>
          <strong>{t('material.diapositiva', idioma)}</strong>
          <ul style={{ margin: '0.4rem 0 0 1.2rem', padding: 0 }}>
            {escena.puntos_diapositiva.map((pt, i) => (
              <li key={i}>{pt}</li>
            ))}
          </ul>
        </div>
        {escena.pregunta_interactiva && (
          <div className="nm-alert nm-alert-info" style={{ marginTop: '0.75rem', marginBottom: 0 }}>
            <strong>{t('material.pregunta_interactiva', idioma)}:</strong> {escena.pregunta_interactiva}
          </div>
        )}
        {renderSources(escena.referencias, `escena_${escena.id}`)}
      </div>
    ))}
  </div>
);

export const QualityPanel: React.FC<{
  paquete: PedagogicalStudentOutput;
  idioma: OutputLanguage;
}> = ({ paquete, idioma }) => {
  const [open, setOpen] = useState(false);
  const ev = paquete.evaluacion_calidad;
  const secciones = paquete.metadatos.alcance.secciones_cubiertas;
  const totalRefs = new Set((paquete.referencias || []).map((r) => r.chunk_id)).size;

  if (ev.estado_evaluacion === 'no_realizada') {
    return <div className="nm-alert nm-alert-warning" role="note" style={{ marginTop: '1rem' }}>
      <strong>{t('modo.basico', idioma)}</strong><p>{t('modo.aviso', idioma)}</p>
    </div>;
  }

  return (
    <div className="nm-card" style={{ marginTop: '1.25rem' }}>
      <button
        type="button"
        onClick={() => setOpen(!open)}
        style={{
          width: '100%',
          background: 'none',
          border: 'none',
          color: 'var(--ink-primary)',
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          cursor: 'pointer',
          padding: 0,
          fontSize: '0.95rem',
          fontWeight: 600,
        }}
      >
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: '0.5rem' }}>
          <Sparkles size={16} color="var(--oracle-coral)" />
          {t('calidad.panel', idioma)}
        </span>
        <span className="mono" style={{ color: '#6ee787' }}>
          {ev.anclaje_fuente_score !== null ? ev.anclaje_fuente_score.toFixed(2) : 'N/A'}
        </span>
      </button>

      {open && (
        <div style={{ marginTop: '1rem', borderTop: '1px solid var(--border-hairline)', paddingTop: '1rem' }}>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: '0.85rem', marginBottom: '0.35rem' }}>
            <span style={{ fontSize: '0.85rem', color: 'var(--ink-secondary)' }}>{t('calidad.anclaje', idioma)}:</span>
            <strong className="mono" style={{ fontSize: '1.35rem', color: '#6ee787' }}>
              {ev.anclaje_fuente_score !== null ? ev.anclaje_fuente_score.toFixed(2) : 'N/A'}
            </strong>
            <span style={{ fontSize: '0.84rem', color: 'var(--ink-muted)' }}>
              ({t('calidad.afirmaciones', idioma, { respaldadas: ev.cantidad_respaldadas, total: ev.cantidad_afirmaciones })})
            </span>
          </div>
          <p style={{ fontSize: '0.8rem', color: 'var(--ink-muted)', marginTop: 0 }}>
            {t('calidad.explicacion_score', idioma)}
          </p>

          <div className="nm-grid-2" style={{ margin: '0.85rem 0', fontSize: '0.86rem' }}>
            {(
              [
                'claridad_pedagogica',
                'adecuacion_perfil',
                'cobertura_objetivos',
                'coherencia_didactica',
                'verificacion_visual',
              ] as const
            ).map((campo) => (
              <div key={campo} style={{ background: 'var(--surface-2)', padding: '0.5rem 0.75rem', borderRadius: '6px' }}>
                <strong>{t(`calidad.${campo}`, idioma)}:</strong>{' '}
                {t(`calidad.valor.${ev[campo]}`, idioma)}
              </div>
            ))}
          </div>

          <div style={{ marginTop: '0.75rem', fontSize: '0.86rem' }}>
            <strong>{t('calidad.cobertura', idioma)}</strong>
            {secciones && secciones.length > 0 ? (
              <ul style={{ margin: '0.35rem 0 0 1.2rem' }}>
                {secciones.map((s, i) => (
                  <li key={i}>{s}</li>
                ))}
              </ul>
            ) : (
              <div style={{ color: 'var(--ink-muted)', fontSize: '0.8rem' }}>
                {t('calidad.cobertura_no_detallada', idioma)}
              </div>
            )}
          </div>

          <div style={{ marginTop: '0.75rem', fontSize: '0.82rem', color: 'var(--ink-muted)' }}>
            <strong>{t('calidad.limitaciones', idioma)}:</strong> {t('calidad.limites', idioma)}
            {ev.observaciones && <div style={{ marginTop: '0.3rem', color: 'var(--ink-secondary)' }}>{ev.observaciones}</div>}
            <div style={{ marginTop: '0.3rem' }}>
              {t('calidad.referencias', idioma, { total: totalRefs })}
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

const PackageViewerView: React.FC<{
  paquete: PedagogicalStudentOutput;
  idioma: OutputLanguage;
  apiClient: APIClient;
  token: string | null;
  registrarEstudio?: boolean;
  confirmedConcepts?: Record<string, boolean>;
  onMarkConcept?: (documentId: string, concepto: string, generationId: string) => void;
  onReviewFlashcard?: (generationId: string, flashcardId: string) => void;
  onSessionInvalid: () => void;
}> = ({
  paquete,
  idioma,
  apiClient,
  token,
  registrarEstudio = true,
  confirmedConcepts = {},
  onMarkConcept,
  onReviewFlashcard,
  onSessionInvalid,
}) => {
  const [rutaOpen, setRutaOpen] = useState(false);

  try {
    validarPaquete(paquete);
  } catch {
    return (
      <ErrorBanner
        error={new APIError('INVALID_RESPONSE', 'Material incompatible.', 502)}
        idioma={idioma}
      />
    );
  }

  const { contenido_adaptado: contenido, metadatos: meta, documento_fuente: fuente } = paquete;
  const documentId = fuente.document_id;

  const renderSources: SourceRenderFn = (refs, keySuffix) => (
    <SourcesDrawer
      key={keySuffix}
      documentId={documentId}
      referencias={refs}
      idioma={idioma}
      apiClient={apiClient}
      token={token}
      onSessionInvalid={onSessionInvalid}
    />
  );

  return (
    <div>
      <div className="nm-card nm-card-terracotta" style={{ marginBottom: '1.25rem' }}>
        <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', marginBottom: '0.55rem' }}>
          <span className={`nm-badge ${paquete.status === 'aprobado' ? 'nm-badge-success' : 'nm-badge-neutral'}`}>
            {paquete.status === 'aprobado' && <CheckCircle2 size={13} />}
            {t(paquete.status === 'aprobado' ? 'material.revisado' : 'modo.basico', idioma)}
          </span>
          {paquete.status === 'aprobado' && <span className="nm-badge nm-badge-neutral">{t('modo.multiagente', idioma)}</span>}
          <span className="nm-badge">{t(`formato.${contenido.tipo}`, idioma)}</span>
          <span className="nm-badge nm-badge-neutral">{t(`perfil.${meta.perfil_aplicado}`, idioma)}</span>
          <span className="nm-badge nm-badge-neutral">{t(`idioma.${meta.idioma_salida}`, idioma)}</span>
          <span className="nm-badge nm-badge-neutral">
            {t('material.tiempo', idioma, { minutos: meta.tiempo_estimado_estudio_minutos })}
          </span>
        </div>

        <h2 style={{ margin: '0.35rem 0 0.5rem 0', fontSize: '1.45rem' }}>{contenido.titulo}</h2>

        {meta.nicho_sector !== 'general' && (
          <div style={{ marginTop: '0.5rem' }}>
            <span className="nm-badge">{t('material.ejemplo_ilustrativo', idioma)}</span>
            <span style={{ fontSize: '0.82rem', color: 'var(--ink-secondary)', marginLeft: '0.5rem' }}>
              {t('material.ejemplo_ayuda', idioma, { nicho: t(`nicho.${meta.nicho_sector}`, idioma) })}
            </span>
          </div>
        )}

        {contenido.introduccion_contextualizada && (
          <p style={{ marginTop: '0.75rem', marginBottom: 0, color: 'var(--ink-secondary)' }}>
            {contenido.introduccion_contextualizada}
          </p>
        )}

        {/* Ruta de aprendizaje */}
        <div style={{ marginTop: '0.9rem', borderTop: '1px solid var(--border-subtle)', paddingTop: '0.75rem' }}>
          <button
            type="button"
            className="nm-btn"
            style={{ fontSize: '0.82rem', padding: '0.35rem 0.75rem' }}
            onClick={() => setRutaOpen(!rutaOpen)}
          >
            {t('material.preparar', idioma)}
          </button>

          {rutaOpen && (
            <div className="nm-grid-2" style={{ marginTop: '0.85rem', fontSize: '0.88rem' }}>
              <div>
                <strong>{t('material.objetivos_aprendizaje', idioma)}</strong>
                <ul style={{ paddingLeft: '1.2rem', marginTop: '0.35rem' }}>
                  {meta.objetivos_aprendizaje.map((o, i) => (
                    <li key={i}>{o}</li>
                  ))}
                </ul>
                <strong>{t('material.prerrequisitos', idioma)}</strong>
                {meta.prerrequisitos.length > 0 ? (
                  <ul style={{ paddingLeft: '1.2rem', marginTop: '0.35rem' }}>
                    {meta.prerrequisitos.map((p, i) => (
                      <li key={i}>{p}</li>
                    ))}
                  </ul>
                ) : (
                  <div style={{ color: 'var(--ink-muted)', fontSize: '0.82rem' }}>
                    {t('material.sin_prerrequisitos', idioma)}
                  </div>
                )}
              </div>

              <div>
                <strong>{t('material.conceptos_clave', idioma)}</strong>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '0.45rem', marginTop: '0.45rem' }}>
                  {Array.from(new Set(meta.conceptos_clave)).map((concepto) => {
                    const key = `${documentId}::${concepto.trim()}`;
                    const done = Boolean(confirmedConcepts[key]);
                    return (
                      <div key={concepto} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '0.5rem', background: 'var(--surface-2)', padding: '0.45rem 0.65rem', borderRadius: '6px' }}>
                        <span>{concepto}</span>
                        {registrarEstudio && onMarkConcept && (
                          <button
                            type="button"
                            className="nm-btn"
                            style={{ fontSize: '0.76rem', padding: '0.25rem 0.55rem' }}
                            disabled={done}
                            onClick={() => onMarkConcept(documentId, concepto, paquete.generation_id)}
                          >
                            {t('progreso.marcar_concepto', idioma, { concepto })}
                          </button>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Contenido específico del formato */}
      {contenido.tipo === 'flashcards' && (
        <FlashcardsRenderer
          contenido={contenido}
          idioma={idioma}
          generationId={paquete.generation_id}
          renderSources={renderSources}
          onReviewCard={
            registrarEstudio && onReviewFlashcard
              ? (fid) => onReviewFlashcard(paquete.generation_id, fid)
              : undefined
          }
        />
      )}

      {contenido.tipo === 'quiz' && token && (
        <QuizInteractiveRenderer
          contenido={contenido}
          idioma={idioma}
          generationId={paquete.generation_id}
          documentId={documentId}
          apiClient={apiClient}
          token={token}
          renderSources={renderSources}
          onSessionInvalid={onSessionInvalid}
        />
      )}

      {contenido.tipo === 'tutorial' && (
        <TutorialRenderer contenido={contenido} idioma={idioma} renderSources={renderSources} />
      )}

      {contenido.tipo === 'resumen_ejecutivo' && (
        <SummaryRenderer contenido={contenido} idioma={idioma} renderSources={renderSources} />
      )}

      {contenido.tipo === 'guion_clase' && (
        <ScriptRenderer contenido={contenido} idioma={idioma} renderSources={renderSources} />
      )}

      <QualityPanel paquete={paquete} idioma={idioma} />

      <div className="nm-card" style={{ marginTop: '1.25rem' }}>
        <h3 style={{ margin: '0 0 0.25rem 0', fontSize: '1.05rem' }}>{t('fuentes.panel', idioma)}</h3>
        <div style={{ fontSize: '0.83rem', color: 'var(--ink-muted)', marginBottom: '0.6rem' }}>
          {fuente.titulo}
        </div>
        {renderSources(paquete.referencias, 'panel_general')}
      </div>
    </div>
  );
};

export const PackageViewer: React.FC<React.ComponentProps<typeof PackageViewerView>> = (props) =>
  <PackageViewerView key={`${props.token}:${props.paquete.generation_id}`} {...props} />;
