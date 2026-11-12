"""A single offline HTML report. Read saved results; never rerun the strategy."""
from collections import Counter
from datetime import datetime, timezone
from html import escape
import json
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
DATA = json.loads((ROOT / 'backtest_results/results.json').read_text(encoding='utf-8'))
OUT = ROOT.parent / 'Aktien_Backtest_2_2.html'
VIENNA = ZoneInfo('Europe/Vienna')
NAMES = {'NVDA':'NVIDIA','TSLA':'Tesla','AMD':'Advanced Micro Devices','AAPL':'Apple',
         'MSFT':'Microsoft','AMZN':'Amazon','GOOGL':'Alphabet','META':'Meta Platforms',
         'PLTR':'Palantir','COIN':'Coinbase (Aktie)'}


def number(value, places=2):
    if value is None:
        return '—'
    return f'{value:,.{places}f}'.replace(',', '_').replace('.', ',').replace('_', '.')


def euro(value):
    return number(value) + ' €'


def percent(value):
    return number(value) + ' %'


def date(value, full=True):
    if isinstance(value, str):
        timestamp = datetime.fromisoformat(value)
    else:
        timestamp = datetime.fromtimestamp(value / 1000, timezone.utc)
    return timestamp.astimezone(VIENNA).strftime('%d.%m.%Y · %H:%M' if full else '%d.%m.%Y')


def cell(value, cls=''):
    return f'<td class="{cls}">{escape(str(value))}</td>'


def row(values, attrs=''):
    return f'<tr {attrs}>' + ''.join(cell(value) for value in values) + '</tr>'


def table(headers, rows, table_id='', classes=''):
    return (f'<div class="table-wrap"><table id="{table_id}" class="{classes}"><thead><tr>' +
            ''.join(f'<th scope="col">{escape(header)}</th>' for header in headers) +
            '</tr></thead><tbody>' + ''.join(rows) + '</tbody></table></div>')


def totals(result):
    counts = Counter()
    for diagnostics in result['diagnostics'].values():
        counts.update(diagnostics)
    return counts


portfolios = DATA['portfolios']
VERSION = portfolios[0]['strategy_version']
EXIT_NAMES = {'STOP':'Stop erreicht','STOP_GAP':'Stop mit Kurslücke',
              'STOP_BOTH_HIT':'Stop zuerst bei unklarer Reihenfolge',
              'TP1':'TP1 erreicht','END_OF_DATA':'Am Datenende geschlossen'}
cards, summary_rows, diagnostic_rows, detail_diagnostics = [], [], [], []
for result in portfolios:
    tf = result['timeframe']
    profit = result['final_capital'] - result['initial_capital']
    color = 'negative' if profit < 0 else 'positive' if profit > 0 else 'neutral'
    count = result['total_trades']
    card_note = ('Kein ausgeführter Trade; Kapital unverändert.' if not count else
                 f"{result['trades'][0]['ticker']}: {EXIT_NAMES.get(result['trades'][0]['reason'],result['trades'][0]['reason'])}."
                 if count == 1 else 'Ergebnisse nach modellierten Kosten.')
    cards.append(f'''<article class="result-card {color}">
      <div class="card-top"><span class="tag">{tf[:-1]} MINUTEN</span><span class="card-policy">Nur Aktienkäufe · TP1</span></div>
      <div class="balance">{euro(result['final_capital'])}</div><div class="label">Endkapital aus 5.000,00 €</div>
      <div class="pnl {color}">{'+' if profit > 0 else ''}{euro(profit)} <span>({percent(result['total_return_pct'])})</span></div>
      <div class="mini-grid"><div><b>{count}</b><span>ausgeführte Trades</span></div>
      <div><b>{percent(result['max_drawdown_pct'])}</b><span>max. Drawdown</span></div>
      <div><b>{percent(result['win_rate']) if count else '—'}</b><span>beobachtete Gewinnquote</span></div></div>
      <p class="card-note">{escape(card_note)}</p>
    </article>''')
    summary_rows.append(row([tf, 'TP1 / Stop', euro(result['initial_capital']), euro(result['final_capital']),
        euro(profit), percent(result['total_return_pct']), count,
        f"{result['winning_trades']} / {result['losing_trades']}",
        percent(result['win_rate']) if count else '—', number(result['profit_factor'], 3) if count else '—',
        percent(result['max_drawdown_pct']), euro(result['expectancy_eur']) if count else '—']))
    counts = totals(result)
    diagnostic_rows.append(row([tf, counts['evaluated_bars'], counts['UNCLEAR'], counts['NO_TARGET'],
        counts['POI_BLOCKED'], counts['POI_INCOMPLETE'], counts['RISK_REWARD'],
        counts['INVALIDATION_OR_RISK'], counts['eligible_signals']]))
    for symbol, values in result['diagnostics'].items():
        detail_diagnostics.append(row([symbol, tf, values.get('evaluated_bars', 0),
            values.get('UNCLEAR', 0), values.get('NO_TARGET', 0), values.get('POI_BLOCKED', 0),
            values.get('RISK_REWARD', 0), values.get('eligible_signals', 0)],
            f'data-symbol="{symbol}" data-timeframe="{tf}"'))

