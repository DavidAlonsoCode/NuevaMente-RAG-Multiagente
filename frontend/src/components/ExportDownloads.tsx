import React, { useState } from 'react';
import {
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  FileDown,
  FileJson,
  FileText,
  HardDrive,
  Layers,
  LucideIcon,
  Table,
} from 'lucide-react';
import { APIClient, APIError } from '../api/client';
import {
  ExportFormat,
  FORMATOS_EXPORTACION,
  GenerationJobResponse,
  OutputLanguage,
  PedagogicalStudentOutput,
} from '../api/types';
import { formatosExportables } from '../flows/validators';
import { t } from '../i18n';

const ICONOS: Record<ExportFormat, LucideIcon> = {
  json: FileJson,
  md: FileText,
  pdf: FileDown,
  csv: Table,
  tsv: Table,
  apkg: Layers,
};

const MIME: Record<ExportFormat, string> = {
  json: 'application/json',
  md: 'text/markdown',
  pdf: 'application/pdf',
  csv: 'text/csv',
  tsv: 'text/tab-separated-values',
  apkg: 'application/octet-stream',
};

export function guardarArchivo(bytes: Uint8Array, nombre: string, fmt: ExportFormat): void {
  const url = URL.createObjectURL(new Blob([bytes], { type: MIME[fmt] }));
  const a = document.createElement('a');
  a.href = url;
  a.download = nombre;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export const ExportDownloads: React.FC<{
  api: APIClient;
  token: string;
  paquete: PedagogicalStudentOutput;
  persistencia: GenerationJobResponse['persistencia'];
  idioma: OutputLanguage;
  onError: (error: APIError) => void;
  onSessionInvalid: () => void;
  verificarAntes?: () => Promise<boolean>;
  enTarjeta?: boolean;
}> = ({ api, token, paquete, persistencia, idioma, onError, onSessionInvalid, verificarAntes, enTarjeta = true }) => {
  const [descargando, setDescargando] = useState<ExportFormat | null>(null);
  const [detallesAbiertos, setDetallesAbiertos] = useState(false);
  const compatibles = formatosExportables(paquete);
  const generationId = paquete.generation_id;
  const ayudaId = `export-incompatible-${generationId}`;
  const guardadoOci = persistencia?.provider === 'oci' && persistencia.status_upload === 'completado';
  const hayIncompatibles = compatibles.length < FORMATOS_EXPORTACION.length;

  const descargar = async (fmt: ExportFormat) => {
    setDescargando(fmt);
    try {
      if (verificarAntes && !(await verificarAntes())) return;
      const bytes = await api.downloadExport(token, generationId, fmt);
      guardarArchivo(bytes, `${generationId}.${fmt}`, fmt);
    } catch (e) {
      if (e instanceof APIError && e.statusCode === 401) onSessionInvalid();
      else if (e instanceof APIError) onError(e);
    } finally {
      setDescargando(null);
    }
  };

  return (
    <div
      className={enTarjeta ? 'nm-card' : undefined}
      style={{ background: enTarjeta ? 'var(--surface-2)' : undefined, marginBottom: '1rem' }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '0.5rem' }}>
        <strong>{t('exportar.titulo', idioma)}</strong>
        <span
          role="status"
          className={`nm-alert ${guardadoOci ? 'nm-alert-success' : 'nm-alert-info'}`}
          style={{ display: 'inline-flex', alignItems: 'center', gap: '0.4rem', margin: 0, padding: '0.3rem 0.65rem' }}
        >
          {guardadoOci ? <CheckCircle2 size={15} /> : <HardDrive size={15} />}
          {t(guardadoOci ? 'material.guardado' : 'almacenamiento.local_dev', idioma)}
        </span>
      </div>

      <div className="nm-actions" style={{ marginTop: '0.75rem', gap: '0.5rem' }}>
        {FORMATOS_EXPORTACION.map((fmt) => {
          const Icono = ICONOS[fmt];
          const compatible = compatibles.includes(fmt);
          return (
            <span key={fmt} title={compatible ? undefined : t('exportar.solo_flashcards', idioma)}>
              <button
                type="button"
                className="nm-btn"
                disabled={!compatible || descargando !== null}
                aria-describedby={compatible ? undefined : ayudaId}
                onClick={() => descargar(fmt)}
              >
                <Icono size={15} />
                {t(`exportar.${fmt}`, idioma)}
              </button>
            </span>
          );
        })}
      </div>
      {hayIncompatibles && (
        <div id={ayudaId} style={{ fontSize: '0.8rem', color: 'var(--ink-muted)', marginTop: '0.5rem' }}>
          {t('exportar.solo_flashcards', idioma)}
        </div>
      )}
      {descargando && (
        <div role="status" style={{ fontSize: '0.8rem', color: 'var(--ink-secondary)', marginTop: '0.5rem' }}>
          {t('exportar.descargando', idioma, { formato: descargando.toUpperCase() })}
        </div>
      )}

      <div style={{ marginTop: '0.65rem' }}>
        <button
          type="button"
          aria-expanded={detallesAbiertos}
          onClick={() => setDetallesAbiertos(!detallesAbiertos)}
          style={{
            background: 'none',
            border: 'none',
            color: 'var(--ink-secondary)',
            cursor: 'pointer',
            display: 'inline-flex',
            alignItems: 'center',
            gap: '0.25rem',
            fontSize: '0.8rem',
            padding: 0,
          }}
        >
          {detallesAbiertos ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          {t('comun.detalles_tecnicos', idioma)}
        </button>
        {detallesAbiertos && (
          <pre
            className="mono"
            style={{
              marginTop: '0.4rem',
              padding: '0.5rem',
              background: 'rgba(0,0,0,0.35)',
              borderRadius: '6px',
              fontSize: '0.78rem',
              overflowX: 'auto',
            }}
          >
            {[
              `${t('exportar.proveedor', idioma)}: ${persistencia?.provider ?? 'N/A'}`,
              `${t('exportar.bucket', idioma)}: ${paquete.almacenamiento_oci?.bucket ?? 'N/A'}`,
              `${t('exportar.objeto', idioma)}: ${paquete.almacenamiento_oci?.objeto_id ?? 'N/A'}`,
            ].join('\n')}
          </pre>
        )}
      </div>
    </div>
  );
};
