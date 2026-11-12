# Lokale Vollversion · Technische Dokumentation

## Systemstatus

Nach dem Backendstart zeigt `http://127.0.0.1:8000/` eine kompakte Übersicht
zu Backend, Marktdaten und Analysebetrieb. Die Statusabfrage ist lesend und
startet weder Kursabrufe noch Analysen. Das Trading-Dashboard ist unter
`/trading` erreichbar. Build: `node backend/observatory/build.mjs`.

## Lokaler LightGBM-Filter für 5m und 15m

Der Bereich **Einstellungen → Quant & Daten** speichert Kursarchive, Modelle
und Prüfkennzahlen dauerhaft in SQLite. **Modelle aktualisieren** lädt
neue Historie und trainiert im Hintergrund. Bestehende Analysen verwenden gespeicherte
Modelle ohne erneutes Training oder zusätzliche Gemini-Anfragen. Nur Modelle mit
bestandener zeitlicher Prüfung können neue Einstiege bei deutlicher Gegenrichtung
sperren. Die Modellprüfung ersetzt keinen Strategie-Backtest. Details: [LIGHTGBM.md](../LIGHTGBM.md).

Aktuelle Bedienung, Datenübersicht und Budgetverteilung: [DASHBOARD-UPDATE.md](../DASHBOARD-UPDATE.md). Die frühere handgewichtete Quant-Bewertung ist im Live-Betrieb abgeschaltet.

<details>
<summary>Archivierte Anleitung der früheren Quant-Bewertung</summary>

## Manuelle Quant-Unterfunktion von Gemini

**Quant ist standardmäßig ausgeschaltet und ausschließlich im Gemini-Modus verfügbar.**
Der Schalter speichert nur die Auswahl. Erst **Quant berechnen + Gemini analysieren**
aktualisiert die Kennzahlen und übergibt sie an Gemini. Ein per Knopfdruck gestarteter
Marktscan macht dies bei eingeschaltetem Quant für jedes verglichene Instrument.
Normale Seitenaktualisierungen, Chartwechsel und Quote-Nachrichten starten keine
Quant-Berechnung. Bereits gespeicherte aktuelle Werte können wiederverwendet werden.
Bei einer neuen oder korrigierten Hauptkerze wird der gespeicherte Stand als veraltet
angezeigt, bis die Berechnung erneut ausdrücklich gestartet wird.

Neben den zwei Indikatorgruppen zeigt eine dritte Karte Richtung und Score,
Delta-Schätzung, CVD mit seinem gespeicherten Anfangszeitpunkt, die CVD-Veränderung
über 20 Hauptkerzen, Z-Score, Marktregime, Volatilität und relative Stärke.
Z-Score und Marktregime sind Kontext; ihre Schwellen und alle Gewichte sind
heuristische Startwerte, keine statistisch belegten Gewinnwahrscheinlichkeiten.

**Richtungsbewertung ab Version 4.2:** Die Zeitrahmen verwenden transparente,
noch nicht empirisch kalibrierte Startprofile:

| Timeframe | Technik einschließlich möglichem Quant-Anteil | Wirtschaftsdaten | Nachrichten |
|---|---:|---:|---:|
| 1m / 5m / 15m | 70 % | 10 % | 20 % |
| 30m / 1h | 60 % | 15 % | 25 % |
| 4h / 1D | 50 % | 25 % | 25 % |

Explizite `DECISION_WEIGHTS_JSON`-Werte haben weiterhin Vorrang und gelten dann
für alle Zeitrahmen. Quant erhält innerhalb des Technikbudgets maximal **12 % der
Gesamtbewertung**, höchstens 40 % des konfigurierten Technikanteils. 12 % entsprechen
dem bisherigen reinen Volumenanteil (20 % Quant mal 60 % Volumen); die doppelten
Preisanteile gehen zurück an die Technik. Vollständige aktuelle Volumendaten sind
Pflicht. Weniger als durchschnittlich fünf Quellkerzen je Hauptkerze reduzieren
den freigegebenen Quant-Anteil proportional: bei 1m/1m auf 2,4 %, bei 4h/1h
normalerweise auf 9,6 %. Das ist eine transparente Auflösungsregel und keine
gemessene Trefferwahrscheinlichkeit.

Quant bewertet ausschließlich zwei **nicht überlappende Volumenfenster**:
letzte drei Hauptkerzen 25 %, vorherige 17 Hauptkerzen 75 %. Kleine Volumenfenster
werden gegen die durchschnittliche Aktivität der 20 Kerzen gedämpft. Die letzte
Einzelkerze wird weiterhin angezeigt, erhält aber keine eigene Zusatzstimme.
Momentum, relative Stärke, Z-Score und Marktregime bleiben Preis-Kontext.
Relative Stärke wird anhand der tatsächlich beobachteten Benchmark-Renditen
skaliert; die feste 2-%-Skala entfällt.

