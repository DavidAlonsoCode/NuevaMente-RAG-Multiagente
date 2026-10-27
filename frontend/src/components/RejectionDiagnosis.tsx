import React from 'react';
import { OutputLanguage } from '../api/types';
import { t } from '../i18n';

// Varios motivos del catálogo público describen el mismo problema: se explican una sola vez.
const GRUPO_MOTIVO: Record<string, string> = {
  afirmacion_sin_respaldo: 'respaldo',
  evidencia_insuficiente: 'respaldo',
  fidelidad_baja: 'respaldo',
  fidelidad_requiere_revision: 'respaldo',
  cobertura_incompleta: 'cobertura',
  contradiccion_o_evaluacion_incompleta: 'contradiccion',
  quiz_invalido: 'quiz',
  verificacion_visual_insuficiente: 'visual',
  claridad_pedagogica: 'pedagogia',
  adecuacion_perfil: 'pedagogia',
  coherencia_didactica: 'pedagogia',
};

interface UbicacionCitada {
  documento: string;
  seccion: string | null;
  pagina: number | null;
}

// Cifras del último borrador rechazado. Si llegan incompletas no se muestran: nunca se inventan.
function leerRevision(valor: any): { afirmaciones: number; sin_respaldo: number; citadas: UbicacionCitada[] } | null {
  if (!valor || !Number.isInteger(valor.afirmaciones) || !Number.isInteger(valor.sin_respaldo)) return null;
  if (valor.sin_respaldo < 1 || valor.sin_respaldo > valor.afirmaciones) return null;
  const citadas = (Array.isArray(valor.citadas) ? valor.citadas : [])
    .filter((u: any) => typeof u?.documento === 'string' && u.documento.trim())
    .map((u: any) => ({
      documento: u.documento.trim(),
      seccion: typeof u.seccion === 'string' && u.seccion.trim() ? u.seccion.trim() : null,
      pagina: Number.isInteger(u.pagina) && u.pagina > 0 ? u.pagina : null,
    }));
  return { afirmaciones: valor.afirmaciones, sin_respaldo: valor.sin_respaldo, citadas };
}

// Explica un rechazo de calidad sin mostrar el borrador: qué pasó, cuánto no se pudo comprobar y qué hacer.
export function RejectionDiagnosis({ error, idioma }: {
  error?: { message?: string; details?: Record<string, any> } | null;
  idioma: OutputLanguage;
}) {
  const razones: unknown[] = Array.isArray(error?.details?.razones_bloqueo) ? error?.details?.razones_bloqueo : [];
  const grupos = [...new Set(razones.map((r) => GRUPO_MOTIVO[String(r)]).filter(Boolean))];
  const revision = leerRevision(error?.details?.revision);
  const lugar = (u: UbicacionCitada) =>
    [u.documento, u.seccion ?? (u.pagina ? t('rechazo.pagina', idioma, { pagina: u.pagina }) : null)]
      .filter(Boolean)
      .join(' › ');

  return (
    <div style={{ fontSize: '0.88rem' }}>
      <div style={{ fontWeight: 600, marginTop: '0.5rem' }}>{t('rechazo.que_paso', idioma)}</div>
      {grupos.length > 0 ? (
        <ul style={{ margin: '0.25rem 0', paddingLeft: '1.2rem' }}>
          {grupos.map((grupo) => <li key={grupo}>{t(`rechazo.motivo.${grupo}`, idioma)}</li>)}
        </ul>
      ) : (
        error?.message && <p style={{ margin: '0.25rem 0' }}>{error.message}</p>
      )}
      {revision && (
        <>
          <p style={{ margin: '0.35rem 0' }}>
            {t('rechazo.cifras', idioma, { sin_respaldo: revision.sin_respaldo, afirmaciones: revision.afirmaciones })}
          </p>
          {revision.citadas.length > 0 && (
            <>
              <div>{t('rechazo.citaban', idioma)}</div>
              <ul style={{ margin: '0.25rem 0', paddingLeft: '1.2rem' }}>
                {revision.citadas.map((u) => <li key={lugar(u)}>{lugar(u)}</li>)}
              </ul>
            </>
          )}
        </>
      )}
      <div style={{ fontWeight: 600, marginTop: '0.5rem' }}>{t('rechazo.que_hacer', idioma)}</div>
      <div style={{ color: 'var(--ink-secondary)' }}>{t('material.rechazo_ayuda', idioma)}</div>
    </div>
  );
}
