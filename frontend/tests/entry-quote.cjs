const assert = require('node:assert/strict');
const fs = require('node:fs');
const Module = require('node:module');
const ts = require('typescript');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const overlays = {};
const charts = require('../node_modules/klinecharts/dist/index.esm.js');
const originalRequire = Module.prototype.require;
Module.prototype.require = function (id) {
  if (id === 'klinecharts') return {...charts, registerIndicator() {}, registerOverlay(value) {overlays[value.name] = value;}};
  return originalRequire.call(this, id);
};
function loadTs(module, filename) {
  const source = fs.readFileSync(filename, 'utf8').replace(/import\.meta\.env/g, "({VITE_API_BASE_URL:'',VITE_DEMO_MODE:'false'})");
  const output = ts.transpileModule(source, {compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2023, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true,
  }, fileName: filename}).outputText;
  module._compile(output, filename);
}
require.extensions['.ts'] = loadTs;
require.extensions['.tsx'] = loadTs;
const {TradingChart} = require('../src/components/TradingChart.tsx');
const {DecisionPanel} = require('../src/components/DecisionPanel.tsx');
const {price} = require('../src/api.ts');
const decision = {
  ticker: 'XAUUSD', timeframe: '5m', signal_type: 'SELL', lifecycle_status: 'VORBEREITET',
  signal_label: 'SELL', entry_mode: 'CONFIRMATION', decision_source: 'gemini', validation_status: 'accepted',
  current_price: 4036.3, entry_price: 4041.5, entry_zone_min: 4041, entry_zone_max: 4042,
  stop_loss: 4051, take_profit_1: 4027.25, take_profit_2: 4020, risk_reward_ratio: 1.5,
  score: 30, strength: 65, setup_quality: 'A', reasons: [], as_of: 1793280000000,
};
const bar = {timestamp: 1793280300000, open: 4035, high: 4036, low: 4029, close: 4030, volume: 1000};
const figures = overlays.agentRiskReward.createPointFigures({
  coordinates: [{x:100,y:200},{x:300,y:150},{x:300,y:300},{x:300,y:350}], overlay: {extendData: decision},
});
assert(figures.some(item => item.type === 'text' && item.attrs.text === 'GEPLANTER ENTRY '+price(4041.5)));
console.log('PASS: prepared overlay explicitly labels a planned entry');

const chart = renderToStaticMarkup(React.createElement(TradingChart, {
  ticker:'XAUUSD', timeframe:'5m', klines:[bar], decision, news:[], markers:[],
}));
assert(chart.includes('Aktueller Kurs'));
assert(chart.includes(price(4030)));
assert(chart.includes(price(4041.5)));
assert(chart.includes('Abstand 0.29%'));
assert(chart.includes('Noch kein Einstieg ausgelöst.'));
console.log('PASS: chart distinguishes the latest candle price from planned entry');

const panel = renderToStaticMarkup(React.createElement(DecisionPanel, {decision, currentPrice:4030}));
assert(panel.includes('Aktueller Kurs'));
assert(panel.includes(price(4030)));
assert(panel.includes('Abstand zum Kurs 0.29%'));
assert(panel.includes('Entry-Zone und Bestätigung abwarten.'));
console.log('PASS: decision panel uses latest chart quote even when saved plan quote is older');

const active = renderToStaticMarkup(React.createElement(DecisionPanel, {
  decision: {...decision,lifecycle_status:'AKTIV'}, currentPrice:4030,
}));
assert(active.includes('Ursprünglicher Entry'));
assert(!active.includes('Noch kein Einstieg ausgelöst.'));
assert(active.includes(price(4041.5)));
console.log('PASS: active entry remains its original price');
console.log('4 focused entry/quote checks passed.');
