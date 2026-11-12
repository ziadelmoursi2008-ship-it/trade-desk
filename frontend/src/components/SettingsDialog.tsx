import { useEffect, useRef, useState } from 'react';
import { Cpu, Radio, X } from 'lucide-react';
import { BackgroundPanel } from './BackgroundPanel';
import { ModelSettings } from './ModelSettings';
import { IS_DEMO } from '../api';

export function SettingsDialog({ ticker,timeframe,onClose,onEnabled,onSelect,initialTab='models' }: {
  ticker:string; timeframe:string; onClose:()=>void; onEnabled:(value:boolean)=>void; onSelect:(ticker:string,timeframe:string)=>void; initialTab?:'models'|'background';
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [tab,setTab] = useState<'models'|'background'>(initialTab);
  useEffect(()=>{ const element=dialog.current; element?.showModal(); return ()=>element?.close(); },[]);
  return <dialog ref={dialog} onCancel={event=>{event.preventDefault();onClose();}} className="settings-dialog" aria-labelledby="settings-title">
    <div className="settings-heading"><div><p className="eyebrow">DEIN ARBEITSPLATZ</p><h2 id="settings-title" className="mt-1 text-xl font-semibold">Einstellungen</h2></div><button type="button" aria-label="Einstellungen schließen" className="desk-icon-button" onClick={onClose}><X size={19}/></button></div>
    <div className="settings-tabs" role="tablist" aria-label="Einstellungsbereiche"><button role="tab" aria-selected={tab==='models'} onClick={()=>setTab('models')}><Cpu size={16}/>Quant & Daten</button><button role="tab" aria-selected={tab==='background'} onClick={()=>setTab('background')}><Radio size={16}/>Dauerbetrieb & API</button></div>
    <div className="settings-content" role="tabpanel" aria-label={tab==='models'?'Quant und gespeicherte Daten':'Dauerbetrieb und API-Budget'}>{IS_DEMO?<div className="setting-card"><h3 className="text-sm font-semibold">{tab==='models'?'Modelle & Daten':'Analysebetrieb'}</h3><p className="mt-3 text-sm leading-relaxed text-slate-400">{tab==='models'?'Die Demo zeigt synthetische Kursreihen und daraus berechnete technische Indikatoren. Training und gespeicherte Modelle stehen in der lokalen Vollversion zur Verfügung.':'Die öffentliche Demo läuft ohne Backend. Automatische Analysen und externe KI-Anfragen sind hier ausgeschaltet.'}</p></div>:tab==='models'?<ModelSettings ticker={ticker} timeframe={timeframe} onEnabled={onEnabled} onSelect={onSelect}/>:<BackgroundPanel/>}</div>
    <footer className="settings-footer"><span>{IS_DEMO?'Portfolio-Demo · keine externen API-Aufrufe':'Training läuft auch bei geschlossenen Einstellungen weiter.'}</span><button type="button" className="desk-button" onClick={onClose}>Fertig</button></footer>
  </dialog>;
}
