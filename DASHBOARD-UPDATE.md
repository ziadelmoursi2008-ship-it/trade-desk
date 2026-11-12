# Dashboard mit zentralen Einstellungen und einem Quant-Filter

## Hauptansicht

Die Hauptansicht enthält Instrument, Timeframe, Analysemodus, einen Kurschart und
die aktuelle Einschätzung. Scanner, Backtests und technische Details haben eigene
Reiter. Der Hauptchart bleibt beim Reiterwechsel montiert; Training aktualisiert
weder seine Komponentenkennung noch einen zweiten Chart. Vor der Initialisierung
entfernt KLineChart eine gegebenenfalls zurückgebliebene Instanz.

**Einstellungen** öffnet ein Dialogfenster mit zwei Bereichen:

- **Quant & Daten**: gemeinsamer LightGBM-Schalter, Trainingsauftrag und Datenübersicht.
- **Dauerbetrieb & API**: Aktien, 5m/15m, Python/Gemini, API-Limits und Berichtstakt.

Der Scanner filtert nach Watchlist/allen Märkten, Richtung, Timeframe und Signalstärke.
Er verwendet gespeicherte Modelle und startet kein Training.

## Welche Daten sind gespeichert?

Unter **Einstellungen → Quant & Daten → Gespeicherte Daten** steht für jede vorhandene
Kombination aus Instrument und 5m/15m:

- Anzahl und erster/letzter Zeitpunkt gespeicherter Kurskerzen;
- Zeitpunkt der letzten tatsächlich neuen oder korrigierten gespeicherten Kurse;
- Zeitpunkt des letzten erfolgreichen Archivabgleichs, auch wenn nichts geändert wurde;
- letztes Training, freigegebenes Modell und Ergebnis des letzten Trainingsauftrags.

Kurszeit, Abrufzeit und Trainingszeit sind getrennt. Für alte Archive ohne erfassten
Abrufzeitpunkt steht **Noch nicht erfasst**. Es werden keine Zeitstempel erfunden.

**Diese Aktie** aktualisiert ausschließlich das aktuelle Instrument für die ausgewählten
Timeframes. **Watchlist** verwendet die unter Dauerbetrieb gespeicherte Aktienauswahl.
5m und 15m können einzeln oder gemeinsam ausgewählt werden. Ein Auftrag persistiert
alle Kombinationen und verarbeitet sie nacheinander. Das Dialogfenster darf geschlossen
werden; der Backend-Prozess muss weiterlaufen. Unveränderte Trainingsdaten verwenden
das vorhandene Modell. Fehlgeschlagene oder schwache Modelle ersetzen kein bisher
freigegebenes Modell. Nach einem Backend-Neustart werden unterbrochene Aufträge markiert.

Neue SQLite-Tabelle: `lightgbm_archive_updates` mit `checked_at` und `changed_at`.
`lightgbm_jobs` erhält die additiven Spalten `batch_id` und `queue_order`.
Bestehende Modelltexte, Kursarchive und Einstellungen werden unverändert übernommen.

## Ein gemeinsamer Quant-Weg

LightGBM ist die einzige laufende Quant-Richtungsprüfung in Python und Gemini.
Die alte handgewichtete Delta/CVD-Richtungsbewertung wird weder in Einzelanalysen
noch im Scanner oder Dauerbetrieb aufgerufen. Ihre alten Dateien und Daten bleiben
für historische Nachvollziehbarkeit erhalten; es gibt keinen zweiten aktiven Schalter.
Alte schreibende `/api/quantitative/*`-Aufrufe antworten mit HTTP 410 und verweisen auf
LightGBM. Alte Browser-Tabs bitte nach der Installation neu laden.

LightGBM erfindet keine Kurslevel und erzeugt keine eigenen Trades. Ein freigegebenes,
aktuelles Modell darf einen neuen Einstieg bei deutlicher Gegenrichtung sperren.
Bestehende Gemini-Setups behalten ihren Lebenszyklus. Fehlende Modelle werden sichtbar
als fehlend protokolliert, ohne einen erfolgreichen Gemini-Aufruf als Fehler auszugeben.

Optionsdaten sind unabhängig vom alten Quant-Schalter. Sie werden im Bereich Details
über einen eigenen Knopf aktualisiert. Normale Analysen verwenden gespeicherten Kontext.
Gemini-Archive enthalten die tatsächlich beobachtete lokale Modellkennung und den
Prüfstatus. Alte KI-Antworten werden nicht nachträglich als LightGBM-Entscheidung umetikettiert.
Python-Backtests bleiben ausdrücklich Tests der Basisstrategie ohne nachträglich auf
eigene Trainingsdaten angewendetes LightGBM.

## Dauerbetrieb und API-Budget

Der lokale Python-Weg prüft neue abgeschlossene Kerzen. Gemini-Anfragen werden über
die Stunde verteilt: Mindestabstand regulärer Starts ist `3600 / Stundenlimit` Sekunden
seit dem letzten tatsächlichen Hintergrund-Aufruf. Es gibt höchstens einen wartenden
oder laufenden Gemini-Auftrag. Die Aktien/Timeframes mit der am längsten zurückliegenden
Analyse kommen zuerst; bei Gleichstand entscheidet die lokale Richtungsstärke.
Nur noch aktuelle Analysekerzen sind zulässig. Die bestehenden atomaren Stunden- und
Tageslimits zählen weiterhin alle SDK-Versuche einschließlich Reparaturen.

Die gespeicherten Limits und die Auswahl Python/Gemini werden nicht automatisch erhöht
oder eingeschaltet. Bei niedrigem Budget erhält nicht jede Aktie jede Kerze eine neue
Gemini-Analyse. Backtest-Berichte haben einen eigenen Takt und starten keine rückwirkenden
Gemini-Aufrufe. Manuell gestartete Analysen/Scans kommen wie bisher zusätzlich hinzu.

Neue API: `GET /api/lightgbm/inventory`, `POST /api/lightgbm/train-batch` mit
`{"symbols":["NVDA","AAPL"],"timeframes":["5m","15m"],"refresh_data":true}`.