summary = table(['Chart','Ausstieg','Startkapital','Endkapital','Netto','Rendite','Trades','Gewinne / Verluste',
                 'Gewinnquote','Profit Factor','Max. Drawdown','Ø Netto / Trade'], summary_rows, 'summary-table')
diagnostics = table(['Chart','Prüfungen','Gruppen uneinig / neutral','Kein geeignetes Ziel','Kurszone blockiert',
                     'POI-Daten fehlen','Chance/Risiko zu klein','Andere Risikogrenze','Freigegeben'], diagnostic_rows)
diagnostics_detail = table(['Aktie','Chart','Prüfungen','Gruppen uneinig / neutral','Kein geeignetes Ziel',
                           'Kurszone blockiert','Chance/Risiko zu klein','Freigegeben'], detail_diagnostics, 'diagnostic-table')

all_trades = [{**trade, 'timeframe': result['timeframe']} for result in portfolios for trade in result['trades']]
trade_rows, trade_detail = [], []
for trade in all_trades:
    trade_rows.append(row([trade['ticker'], trade['timeframe'], 'Kauf' if trade['type'] == 'BUY' else 'Leerverkauf',
        date(trade['entry_time']), date(trade['time']), number(trade['entry_price'], 4),
        number(trade['exit_price'], 4), number(trade['take_profit_1'], 4), number(trade['stop_loss'], 4),
        euro(trade['pnl']), EXIT_NAMES.get(trade['reason'],trade['reason'])]))
    trade_detail.append(f'''<div class="trade-detail">
      <div><span class="label">Stückzahl (Bruchstücke)</span><b>{number(trade['units'], 6)}</b></div>
      <div><span class="label">Einstiegsgebühr</span><b>{number(trade['entry_fee_usd'], 4)} USD</b></div>
      <div><span class="label">Ausstiegsgebühr</span><b>{number(trade['exit_fee_usd'], 4)} USD</b></div>
      <div><span class="label">Brutto-Kursverlust</span><b>{number(trade['gross_pnl_usd'], 4)} USD</b></div>
      <div><span class="label">Umrechnung bei Einstieg / Ausstieg</span><b>{number(trade['entry_usd_per_eur'], 6)} / {number(trade['exit_usd_per_eur'], 6)} USD je EUR</b></div>
      <div><span class="label">Kontostand nach dem Trade</span><b>{euro(trade['capital_after'])}</b></div></div>''')
trades_table = table(['Aktie','Chart','Richtung','Einstieg · Wien','Ausstieg · Wien','Einstieg USD',
                      'Ausstieg USD','TP1 USD','Stop USD','Netto EUR','Ausgang'], trade_rows, 'trades-table')

individual_rows = []
for result in sorted(DATA['individual'], key=lambda result: (result['symbols'][0], int(result['timeframe'][:-1]))):
    symbol = result['symbols'][0]
    individual_rows.append(row([symbol, NAMES[symbol], result['timeframe'], euro(result['final_capital']),
        euro(result['final_capital'] - result['initial_capital']), percent(result['total_return_pct']),
        result['total_trades'], percent(result['win_rate']) if result['total_trades'] else '—',
        percent(result['max_drawdown_pct'])], f'data-symbol="{symbol}" data-timeframe="{result["timeframe"]}"'))
individual_table = table(['Aktie','Unternehmen','Chart','Endkapital','Netto','Rendite','Trades','Gewinnquote','Max. Drawdown'],
                         individual_rows, 'individual-table')

candidate_rows = []
candidate_data = []
executed_keys = {(trade['ticker'], trade['timeframe'], trade['signal_at']):trade for trade in all_trades}
for path in (ROOT / 'backtest_results').glob('candidates_*.json'):
    candidate_data += json.loads(path.read_text(encoding='utf-8'))['candidates']
for candidate in sorted(candidate_data, key=lambda item: item['signal_at']):
    executed = executed_keys.get((candidate['ticker'], candidate['timeframe'], candidate['signal_at']))
    disposition = ('Ausgeführt: '+EXIT_NAMES.get(executed['reason'],executed['reason']) if executed else
                   'Übersprungen: nur Käufe' if candidate['direction'] == -1 else 'Nicht ausgeführt')
    candidate_rows.append(row([candidate['ticker'], candidate['timeframe'], date(candidate['signal_at']),
        'Kauf' if candidate['direction'] == 1 else 'Verkauf / Short', number(candidate['reference_price'], 4),
        number(candidate['target'], 4), number(candidate['stop'], 4), number(candidate['strength'], 1), disposition]))
candidates_table = table(['Aktie','Chart','Signalzeit · Wien','Richtung','Bezugskurs USD','TP1 USD','Stop USD',
                          'Indikator-Score','Behandlung im Test'], candidate_rows, 'candidates-table')

# Plot is inline SVG; no images, network, libraries or sibling files are needed.
curves = {result['timeframe']: [[point['timestamp'], point['equity']] for point in result['equity_curve']]
          for result in portfolios}
