# Lokaler LightGBM-Richtungsfilter

Für jedes Instrument und jeden Zeitraum (5m/15m) wird ein eigenes Modell trainiert.
Es unterstützt Python- und Gemini-Modus. Es gibt keine zusätzlichen Gemini-Aufrufe.
Die alte handgewichtete Quant-Bewertung ist im Live-Betrieb abgeschaltet. Historische Archive bleiben erhalten.

## Benutzung

1. Backend neu starten und die Website öffnen.
2. Instrument und 5m oder 15m auswählen.
3. **Einstellungen → Quant & Daten** öffnen, **Diese Aktie** oder **Watchlist** und 5m/15m wählen; anschließend **Modelle aktualisieren** drücken.
4. Der Hintergrundauftrag lädt bis zu 60 Kalendertage echter Yahoo-Kurse, aktualisiert
   das dauerhafte Archiv und trainiert mit höchstens zwei CPU-Threads. Anbieterlimits
   können die verfügbare Historie einschränken. Der Browser darf geschlossen werden;
   das Backend muss während des Trainings laufen.
5. Bei ausreichend Daten werden Training und Kennzahlen gespeichert. Nur ein Modell,
   das die zeitlich spätere Vergleichsprüfung besteht, bekommt Filtereinfluss.
   Das beginnt frühestens mit einer neuen abgeschlossenen Kerze nach der Modellprüfung.
6. **Filter EIN/AUS** gilt für beide Modi und alle Instrumente. Modelle bleiben bei AUS
   gespeichert. Normale Seitenaufrufe starten weder Training noch historische Downloads.

Der erste Download benötigt Internet für Kursdaten. Das Modell selbst braucht keine
KI-API, keinen API-Schlüssel und keine GPU. LightGBM benötigt ein Windows-64-Bit-Wheel;
`LightGBM-installieren.cmd` installiert und prüft es. Bei fehlender Windows-VC-Runtime
zeigt der Importtest den Fehler; es werden keine Modelle als lauffähig ausgegeben.

## Was das Modell lernt

18 ausschließlich rückwärts berechnete Merkmale aus abgeschlossenen OHLCV-Kerzen:
Renditen, EMA-Abstände, RSI, ATR, Volatilität, Kerzengeometrie, relative Volumenwerte
und Tageszeit. Fehlendes Volumen bleibt fehlend. Kein aktueller News- oder Gemini-Text
wird rückwirkend als historisches Wissen verwendet.

Ziel ist **Abwärts / Seitwärts / Aufwärts über drei Kerzen**, ab dem nächsten Open
bis zum Close der dritten folgenden Kerze: 15 Minuten auf 5m, 45 Minuten auf 15m.
Die Richtung muss eine modellierte Hin-und-zurück-Kostenhürde von insgesamt 0,22 %
überschreiten (je Seite 0,05 % Gebühr + 0,05 % Slippage + 0,01 % halber Spread).
Übernacht- oder Datenlücken werden nicht als zusammenhängende Zukunftsfenster gelabelt.
Dies sind Modellannahmen, keine individuellen Brokerkonditionen.

**Es wird keine Wahrscheinlichkeit „TP vor SL“ trainiert.** Dafür fehlen derzeit
ausreichend abgeschlossene, unverändert archivierte Setups. Die Modellausgaben
werden auf einem eigenen Zeitraum konservativ kalibriert; sie bleiben Modellschätzungen,
keine gemessenen Trade-Trefferquoten. Die Kalibrierung kann Sicherheit reduzieren,
aber nicht erhöhen.

## Zeitliche Prüfung

Mindestens 600 verwendbare Beispiele und 30 verschiedene lokale Handelstage;
mindestens 300 Trainings-, 100 Validierungs- und 100 Testbeispiele sowie mindestens
20 Trainingsbeispiele je Klasse. Die Tage werden chronologisch ungefähr 60/20/20
geteilt. Der mittlere Block wird an einer Tagesgrenze nochmals in frühe Validierung
und spätere Kalibrierung geteilt (je mindestens 50 Beispiele). Über die Grenzen
reichende Zukunftslabels werden entfernt. Nur die frühe Validierung bestimmt den
Trainingsstopp. Nur die spätere Kalibrierung bestimmt die Temperatur (festes Raster
von 1 bis 4). Der letzte Testblock beeinflusst weder Training noch Kalibrierung.
Modellparameter sind fest; kein
Durchprobieren zahlreicher Kombinationen am letzten Testblock.

Freigabe erfordert auf dem letzten Testblock gleichzeitig:

- Log-Loss mindestens 2 % unter einer konstanten Klassenhäufigkeits-Prognose;
- Klassen-Trefferrate mehr als 2 Prozentpunkte über deren Mehrheitsklassen-Prognose;
- durchschnittliche klassenweise Trefferrate über 36 %;
- mindestens 20 deutliche Richtungsprognosen (Modellwert ≥ 60 %), davon ≥ 55 % korrekt.

Diese Kriterien müssen zusätzlich auf vorab ausgewählten, nicht überlappenden
Prognosefenstern bestehen. Beide chronologischen Testhälften müssen jeweils einen
geringeren Log-Loss als die Basisprognose aufweisen. Nicht überlappende Fenster
sind nicht automatisch statistisch unabhängig.

