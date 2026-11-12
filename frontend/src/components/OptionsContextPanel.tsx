import { useEffect, useState } from 'react';
import { price, timeLabel } from '../api';
import type { OptionsContextData } from '../types';

const labels = {AVAILABLE: 'ZEITNAHER FEED · MODELL', REFERENCE: 'NUR REFERENZ', STALE: 'ARCHIVIERT',
  NOT_CALCULATED: 'NOCH NICHT GELADEN', UNAVAILABLE: 'NICHT VERFÜGBAR', UNSUPPORTED: 'NICHT UNTERSTÜTZT'};
const numeric = (value: number | null | undefined, digits = 4) =>
  value == null || !Number.isFinite(value) ? '—' : value.toLocaleString('de-AT', {maximumFractionDigits: digits, minimumFractionDigits: digits});

export function OptionsContextPanel({ data }: { data?: OptionsContextData | null }) {
  const [now, setNow] = useState(Date.now);
  useEffect(() => { const timer = setInterval(() => setNow(Date.now()), 10000); return () => clearInterval(timer); }, []);
  const recent = (at?: number | null, seconds = 120) => at != null && now / 1000 - at >= -5 && now / 1000 - at <= seconds;
  const oldModel = data?.computed_at != null && !recent(data.computed_at, 300);
  const usable = !!data?.usable_for_ai && !oldModel && recent(data.spot_at) && data.contracts.some(c => c.fresh_quote && recent(c.quote_at));
  const status = oldModel && (data?.status === 'AVAILABLE' || data?.status === 'REFERENCE') ? 'STALE'
    : data?.status === 'AVAILABLE' && !usable ? 'REFERENCE' : data?.status ?? 'NOT_CALCULATED';
  const reason = status !== data?.status && data ? 'Gespeicherte Optionswerte inzwischen zu alt. Per Quant-Knopf aktualisieren.' : data?.reason;
  const archived = status === 'STALE';
  const hasValues = !!data?.contracts.length;
  return <section aria-label="Optionsrisiko und Greeks" className="rounded-xl border border-violet-500/25 bg-[#0f172a] p-4">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h2 className="text-sm font-bold text-violet-200">Optionsrisiko · Delta, Gamma, Theta, Vega, Rho</h2>
      <span className={'text-[10px] font-bold ' + (status === 'AVAILABLE' ? 'text-cyan-200' : 'text-amber-200')}>{labels[status]}</span>
    </div>
    <p className="mt-2 text-xs text-slate-300">{reason || 'Mit „Quant berechnen + Gemini analysieren“ laden. Danach wird der gespeicherte Stand verwendet.'}</p>
    <p className="mt-2 text-[11px] text-violet-200">{usable ? 'Gemini erhält die zeitnahen Modellwerte als Optionsrisiko.' : 'Keine numerischen Optionswerte für die Intraday-KI freigegeben.'} Keine zusätzliche Richtungsstimme.</p>
    <p className="mt-1 text-[10px] text-slate-400">Für dieselben Kontrakte gelten dieselben Greeks auch im 5-/15-Minuten-Chart. Sie ändern sich mit Kurs, IV und Restlaufzeit.</p>
    {hasValues && <>
      <p className="mt-2 text-[10px] text-slate-400">{data?.provider} · {data?.feed} · Berechnet {timeLabel((data?.computed_at ?? 0) * 1000)}{data?.cache_hit ? ' · gespeicherter Stand' : ''}</p>
      <p className="mt-1 text-[10px] text-slate-400">Basiswert {price(data?.spot, 'USD')} · Kurszeit {timeLabel((data?.spot_at ?? 0) * 1000)} · {data?.spot_time_basis}</p>
      <details className="mt-3">
        <summary className="cursor-pointer text-xs font-semibold text-violet-200">{archived ? 'Archivierte' : 'Berechnete'} Greeks anzeigen ({data?.contracts.length} Kontrakte)</summary>
        <div className={'mt-2 overflow-x-auto ' + (archived ? 'opacity-60' : '')}>
          <table className="w-full whitespace-nowrap text-right text-[10px]">
            <caption className="py-2 text-left text-slate-400">Je Optionsanteil, in USD. Theta: pro Kalendertag · Vega: pro 1 Prozentpunkt IV · Rho: pro 1 Prozentpunkt Zins.</caption>
            <thead className="border-b border-slate-700 text-slate-400"><tr>
              {['Typ / Verfall', 'Strike', 'IV', 'Delta', 'Gamma', 'Theta/Tag', 'Vega/1 PP', 'Rho/1 PP', 'Quote-Zeit'].map(label => <th className="px-2 py-2" scope="col" key={label}>{label}</th>)}
            </tr></thead>
            <tbody>{data?.contracts.map(contract => <tr key={contract.symbol} className="border-b border-slate-800 text-slate-200" title={contract.symbol}>
              <td className="px-2 py-2 text-left">{contract.kind === 'call' ? 'Call' : 'Put'} · {contract.expiration}</td>
              <td className="px-2">{numeric(contract.strike, 2)}</td><td className="px-2">{numeric(contract.iv * 100, 1)}%</td>
              {(['delta', 'gamma', 'theta', 'vega', 'rho'] as const).map(key => <td className="px-2" key={key}>{numeric(contract.greeks[key])}</td>)}
              <td className="px-2 text-slate-400">{contract.quote_at ? timeLabel(contract.quote_at * 1000) : 'Unbekannt'}{contract.quote_at && (!usable || !contract.fresh_quote || !recent(contract.quote_at)) ? ' · Referenz' : ''}</td>
            </tr>)}</tbody>
          </table>
        </div>
        <p className="mt-2 text-[10px] text-slate-400">Delta: Änderung des Optionspreises je 1 USD im Basiswert. Gamma: Änderung dieses Delta je 1 USD. Das Vorzeichen ist keine Aktien-Handelsempfehlung.</p>
      </details>
    </>}
    {data?.assumptions && <details className="mt-3 text-[10px] text-slate-400">
      <summary className="cursor-pointer">Modellannahmen und Datenqualität</summary>
      <p className="mt-2">{data.model}. Modellzins {numeric(data.assumptions.rate * 100, 2)}%: {data.assumptions.rate_source}</p>
      <p>Dividendenrendite {numeric(data.assumptions.dividend_yield * 100, 2)}%: {data.assumptions.dividend_source}. Verfall: {data.assumptions.expiry_time}.</p>
      {data.warnings.map(warning => <p className="mt-1" key={warning}>{warning}</p>)}
      <p className="mt-1">Abrufzeit und letzter Optionshandel belegen keine Quote-Aktualität. Yahoo liefert hier keinen verlässlichen Quote-Zeitpunkt.</p>
    </details>}
  </section>;
}
