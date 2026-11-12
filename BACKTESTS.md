Aktuell: Der Reiter **Backtests** trennt die Python-Basisstrategie und archivierte **Gemini + LightGBM**-Entscheidungen. Die frühere handgewichtete Quant-Bewertung ist abgeschaltet. Bedienung und Versionswechsel: [DASHBOARD-UPDATE.md](DASHBOARD-UPDATE.md).

# Zwei getrennte Backtest-Profile

Unter dem Chart stehen jetzt zwei Auswertungen für die ausgewählte Aktie auf 5m
oder 15m. Die Anzeige ist unabhängig vom gewählten Live-Analysemodus. Normales
Polling und Chartwechsel lesen nur gespeicherte Berichte. Die Knöpfe starten
einmalige Auswertungen; der separat eingeschaltete Dauerbetrieb kann Berichte
zusätzlich regelmäßig erneuern.

Ergänzung: Der separat eingeschaltete **Dauerbetrieb** kann Daten und Analysen
auch ohne Browser sammeln und Berichte regelmäßig erneuern. Die beiden Knöpfe
bleiben einmalige Auswertungen. Einrichtung, API-Limits und Voraussetzungen stehen
in DAUERBETRIEB.md. Manuelle und automatische KI-Aufzeichnungen bleiben getrennt.

## 1. Python-Technik

Der Knopf aktualisiert die echten Kursarchive der ausgewählten Aktie (1m, 5m,
15m) sowie EURUSD zur Abrechnung und führt die aktuelle Python-Strategie erneut
aus. Er verwendet die bestehenden Originalfunktionen für Indikatoren,
Prognosen, kleinere POI-Zeiträume und die Trade-Prüfung. Kein Gemini-Aufruf.

Historische Merkmale werden nur aus bereits abgeschlossenen Kerzen erzeugt,
jeweils nach mindestens 200 Warmup-Kerzen. Ein Einstieg findet frühestens am
nächsten 1m-Open statt. Gebühren, Slippage, Spread und Euro-Umrechnung werden
modelliert. Das vorhandene TP1-Ausführungsmodell bleibt unverändert.

Die mitgelieferten 20 Einzelaktienberichte wurden mit dem aktuellen Code neu
erstellt: zehn Katalog-Aktien, je 5m und 15m. Gemeinsamer historischer Zeitraum:
21.10.2026, 16:50 UTC bis 27.10.2026, 18:17 UTC. Neuester Code bedeutet hier
nicht, dass die eingefrorenen historischen Kurse Echtzeitdaten wären.

Der gemeinsame 5m-Portfoliolauf ergab einen Trade und 4.962,94 EUR Endkapital
(-0,7411 Prozent). Der 15m-Lauf hatte keinen Trade. Start je 5.000 EUR.
Die Stichprobe ist zu klein für eine belastbare Profitabilitätsaussage.
Die Einzelaktienkonten sind unabhängige Alternativen, kein zusammenzurechnendes
Portfolio. Details stehen in backtest_results/BERICHT.md und den CSV-Dateien.

## 2. Python-Indikatoren + Quant + Gemini

Dieses Profil wertet tatsächlich beobachtete Aktivierungen aus der laufenden
Gemini-Strategie aus. Es gibt keinen rückwirkenden Gemini-Aufruf für jede alte
Kerze. Historische KI-Antworten ohne nachgewiesene Berechnungsversion werden
nicht nachträglich dem aktuellen Code zugerechnet.

Beim normalen Nutzen von Gemini mit aktivem, tatsächlich gewichtetem Quant
werden künftig vollständige Eingaben, Quellenstände, die validierte Antwort,
Code-Prüfsummen und der Beobachtungszeitpunkt archiviert. Neue Setups erhalten
einen Verweis auf ihren Entstehungskontext. Die bestehende Signalverwaltung
protokolliert ihre Aktivierung einschließlich des Zeitpunkts, zu dem die
Aktivierung tatsächlich bekannt war. Alte Setups ohne diesen Herkunftsnachweis
werden nicht nachträglich als kombinierte Setups eingestuft.

Nach der Installation:

1. Gemini wählen, Quant einschalten und `Quant berechnen + Gemini analysieren`
   nutzen. Ein erfolgreicher neuer Aufruf mit frischem, positiv gewichtetem Quant
   legt den ersten Kontext an. Unstempelbare alte Cache-Antworten zählen nicht.
