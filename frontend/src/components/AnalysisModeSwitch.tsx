import type { AnalysisMode } from '../types';

export function AnalysisModeSwitch({ mode, busy, onSelect, compact = false }: {
  mode: AnalysisMode | null; busy: boolean; onSelect: (mode: AnalysisMode) => void; compact?:boolean;
}) {
  if (compact) return <div className="segmented" role="group" aria-label="Analysemodus">{(['python','gemini'] as const).map(value=><button type="button" key={value} disabled={busy||mode==null} aria-pressed={mode===value} onClick={()=>onSelect(value)} title={value==='python'?'Technik und lokaler Quant-Filter':'Gemini mit Nachrichten und lokalem Quant-Filter'}>{value==='python'?'Python':'Gemini'}</button>)}</div>;
  return <section aria-label="Analysemodus" className="mb-5 rounded-xl border border-slate-700 bg-slate-900 p-4">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div><h2 className="text-sm font-semibold text-slate-100">Analysemodus selbst wählen</h2>
        <p className="mt-1 text-xs text-slate-400">{mode === 'python' ? 'Python entscheidet mit technischen Daten. KI, Wirtschaftsdaten und Nachrichten sind ausgeschaltet.' : mode === 'gemini' ? 'Gemini analysiert Technik, Wirtschaftsdaten und Nachrichten.' : 'Gespeicherte Auswahl wird geladen…'}</p>
      </div>
      <div className="flex flex-wrap gap-2">
        {([['gemini', 'Gemini + News'], ['python', 'Python · nur Technik']] as const).map(([value, label]) =>
          <button key={value} type="button" disabled={busy || mode == null} aria-pressed={mode === value}
            onClick={() => onSelect(value)} className={'rounded-lg border px-4 py-2 text-xs font-bold disabled:opacity-50 ' +
              (mode === value ? 'border-cyan-400 bg-cyan-400 text-slate-950' : 'border-slate-600 text-slate-300 hover:border-cyan-500')}>
            {label}
          </button>)}
      </div>
    </div>
    <p className="mt-2 text-[10px] text-slate-500">Auswahl wird gespeichert. Kein automatischer Wechsel bei Fehlern oder API-Limits.</p>
  </section>;
}
