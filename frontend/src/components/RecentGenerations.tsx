import React, { useEffect, useState } from 'react';
import { APIClient, APIError } from '../api/client';
import { OutputLanguage } from '../api/types';
import { t } from '../i18n';
import { ErrorBanner } from './ErrorBanner';

// La lista se vuelve a pedir al abrirla y cada vez que cambia `actualizar` (por ejemplo, al terminar
// una generación): si quedaba abierta desde antes de generar, antes seguía vacía.
export function RecentGenerations({ api, token, idioma, actualizar, onOpen }: {
  api: APIClient; token: string; idioma: OutputLanguage; actualizar?: string;
  onOpen: (generationId: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<any[] | null>(null);
  const [error, setError] = useState<APIError | null>(null);
  useEffect(() => {
    if (!open) return;
    let active = true;
    setError(null);
    api.getGenerations(token, { limit: 5 }).then((page) => {
      if (active) setItems(page.items || []);
    }).catch((err) => {
      if (!active) return;
      setItems([]);
      if (err instanceof APIError) setError(err);
    });
    return () => { active = false; };
  }, [open, actualizar, api, token]);
  const etiqueta = (item: any) => {
    const p = item.parametros || {};
    const partes = [(item.created_at || '').slice(0, 16).replace('T', ' ')];
    if (p.formato_salida) partes.push(t(`formato.${p.formato_salida}`, idioma));
    partes.push(t(`estados.trabajo.${item.status}`, idioma));
    return partes.join(' · ');
  };
  return <details onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary>{t('historial.recientes', idioma)}</summary>
    <ErrorBanner error={error} idioma={idioma} />
    {items === null ? (
      <div style={{ fontSize: '0.8rem', color: 'var(--ink-muted)', padding: '0.35rem 0' }}>{t('historial.cargando', idioma)}</div>
    ) : items.length === 0 && !error ? (
      <div style={{ fontSize: '0.8rem', color: 'var(--ink-muted)', padding: '0.35rem 0' }}>{t('historial.recientes_vacio', idioma)}</div>
    ) : items.map((item) => <button type="button" className="nm-btn nm-btn-block" key={item.generation_id} onClick={() => onOpen(item.generation_id)}>
      {etiqueta(item)}
    </button>)}
  </details>;
}