2. Die tatsächlichen Setups und Aktivierungen werden bei den vorhandenen
   Analysen/Kursbeobachtungen aufgezeichnet. Keine zusätzliche KI-Anfrage dafür.
3. `Gemini + Quant auswerten` drücken, sobald passende Aktivierungen und
   nachfolgende Kursdaten vorliegen. Der Knopf lädt Kursdaten und simuliert die
   beobachteten Freigaben. Kein erneuter Aufruf von Gemini für den Backtest.

Fehlen Aktivierungen, steht dort ein Archivhinweis statt erfundener Nullrenditen.
Die früher gespeicherten Gemini-Antworten haben keinen vollständigen
Herkunftsnachweis für diese Codeversion und werden deshalb nicht übernommen.

Die Performance-Simulation nimmt den ersten 1m-Open **streng nach** Bekanntwerden
der Aktivierung. Fehlt genau diese Kerze, wird kein späterer Einstieg erfunden.
Es ist ein kostenbehafteter Test der beobachteten Freigaben mit Vollausstieg an
TP1 oder Stop. Es ist kein vollständiger historischer Replay sämtlicher
KI-Entscheidungen, TP2-Teilpositionen oder späterer Planänderungen. Ein solcher
vollständiger historischer Test erfordert zusätzliche historische Archive.

## Gemeinsame Annahmen und Grenzen

- Nur die zehn vorhandenen US-Katalog-Aktien, 5m/15m, jeweils ein eigenständiges
  Konto mit 5.000 EUR, höchstens eine finanzierte Position, keine Hebelung.
- Nur Aktienkäufe; SELL-Freigaben werden gezählt, aber nicht als Leerverkauf
  ausgeführt. Das entspricht der bisherigen Python-Voreinstellung.
- Ganze Position an TP1 oder Stop; kein TP2 und keine nachträgliche Zielanpassung.
- Je Ausführung 0,05 Prozent Gebühr; Marktorders zusätzlich 0,05 Prozent
  Slippage und 0,01 Prozent halber Spread. Stop zuerst bei Stop/TP-Doppeltreffer.
- Euro-Abrechnung mit der letzten bereits abgeschlossenen EURUSD-Tageskerze als
  Proxy. Kein exaktes Intraday-Währungs- oder Liquiditätsmodell.
- Die Zeiträume und Archivabdeckung können verschieden sein. Deshalb werden
  die beiden Renditen nicht automatisch als gleichwertiger Vergleich bewertet.

## Versionierung, Persistenz und Bedienung

Ein Bericht enthält Berechnungs-Prüfsummen, Ausführungsannahmen und Hashes der
verwendeten Kursarchive. Änderungen an Strategie, Runner oder Daten machen ihn
ungültig. Neue beobachtete Gemini-Aktivierungen erfordern eine neue Auswertung.
Ungültige Ergebnisse werden nicht als aktuelle Kennzahlen angezeigt.

Die Berechnung läuft in einem eigenen Worker; normale Kursabfragen werden nicht
mit dem Replay belastet. Doppelte Knopfdrücke starten keinen zweiten identischen
Auftrag. Nach einem Backend-Neustart werden unterbrochene Aufträge gekennzeichnet
und können per Knopfdruck neu gestartet werden. Es gibt keine geplanten Jobs.

Neue additive SQLite-Tabellen: backtest_context_archive, backtest_event_archive
und backtest_jobs. Die bisherigen Signal-, Quant-, Options- und KI-Archive bleiben
erhalten. Keine API-Schlüssel im Archiv und keine Änderung der vorhandenen .env.

API:

- GET /api/backtests?ticker=AAPL&timeframe=5m: beide gespeicherten Profile lesen.
- POST /api/backtests/run mit ticker, timeframe, profile (`python` oder
  `gemini_quant`): manuellen Auftrag starten. `refresh_data` ist standardmäßig
  true; false erlaubt ein ausdrücklich gewähltes unverändertes Kursarchiv.
- GET /api/backtests/jobs/{id}: Fortschritt lesen, keine neue Berechnung.

Der Originalordner ist für diese Agentensitzung weiterhin nicht beschreibbar.
Das Update wird deshalb mit SHA-geprüftem Installer, Quelltext-Diff und Sicherung
der bisherigen Dateien geliefert. Backend/Frontend vor dem Einspielen beenden.
