export const TIMEFRAMES = ['1m', '5m', '15m', '30m', '1h', '4h', '1D'];
export const IS_DEMO = import.meta.env.VITE_DEMO_MODE === 'true';
export const API_BASE = (import.meta.env.VITE_API_BASE_URL || (IS_DEMO ? '' : 'http://127.0.0.1:8000')).replace(/\/$/, '');
export const WS_BASE = API_BASE.replace(/^http/, 'ws');
export const STATUS_URL = IS_DEMO ? './status/' : API_BASE + '/';

async function demoResponse<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  signal?.throwIfAborted();
  const { demoRequest } = await import('./demo');
  signal?.throwIfAborted();
  return demoRequest<T>(path,body);
}

export function normalizeSymbol(value: string): string {
  const symbol = value.trim().toUpperCase();
  return ({ GOLD: 'XAUUSD', 'XAU/USD': 'XAUUSD', 'XAUUSD=X': 'XAUUSD', BTC: 'BTC-USD', ETH: 'ETH-USD', SOL: 'SOL-USD' } as Record<string, string>)[symbol] || symbol;
}

export async function apiGet<T>(path: string, signal?: AbortSignal, timeout = 60000): Promise<T> {
  if (IS_DEMO) return demoResponse<T>(path,undefined,signal);
  const combined = signal ? AbortSignal.any([signal, AbortSignal.timeout(timeout)]) : AbortSignal.timeout(timeout);
  const response = await fetch(API_BASE + path, { signal: combined });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(typeof data.detail === 'string' ? data.detail : 'Anfrage fehlgeschlagen (' + response.status + ').');
  }
  return data as T;
}

export async function saveAnalysisMode(mode: 'gemini' | 'python') {
  if (IS_DEMO) return demoResponse<{mode:'gemini'|'python'}>('/api/analysis-mode',{mode});
  const response = await fetch(API_BASE + '/api/analysis-mode', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ mode }),
    signal: AbortSignal.timeout(10000),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.detail || 'Analysemodus konnte nicht gespeichert werden.');
  return result as { mode: 'gemini' | 'python' };
}

export async function apiPost<T>(path: string, body: unknown, signal?: AbortSignal, timeout = 180000): Promise<T> {
  if (IS_DEMO) return demoResponse<T>(path,body,signal);
  const combined = signal ? AbortSignal.any([signal, AbortSignal.timeout(timeout)]) : AbortSignal.timeout(timeout);
  const response = await fetch(API_BASE + path, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body), signal: combined,
  });
  const result = await response.json();
  if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Anfrage fehlgeschlagen.');
  return result as T;
}

export function price(value: number | null | undefined, currency?: string | null): string {
  if (value == null || !Number.isFinite(value)) return '—';
  const decimals = Math.abs(value) < 1 ? 6 : Math.abs(value) < 10 ? 4 : 2;
  if (currency === 'GBp' || currency === 'GBX') return price(value) + ' GBp';
  return new Intl.NumberFormat('de-AT', {
    ...(currency ? { style: 'currency', currency } : {}),
    minimumFractionDigits: 2, maximumFractionDigits: decimals,
  }).format(value);
}

export function timeLabel(timestamp?: number | null) {
  return timestamp ? new Date(timestamp).toLocaleString('de-AT', { dateStyle: 'short', timeStyle: 'short' }) : '—';
}
