import { useCallback, useSyncExternalStore } from 'react';

// Sólo memoria: permite cambiar de pestaña/documento sin perder una intención
// pendiente. El contenido privado nunca se serializa al almacenamiento web.
const states = new Map<string, unknown>();
const listeners = new Set<() => void>();
let revision = 0;
let epoch = 0;
const subscribe = (listener: () => void) => {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
};
const notify = () => { revision++; listeners.forEach((listener) => listener()); };

export function clearStudyState() { epoch++; states.clear(); notify(); }

export function useScopedState<T>(key: string, initial: T): [T, (value: T | ((old: T) => T)) => void] {
  useSyncExternalStore(subscribe, () => revision);
  const value = states.has(key) ? states.get(key) as T : initial;
  const currentEpoch = epoch;
  const setValue = useCallback((next: T | ((old: T) => T)) => {
    if (currentEpoch !== epoch) return;
    const old = states.has(key) ? states.get(key) as T : initial;
    states.set(key, typeof next === 'function' ? (next as (old: T) => T)(old) : next);
    notify();
  }, [key, currentEpoch]);
  return [value, setValue];
}

export function useOtherPendingChat(workspaceId: string, ownKey: string): boolean {
  useSyncExternalStore(subscribe, () => revision);
  return [...states].some(([key, turns]) => key.startsWith(`chat:${workspaceId}:`) && key !== ownKey &&
    Array.isArray(turns) && turns.some((turn) => ['admitting', 'queued', 'running'].includes(turn.status)));
}
