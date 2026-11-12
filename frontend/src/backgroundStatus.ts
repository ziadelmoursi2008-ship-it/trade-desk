import type { BackgroundStatus } from './types';

const labels: Record<string,string> = {RUNNING:'Aktiv',MARKET_CLOSED:'Börse geschlossen · KI pausiert',
  WAITING_FOR_DATA:'Warte auf Kursdaten',API_LIMIT:'Gemini-Limit erreicht · Python läuft weiter',
  MISSING_API_KEY:'Gemini-Schlüssel fehlt · Python läuft weiter',WAITING_FOR_ANALYSIS:'Gemini-Antwort nicht verwertbar',ERROR:'Kursfeed oder Verarbeitung prüfen'};

export function backgroundStateLabel(data: BackgroundStatus | null, offline: boolean) {
  return offline ? 'Backend nicht erreichbar · Betrieb nicht bestätigt'
    : !data ? 'Status wird geladen…' : !data.settings.enabled ? 'AUS'
    : !data.worker_alive ? 'Startet oder wartet auf Backend' : labels[data.runtime.status || 'RUNNING'] || 'Aktiv';
}
