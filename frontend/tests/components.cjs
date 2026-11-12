/* Regression checks for trade drawings and rendered UI; no browser or extra dependencies. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const Module = require('node:module');
const ts = require('typescript');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const overlays = {};
const indicators = {};
const actualCharts = require('../node_modules/klinecharts/dist/index.esm.js');
const originalRequire = Module.prototype.require;
Module.prototype.require = function (id) {
  if (id === 'klinecharts') return { ...actualCharts, registerOverlay(template) { overlays[template.name] = template; }, registerIndicator(template) { indicators[template.name] = template; } };
  return originalRequire.call(this, id);
};
function loadTs(module, filename) {
  const source = fs.readFileSync(filename, 'utf8').replace(/import\.meta\.env/g, "({VITE_API_BASE_URL:'',VITE_DEMO_MODE:'false'})");
  const output = ts.transpileModule(source, { compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2023, jsx: ts.JsxEmit.ReactJSX,
    esModuleInterop: true,
  }, fileName: filename }).outputText;
  module._compile(output, filename);
}
require.extensions['.ts'] = loadTs;
require.extensions['.tsx'] = loadTs;
const { TradingChart } = require('../src/components/TradingChart.tsx');
const { DecisionPanel } = require('../src/components/DecisionPanel.tsx');
const { TechnicalForecastPanel } = require('../src/components/TechnicalForecastPanel.tsx');
const { ScannerPanel } = require('../src/components/ScannerPanel.tsx');
const { BacktestPanel } = require('../src/components/BacktestPanel.tsx');
const { NewsPanel } = require('../src/components/NewsPanel.tsx');
const { AnalysisModeSwitch } = require('../src/components/AnalysisModeSwitch.tsx');
const { QuantitativeControls, QuantitativeCard } = require('../src/components/QuantitativePanel.tsx');
const { GeminiStatus } = require('../src/components/GeminiStatus.tsx');
const { normalizeSymbol, price } = require('../src/api.ts');

let count = 0;
function test(name, callback) { callback(); count++; console.log('PASS', name); }
const decision = {
  ticker: 'TEST', timeframe: '15m', signal_type: 'BUY', score: 80, strength: 80,
  setup_quality: 'A', current_price: 100, entry_price: 100, stop_loss: 98,
  entry_zone_min: 99.8, entry_zone_max: 100.2, take_profit_1: 104, take_profit_2: 106,
  risk_reward_ratio: 2, risk_pct: 2, potential_profit_pct: 4, reasons: ['Test setup'],
  strategy_version: '2.0', as_of: 1792920000000,
};

test('Price scale includes both stop and furthest target', () => {
  const values = indicators.AGENT_RANGE.calc([{}, {}], { extendData: { lower: 98, upper: 106 } });
  assert.deepEqual(values, [{ lower: 98, upper: 106 }, { lower: 98, upper: 106 }]);
  assert.deepEqual(indicators.AGENT_RANGE.calc([{}], { extendData: null }), [{}]);
});

test('Long has green profit bands above entry and red risk below', () => {
  const figures = overlays.agentRiskReward.createPointFigures({
    coordinates: [{x:100,y:200},{x:300,y:250},{x:300,y:100},{x:300,y:50}],
    overlay: { extendData: decision },
  });
  const rectangles = figures.filter(f => f.type === 'rect');
  assert.equal(rectangles.length, 3);
  assert.equal(rectangles[0].attrs.y, 100);
  assert.equal(rectangles[1].attrs.y, 50);
  assert.equal(rectangles[2].attrs.y, 200);
  assert(figures.some(f => f.type === 'text' && f.attrs.text.startsWith('TP1')));
  assert(figures.some(f => f.type === 'text' && f.attrs.text.startsWith('TP2')));
});

test('A single target draws one profit band and no invented TP2', () => {
  const single = {...decision,take_profit_2:null,decision_source:'python',entry_kind:'CURRENT'};
  const figures = overlays.agentRiskReward.createPointFigures({
    coordinates:[{x:100,y:200},{x:300,y:250},{x:300,y:100},{x:300,y:100}],
    overlay:{extendData:single},
  });
  assert.equal(figures.filter(f => f.type === 'rect').length,2);
  assert(!figures.some(f => f.type === 'text' && f.attrs.text.startsWith('TP2')));
  assert(!JSON.stringify(figures).includes('NaN'));
  const html = renderToStaticMarkup(React.createElement(DecisionPanel,{decision:single}));
  assert(html.includes('Entry am aktuellen Kurs'));
  assert(!html.includes('TP2'));
});
test('Short has green profit below entry and red stop above', () => {
  const figures = overlays.agentRiskReward.createPointFigures({
    coordinates: [{x:100,y:200},{x:300,y:150},{x:300,y:300},{x:300,y:350}],
    overlay: { extendData: {...decision, signal_type:'SELL',stop_loss:102,take_profit_1:96,take_profit_2:94} },
  });
  const rectangles = figures.filter(f => f.type === 'rect');
  assert.equal(rectangles[0].attrs.y, 200);
  assert.equal(rectangles[1].attrs.y, 300);
  assert.equal(rectangles[2].attrs.y, 150);
});
test('Zoomed native coordinates preserve box geometry', () => {
  const figures = overlays.agentRiskReward.createPointFigures({
    coordinates: [{x:20,y:400},{x:400,y:500},{x:400,y:200},{x:400,y:100}],
    overlay: { extendData: decision },
  });
  assert.equal(figures[0].attrs.height, 200);
  assert.equal(figures[0].attrs.width, 380);
  assert.equal(figures[2].attrs.height, 100);
});
test('Neutral decisions render no entry or profit targets', () => {
  const html = renderToStaticMarkup(React.createElement(DecisionPanel, {decision: {...decision,signal_type:'NEUTRAL',entry_price:null,stop_loss:null,take_profit_1:null,take_profit_2:null}}));
  assert(html.includes('ABWARTEN'));
  assert(!html.includes('TP1-Kurspotenzial'));
  assert(!html.includes('Limit-Entry'));
});
test('Prepared opportunities show strength, entry condition and lifecycle counters', () => {
  const html = renderToStaticMarkup(React.createElement(DecisionPanel, {decision: {
    ...decision, signal_label:'STRONG_BUY', lifecycle_status:'VORBEREITET', entry_mode:'CONFIRMATION',
    entry_condition:'Reaktion am Orderblock abwarten', opposing_bars:1, valid_for_bars:12, elapsed_bars:3,
    verification_status:'verified', last_verified_at:1792920000000,
  }}));
  assert(html.includes('STRONG_BUY'));
  assert(html.includes('VORBEREITET'));
  assert(html.includes('Reaktion am Orderblock abwarten'));
  assert(html.includes('Gegenbestätigungen 1/2'));
  assert(html.includes('Laufzeit 3/12'));
});
test('Unavailable follow-up preserves weak setup and marks its verification pending', () => {
  const html = renderToStaticMarkup(React.createElement(DecisionPanel, {decision: {
    ...decision, signal_label:'WEAK_BUY', lifecycle_status:'AKTIV', hold_previous:true,
    verification_status:'unavailable', last_verified_at:1792920000000,
  }}));
  assert(html.includes('WEAK_BUY'));
  assert(html.includes('vorherige Richtung vorläufig halten'));
  assert(html.includes('Letzter gespeicherter Stand'));
  assert(html.includes('TP1-Kurspotenzial'));
});
test('Unavailable AI is not presented as a neutral market judgment or a running setup', () => {
  const html = renderToStaticMarkup(React.createElement(DecisionPanel, {decision:{
    ...decision, signal_type:'NEUTRAL', lifecycle_status:'NEUTRAL', setup_id:null,
    verification_status:'unavailable', validation_status:'unavailable',
    analysis_error:{kind:'timeout',message:'Gemini hat nicht rechtzeitig geantwortet.'},
  }}));
  assert(html.includes('KI NICHT VERFÜGBAR'));
  assert(!html.includes('Gegenbestätigungen'));
  assert(!html.includes('Laufzeit 0/12'));
  assert(html.includes('keine bestätigte NEUTRAL-Bewertung'));
});
test('News acquisition errors do not claim there were no news or show misleading zero counts', () => {
  const html = renderToStaticMarkup(React.createElement(NewsPanel, {news:[],pipeline:{
    feed_status:'unavailable',normal_feed_items:0,normal_candidates:0,normal_evaluated:0,normal_relevant:0,
    source_statuses:[{source:'Google News',status:'error',recent_count:0,last_success_at:null,error_code:'timeout'}],
  }}));
  assert(html.includes('Nachrichtenabruf fehlgeschlagen'));
  assert(html.includes('Das bedeutet nicht, dass es keine Nachrichten gab'));
  assert(!html.includes('0 Feed-Treffer'));
  assert(html.includes('Google News: Abruffehler'));
});
test('Cached source articles show degraded acquisition and the time of the last successful fetch', () => {
  const html = renderToStaticMarkup(React.createElement(NewsPanel, {news:[],pipeline:{
    feed_status:'partial',normal_feed_items:10,normal_candidates:10,normal_evaluated:10,normal_relevant:0,ai_status:'evaluated',
    source_statuses:[{source:'Yahoo Finance',status:'cached',recent_count:10,last_success_at:1792920000,error_code:'http_503'}],
  }}));
  assert(html.includes('gespeicherte Artikel'));
  assert(html.includes('letzter erfolgreicher Abruf'));
  assert(html.includes('10 KI-bewertet'));
  assert(html.includes('nicht als passend eingestuft'));
});
test('News shows fixed macro weights and AI-assigned importance separately', () => {
  const html = renderToStaticMarkup(React.createElement(NewsPanel, {news:[{
    ticker:'TEST',headline:'A relevant event',source:'Publisher',url:'https://example.test',
    published_ts:1792920000,sentiment:0.2,event_type:'earnings',summary:'A relevant event',
    credibility:null,relevance:1,impact_score:60,analysis_method:'gemini',importance_score:85,
    importance_reason:'Relevant für diesen Timeframe',effect_horizon:'intraday',
  }], policy:{decision_weights_pct:{technical:50,hard_data:25,normal_news:25}}, hardData:{status:'live',items:[{
    series_id:'GDPC1',name:'BIP',period:'2026-Q3',value:100,unit:'Index',change_value:null,
    source_url:'https://example.test/gdp',importance_pct:10,decision_weight_pct:2.5,
  }]}}));
  assert(html.includes('Technik 50%'));
  assert(html.includes('Festes Makrogewicht 10%'));
  assert(html.includes('KI-Wichtigkeit 85/100'));
});
test('Scanner exposes timeframe, strength and scan action', () => {
  const html = renderToStaticMarkup(React.createElement(ScannerPanel, {onSelect(){}}));
  assert(html.includes('Scanner Zeitrahmen'));
  assert(html.includes('Scanner Signalstärke'));
  assert(html.includes('Markt scannen'));
});
test('Manual mode switch exposes both choices and never advertises automatic fallback', () => {
  const html = renderToStaticMarkup(React.createElement(AnalysisModeSwitch, {mode:'python',busy:false,onSelect(){}}));
  assert(html.includes('Gemini + News'));
  assert(html.includes('Python · nur Technik'));
  assert(html.includes('KI, Wirtschaftsdaten und Nachrichten sind ausgeschaltet'));
  assert(html.includes('Kein automatischer Wechsel'));
  assert.equal((html.match(/aria-pressed="true"/g) || []).length, 1);
});
test('Technical decision is labeled as Python with a setup score instead of AI confidence', () => {
  const html = renderToStaticMarkup(React.createElement(DecisionPanel, {decision:{...decision,decision_source:'python',confidence:80}}));
  assert(html.includes('Technischer Setup-Score'));
  assert(html.includes('Python · nur Technik'));
  assert(!html.includes('KI-Konfidenz'));
  assert(!html.includes('Gemini'));
});
test('Python scanner describes technical analysis without Gemini or news', () => {
  const html = renderToStaticMarkup(React.createElement(ScannerPanel, {mode:'python',onSelect(){}}));
  assert(html.includes('ausschließlich anhand technischer Daten'));
  assert(!html.includes('Gemini'));
  assert(html.includes('Nächstes technisches Kursziel ab aktuellem Kurs'));
  assert(html.includes('mindestens 1R'));
});

test('A current outlook shows its first target below 1R without waiting-entry language', () => {
  const forecast={ticker:'TEST',timeframe:'15m',strategy_version:'2.0-technical-projection',analysis_kind:'CURRENT_PROJECTION',
    status:'AVAILABLE',direction:'DOWN',reference_price:100,target_price:98,secondary_target_price:null,
    target_zone_min:97.9,target_zone_max:98.1,target_basis:'support',invalidation_price:103,invalidation_basis:'resistance',
    direction_score:20,indicator_strength:80,distance_pct:2,risk_reward_ratio:2/3,trade_eligible:false,min_reward_risk:1,
    summary:'Aktuell spricht die Technik für eine weitere Abwärtsbewegung bis 98.',
    trade_reason:'Das nächste Ziel bietet ab dem aktuellen Kurs weniger als 1R.',reasons:[],
    observed_at:1792920000000,analysis_bar_at:1792920000000,generated_at:1792920000000};
  const html=renderToStaticMarkup(React.createElement(TechnicalForecastPanel,{forecast}));
  assert(html.includes('Tendenz abwärts'));
  assert(html.includes('Technisches Hauptziel'));
  assert(html.includes('98,00'));
  assert(html.includes('weniger als 1R'));
  assert(!html.includes('Entry'));
  assert(!html.includes('VORBEREITET'));
  const figures=overlays.agentForecast.createPointFigures({
    coordinates:[{x:100,y:100},{x:300,y:200},{x:300,y:50}],overlay:{extendData:forecast},
  });
  assert(figures.some(f=>f.type==='text'&&f.attrs.text.startsWith('AKTUELL')));
  assert(figures.some(f=>f.type==='text'&&f.attrs.text.startsWith('ZIEL')));
  assert(figures.some(f=>f.type==='text'&&f.attrs.text.startsWith('UNGUELTIG')));
  assert(!figures.some(f=>f.type==='text'&&f.attrs.text.includes('ENTRY')));
  assert(!JSON.stringify(figures).includes('NaN'));
});

test('Timeframe minimum and blocking lower POIs explain the missing trade approval', () => {
  const forecast={ticker:'TEST',timeframe:'4h',status:'AVAILABLE',direction:'UP',reference_price:100,
    target_price:110,secondary_target_price:null,target_zone_min:109.8,target_zone_max:110.2,
    invalidation_price:98,indicator_strength:80,distance_pct:10,min_distance_pct:4.8,
    risk_reward_ratio:5,trade_eligible:false,summary:'Technisches Hauptziel aus 4h.',
    trade_reason:'Keine Trade-Freigabe wegen einer Gegenzone.',
    observed_at:1792920000000,analysis_bar_at:1792920000000,
    poi_check:{status:'BLOCKED',required_timeframes:['1h','30m','15m','5m','1m'],
      checked_timeframes:['1h','30m','15m','5m','1m'],unavailable_timeframes:[],
      obstacles:[{timeframe:'5m',kind:'order_blocks',price:103,blocking:true,reason:'Relevante Gegenzone.'}]}};
  const html=renderToStaticMarkup(React.createElement(TechnicalForecastPanel,{forecast}));
  assert(html.includes('4,8 %'));
  assert(html.includes('POI-Prüfung bis zum Hauptziel'));
  assert(html.includes('keine Trade-Freigabe'));
  assert(html.includes('5m'));
  assert(html.includes('Orderblock'));
  assert(html.includes('103,00'));
});

test('A missing main target shows the minimum and an unavailable POI feed stays explicit', () => {
  const forecast={ticker:'TEST',timeframe:'1D',status:'NO_TARGET',direction:'UP',reference_price:100,
    target_price:null,nearest_zone_price:104,min_distance_pct:9.6,indicator_strength:80,
    summary:'Keine vorhandene Kurszone erfüllt die Mindeststrecke.',
    observed_at:1792920000000,analysis_bar_at:1792920000000,
    poi_check:{status:'NOT_CHECKED',required_timeframes:['4h','1h'],checked_timeframes:[],
      unavailable_timeframes:[],obstacles:[]}};
  const html=renderToStaticMarkup(React.createElement(TechnicalForecastPanel,{forecast}));
  assert(html.includes('9,6 %'));
  assert(html.includes('104,00'));
  assert(!html.includes('Technisches Hauptziel ·'));
  const partial=renderToStaticMarkup(React.createElement(TechnicalForecastPanel,{forecast:{...forecast,
    status:'AVAILABLE',target_price:110,trade_reason:'POI-Prüfung unvollständig.',
    poi_check:{...forecast.poi_check,status:'INCOMPLETE',checked_timeframes:['4h'],unavailable_timeframes:['1h']}}}));
  assert(partial.includes('Prüfung unvollständig'));
  assert(partial.includes('Daten fehlen oder sind veraltet: 1h'));
});

test('An unclear current outlook never falls back to old waiting trade boxes', () => {
  const forecast={ticker:'TEST',timeframe:'15m',status:'UNCLEAR',direction:'UNCLEAR',reference_price:100,target_price:null,
    secondary_target_price:null,invalidation_price:null,indicator_strength:0,observed_at:1792920000000,analysis_bar_at:1792920000000,
    summary:'Trend und Momentum bestätigen keine gemeinsame Richtung.'};
  const html=renderToStaticMarkup(React.createElement(TradingChart,{ticker:'TEST',timeframe:'15m',klines:[],decision,forecast,news:[],markers:[]}));
  assert(html.includes('Kein aktuelles technisches Kursziel verfügbar'));
  assert(!html.includes('Projektion ab Entry'));
});
test('Unavailable backtest shows an explicit data warning', () => {
  const html = renderToStaticMarkup(React.createElement(BacktestPanel, {backtest:{total_trades:0,strategy_version:'2.0',win_rate:0,winning_trades:0,losing_trades:0,profit_factor:null,total_return_pct:0,final_capital:10000,max_drawdown_pct:0,error:'Zu wenig Historie'}}));
  assert(html.includes('Zu wenig Historie'));
});
test('Fallback news does not claim Gemini or verified credibility', () => {
  const html = renderToStaticMarkup(React.createElement(NewsPanel, {news:[{
    ticker:'TEST',headline:'Test headline',source:'Test publisher',url:'https://example.test',
    published_ts:1792920000,sentiment:0.2,event_type:'earnings',summary:'Test headline',
    credibility:null,relevance:0.8,impact_score:20,analysis_method:'keywords'
  }]}));
  assert(html.includes('Keyword-Fallback'));
  assert(html.includes('nicht geprüft'));
  assert(!html.includes('Gemini'));
});
test('Hard economic data is separated from instrument news and shows AI search disabled', () => {
  const html = renderToStaticMarkup(React.createElement(NewsPanel, {news:[],hardData:{status:'live',sources:['BLS'],snapshot_id:'x',fetched_at:1792920000,items:[{
    series_id:'LNS14000000',period:'2026-09',name:'US-Arbeitslosenquote',category:'labor',value:4.2,unit:'%',
    previous_value:4.1,previous_period:'2026-08',change_value:0.1,change_unit:'Prozentpunkte',year_over_year_pct:null,
    source:'BLS',source_url:'https://example.test/bls',preliminary:false,fetched_at:1792920000,stale:false
  }]}}));
  assert(html.includes('Harte Wirtschaftsdaten'));
  assert(html.includes('US-Arbeitslosenquote'));
  assert(html.includes('KI-Suche aus'));
  assert(html.includes('Passende Instrument-Nachrichten'));
});
test('Gold and crypto aliases match backend canonical symbols', () => {
  assert.equal(normalizeSymbol('gold'),'XAUUSD');
  assert.equal(normalizeSymbol('xau/usd'),'XAUUSD');
  assert.equal(normalizeSymbol('btc'),'BTC-USD');
  assert.equal(price(null),'—');
  assert(price(123,'GBp').includes('GBp'));
});
test('Chart renders accessible controls with explicit no-setup state', () => {
  const html = renderToStaticMarkup(React.createElement(TradingChart,{ticker:'TEST',timeframe:'15m',klines:[],news:[],markers:[]}));
  assert(html.includes('Profit / Stop'));
  assert(html.includes('Keine Kursdaten geladen.'));
  assert(html.includes('keine Profit-/Stop-Box'));
});
test('Quant toggle and calculate action are separate and explain manual operation', () => {
  const html = renderToStaticMarkup(React.createElement(QuantitativeControls, {
    settings:{enabled:false,revision:0,configured_weight_pct:20}, running:false, saving:false,
    onToggle(){}, onCalculate(){},
  }));
  assert(html.includes('role="switch"'));
  assert(html.includes('aria-checked="false"'));
  assert(html.includes('Quant: AUS'));
  assert(html.includes('disabled=""'));
  assert(html.includes('Quant berechnen + Gemini analysieren'));
  assert(html.includes('Einschalten, Kursupdates und Chartwechsel starten keine Quant-Berechnung'));
});
test('Current Quant card shows bullish context, actual weighting and estimated delta', () => {
  const html = renderToStaticMarkup(React.createElement(QuantitativeCard, {weight:20,data:{
    status:'AVAILABLE',direction:'BULLISH',direction_score:72,reason:'Calculated',method_note:'OHLCV-Schätzung',
    delta:100,delta_pct:20,cvd:1000,cvd_change_20:400,computed_at:1793000000000,
    group_comparison:'ALL_ALIGNED',components:[{key:'delta',name:'Delta-Schätzung',score:70,direction:'BULLISH',weight_pct:30}],
  }}));
  assert(html.includes('BULLISH'));
  assert(html.includes('72/100'));
  assert(html.includes('20 % angewendet'));
  assert(html.includes('Quant bestätigt beide Indikatorgruppen'));
  assert(html.includes('OHLCV-Schätzung'));
});
test('Stale Quant shows its archived value with zero current weight', () => {
  const html = renderToStaticMarkup(React.createElement(QuantitativeCard, {weight:20,data:{
    status:'STALE',direction:'BULLISH',direction_score:72,reason:'Refresh required',method_note:'Estimated',
  }}));
  assert(html.includes('VERALTET'));
  assert(html.includes('0 % angewendet'));
  assert(html.includes('Archivierter Score: 72/100'));
  assert(!html.includes('20 % angewendet'));
});
test('Both scanner modes use the same stored LightGBM filter without training', () => {
  const props = {onSelect(){}};
  const gemini = renderToStaticMarkup(React.createElement(ScannerPanel,{...props,mode:'gemini'}));
  const python = renderToStaticMarkup(React.createElement(ScannerPanel,{...props,mode:'python'}));
  assert(gemini.includes('LightGBM-Modelle'));
  assert(python.includes('LightGBM-Modelle'));
  assert(gemini.includes('Scanner Richtung'));
  assert(gemini.includes('Scanner Märkte'));
  assert(!gemini.includes('Delta/CVD'));
});
test('Gemini waiting decision remains visibly a successful response despite an ended signal', () => {
  const html = renderToStaticMarkup(React.createElement(GeminiStatus,{busy:false,analysis:{
    ai_analysis_method:'gemini',analyzed_at:1793000000000,news_pipeline:{ai_status:'evaluated',cache_hit:true},
    latest_ai_decision:{signal_type:'NEUTRAL',validation_status:'accepted_neutral',reasons:['Gemini bewertet das Gesamtbild als ABWARTEN.']},
    decision:{lifecycle_status:'BEENDET'},
  }}));
  assert(html.includes('Gemini hat geantwortet'));
  assert(html.includes('ABWARTEN ist eine gültige KI-Antwort'));
  assert(html.includes('archivierte Antwort'));
});
test('Gemini error and running status are shown distinctly from a neutral decision', () => {
  const running = renderToStaticMarkup(React.createElement(GeminiStatus,{busy:true,analysis:null}));
  assert(running.includes('Gemini-Analyse läuft'));
  const error = renderToStaticMarkup(React.createElement(GeminiStatus,{busy:false,analysis:{
    ai_analysis_method:'keywords',news_pipeline:{ai_status:'unavailable',ai_error:{message:'Gemini hat nicht rechtzeitig geantwortet.'}},
  }}));
  assert(error.includes('nicht rechtzeitig'));
  assert(!error.includes('Gemini hat geantwortet'));
});
test('Quant partial card explains why its applied weight is zero', () => {
  const html = renderToStaticMarkup(React.createElement(QuantitativeCard,{weight:0,data:{
    status:'PARTIAL',direction:'UNAVAILABLE',direction_score:null,covered_bars:19,window_bars:20,
    reason:'Missing source interval',method_note:'Estimated',
  }}));
  assert(html.includes('0 %: Quellkerzen oder nutzbares Volumen fehlen'));
  assert(html.includes('19/20 Intervalle'));
});
test('Quant UI explains the capped budget, data resolution and non-voting price context', () => {
  const controls = renderToStaticMarkup(React.createElement(QuantitativeControls,{
    settings:{enabled:true,revision:1,configured_weight_pct:12},running:false,saving:false,onToggle(){},onCalculate(){},
  }));
  assert(controls.includes('bis zu 12 %'));
  assert(controls.includes('bei wenigen Quellkerzen reduziert'));
  const html = renderToStaticMarkup(React.createElement(QuantitativeCard,{weight:2.4,data:{
    status:'AVAILABLE',direction:'NEUTRAL',direction_score:50,reason:'Volume only',method_note:'Estimated',
    weight_quality_factor:.2,source_bars_per_main:1,components:[],
  }}));
  assert(html.includes('2,4 % angewendet'));
  assert(html.includes('3 und 17 Kerzen'));
  assert(html.includes('Preis-Kontext'));
});
test('News UI shows horizon weights and the strength of the evaluated evidence', () => {
  const html = renderToStaticMarkup(React.createElement(NewsPanel,{news:[],policy:{
    timeframe:'5m',decision_weights_pct:{technical:58,quantitative:12,hard_data:10,normal_news:20},
  },pipeline:{ai_status:'evaluated',normal_news_evidence_pct:.0035,source_statuses:[]}}));
  assert(html.includes('5m'));
  assert(html.includes('Quant 12%'));
  assert(html.includes('Bewertete Nachrichtenstärke'));
  assert(html.includes('Artikelanzahl allein'));
});
const { OptionsContextPanel } = require('../src/components/OptionsContextPanel.tsx');
const optionsData = {
  ticker:'AAPL',status:'REFERENCE',reason:'Quote-Zeitpunkt unbekannt',usable_for_ai:false,
  direction_weight_pct:0,provider:'yahoo',feed:'Yahoo',computed_at:Date.now()/1000,spot:100,spot_at:Date.now()/1000,
  contracts:[{symbol:'AAPL261030C00100000',kind:'call',expiration:'2026-10-30',strike:100,iv:.3,
    bid:1,ask:1.1,quote_at:null,fresh_quote:false,greeks:{delta:.52,gamma:.1,theta:-.05,vega:.12,rho:.002}}],
  model:'Black–Scholes–Merton',warnings:['Keine Dealerpositionen bekannt'],
  assumptions:{rate:.04,rate_source:'Konfiguriert',dividend_yield:0,dividend_source:'Modellannahme q=0',expiry_time:'16:00 New York'},
};
test('Options reference values show units and never claim live AI use', () => {
  const html = renderToStaticMarkup(React.createElement(OptionsContextPanel,{data:optionsData}));
  assert(html.includes('NUR REFERENZ'));
  assert(html.includes('Keine numerischen Optionswerte'));
  assert(html.includes('Unbekannt'));
  assert(html.includes('Theta/Tag'));
  assert(html.includes('Vega/1 PP'));
  assert(html.includes('je 1 USD'));
  assert(!html.includes('BULLISH'));
  assert(!html.includes('angewendet'));
});
test('Archived Greeks remain visible with an archive label', () => {
  const html = renderToStaticMarkup(React.createElement(OptionsContextPanel,{data:{...optionsData,status:'STALE'}}));
  assert(html.includes('ARCHIVIERT'));
  assert(html.includes('Archivierte'));
  assert(html.includes('Call'));
});
test('Missing options explain manual loading without invented numbers', () => {
  const html = renderToStaticMarkup(React.createElement(OptionsContextPanel,{}));
  assert(html.includes('NOCH NICHT GELADEN'));
  assert(html.includes('Quant berechnen'));
  assert(!html.includes('<table'));
});
test('Fresh Greeks are described as model risk context without extra direction', () => {
  const html = renderToStaticMarkup(React.createElement(OptionsContextPanel,{data:{...optionsData,status:'AVAILABLE',usable_for_ai:true,
    contracts:optionsData.contracts.map(c=>({...c,quote_at:Date.now()/1000,fresh_quote:true}))}}));
  assert(html.includes('ZEITNAHER FEED'));
  assert(html.includes('Gemini erhält'));
  assert(html.includes('Keine zusätzliche Richtungsstimme'));
});
test('Options UI expires old model values even if backend polling has stopped', () => {
  const html = renderToStaticMarkup(React.createElement(OptionsContextPanel,{data:{...optionsData,status:'AVAILABLE',
    usable_for_ai:true,computed_at:Date.now()/1000-301}}));
  assert(html.includes('ARCHIVIERT'));
  assert(html.includes('Keine numerischen Optionswerte'));
});
const { BacktestComparisonPanel } = require('../src/components/BacktestComparisonPanel.tsx');
test('Backtest comparison exposes two explicit profiles and manual controls', () => {
  const html = renderToStaticMarkup(React.createElement(BacktestComparisonPanel,{ticker:'AAPL',timeframe:'5m'}));
  assert(html.includes('1 · Python-Basisstrategie'));
  assert(html.includes('2 · Gemini + LightGBM'));
  assert(html.includes('Python neu berechnen'));
  assert(html.includes('Gemini + LightGBM auswerten'));
  assert(html.includes('Keine zusätzlichen Gemini-Aufrufe'));
  assert(html.includes('Kein vollständiger KI-Test') || html.includes('kein vollständiger KI-Test'));
});
test('Archived Gemini results never claim to be a technical-only replay', () => {
  const report = {analysis_mode:'gemini_quant',total_trades:0,winning_trades:0,losing_trades:0,profit_factor:null,
    total_return_pct:0,final_capital:5000,max_drawdown_pct:0,strategy_version:'archived',scope:'Archivierte Aktivierungen',
    news_coverage_bars:0,costs:'Kosten',assumptions:'TP1',position_sizing:'Ein Konto',recent_trades:[],coverage_note:'Teilweise fehlende Ausführungskerzen'};
  const html = renderToStaticMarkup(React.createElement(BacktestPanel,{backtest:report}));
  assert(html.includes('tatsächlich aufgezeichneten KI-Kontexten'));
  assert(!html.includes('bewertet ausschließlich die Python-Technik'));
  assert(html.includes('Teilweise fehlende Ausführungskerzen'));
});
const { BackgroundPanel } = require('../src/components/BackgroundPanel.tsx');
const { backgroundStateLabel } = require('../src/backgroundStatus.ts');
test('Background controls explain browser independence, PC uptime and explicit activation', () => {
  const html = renderToStaticMarkup(React.createElement(BackgroundPanel));
  assert(html.includes('Dauerbetrieb einschalten'));
  assert(html.includes('aria-checked="false"'));
  assert(html.includes('PC und Backend müssen eingeschaltet bleiben'));
  assert(html.includes('im Energiesparmodus läuft keine Beobachtung'));
  assert(html.includes('Autostart-einrichten.cmd'));
});
test('Background status never claims to run with missing or offline backend evidence', () => {
  assert(backgroundStateLabel(null,false).includes('geladen'));
  const data={settings:{enabled:true},worker_alive:false,runtime:{status:'RUNNING'}};
  assert(backgroundStateLabel(data,false).includes('wartet'));
  assert(backgroundStateLabel({...data,worker_alive:true},true).includes('nicht bestätigt'));
  assert.equal(backgroundStateLabel({...data,settings:{enabled:false}},false),'AUS');
  assert(backgroundStateLabel({...data,worker_alive:true,runtime:{status:'MARKET_CLOSED'}},false).includes('KI pausiert'));
});
test('LightGBM explains separate timeframes, manual background training and baseline backtests', () => {
  const { LightGBMPanel } = require('../src/components/LightGBMPanel.tsx');
  const html = renderToStaticMarkup(React.createElement(LightGBMPanel, { ticker: 'NVDA', timeframe: '5m', onChange() {} }));
  assert(html.includes('lokaler Richtungsfilter'));
  assert(html.includes('15 Minuten (3 Kerzen)'));
  assert(html.includes('Daten aktualisieren &amp; trainieren'));
  assert(html.includes('Basisstrategie ohne LightGBM'));
  assert(html.includes('aria-checked="false"'));
  const unsupported = renderToStaticMarkup(React.createElement(LightGBMPanel, { ticker: 'NVDA', timeframe: '1h', onChange() {} }));
  assert(unsupported.includes('Training für 5m und 15m verfügbar'));
});
test('Workspace shows one main chart and keeps detailed panels outside the main view', () => {
  const App = require('../src/App.tsx').default;
  const html = renderToStaticMarkup(React.createElement(App));
  assert.equal((html.match(/aria-label="Kurschart /g)||[]).length,1);
  assert(html.includes('Einstellungen öffnen'));
  assert(html.includes('Arbeitsbereiche'));
  assert(!html.includes('Quantitative Gemini-Unterfunktion'));
  assert(!html.includes('Zwei historische Backtests'));
  assert(!html.includes('Daten aktualisieren &amp; trainieren'));
});
test('Model settings separate archive times, training scope and background operation', () => {
  const { ModelSettings } = require('../src/components/ModelSettings.tsx');
  const html=renderToStaticMarkup(React.createElement(ModelSettings,{ticker:'NVDA',timeframe:'15m',onEnabled(){},onSelect(){}}));
  for (const text of ['Diese Aktie','Watchlist','Gespeicherte Daten','Zuletzt aktualisiert','Timeframes trainieren','Abrufzeitpunkt','verändert den Hauptchart nicht']) assert(html.includes(text),text);
  assert(!html.includes('Kurschart'));
});
console.log(count + ' frontend regression checks passed.');