begin, finish = portfolios[0]['start_at'], portfolios[0]['end_at']
plot_values = [point[1] for curve in curves.values() for point in curve] + [5000.]
y_min, y_max = min(plot_values), max(plot_values)
padding = max((y_max-y_min)*.16, 2.)
y_min, y_max = y_min-padding, y_max+padding
LEFT, RIGHT, TOP, BOTTOM = 88, 1138, 28, 315
def x(timestamp): return LEFT+(timestamp-begin)/(finish-begin)*(RIGHT-LEFT)
def y(value): return BOTTOM-(value-y_min)/(y_max-y_min)*(BOTTOM-TOP)
chart_description = '; '.join(result['timeframe']+': '+euro(result['final_capital']) for result in portfolios)
svg = ['<svg id="equity-chart" viewBox="0 0 1180 365" role="img" aria-label="'+escape(chart_description)+'">']
for step in range(5):
    value = y_min+(y_max-y_min)*step/4
    svg.append(f'<line x1="{LEFT}" x2="{RIGHT}" y1="{y(value):.2f}" y2="{y(value):.2f}" class="grid-line"/>')
    svg.append(f'<text x="{LEFT-14}" y="{y(value)+4:.2f}" class="axis" text-anchor="end">{number(value,0)} €</text>')
for step in range(6):
    stamp = begin+(finish-begin)*step/5
    label = datetime.fromtimestamp(stamp/1000,timezone.utc).astimezone(VIENNA).strftime('%d.%m. %H:%M')
    svg.append(f'<text x="{x(stamp):.2f}" y="345" class="axis" text-anchor="middle">{label}</text>')
svg.append(f'<line x1="{LEFT}" x2="{RIGHT}" y1="{y(5000):.2f}" y2="{y(5000):.2f}" class="baseline"/>')
for tf in ('15m','5m'):
    points = [[begin, 5000.]] + curves[tf]
    path = ' '.join(('M' if index == 0 else 'L')+f'{x(stamp):.2f},{y(value):.2f}' for index,(stamp,value) in enumerate(points))
    color = '#a78bfa' if tf == '15m' else '#38bdf8'
    svg.append(f'<path id="curve-{tf}" d="{path}" fill="none" stroke="{color}" stroke-width="2.4" vector-effect="non-scaling-stroke"/>')
svg += ['<line id="crosshair" y1="28" y2="315" class="crosshair" visibility="hidden"/>',
        '<rect id="chart-overlay" x="88" y="28" width="1050" height="287" fill="transparent"/>','</svg>']
chart = ''.join(svg)

coverage_rows = []
for symbol in DATA['metadata']['symbols']:
    counts = []
    for tf, seconds in [('1m',60),('5m',300),('15m',900)]:
        snapshot = json.loads((ROOT / 'backtest_data' / f'{symbol}_{tf}.json').read_text(encoding='utf-8'))
        cutoff = datetime.fromisoformat(snapshot['snapshot_at']).timestamp()*1000
        closed = [bar for bar in snapshot['klines'] if bar['timestamp']+seconds*1000 <= cutoff]
        counts.append(len(closed))
    coverage_rows.append(row([symbol, NAMES[symbol], *counts, 'Vollständig verfügbar']))
coverage_table = table(['Aktie','Unternehmen','Geschlossene 1m-Kerzen','Geschlossene 5m-Kerzen',
                        'Geschlossene 15m-Kerzen','Datenstatus'],coverage_rows)

previous = json.loads((ROOT/'comparison_baseline.json').read_text(encoding='utf-8'))
assert previous['metadata']['data_sha256']==DATA['metadata']['data_sha256']
assert previous['metadata']['start']==DATA['metadata']['start'] and previous['metadata']['end']==DATA['metadata']['end']
comparison_rows=[]
for timeframe in ('5m','15m'):
    for label,items in [('Vorher · 2.1',previous['portfolios']),('Neu · 2.2',portfolios)]:
        result=next(item for item in items if item['timeframe']==timeframe)
        eligible=sum(values.get('eligible_signals',0) for values in result['diagnostics'].values())
        comparison_rows.append(row([label,timeframe,eligible,result['total_trades'],euro(result['final_capital']),
                                    euro(result['final_capital']-result['initial_capital']),percent(result['total_return_pct'])]))
comparison_table=table(['Code','Chart','Freigegebene BUY-/SELL-Szenarien','Ausgeführte Käufe','Endkapital','Netto','Rendite'],comparison_rows,'comparison-table')
signal_description=f"{len(candidate_data)} Szenarien wurden freigegeben: {sum(item['direction']>0 for item in candidate_data)} Kauf und {sum(item['direction']<0 for item in candidate_data)} Verkauf/Short. Im Kaufmodus werden Verkaufsszenarien nicht gehandelt. Der Indikator-Score ist keine Erfolgswahrscheinlichkeit."
extra_diagnostics=' '.join(f"{result['timeframe']}: {totals(result)['market_closed']} Prüfzeitpunkte am regulären Marktschluss ausgelassen; {result['execution_rejections'].get('sell_skipped_long_only',0)} SELL-Szenarien wegen Kaufmodus übersprungen; {result['execution_rejections'].get('capital_busy',0)} Signale wegen gebundenem Kapital übersprungen." for result in portfolios)
exit_counts=Counter(trade['reason'] for trade in all_trades)
exit_description='Ausgänge: '+', '.join(str(count)+' × '+EXIT_NAMES.get(reason,reason) for reason,count in exit_counts.items())+'. Stop-Ausführungen berücksichtigen Slippage und Spread; der tatsächliche Modell-Ausstieg kann deshalb schlechter als das Stop-Level ausfallen.'
final_readout=' · '.join(result['timeframe']+': '+euro(result['final_capital'])+' am Ende' for result in portfolios)
validation=DATA['metadata'].get('validation',{})

