/** Cold jsdom imports and accessible DOM queries also compete with Vite's coordinator. */
export function frontendTestWorkers(available: number): number {
  return Math.min(4, available);
}