Diese Startgrenzen sind eine Mindestprüfung, kein Profitabilitätsnachweis. Wiederholte
Trainingsläufe mit neueren Daten ersetzen keinen späteren Paper-Trading-Vergleich.
Das tatsächlich geprüfte Modell wird gespeichert, ohne nachträgliches Neutraining
auf den Testlabels. Testdaten und Trainingszeitraum sind in der Oberfläche sichtbar.

## Signale und bestehende Backtests

Nur ein freigegebenes Modell mit passender Merkmalsversion und höchstens 14 Tage
alter zugrunde liegender Datenhistorie darf einen **neuen** Einstieg bei deutlicher
Gegenrichtung sperren. Veraltete Marktdaten, zu wenig Warmup oder Fehler geben dem
Modell keinen Filtereinfluss. Es erzeugt keine eigenen Trades oder Kurslevel.
Bereits vorbereitete/aktive Gemini-Setups behalten ihre bisherige Lebenszyklusprüfung.

Die vorhandenen Python-Backtests bleiben Tests der Basisstrategie **ohne LightGBM**.
Ein heute trainiertes Modell wird nicht rückwirkend auf seine eigenen Trainingsdaten
angewendet. Im Gemini+Quant-Archiv werden neue tatsächlich beobachtete Entscheidungen
mit LightGBM-Modellkennung und Filteraktion erfasst. Änderungen am ML-Code sind Teil
der Archiv-Prüfsumme. Die Modellprüfung ist kein vollständiger Handels-Backtest.

## Speicherung und Effizienz

SQLite `backend/trading_platform.db` (oder `TRADING_DB_PATH`):

- `background_price_bars`: vorhandenes Kursarchiv wird weiterverwendet; kein zweites
  Kursarchiv. Analysen ergänzen abgeschlossene 5m/15m-Kerzen. Manuelles Aktualisieren
  übernimmt auch ältere Korrekturen in der vom Anbieter gelieferten Historie.
- `lightgbm_models`: natives LightGBM-Textmodell, SHA-256, Datenfingerabdruck,
  Merkmalsversion, Trainingszeiträume, Parameter und Prüfkennzahlen. Kein Pickle.
- `lightgbm_jobs`: dauerhafter Auftragsstatus. Ein Training gleichzeitig; Doppelklicks
  erzeugen keinen zweiten Auftrag. Unterbrochene Aufträge werden nach Neustart markiert.
- `app_settings`: Filterauswahl.

Unveränderte Daten verwenden das gespeicherte Modell. Neu trainiert wird bewusst
mit der vollständigen gespeicherten Datenreihe (bis zu zwei Jahre), statt alte
Beispiele durch wiederholtes Weitertrainieren mehrfach zu übergewichten. Ein neues
Modell wird atomar freigegeben; ein fehlgeschlagenes oder schwaches ersetzt kein
bisher freigegebenes Modell. Ein aktives und bis zu fünf aktuelle Modelle bleiben
gespeichert; acht geladene Modelle und 256 Prognosen werden im RAM zwischengespeichert.

API: `GET /api/lightgbm?ticker=NVDA&timeframe=5m`,
`POST /api/lightgbm/settings` mit `{"enabled":true}`,
`POST /api/lightgbm/train` mit `{"ticker":"NVDA","timeframe":"5m","refresh_data":true}`.
Mit `refresh_data:false` wird ausschließlich das lokale Archiv benutzt.

## Qualitätsupdate 2.0 – Prüfung und Installation

Die Modellversion wurde erhöht. Alte Modelle bleiben gespeichert, dürfen aber mit
geänderter Prüfmethodik nicht weiter abstimmen. Die Datenübersicht zeigt bei solchen
aktiven Modellen „Neu trainieren“. Nach Installation Backend neu starten und
„Modelle aktualisieren“ ausführen. Kein Modell wird durch dieses Paket automatisch
aktiviert. Auch bei lokalem Training und Inferenz werden offene Kerzen ausgeschlossen.

391 Backend-Tests: keine Fehler, ein übersprungener Test. 46 Frontend-Prüfungen
bestanden; TypeScript und Produktionsbuild erfolgreich. Kein visueller Browsertest.

Ein Vergleich auf identischen finalen Zeiträumen des vorhandenen Kursarchivs umfasst
26 Instrument-/Timeframe-Kombinationen; acht weitere haben zu wenig Historie.
13 Kombinationen zeigen geringeren Log-Loss, sieben höhere Klassentrefferquote.
Weder die alte noch die neue Version gibt dabei ein Modell frei. Dies belegt keine
allgemeine Verbesserung der Richtungstrefferquote und keinen Handelsgewinn.
Die Daten wurden nicht zum nachträglichen Ändern der Parameter oder Freigabegrenzen
verwendet. Einzelwerte und Zeiträume stehen in `quality-results.json` im Updatepaket.

Methodische Grundlage: getrennte Daten für Modellanpassung und Kalibrierung,
[scikit-learn: Probability calibration](https://scikit-learn.org/stable/modules/calibration.html).

Die aktuelle Ausführungsumgebung verweigert Schreibzugriffe auf den Projektordner.
Das geprüfte Update liegt deshalb separat bereit. `Installieren.cmd` prüft vor jeder
Änderung die SHA-256-Prüfsummen der Originaldateien, sichert betroffene Dateien und
den vorhandenen Frontend-Build und übernimmt dann die geprüften Dateien.
Vorher Backend und Frontend beenden; anschließend wie gewohnt starten. Kursarchive,
Datenbanken und API-Konfiguration werden nicht übertragen oder verändert.
