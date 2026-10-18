import React, { useEffect, useState } from 'react';
import { APIClient, APIError } from '../api/client';
import { OutputLanguage } from '../api/types';
import { t } from '../i18n';
import { ErrorBanner } from './ErrorBanner';

export function RecentGenerations({ api, token, idioma, onOpen }: {
  api: APIClient; token: string; idioma: OutputLanguage; onOpen: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<any[]>([]);
  const [error, setError] = useState<APIError | null>(null);
  useEffect(() => {
    if (!open) return;
    let active = true;
    api.getGenerations(token, { limit: 5 }).then((page) => {
      if (active) setItems(page.items);
    }).catch((err) => { if (active && err instanceof APIError) setError(err); });
    return () => { active = false; };
  }, [open, api, token]);
  return <details onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary>{t('historial.recientes', idioma)}</summary>
    <ErrorBanner error={error} idioma={idioma} />
    {items.map((item) => <button className="nm-btn nm-btn-block" key={item.generation_id} onClick={onOpen}>
      {item.titulo || item.documento_titulo || item.generation_id} · {t(`estados.trabajo.${item.status}`, idioma)}
    </button>)}
  </details>;
}
