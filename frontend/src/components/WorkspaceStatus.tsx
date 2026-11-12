import { useEffect, useState } from 'react';
import { Cpu, Radio } from 'lucide-react';
import { apiGet } from '../api';
import type { BackgroundStatus, LightGBMData } from '../types';

export function WorkspaceStatus({ prediction,onSettings }: {prediction?:LightGBMData;onSettings:(tab:'models'|'background')=>void}) {
  const [background,setBackground] = useState<BackgroundStatus | null>(null);
  const [offline,setOffline] = useState(false);
  useEffect(()=>{
    const controller=new AbortController(); let inFlight=false;
    async function load() {
      if(inFlight)return; inFlight=true;
      try { const value=await apiGet<BackgroundStatus>('/api/background',controller.signal,15000); if(!controller.signal.aborted){setBackground(value);setOffline(false);} }
      catch {if(!controller.signal.aborted)setOffline(true);} finally{inFlight=false;}
    }
    void load(); const timer=setInterval(()=>void load(),15000);
    return ()=>{controller.abort();clearInterval(timer);};
  },[]);
  const labels:Record<string,string>={AVAILABLE:'Quant bereit',NO_MODEL:'Quant · kein Modell',DISABLED:'Quant aus',STALE:'Quant veraltet',NOT_INSTALLED:'Quant fehlt',WAITING_FOR_NEW_BAR:'Quant wartet auf neue Kerze',WARMUP:'Quant lädt Historie',UNSUPPORTED:'Quant · 5m / 15m'};
  const running=background?.settings.enabled && background.worker_alive && !offline;
  return <div className="workspace-status"><button type="button" onClick={()=>onSettings('models')} title="Quant und Modelle einstellen"><Cpu size={13}/>{labels[prediction?.status ?? ''] ?? 'Quant · Status laden'}</button><span className="status-divider"/>
    <button type="button" onClick={()=>onSettings('background')} title="Dauerbetrieb in den Einstellungen verwalten"><Radio size={13} className={running?'text-emerald-300':''}/>{offline?'Backend nicht erreichbar':!background?'Dauerbetrieb laden…':!background.settings.enabled?'Dauerbetrieb aus':running?'Dauerbetrieb aktiv':'Dauerbetrieb wartet'}{running && <span className="text-slate-500">· {background?.settings.gemini_enabled?'mit Gemini':'lokal'}</span>}</button></div>;
}
