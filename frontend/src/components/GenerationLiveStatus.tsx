import React from 'react';
import { OutputLanguage } from '../api/types';
import { CATALOGOS, t } from '../i18n';
import { useGenerationProgress } from '../flows/generationProgress';
import { ErrorBanner } from './ErrorBanner';

export function GenerationLiveStatus({ stream, idioma, modo = 'multiagente' }: {
  stream: ReturnType<typeof useGenerationProgress>; idioma: OutputLanguage;
  modo?: 'multiagente' | 'basico';
}) {
  const step = stream.progress?.step;
  const key = `live.step.${step}`;
  const stage = CATALOGOS[idioma][key] ?? t('live.step.generation', idioma);
  return <div className="nm-live-progress">
    <div role="status" aria-live="polite" aria-atomic="true">
      <p>{t('generar.etapa', idioma, { etapa: stage })}</p>
      {step && ['supervisor', 'researcher', 'writer', 'critic', 'finalizer'].includes(step) &&
        !!stream.progress?.iteration && <p>{t(modo === 'basico' ? 'modo.redaccion_unica' : 'live.draft', idioma, { numero: stream.progress.iteration })}</p>}
      <p className="nm-live-connection"><span aria-hidden="true">●</span> {t(`live.${stream.phase}`, idioma)}</p>
    </div>
    <ErrorBanner error={stream.error} idioma={idioma} />
    {(stream.phase === 'error' || stream.phase === 'reconnecting') &&
      <button type="button" className="nm-btn" onClick={stream.reconnect}>{t('live.reconnect', idioma)}</button>}
  </div>;
}
