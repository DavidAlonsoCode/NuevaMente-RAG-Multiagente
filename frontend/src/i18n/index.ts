import { OutputLanguage } from '../api/types';
import { CATALOGO_ES } from './es';
import { CATALOGO_EN } from './en';
import { CATALOGO_PT } from './pt';

export const CATALOGOS: Record<OutputLanguage, Record<string, string>> = {
  es: CATALOGO_ES,
  en: CATALOGO_EN,
  pt: CATALOGO_PT,
};

export const IDIOMA_POR_DEFECTO: OutputLanguage = 'es';

export function t(
  clave: string,
  idioma: OutputLanguage = IDIOMA_POR_DEFECTO,
  vars?: Record<string, string | number | undefined | null>
): string {
  const catalogo = CATALOGOS[idioma] ?? CATALOGOS[IDIOMA_POR_DEFECTO];
  let texto = catalogo[clave] ?? CATALOGOS[IDIOMA_POR_DEFECTO][clave];
  if (texto === undefined) {
    throw new Error(`Clave de i18n inexistente: ${clave}`);
  }
  if (vars) {
    for (const [k, v] of Object.entries(vars)) {
      texto = texto.replaceAll(`{${k}}`, String(v ?? ''));
    }
  }
  return texto;
}