Bei aktivem Quant werden VWAP und OBV/CMF/MFI aus den technischen Richtungsgruppen
für Gemini entfernt. Diese Gruppen und der Quant-Block teilen sich dadurch keine
direkte Volumenstimme. Volumenprofil und Kurszonen bleiben für die Levelwahl nutzbar.
BUY benötigt einen gewichteten Score ab 55, SELL höchstens 45; dazwischen gibt es
keine neue Trade-Freigabe, unabhängig von der getrennten KI-Konfidenz.

Fehlendes Volumen behält das Merkmal `VolumeKnown=false`; ein numerischer
Kompatibilitätswert null wird nicht als beobachteter Nullumsatz ausgegeben.
Betroffene Volumenindikatoren und Quant-Fenster werden nicht vollständig gewertet.
SQLite ergänzt dazu `volume_known`; vorhandene Quellkerzen und Archive bleiben
erhalten. Alte, noch nicht erneut geprüfte Quellzeilen gelten konservativ als
unbekannt, bis ein manueller Abruf sie bestätigt. Gleiche bestätigte Eingaben
werden weiterhin wiederverwendet.

**Datenmethode:** Der vorhandene Yahoo-OHLCV-Feed liefert keine Aggressor-Seite.
Delta und CVD sind deshalb ausdrücklich **Schätzungen aus Kerzenrichtung und
Quellvolumen**, keine gemessenen aggressiven Käufe/Verkäufe. Steigende Quellkerzen
zählen positiv, fallende negativ. Bei gleichem Open/Close entscheidet der Vergleich
zum vorigen Close; bei erneutem Gleichstand bleibt das vorige Vorzeichen bestehen.
Ein völlig unbekannter Anfang bleibt neutral. Die
[TradingView-Methodenbeschreibung](https://www.tradingview.com/support/solutions/43000725057-volume-delta/)
beschreibt diese Art der Näherung. CVD wird ab dem angezeigten Datenanker fortgeführt;
sein absolutes Niveau ist zwischen Instrumenten oder unterschiedlichen Datenankern
nicht vergleichbar. Für die Richtung zählt seine Veränderung über 20 Hauptkerzen.

| Haupt-Timeframe | Quellkerzen für die Schätzung |
|---|---|
| 1m | 1m, ausdrücklich gröbere Chartkerzen-Schätzung |
| 5m / 15m | 1m |
| 30m / 1h | 5m |
| 4h / 1D | 1h |

Nur abgeschlossene Quellkerzen innerhalb der jeweiligen Hauptkerze zählen.
Mindestens 21 Hauptkerzen und 20 zeitlich lückenlos abgedeckte Hauptintervalle
mit nutzbarem Quellvolumen sind für einen Richtungsscore nötig. Delta/CVD verwenden
durchgehend das beobachtete Volumen derselben kleineren Quellkerzen. Yahoo kann
zwischen Zeitrahmen unterschiedliche Volumensummen liefern; solche Abweichungen
werden angezeigt und nicht durch Skalierung korrigiert. Verkürzte Tages- oder
4h-Handelssitzungen benötigen zusammenhängende Quellkerzen sowie passende Volumina
und tatsächliche Open-/Close-Grenzpreise. Fehlende Kerzen werden nicht aus einer alten
Antwort stillschweigend ergänzt. Fehlendes Volumen und veraltete Daten erhalten
kein Quant-Entscheidungsgewicht. Relative Stärke verwendet nur exakt zeitlich
passende Kerzen; NASDAQ-Aktien vergleichen mit QQQ, andere Aktien/QQQ mit SPY.
Krypto und Gold erhalten ohne geeigneten Vergleich keinen erfundenen Benchmark.

SQLite speichert Auswahl, Versionskennung, verarbeitete Quellkerzen mit Vorzeichen,
Delta und laufendem CVD sowie den letzten Stand und archivierte Snapshots.
Unveränderte Quellkerzen werden wiederverwendet. Neue Kerzen werden angehängt;
nach Datenkorrekturen wird erst ab der betroffenen Stelle neu gerechnet. Die kleinen
Statistikfenster werden nur bei geänderten Eingaben auf ausdrücklichen Aufruf
aktualisiert. Alles bleibt über Neustarts erhalten. Ein fehlgeschlagener manueller
Abruf hält den alten Stand als veraltet, bis ein erfolgreicher neuer Aufruf vorliegt.
Es gibt dafür keinen Backtest und keine zusätzliche Gemini-Anfrage nur für Quant:
die Kennzahlen gehen in die bestehende Gesamtanalyse.

API: `GET/POST /api/quantitative/settings` liest/speichert `{"enabled":true|false}`.
`POST /api/quantitative/calculate?ticker=NVDA&timeframe=15m&revision=1` startet
Berechnung und Gemini-Gesamtanalyse. Die aktuelle Revision kommt aus den Settings;
überholte Anfragen werden mit HTTP 409 abgelehnt. Der Python-Modus berechnet,
verwendet und zeigt diesen Quant-Block nicht an.


</details>

## Historischer Aktien-Backtest mit korrigierter Technik 2.2

`backend/technical_backtest.py` verwendet die aktuellen Indikator-, Prognose-
und Validierungsfunktionen von `2.2-indicator-fixes`. `backend/run_stock_backtest.py`
testet 5m und 15m mit je einem unabhängigen Konto von 5.000 EUR. Es werden nur
Käufe aus dem Aktienkatalog ausgeführt, höchstens eine offene Position, mit dem
gesamten verfügbaren Kapital einschließlich Gebühr. TP1 oder der ursprüngliche
Stop schließen die komplette Position; es gibt kein TP2 und keine echten Orders.

```powershell
python backend/run_stock_backtest.py
python backend/check_suite.py
python create_html_report.py
```

Die mitgelieferten Kursschnappschüsse entsprechen exakt dem Vergleichslauf
der alten Version. Neue Daten können bei laufender App über
`python backend/download_backtest_data.py` gesichert werden. Ein fairer
Alt-/Neu-Vergleich benötigt identische Kursdaten und denselben Zeitraum.

Im Python-Modus lädt `/api/analysis` echte gespeicherte Einzelaktien-Ergebnisse
aus `backtest_results`. Versionsnummer, Quell-Prüfsummen, Aktie, Timeframe,
Kapital und Kosten müssen passen. Ein Ergebnis des alten Codes wird nicht als
aktueller Backtest ausgegeben. Neu gespeicherte Ergebnisse werden auch innerhalb
derselben Analysekerze neu gelesen. Für Gemini wird weiterhin keine historische
KI-Entscheidung erfunden.

Ergebnisse: `backtest_results/BERICHT.md`, `results.json`, `trades_5m.csv`,
`trades_15m.csv` und der eigenständige HTML-Bericht. Die 1m-Historie begrenzt
den Zeitraum; 200 Kerzen jedes benötigten Timeframes dienen als Warmup.
Signale werden je Hauptkerzenschluss geprüft und am nächsten 1m-Open ausgeführt.
Gebühren, modellierte Slippage/Spread und die Euro-Umrechnung sind berücksichtigt.

Lokales Dashboard für technische Aktien-, ETF-, Krypto- und Goldanalyse. React + TypeScript + Vite, KLineChart, FastAPI, SQLite und eine gemeinsame Gemini-Auswertung von Technik, Makrodaten und Nachrichten. Die Plattform analysiert; sie sendet keine Broker-Orders.

## Installation und Start unter Windows

Getestet mit Node.js 24.13 und Python 3.13. Node.js 24 LTS wird empfohlen; der native Vite-Konfigurationsloader benötigt mindestens Node 22.18.

1. Backend-Abhängigkeiten installieren (einmalig):

   ```powershell
   cd backend
   python -m venv venv
   .\venv\Scripts\python.exe -m pip install -r requirements.txt
   ```

2. Frontend-Abhängigkeiten installieren (einmalig):

   ```powershell
   cd frontend
   npm ci
   ```

3. Im Projektordner `start_backend.bat` und `start_frontend.bat` öffnen. Fehlende Umgebungen/Abhängigkeiten werden beim ersten Start angelegt.
4. Dashboard: http://localhost:5173 · API: http://127.0.0.1:8000/docs

Beide Dienste binden standardmäßig an den eigenen Computer. Bei abweichenden Ports `FRONTEND_ORIGINS` und gegebenenfalls `VITE_API_BASE_URL` in den jeweiligen Umgebungsdateien anpassen.

## Konfiguration

`backend/.env.example` nach `backend/.env` kopieren und einen eigenen `GEMINI_API_KEY` eintragen. Ohne Schlüssel oder bei API-Ausfall werden Daten und Keyword-Auswertung weiterhin angezeigt, es entsteht aber ausdrücklich **kein Ersatz-Trade**. Pro Instrument und abgeschlossener Kerze werden technische Analyse, strukturierte Wirtschaftsdaten und bis zu 30 lokal priorisierte Artikel gemeinsam in einem Gemini-Aufruf ausgewertet. Der gesamte Kontext samt Ergebnis wird in SQLite archiviert und bei identischem Kontext wiederverwendet. Die KI erhält keine Such- oder Browsing-Werkzeuge. Ein kompletter Scan kann bei leerem Cache bis zu 16 Gemini-Anfragen auslösen. Aktuelle Kosten und Verfügbarkeit beim eigenen Anbieter prüfen.

Ein anderes Modell ist mit `GEMINI_MODEL` konfigurierbar. API-Schlüssel bleiben ausschließlich im Backend. Konfigurationsdateien, Datenbank, virtuelle Umgebung und Abhängigkeiten werden von der Versionskontrolle ausgeschlossen.

## Manueller Analysemodus

Oben im Dashboard stehen **Gemini + News** und **Python · nur Technik**. Ausschließlich ein Klick auf diese Auswahl ändert den Modus; API-Limits, Timeouts oder ein wieder erreichbarer KI-Dienst lösen keinen automatischen Wechsel aus. Die Auswahl wird in SQLite gespeichert und bleibt nach einem Neustart erhalten. Sie gilt auch für den Scanner. `GET /api/analysis-mode` liest die Auswahl, `POST /api/analysis-mode` mit `{"mode":"python"}` beziehungsweise `{"mode":"gemini"}` ändert sie ausdrücklich.

Im Python-Modus werden nur OHLCV-Daten und technische Indikatoren geladen. Es gibt keine Gemini-, RSS- oder Wirtschaftsdatenauswertung. Der Live-Python-Modus verwendet `technical_forecast.py` mit Version `2.2-indicator-fixes`.

### Indikator-Korrekturen in Version 2.2

Neue Strukturereignisse werden für beide Richtungen gleich priorisiert; gleichzeitige beidseitige Sweeps bleiben neutral. Gleiche oder fehlende DMI-Werte erzeugen keine Verkaufsstimme. Ohne gerichtete Bewegung wird ADX nach seinem Warmup zu null, statt dauerhaft zu fehlen. VWAP verwendet den Kalendertag der Börsenzeitzone, und MFI benötigt 14 tatsächlich beobachtete Preisvergleiche.

Orderblocks werden nach Ursprungszone dedupliziert; der erste gültige 20-Kerzen-Ausbruch wird berücksichtigt. Das approximative Volumenprofil bleibt in der beobachteten Kursspanne und erweitert seine Value Area zusammenhängend vom POC aus. Nicht verfügbare Supertrend-Werte werden neutral angezeigt. Fehlender Tages-VWAP wird von fehlendem Volumen unterschieden.

Die Gruppengewichte, Schwellen, Mindestzielstrecken und POI-Blockierregeln bleiben bestehen. Historische Ergebnisse der Version 2.1 sind keine Backtestergebnisse dieser korrigierten Version.

## Technischer Ausblick ab aktuellem Kurs

Die Python-Analyse beschreibt ein technisches Szenario ab dem **aktuell beobachteten Kurs**. Richtung, Hauptziel und Invalidation stammen aus den abgeschlossenen Kerzen des gewählten Timeframes. Es werden keine künftigen Warte-Entries gesucht. Die Oberfläche zeigt **AKTUELL**, **ZIEL** und **UNGÜLTIG AB**. Kursziel und Indikatorstärke sind keine gemessene Trefferwahrscheinlichkeit.

Das Hauptziel ist die erste vorhandene Kurszone, deren Abstand die Mindeststrecke des gewählten Timeframes erfüllt. Die Mindeststrecke verdoppelt sich pro unterstütztem Timeframe-Schritt: **1m 0,15 %, 5m 0,3 %, 15m 0,6 %, 30m 1,2 %, 1h 2,4 %, 4h 4,8 %, 1D 9,6 %**. Sie ist eine Filterbedingung, keine Gewinnprognose. Fehlt ein passendes echtes Kurslevel, wird kein Ziel am Mindestprozentsatz erfunden. Bei FVGs und Orderblocks zählt die zuerst erreichte Zonenkante.

Nähere Gegenzonen werden als Hindernisse geprüft. Gegen-POIs im Haupt-Timeframe blockieren eine Trade-Freigabe. Für ein vorhandenes Hauptziel werden außerdem **alle unterstützten kleineren Timeframes** separat geladen, bei 4h also 1h, 30m, 15m, 5m und 1m. Nur zum Zeitpunkt des Bezugskurses bereits abgeschlossene Kerzen werden verwendet. Ausfälle, veraltete Daten oder ein falscher Kerzen-Timeframe machen die Prüfung unvollständig und verhindern die Freigabe. Neue untergeordnete POIs verändern den archivierten Kontext auch dann, wenn die letzte Hauptkerze gleich bleibt.

Die Relevanz kleinerer POIs wird transparent heuristisch gewertet: Eine Quell-ATR oder Zonenbreite ab **0,25 ATR des Haupt-Timeframes** gilt als wesentlich; überlappende Gegenzonen verschiedener Timeframes ebenfalls. Wesentliche POIs vor dem Hauptziel blockieren den Trade. Einzelne kleinere Zonen bleiben als Warnungen sichtbar. Unterstützende, bereits gefüllte/invalidierte oder erst hinter dem Hauptziel liegende Zonen blockieren diesen Weg nicht. Die Oberfläche zeigt Mindeststrecke, geprüfte Timeframes, Datenlücken und die erkannten Hindernisse.

Zusätzlich gelten weiterhin mindestens **1R**, der ATR-Risikorahmen, frische Daten und ein geöffneter Markt. Ein technisch passendes Hauptziel kann als nicht freigegebenes Szenario sichtbar bleiben. Weitere technische Zonen sind nur Information und werden nicht als ungeprüftes TP2 in das Handelssignal übernommen.

Indikatoren und FVGs stammen weiterhin aus abgeschlossenen Analysekerzen. Die letzte Quote dient als Bezugspunkt. Die Invalidation basiert auf einer vorhandenen technischen Zone mit 0,25 ATR Puffer. Bei nahe beieinander liegenden FVGs f?r diese Grenze bleiben die Impuls-/Gr??enregeln erhalten: innerhalb 0,25 ATR zus?tzlicher Entfernung zuerst die st?rkere mittlere Impulskerze, bei innerhalb 10 % ?hnlichen Werten die gr??ere L?cke und anschlie?end bearish die h?here beziehungsweise bullish die tiefere Zone.

`GET /api/analysis` liefert im Python-Modus `forecast` und `forecast_history`. Kursupdates ?ber `/ws/quotes/{ticker}` enthalten ebenfalls den neu berechneten Ausblick. `GET /api/forecasts/history?ticker=META&timeframe=30m` liest die archivierten Ausblicke. Die zus?tzliche Tabelle `technical_forecast_snapshots` speichert alle Ausblicke einschlie?lich solcher unter 1R; identischer Kurs-/Kerzenkontext wird dedupliziert. Bestehende Tabellen bleiben erhalten. Die bisherigen `decision`-Felder bleiben als Kompatibilit?tssnapshot verf?gbar; ein dortiger Entry ist ausschlie?lich ein Alias des beobachteten Bezugskurses und kein berechneter Warte-Entry.

Alte, noch **VORBEREITETE Python-Pl?ne** werden vor einer neuen Analyse beendet und mit `RETIRED_FOR_CURRENT_PROJECTION` archiviert, damit sie nicht sp?ter als wartender Entry aktiviert werden. Bestehende aktive Altpl?ne behalten ihre urspr?nglichen Level und ihre Kurs?berwachung. Neue Ausblicke erzeugen keine vorbereiteten Signalzust?nde. Das fr?here Entry-Verfahren in `technical_strategy.py` bleibt nur f?r historische Kompatibilit?t und gemeinsame Zonenfunktionen erhalten; es ist nicht der Live-Python-Analysepfad.

Gemini verwendet weiterhin seine bisherige Handelsplan- und Signalverwaltung, die Mindestgrenze von 1,5R und die maximale Entry-Entfernung von 2 ATR. Der Moduswechsel bleibt ausschlie?lich manuell; eine neue technische Analyse aktiviert Gemini nicht automatisch.

## Wirtschafts- und Nachrichtendaten im Gemini-Modus

- Harte US-Wirtschaftsdaten kommen ohne API-Schlüssel aus den offiziellen Schnittstellen von BLS und FRED. Enthalten sind Inflation, Kerninflation, Arbeitslosen- und Erwerbsquote, Beschäftigung, Stundenlöhne, Fed-Zielkorridor, zehnjährige Treasury-Rendite, reales BIP, Core PCE und Einzelhandelsumsätze.
- Die Rohwerte, Vorwerte, Veränderungen, Perioden, Quellen und vorläufigen Kennzeichnungen werden in SQLite gespeichert. Der Live-Abruf wird standardmäßig sechs Stunden zwischengespeichert; bei einem Anbieterausfall bleiben die zuletzt gespeicherten Werte sichtbar und werden als veraltet markiert.
- Normale instrumentbezogene Meldungen kommen aus Google-News- und Yahoo-Finance-RSS. Das Backend liest den Feed, prüft zuerst die Zeitstempel und übernimmt dann je Quelle bis zu 30 Artikel aus den letzten 72 Stunden. Die Google-Suche begrenzt bereits an der Quelle auf drei Tage. Nach Deduplizierung und lokaler Priorisierung gehen bis zu 30 Kandidaten in den gemeinsamen Kontext. Die Oberfläche unterscheidet Feed-Treffer, Kandidaten, tatsächlich von Gemini bewertete und relevante Artikel.
- `news_feed_snapshots` speichert den letzten erfolgreichen Abruf je Instrument und Quelle. Bei Verbindungsfehlern oder HTTP 5xx gibt es einen begrenzten Wiederholungsversuch. Fehlschläge leeren keine gespeicherten Artikel; noch im 72-Stunden-Fenster liegende Daten werden mit Herkunft und Alter als Ersatz verwendet. Fehlerhafte Quellen werden nach 15 Sekunden erneut prüfbar, erfolgreiche Abrufe nach fünf Minuten. HTML-Fehlerseiten, leere RSS-Feeds und Anbieterfehler haben unterschiedliche Statusanzeigen.
- BLS/FRED liefern veröffentlichte Ist- und Vorwerte, aber keine Markt-Konsensprognosen. Fehlende Prognosewerte werden weder vom Backend noch von der KI erfunden.

## Marktdaten und Gold

- OHLCV-Kerzen werden von Yahoo Finance abgefragt und etwa alle 25 Sekunden aktualisiert. Diese Abfrage ist kein garantierter Echtzeit-Börsenfeed. Quelle, Zeitstempel, möglicher Verzug und geschlossener Markt werden im Dashboard angezeigt.
- Zeitrahmen: 1m, 5m, 15m, 30m, 1h, 4h und 1D. 4h-Kerzen entstehen aus echten Stundenkerzen, getrennt nach dem lokalen Kalendertag und verankert an dessen erster verfügbarer Kerze.
- **Gold / XAUUSD verwendet GC=F als eindeutig gekennzeichneten Gold-Futures-Proxy.** Es handelt sich nicht um einen Spot-XAU/USD-Kurs. Ein exakter Spot-Feed benötigt einen dafür geeigneten Datenanbieter. Das optionale TradingView-Widget zeigt Gold über OANDA:XAUUSD und verwendet einen eigenen Feed.
- Preise und Volumen werden nicht erfunden. Bei fehlendem Volumen entfällt der VWAP-Beitrag; Fehler des Datenanbieters führen zu einer verständlichen Fehlermeldung.
- Analysen verwenden abgeschlossene Kerzen. Tageskerzen werden konservativ bis zum nächsten lokalen Tagesbeginn ausgeschlossen, außer ein passender Handelsschluss ist in den Metadaten bekannt. Der Chart darf die laufende, noch unvollständige Kerze anzeigen.

## Signale und Risikoboxen

Die technischen Daten werden in zwei unabhängig gewichtete Familien zusammengefasst. **Trend & Marktstruktur** enthält EMA/SMA, Ichimoku, Supertrend, ADX/DMI, Swing-Struktur, BOS, Liquidität, FVGs und heuristische Orderblocks. **Momentum & Marktteilnahme** enthält RSI, Stochastic RSI, MACD, ROC, CCI, Williams %R, VWAP, OBV, CMF und MFI. Stark verwandte Werte zählen innerhalb ihrer Familie und werden nicht als viele unabhängige Bestätigungen behandelt. ATR, Bollinger, Keltner, realisierte Volatilität und Volumenprofil bilden einen separaten Risikokontext.

- Gemini erhält beide Familien mit allen Einzelwerten, Risikozonen, letzten 20 Kerzen, harten Daten und gefilterten Nachrichten in einem Aufruf. Gemini entscheidet BUY, SELL oder ABWARTEN und bestimmt Entry-Zone, Stop, TP1 und TP2 anhand konkreter Struktur-, FVG-, Orderblock-, Liquiditäts-, Volumenprofil- und Volatilitätszonen.
- Im Gemini-Modus erzeugt Python keine Richtung und keine Kurslevel. `decision_engine.py` prüft positive/endliche Zahlen, Level-Geometrie, Richtungskonsistenz, mindestens 1,5R und ATR-Plausibilität. Ein abgelehnter neuer Vorschlag löscht kein vorhandenes Setup; die aktuelle Prüfung wird dann als nicht verwertbar markiert. Ausschließlich im manuell gewählten Python-Modus generiert `technical_strategy.py` technische Pläne.
- TP1 und TP2 sind keine festen Vielfachen des Stop-Abstands. Gemini muss die verwendeten Kurszonen in `level_basis` benennen.
- ATR und RSI nutzen Wilder-Glättung. Intraday-VWAP startet pro lokalem Kalendertag neu; für Tageskerzen wird kein intraday-VWAP vorgetäuscht.
- Im AI Chart liegt die grüne Fläche zwischen Entry und TP2, in zwei unterschiedlich hervorgehobenen Bändern für TP1/TP2. Die rote Fläche reicht vom Entry bis zum Stop. SELL-Setups sind entsprechend umgekehrt. Beide Flächen bleiben an native Kurs-/Zeitkoordinaten gebunden.
- Der Chart zeigt fünf EMAs, Volumen, simulierte BUY/SELL-Einstiege und Nachrichtenmarker. Die TradingView-Ansicht ist getrennt und erhält denselben ausgewählten Zeitrahmen.

## Persistente Signalverwaltung (4.1-ai)

`signal_manager.py` speichert pro Instrument und Timeframe einen eigenständigen Zustand samt ursprünglichem Gemini-Plan. Richtung und Stärke werden getrennt vom Lebenszyklus angezeigt: `STRONG_BUY`, `BUY`, `WEAK_BUY`, `NEUTRAL`, `WEAK_SELL`, `SELL`, `STRONG_SELL` sowie `VORBEREITET`, `AKTIV`, `BEENDET`, `ABGELAUFEN`.

- Ab 80 Punkten gilt ein Signal als stark, ab 65 als normal, ab 55 als schwach. Bei 45–54 wird die vorherige Richtung vorläufig gehalten (`HOLD_PRE`). Eine ausdrücklich abgeschwächte Gemini-Bewertung begrenzt die verwaltete Signalstärke auf 64; die unveränderte KI-Konfidenz wird zusätzlich gespeichert und angezeigt.
- Gemini erhält den bisherigen Plan einschließlich Original-Leveln, Konfidenz, Zustand und Makro-Baseline. Ein neutraler neuer Einstiegsvorschlag kann den alten Plan abschwächen, ohne ihn zu löschen. Entry, Stop und Ziele werden nicht stillschweigend durch neue Level ersetzt.
- Zwei aufeinanderfolgende abgeschlossene Analysekerzen mit Gemini-Gegenbestätigung oder mit Konfidenz für die bisherige Richtung unter 45 beenden das Setup. Abfragen derselben Kerze zählen höchstens einmal. Fehlgeschlagene/abgelehnte Analysen und veraltete Kurse erzeugen keine Gegenbestätigung. Nicht analysierte Zwischenkerzen werden nicht nachträglich als KI-Bestätigungen erfunden.
- Sofortige Beendigung bei gemeldeter technischer Invalidation, validiertem starkem Gegenvorschlag mit mindestens 80 Konfidenzpunkten oder von Gemini bewerteter starker Makro-Gegenwirkung auf tatsächlich geänderte offizielle Daten.
- Stop/Invalidation und Ziele werden anhand verfügbarer Kurse überwacht, auch über die Quote-Verbindung ohne Gemini-Aufruf. TP1 wird markiert; TP2 beendet das Setup. Bei unbekannter Reihenfolge von Stop und Ziel innerhalb einer OHLC-Kerze gilt konservativ der Stop. Es erfolgt keine Broker-Ausführung; verzögerte Kurse garantieren keine Tick-genaue Überwachung.
- Gemini kann Chancen vor Erreichen der Entry-Zone vorbereiten: LIMIT, STOP oder Einstieg nach ausdrücklich bestätigter Bedingung. Eine Bedingung wie eine Rejection-Kerze bestätigt ausschließlich Gemini. Bei geplanten STOP-Ausbrüchen kann eine separate Invalidation während der Wartephase nötig sein. Vor der Erstellung liegende Kerzenextreme aktivieren keinen neuen Plan.
- Gemini legt die Gültigkeit auf 1–100 abgeschlossene Kerzen fest (Kompatibilitätsstandard 12). Beendete/abgelaufene Setups werden in derselben Analysekerze nicht wieder eröffnet. Bei API-Ausfall bleibt das letzte Setup mit ausstehender Prüfung erhalten.

SQLite-Tabellen `signal_states` und `signal_events` speichern Zustand, Zeitpunkte, Original-Level, Konfidenz, Zähler und Ereignisse; alte Tabellen bleiben erhalten. Die Oberfläche zeigt die jüngste Historie; `GET /api/signals/history?ticker=BTC-USD&timeframe=5m&limit=30` liefert vollständige Ereignissnapshots. Scans und Einzelansichten verwenden dieselbe Verwaltung.

## Feste Makrogewichte und individuelle Nachrichtenbewertung

Die Startanteile richten sich nach dem Timeframe; siehe die Tabelle zur Quant-Unterfunktion. `DECISION_WEIGHTS_JSON` überschreibt diese Profile ausdrücklich mit den drei Schlüsseln `technical`, `hard_data`, `normal_news` und muss insgesamt 100 ergeben. Es handelt sich um konfigurierbare Startprioritäten, keine statistisch belegten Erfolgswahrscheinlichkeiten.

Innerhalb des Makroblocks gelten feste Einzelgewichte (zusammen 100): Fed-Unter-/Obergrenze je 9, Treasury-Rendite 12; CPI 12, Core CPI 8, Core PCE 10; Beschäftigung 14, Arbeitslosenquote 5, Erwerbsquote 1, Löhne 5; reales BIP 10, Einzelhandel 5. Damit entfallen 30 % auf Zinsen, 30 % auf Inflation, 25 % auf Arbeitsmarkt/Löhne und 15 % auf Wachstum. `MACRO_WEIGHTS_JSON` erlaubt Anpassungen bei gleichen Schlüsseln und einer Summe von 100. Fehlende/veraltete Reihen wirken neutral; andere Gewichte steigen dadurch nicht künstlich an.

Gemini beurteilt die Wirkung jeder harten Datenreihe für das Instrument. Normale Artikel erhalten von Gemini jeweils Wichtigkeit (0–100), Wirkung, Relevanz, Neuigkeit, Begründung, Wirkungshorizont und Ereignisschlüssel. Gleiche Ereignisse zählen nur einmal mit dem stärksten relevanten Artikel. Python berechnet die feste gewichtete Summe aus Geminis Einzelurteilen exakt; die ursprünglich gemeldete KI-Summe bleibt zusätzlich archiviert. Eine falsche Addition verwirft keinen ansonsten gültigen Plan. Eine Handelsrichtung, die der gewichteten Richtung widerspricht, wird weiterhin abgelehnt. Python erzeugt keine Kurslevel und keine Handelsrichtung.

Bei Nachrichten bleibt die absolute Stärke erhalten: Ereignisstärke =
Wichtigkeit/100 × Relevanz × Neuigkeit. Der News-Score lautet
50 + 50 × gewichtete Sentimentrichtung × stärkste Ereignisstärke.
Eine schwache einzelne Meldung bleibt so nahe neutral; viele schwache oder
doppelte Meldungen erhöhen die Stärke nicht allein durch ihre Anzahl.
Fehlende oder nicht endliche Bewertungszahlen werden nicht als starke Evidenz genutzt.

Normale neue Schlagzeilen erzeugen innerhalb derselben Analysekerze keinen weiteren Gemini-Aufruf. Die Erholung einer zuvor fehlenden News-Quelle und geänderte offizielle Wirtschaftsdaten erlauben eine zusätzliche Prüfung, zählen aber keine weitere Gegenkerze. Kontext und Gewichtsversion werden persistent zwischengespeichert. Bei ungültigen Kursleveln ist höchstens eine gezielte Plan-Nachbesserung mit den bereits geladenen Daten erlaubt. Gemini erhält dabei keine Suchwerkzeuge. Ein kompletter Scan kann daher zusätzlich zu den Kontextanfragen einzelne kleine Nachbesserungsanfragen auslösen.

Die Vollständigkeit aller Artikel-IDs und Makrodatenreihen wird lokal geprüft. Fehlende Einzelbewertungen werden höchstens einmal mit einer gezielten Bewertungsanfrage ergänzt. Bleibt das Ergebnis unvollständig, wird es als KI-Fehler gekennzeichnet. Keyword-Vorsortierung wird weder als KI-Bewertung noch als KI-Relevanz gezählt. Das kompatible Anbieterschema bleibt von den strengeren lokalen Vollständigkeitsprüfungen getrennt.

Gemini soll zukünftige Pullback-/Ausbruchs-Einstiege an EMA, Orderblocks, FVGs und Widerstands-/Unterstützungszonen aktiv prüfen. Fehlende Berührung allein ist kein NEUTRAL-Grund. Eine Rejection, ein Kerzenschluss oder Retest erfordert eine ausdrücklich von Gemini bestätigte Bedingung. Ein vor der KI-Antwort erfasster Kurs aktiviert keinen neu erstellten Plan rückwirkend. Die Anzeige trennt KI-Ausfälle, ungültige Vorschläge und echte Abwarten-Entscheidungen; ohne vorhandenes Setup werden keine Lebensdauer-/Gegenzähler angezeigt.

## Opportunity Scanner

Zeitrahmen und Mindest-Signalstärke wählen, dann **Markt scannen**. Der Scan vergleicht 16 ausdrücklich aufgeführte Aktien, ETFs, Kryptowährungen und den Gold-Proxy.

Beide Modi verlangen BUY/SELL-Setups mit Grade A+/A/B, ausreichender Signalstärke, frischen Daten und geöffnetem Markt. Im **Python-Modus** gilt mindestens **1R**; sortiert wird nach dem kleinsten **prozentualen Abstand zwischen Entry und aktuellem Kurs**, danach nach Stärke. Im **Gemini-Modus** gelten weiterhin mindestens 1,5R und die Sortierung nach TP1-Kurspotenzial, Stärke und Chance/Risiko. Identische Gemini-Kontexte werden aus dem SQLite-Cache wiederverwendet.

## Backtesting und Grenzen

Der frühere Python-Regelbacktest ist für Strategie `4.1-ai` deaktiviert. Er würde eine andere Strategie als die aktuelle Gemini-Gesamtentscheidung testen und dadurch irreführende Kennzahlen liefern. Neue Gemini-Kontexte und Signalzustände werden archiviert. Ein zukünftiger Replay-Test kann ausschließlich diese zum damaligen Zeitpunkt tatsächlich bekannten KI-Ausgaben, Nachrichten und Makrodaten verwenden; heutige KI-Urteile werden nicht rückwirkend erfunden.

Die vorhandene Datenbank bleibt erhalten. Artikel, harte Daten, Makroauswertungen und vollständige Gemini-Kontexte werden separat gespeichert. Alte Signale bleiben als Legacy-Historie erhalten. Neue akzeptierte Signale werden pro Instrument, Zeitrahmen und Kerze einmal gespeichert.

## Prüfung

```powershell
cd backend
.\venv\Scripts\python.exe -B -m unittest -v test_platform test_signal_management test_news_reliability test_manual_modes test_next_entries test_current_outlook test_timeframe_analysis

cd ..\frontend
npm test
npm run lint
npm run build
```

Die Tests verwenden synthetische Daten ausschließlich in isolierten Testfällen und temporären Datenbanken. Der normale Betrieb hat keinen Demo-Preis-Fallback.

Enthaltene Prüfungen: RSI-Sonderfälle, kausale Indikatoren, beide Indikatorgruppen, Marktstrukturkontext, fehlendes Volumen, Gold-Mapping, Annahme und Ablehnung von Gemini-Leveln, ausdrücklich fehlender Python-Fallback, Scanner-Ranking, Kontext-Caching, Datenbank-Deduplizierung, API-Verträge sowie native Long-/Short-Risikobox-Geometrie.

Live-Feeds und Gemini benötigen funktionierenden Netzwerkzugriff. Ein erfolgreicher Build und Offline-Tests bestätigen keine externe Datenanbieter-Verfügbarkeit.