payload = json.dumps({'curves':curves,'begin':begin,'finish':finish,'min':y_min,'max':y_max},
                     separators=(',',':')).replace('<','\\u003c')
page = r'''<!DOCTYPE html>
<html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark"><title>Neuer Aktien-Backtest · Version 2.2 · 5.000 € · TP1</title>
<style>
:root{--bg:#0b1220;--panel:#111c2e;--line:#27374d;--text:#e2eaf6;--muted:#96a7be;--blue:#38bdf8;--purple:#a78bfa;--red:#fda4af;--green:#6ee7b7}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.65 system-ui,-apple-system,Segoe UI,sans-serif}main{max-width:1320px;margin:auto;padding:40px 32px 70px}h1,h2,h3,p{margin-top:0}h1{font-size:clamp(30px,4vw,48px);line-height:1.14;letter-spacing:-1.6px;margin:14px 0}h2{font-size:23px;letter-spacing:-.5px;margin-bottom:12px}h3{font-size:16px}p{color:var(--muted)}strong,b{color:var(--text)}a{color:#8ed5f6;text-decoration:none}a:hover{text-decoration:underline}.eyebrow{font-size:11px;letter-spacing:2px;color:var(--blue);font-weight:750}.heading-row{display:flex;align-items:start;justify-content:space-between;gap:16px}.subheading{font-size:17px;max-width:880px}.badges,.actions,.nav,.chart-controls{display:flex;gap:10px;align-items:center;flex-wrap:wrap}.badge{font-size:12px;padding:5px 11px;border:1px solid var(--line);border-radius:20px;color:#c4d2e6;background:#111c2e}.badge.emphasis{color:#8ceac4;border-color:#235b4c;background:#0f2925}.nav{margin:25px 0 28px;border-bottom:1px solid var(--line);padding-bottom:15px;font-size:13px}.nav a{color:#b5c4d8;margin-right:12px}button,input,select{font:inherit}button{border:1px solid #34465f;background:#17243a;color:#e1ebf8;border-radius:8px;padding:9px 14px;cursor:pointer}button:hover{background:#223451}button:focus-visible,a:focus-visible,input:focus-visible,select:focus-visible,summary:focus-visible{outline:2px solid var(--blue);outline-offset:4px}.small{font-size:12px}.muted,.label{color:var(--muted)}.label{font-size:12px}.result-grid{display:grid;grid-template-columns:1fr 1fr;gap:18px;margin:22px 0}.result-card{background:linear-gradient(135deg,#142238,#101b2d);border:1px solid var(--line);border-radius:15px;padding:25px}.card-top{display:flex;justify-content:space-between;align-items:center;gap:10px}.tag{font-size:11px;letter-spacing:1.4px;font-weight:750;color:var(--blue)}.result-card:nth-child(2) .tag{color:var(--purple)}.card-policy{font-size:12px;color:var(--muted)}.balance{font-size:clamp(28px,3.5vw,40px);letter-spacing:-1px;line-height:1.3;margin-top:18px;font-weight:720}.pnl{font-size:19px;font-weight:650;margin:14px 0 23px}.pnl span{font-size:13px;font-weight:500}.negative{color:var(--red)}.positive{color:var(--green)}.neutral{color:var(--text)}.mini-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;border-top:1px solid var(--line);padding-top:16px}.mini-grid b,.mini-grid span{display:block}.mini-grid b{font-size:20px}.mini-grid span{font-size:11px;color:var(--muted)}.card-note{font-size:12px;margin:16px 0 0}.callout{border:1px solid #67522e;border-left:3px solid #fbbf24;background:#272318;border-radius:9px;padding:16px 19px;margin:22px 0;color:#dfcda3}.callout strong{color:#fde8b2}.callout p{margin:3px 0 0;color:#d4c39c;font-size:13px}.info{background:#112436;border-color:#244f70;border-left-color:var(--blue);color:#bcd5e8}.info strong{color:#c4eaff}.info p{color:#bcd5e8}.section{margin-top:38px;scroll-margin-top:20px}.section-heading{display:flex;justify-content:space-between;gap:16px;align-items:start}.section-intro{max-width:1000px;margin-bottom:15px;font-size:13px}.panel{background:var(--panel);border:1px solid var(--line);border-radius:13px;padding:20px}.table-wrap{overflow:auto;border:1px solid var(--line);border-radius:11px;margin:14px 0 7px}table{width:100%;border-collapse:collapse;font-size:12px;font-variant-numeric:tabular-nums}th,td{padding:12px 13px;text-align:right;border-bottom:1px solid #223248;white-space:nowrap}th{color:#a5c5df;font-size:11px;font-weight:650;background:#142238}td:first-child,th:first-child{text-align:left}tbody tr:last-child td{border-bottom:0}tbody tr:hover{background:#16273d}.table-note{font-size:11px;margin-top:8px}.chart-controls{justify-content:space-between}.legend{display:flex;gap:17px}.legend label{display:flex;gap:7px;align-items:center;font-size:12px;cursor:pointer}.dot{display:inline-block;width:9px;height:9px;border-radius:50%;background:var(--blue)}.dot.purple{background:var(--purple)}input[type=checkbox]{accent-color:var(--blue)}#equity-chart{display:block;width:100%;height:auto;touch-action:pan-y}.axis{fill:#93a7c2;font-size:11px;font-family:system-ui}.grid-line{stroke:#283a52;stroke-width:1}.baseline{stroke:#61718a;stroke-dasharray:5 6;opacity:.8}.crosshair{stroke:#d0d9e8;stroke-width:1;stroke-dasharray:3 4}.chart-readout{font-size:12px;color:#afc1d9;min-height:23px;padding:4px 0}.chart-caption{margin:8px 0 0;font-size:11px;color:var(--muted)}.trade-detail{display:grid;grid-template-columns:repeat(3,1fr);gap:17px;padding:18px 0}.trade-detail b,.trade-detail .label{display:block}.trade-detail b{font-size:13px;margin-top:3px}.filters{display:flex;gap:10px;align-items:center;flex-wrap:wrap}.filters input,.filters select{background:#101a2c;color:var(--text);border:1px solid #34465f;border-radius:7px;padding:8px 10px;font-size:12px}.filters input{width:205px}.filter-count{font-size:12px;color:var(--muted)}.two-columns{display:grid;grid-template-columns:1fr 1fr;gap:20px}.method-list{margin:0;padding-left:20px;color:#b3c2d5;font-size:13px}.method-list li{margin:7px 0}.method-list strong{font-weight:600}.stock-list{display:flex;gap:8px;flex-wrap:wrap;margin:15px 0}.stock-list span{background:#142238;border:1px solid #2a3a52;border-radius:6px;padding:5px 10px;font-size:12px}details{border:1px solid var(--line);border-radius:10px;margin-top:16px;padding:14px 17px}summary{cursor:pointer;font-size:13px;font-weight:600;color:#c5d9ef}details[open] summary{margin-bottom:16px}.footer{border-top:1px solid var(--line);padding-top:22px;margin-top:40px;font-size:11px;color:#8195af}.footer p{font-size:11px;margin-bottom:5px}.print-only{display:none}
@media(max-width:800px){main{padding:25px 16px 45px}.result-grid,.two-columns{grid-template-columns:1fr}.heading-row,.section-heading{display:block}.actions{margin-top:15px}.result-card{padding:20px}.trade-detail{grid-template-columns:1fr 1fr}.nav{gap:9px}.nav a{margin:0 7px 0 0}.panel{padding:13px}.card-policy{font-size:10px}}
@media print{body{background:white;color:#111;font-size:11px}main{padding:0;max-width:none}h1,h2,h3,b,strong{color:#111}.actions,.nav,.filters,.chart-controls,.no-print{display:none!important}.print-only{display:block}.result-card,.panel,.table-wrap,details{background:white;border-color:#bbb;break-inside:avoid}.result-card{background:white}.balance{font-size:28px}.label,p,.muted,.card-policy,.card-note,.mini-grid span,.footer{color:#444}.callout{background:#faf6e9;color:#554722}.callout p,.callout strong{color:#554722}th{background:#eee;color:#111}td,th{border-color:#ddd;padding:7px}.table-wrap{overflow:visible}table{font-size:8px}td,th{white-space:normal}.section{margin-top:25px}.negative{color:#a02030}.axis{fill:#444}.grid-line{stroke:#ddd}.stock-list span{color:#111;background:#eee;border-color:#bbb}details{display:block}.footer p{color:#555}}
</style></head><body><main>
<header><div class="heading-row"><div><div class="eyebrow">NEUER DURCHLAUF · KORRIGIERTER CODE 2.2</div><h1>5.000 € Aktien-Backtest</h1>
<p class="subheading">Deine aktuelle technische Strategie auf 5- und 15-Minuten-Charts. Vollständiger Ausstieg am ersten Take-Profit oder am Stop.</p></div>
<div class="actions"><button id="print-report" type="button">Drucken / als PDF speichern</button></div></div>
<div class="badges"><span class="badge emphasis">@@VERSION@@ · 10 Aktien · ohne KI</span><span class="badge">@@DATE_RANGE@@</span><span class="badge">Nur Käufe · keine Leerverkäufe</span><span class="badge">Kosten berücksichtigt</span></div></header>
<nav class="nav" aria-label="Berichtsabschnitte"><a href="#ergebnis">Ergebnisse</a><a href="#vergleich">Alt / Neu</a><a href="#kapital">Kapitalverlauf</a><a href="#trades">Trades</a><a href="#signale">Signalfilter</a><a href="#aktien">Einzelaktien</a><a href="#methode">Annahmen & Daten</a></nav>
<section id="ergebnis"><h2>Zwei Charts, zwei getrennte Konten</h2><p class="section-intro">Jeder Zeitrahmen startet unabhängig mit 5.000 €. Pro Konto wird höchstens eine Position gleichzeitig gehalten. Das gesamte verfügbare Kapital einschließlich Einstiegsgebühr wird eingesetzt; Gewinne und Verluste werden reinvestiert.</p>
<div class="result-grid">@@CARDS@@</div>
<div class="callout"><strong>Eine erste Diagnose, noch kein belastbares Qualitätsurteil.</strong><p>Im gesamten Test wurde nur ein Kauf ausgeführt. Auf 15 Minuten gab es keine Freigabe. Damit lässt sich die Profitabilität der Strategie noch nicht zuverlässig beurteilen. Der auffällige Befund ist die sehr geringe Handelsfrequenz.</p></div>
@@SUMMARY@@<p class="table-note">„—“ bedeutet: ohne Trades nicht berechenbar. Die Kennzahlen beruhen ausschließlich auf dieser kleinen historischen Stichprobe. Die beiden Konten werden nicht zusammengerechnet.</p></section>
<section class="section" id="vergleich"><h2>Alter und korrigierter Code im direkten Vergleich</h2><p class="section-intro">Beide Läufe verwenden exakt dieselben gesicherten Kursdaten, denselben Zeitraum und dieselben Kosten. Version 2.2 enthält die geprüften Indikator-Korrekturen. Die Einstiegsfilter und die Vorgabe „nur Käufe“ wurden beibehalten.</p>@@COMPARISON@@<div class="callout info"><strong>Mehr Freigaben bedeuten hier noch keine zusätzlichen Käufe.</strong><p>Der neue Lauf hat vier 5-Minuten-Szenarien statt zuvor zwei. Drei davon sind Verkaufsszenarien und werden ohne Leerverkäufe übersprungen. Es bleibt derselbe Palantir-Kauf; das Kontorergebnis ist deshalb unverändert. Auf 15 Minuten gibt es weiterhin keine Freigabe.</p></div></section>
<section class="section" id="kapital"><h2>Kapitalverlauf</h2><p class="section-intro">Bewertung nach jeder 1-Minuten-Kerze, einschließlich der Kosten eines Ausstiegs. Die gestrichelte Linie markiert das Startkapital.</p>
<div class="panel"><div class="chart-controls"><div class="legend"><label><input type="checkbox" data-curve="5m" checked><span class="dot"></span>5 Minuten</label><label><input type="checkbox" data-curve="15m" checked><span class="dot purple"></span>15 Minuten</label></div><span class="small muted">Mit der Maus über den Verlauf fahren</span></div>
@@CHART@@<div id="chart-readout" class="chart-readout" aria-live="polite">@@FINAL_READOUT@@</div><p class="chart-caption">Zeitachse: Wien (Europe/Vienna), einschließlich Nächten und Wochenende. Drawdown bezieht sich auf diese Minutenbewertungen; innerhalb einer Minute kann er höher sein.</p></div></section>
<section class="section" id="trades"><div class="section-heading"><div><h2>Alle ausgeführten Trades</h2><p class="section-intro">Aktienkurse und Gebühren in US-Dollar; Kontostand und Nettoergebnis in Euro. Alle Uhrzeiten sind Wiener Ortszeit.</p></div><button type="button" data-export="trades-table" data-filename="Aktien_Trades.csv">Trades als CSV</button></div>
@@TRADES@@<div class="panel">@@TRADE_DETAIL@@<p class="small muted">@@EXIT_DESCRIPTION@@</p></div>
<details><summary>Alle freigegebenen Kauf- und Verkaufsszenarien</summary><p class="small muted">@@SIGNAL_DESCRIPTION@@</p>@@CANDIDATES@@</details></section>
<section class="section" id="signale"><h2>Warum wurden so wenige Trades freigegeben?</h2><p class="section-intro">Die aktuelle Strategie verlangt eine gemeinsame Richtung beider Gruppen und ein geeignetes Ziel mit freiem Kursweg. Die Tabelle zählt je Aktie und abgeschlossener Hauptkerze den zuerst gemeldeten Ablehnungsgrund. Mehrere weitere Hürden können gleichzeitig bestehen.</p>
@@DIAGNOSTICS@@<div class="callout info"><strong>„Neutral“ entsteht nicht nur durch widersprechende Indikatoren.</strong><p>Auch ein fehlendes Ziel oder eine entgegenstehende Kurszone kann einen Einstieg verhindern. Zusätzliche Indikatoren lösen diese Hürden nicht automatisch.</p></div>
<p class="small muted">@@EXTRA_DIAGNOSTICS@@</p>
<details><summary>Ablehnungsgründe für jede einzelne Aktie</summary>@@DIAGNOSTICS_DETAIL@@</details></section>
<section class="section" id="aktien"><div class="section-heading"><div><h2>Alle Einzelaktien im Vergleich</h2><p class="section-intro">Jede Zeile ist ein separater Vergleichstest mit eigenen 5.000 €. Diese Kontostände dürfen nicht zum oben gezeigten gemeinsamen Aktienkonto addiert werden.</p></div><button type="button" data-export="individual-table" data-filename="Aktien_Einzeltests.csv">Tabelle als CSV</button></div>
<div class="filters"><label class="small" for="stock-search">Aktie suchen</label><input id="stock-search" type="search" placeholder="z. B. PLTR oder Palantir"><label class="small" for="timeframe-filter">Chart</label><select id="timeframe-filter"><option value="all">5 und 15 Minuten</option><option value="5m">5 Minuten</option><option value="15m">15 Minuten</option></select><span id="filter-count" class="filter-count">20 von 20 Zeilen</span></div>
@@INDIVIDUAL@@<p class="table-note">Coinbase (COIN) wurde als Aktie getestet. Es wurden keine Kryptowährungen, ETFs oder Währungspaare gehandelt.</p></section>
<section class="section" id="methode"><h2>So wurde gerechnet</h2><div class="two-columns"><div class="panel"><h3>Handelsregeln</h3><ul class="method-list">
<li><strong>Aktuelle Technik:</strong> dieselben korrigierten Indikator-, Prognose- und Validierungsfunktionen wie in Strategie @@VERSION@@. Die verwendeten Quelldateien und Kursdaten sind über Prüfsummen festgehalten.</li>
<li><strong>Zwei Gruppen:</strong> Trend & Marktstruktur sowie Momentum & Marktteilnahme müssen die Richtung gemeinsam bestätigen.</li>
<li><strong>Weitere Prüfungen:</strong> mindestens 1R, passende Mindest-Zielstrecke, ATR-Risikorahmen und alle kleineren POI-Timeframes: 1m für 5m; 5m und 1m für 15m.</li>
<li><strong>Warmup:</strong> 200 abgeschlossene Kerzen je benötigtem Zeitrahmen, bevor dieser im Replay verwendet wird.</li>
<li><strong>Einstieg:</strong> Signal nach Abschluss der Hauptkerze; frühestens am folgenden 1m-Eröffnungskurs, einschließlich Kosten.</li>
<li><strong>Ausstieg:</strong> 100 % an TP1 oder am ursprünglichen Stop. Kein TP2, kein Trailing. Ursprüngliche Level bleiben bestehen.</li>
<li><strong>Positionsgrenze:</strong> eine Position je Konto. Bei gleichzeitigen Signalen entscheidet die höchste technische Signalstärke, bei Gleichstand das Aktiensymbol.</li></ul></div>
<div class="panel"><h3>Kosten & Abrechnung</h3><ul class="method-list">
<li><strong>Gebühr:</strong> 0,05 % bei Einstieg und Ausstieg.</li><li><strong>Marktausführung:</strong> zusätzlich 0,05 % Slippage und 0,01 % halber Spread je Marktorder.</li>
<li><strong>TP1:</strong> modellierte Limit-Ausführung zum Zielkurs; dort fällt die Ausstiegsgebühr an.</li>
<li><strong>Euro-Konto:</strong> Umrechnung mit der zuletzt bereits abgeschlossenen täglichen EURUSD-Kerze. Das ist ein Abrechnungsproxy, kein exakter Intraday-Wechselkurs.</li>
<li><strong>Aktien:</strong> Bruchstücke sind erlaubt; keine Hebelung und keine Leerverkäufe im berichteten Lauf.</li>
<li><strong>Stop und Ziel in derselben Minute:</strong> der Stop zählt zuerst. Kurslücken am Stop werden zum schlechteren verfügbaren Eröffnungskurs berücksichtigt.</li>
<li><strong>Haltedauer:</strong> Übernachtpositionen sind erlaubt. Eine am Datenende offene Position wird geschlossen.</li></ul></div></div>
<details><summary>Datenabdeckung und getestete Aktien</summary><p class="small muted">Quelle: Yahoo Finance über die bestehende lokale App. Die Daten wurden vor dem Test gesichert. Gezeigt werden die bereits abgeschlossenen Kerzen des jeweiligen Schnappschusses; ein Teil dient nur als Vorgeschichte und Warmup.</p><div class="stock-list">@@STOCKS@@</div>@@COVERAGE@@<p class="small muted">Gemeinsamer ausgewerteter Zeitraum: <strong>@@START@@ bis @@END@@</strong> (Wien). Die verfügbaren 1m-Kerzen begrenzen den gemeinsamen Zeitraum; die längeren 5m-/15m-Vorgeschichten erweitern diesen nicht.</p></details>
<details><summary>Grenzen und Einordnung des Ergebnisses</summary><ul class="method-list"><li>Nur wenige Handelstage und ein ausgeführter Trade. Kein Langzeitnachweis, keine verlässliche Schätzung der zukünftigen Erfolgsquote.</li><li>Feste heutige Aktienauswahl aus deinem Projektkatalog. Keine rückwirkende Rekonstruktion eines damaligen Aktienuniversums.</li><li>Neubewertung einmal je abgeschlossener Hauptkerze. Zusätzliche Live-Neuberechnungen innerhalb der Kerze werden nicht rekonstruiert.</li><li>Die Minutenkerzen zeigen keine Tick-Reihenfolge. Der Stop gewinnt deshalb bei unklarer Reihenfolge.</li><li>Limit-Ausführung und Bruchstückhandel sind modelliert. Tatsächliche Liquidität, Warteschlangen, Steuern und ein vollständiges Broker-Kostenmodell sind nicht enthalten.</li><li>Keine KI, Nachrichten- oder Makrodaten in der Handelsentscheidung. Keine Suche nach historisch optimalen Parametern.</li><li>Dies ist eine historische Simulation; es wurden keine echten Orders erteilt.</li></ul></details></section>
<footer class="footer"><p>Ergebnisstand: @@GENERATED@@ (Wien) · Technische Strategie @@VERSION@@ · @@TEST_COUNT@@ Backend-Tests bestanden; die Handelsabrechnung wurde zusätzlich unabhängig nachgerechnet.</p><p>Dieser Bericht enthält alle Tabellen und Grafiken in einer einzigen Datei und funktioniert offline. Er zeigt den bereits ausgeführten Test; beim Öffnen wird kein neuer Test gestartet.</p></footer>
</main><script id="chart-data" type="application/json">@@PAYLOAD@@</script>
<script>
'use strict';
const plot=JSON.parse(document.getElementById('chart-data').textContent);
const currency=new Intl.NumberFormat('de-AT',{style:'currency',currency:'EUR'});
const localTime=new Intl.DateTimeFormat('de-AT',{timeZone:'Europe/Vienna',dateStyle:'short',timeStyle:'short'});
document.getElementById('print-report').addEventListener('click',()=>window.print());
document.querySelectorAll('[data-curve]').forEach(input=>input.addEventListener('change',()=>{
  document.getElementById('curve-'+input.dataset.curve).style.display=input.checked?'':'none';
}));
const chart=document.getElementById('equity-chart');
const overlay=document.getElementById('chart-overlay');
const crosshair=document.getElementById('crosshair');
const readout=document.getElementById('chart-readout');
function valueAt(points,stamp){let low=0,high=points.length-1;while(low<high){const middle=Math.ceil((low+high)/2);if(points[middle][0]<=stamp)low=middle;else high=middle-1;}return points[low][0]>stamp?5000:points[low][1];}
overlay.addEventListener('pointermove',event=>{
  const point=chart.createSVGPoint();point.x=event.clientX;point.y=event.clientY;
  const relative=point.matrixTransform(chart.getScreenCTM().inverse());
  const axis=Math.max(88,Math.min(1138,relative.x));const stamp=plot.begin+(axis-88)/1050*(plot.finish-plot.begin);
  crosshair.setAttribute('x1',axis);crosshair.setAttribute('x2',axis);crosshair.setAttribute('visibility','visible');
  readout.textContent=localTime.format(new Date(stamp))+' · 5 Min.: '+currency.format(valueAt(plot.curves['5m'],stamp))+' · 15 Min.: '+currency.format(valueAt(plot.curves['15m'],stamp));
});
overlay.addEventListener('pointerleave',()=>{crosshair.setAttribute('visibility','hidden');});
function filterStocks(){const query=document.getElementById('stock-search').value.trim().toLocaleLowerCase('de');const tf=document.getElementById('timeframe-filter').value;let visible=0;const rows=Array.from(document.querySelectorAll('#individual-table tbody tr'));rows.forEach(row=>{const show=(tf==='all'||row.dataset.timeframe===tf)&&row.textContent.toLocaleLowerCase('de').includes(query);row.hidden=!show;if(show)visible++;});document.getElementById('filter-count').textContent=visible+' von '+rows.length+' Zeilen';}
document.getElementById('stock-search').addEventListener('input',filterStocks);
document.getElementById('timeframe-filter').addEventListener('change',filterStocks);
document.querySelectorAll('[data-export]').forEach(button=>button.addEventListener('click',()=>{
  const table=document.getElementById(button.dataset.export);
  const lines=Array.from(table.rows).filter(row=>!row.hidden).map(row=>Array.from(row.cells).map(cell=>'"'+cell.textContent.trim().replaceAll('"','""')+'"').join(';'));
  const url=URL.createObjectURL(new Blob(['\uFEFF'+lines.join('\r\n')],{type:'text/csv;charset=utf-8'}));
  const link=document.createElement('a');link.href=url;link.download=button.dataset.filename;document.body.appendChild(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
}));
</script></body></html>'''
substitutions = {'CARDS':''.join(cards),'SUMMARY':summary,'CHART':chart,'TRADES':trades_table,
    'TRADE_DETAIL':''.join(trade_detail),'CANDIDATES':candidates_table,'DIAGNOSTICS':diagnostics,
    'DIAGNOSTICS_DETAIL':diagnostics_detail,'INDIVIDUAL':individual_table,
    'STOCKS':''.join(f'<span>{symbol} · {escape(NAMES[symbol])}</span>' for symbol in DATA['metadata']['symbols']),
    'COVERAGE':coverage_table,'START':date(DATA['metadata']['start']),'END':date(DATA['metadata']['end']),
    'GENERATED':date(DATA['metadata']['created_at']),'PAYLOAD':payload,
    'VERSION':escape(VERSION),'TEST_COUNT':str(validation.get('backend_tests','—')),
    'DATE_RANGE':date(DATA['metadata']['start'],False)+' – '+date(DATA['metadata']['end'],False),
    'COMPARISON':comparison_table,'FINAL_READOUT':escape(final_readout),
    'SIGNAL_DESCRIPTION':escape(signal_description),'EXTRA_DIAGNOSTICS':escape(extra_diagnostics),
    'EXIT_DESCRIPTION':escape(exit_description)}
for key,value in substitutions.items():
    page = page.replace('@@'+key+'@@',value)
assert '@@' not in page
OUT.write_text(page,encoding='utf-8')
print(OUT)
print(f'{OUT.stat().st_size:,} bytes; one self-contained HTML report.')
