import React from 'react';
import { DocumentoResponse, OutputLanguage } from '../api/types';
import { CATALOGOS, t } from '../i18n';

export function DocumentStatus({ doc, tracking, idioma }: {
  doc: DocumentoResponse; tracking?: any; idioma: OutputLanguage;
}) {
  const stage = `upload.etapa_${tracking?.progreso?.step}`;
  const parser = `upload.parser_${doc.error?.code?.replace(/^PARSER_/, '').toLowerCase()}`;
  return <div role="status">
    {doc.status === 'processing' && CATALOGOS[idioma][stage] && <p>{t(stage, idioma)}</p>}
    {doc.status === 'processing' && !doc.cobertura && <p>{t('upload.cobertura_pendiente', idioma)}</p>}
    {!!doc.cobertura?.paginas_visuales_pendientes?.length && <p className="nm-alert nm-alert-warning">
      {t('upload.vision_pendiente', idioma, { paginas: doc.cobertura.paginas_visuales_pendientes.join(', ') })}
    </p>}
    {!!doc.cobertura?.paginas_omitidas?.length && <p className="nm-alert nm-alert-warning">
      {t('upload.omitidas', idioma, { paginas: doc.cobertura.paginas_omitidas.join(', ') })}
    </p>}
    {doc.status === 'failed' && (
      <div className="nm-alert nm-alert-error" role="alert">
        <p style={{ margin: 0, fontWeight: 600 }}>{t('estados.documento.failed', idioma)}</p>
        <p style={{ margin: '0.25rem 0 0 0', fontSize: '0.88rem' }}>
          {CATALOGOS[idioma][parser] ? t(parser, idioma) : doc.error?.message || t('estados.documento.failed', idioma)}
        </p>
      </div>
    )}
  </div>;
}
