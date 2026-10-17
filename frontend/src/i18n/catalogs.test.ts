import { expect, it } from 'vitest';
import { CATALOGOS } from './index';

it.each(['es', 'en', 'pt'] as const)('conserva claves y variables de traducción en %s', idioma => {
  expect(Object.keys(CATALOGOS[idioma]).sort()).toEqual(Object.keys(CATALOGOS.es).sort());
  for (const [clave, texto] of Object.entries(CATALOGOS[idioma])) {
    expect(texto.trim(), clave).not.toBe('');
    const variables = (valor: string) => [...valor.matchAll(/\{(\w+)\}/g)].map(match => match[1]).sort();
    expect(variables(texto), clave).toEqual(variables(CATALOGOS.es[clave]));
  }
});
