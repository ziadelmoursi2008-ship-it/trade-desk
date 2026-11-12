# Optionsrisiko für Gemini + Quant

## Bedienung

1. Gemini-Modus wählen und Quant einschalten.
2. `Quant berechnen + Gemini analysieren` drücken. Dieser eine Vorgang aktualisiert
   den Quant-Block und lädt zusätzlich einen kleinen Optionskontext.
3. Unter den Indikatorgruppen erscheint `Optionsrisiko`. Die Tabelle zeigt bis zu
   acht kursnahe Calls/Puts aus zwei Verfallsterminen (1–45 Tage, keine 0DTE).
4. `Markt scannen` führt bei eingeschaltetem Quant denselben Ablauf je Instrument
   aus. Unterstützt sind die zwölf US-Aktien/ETFs im vorhandenen Katalog. Krypto,
   Gold und andere Symbole erhalten keinen erfundenen Ersatz-Optionskontext.

Chartwechsel, normale Analyse, Kursabfragen und WebSocket-Updates laden keinen
neuen Optionsfeed und berechnen keine neuen Greeks. Sie lesen den gespeicherten
Stand. Bei Quant AUS oder im Python-Modus findet auch dieser Optionszugriff nicht
statt. Die Anzeige prüft ihr Datenalter lokal, auch wenn die Verbindung ausfällt.

## Aussage und Gewichtung

Delta, Gamma, Theta, Vega und Rho werden aus dem abgerufenen Kurs, der Provider-IV
und den dokumentierten Modellannahmen in Python berechnet; sie sind keine festen Tabellenwerte. Strike und Verfall
gehören zum Kontrakt. Kurs, implizite Volatilität und verbleibende Laufzeit ändern
die Werte. Derselbe Kontrakt hat zum selben Bewertungszeitpunkt dieselben Greeks
im 5- und 15-Minuten-Chart. Das ist anders als ein chartabhängiger RSI oder CVD.

Die Greeks beschreiben den Optionspreis, nicht die Richtung der Aktie. Beide
Long-Optionstypen haben positives Gamma. Ohne Dealerpositionen wird aus Open
Interest kein Netto-Gamma, Pinning oder Hedgefluss geschätzt. Options-Delta und
Volumen-Delta/CVD bleiben ausdrücklich getrennt.

Es gibt **keine zusätzliche Richtungsgewichtung**. Das bisherige Quant-Gewicht
(maximal 12 Prozent, abhängig von Datenqualität) bleibt unverändert. Gemini darf
zeitnahe Greeks als Optionsrisiko in der Begründung erläutern, aber damit weder
Richtungsscore noch Entry, Stop oder Ziele ändern. Die kompakte Zusammenfassung
geht in den bestehenden gemeinsamen Gemini-Aufruf; kein Modellaufruf pro Kontrakt.
Die vorhandene Reparatur ungültiger Gemini-Pläne kann weiterhin einen zweiten
Aufruf auslösen.

## Datenquellen

- Standard: Yahoo-Optionskette, ohne zusätzliche Zugangsdaten. Ein eigener kleiner
  Adapter nutzt die bereits mit yfinance installierte curl_cffi-Bibliothek. Alle
  Optionsanfragen einschließlich Cookie/Crumb haben ein gemeinsames Zeitbudget;
  der normale yfinance-Marktdatenclient wird dabei nicht verändert.
- Yahoo liefert hier keine verlässliche Zeit der Geld-/Brief-Quote. Abrufzeit und
  letzter Handel beweisen keine Quote-Aktualität. Deshalb sind Yahoo-Greeks nur
  **REFERENZWERTE** in der Website. Die numerischen Werte werden der Intraday-KI
  nicht übergeben. Ein blockierter Yahoo-Zugang ergibt NICHT VERFÜGBAR, keine
  Ersatzwerte und keine Unterbrechung der normalen Gemini-Analyse.
- Optional Alpaca: Backend-`.env` um `ALPACA_API_KEY` und `ALPACA_SECRET_KEY`
  ergänzen. `OPTIONS_PROVIDER=alpaca` und `OPTIONS_ALPACA_FEED=opra` wählen.
  Ein entsprechender Datenzugang ist erforderlich; es wird nichts abonniert oder
  gekauft. `indicative` wird stets als Referenz behandelt. Bei 401/403 erfolgt kein
  stiller Wechsel auf einen anderen Feed. Schlüssel gehören ausschließlich ins
  Backend und werden nicht in Optionssnapshots, Frontend oder Manifest gespeichert.

Alpaca-OPRA-Quotes werden nur mit plausiblen IV-/Spread-Werten und höchstens zwei
Minuten alten, zeitlich passenden Options- und Basiswertdaten für den KI-Risikohinweis
freigegeben. Das ist eine Aktualitätsprüfung von Kursdaten, keine Zusicherung
ausführbarer Kurse oder exakter American-Option-Greeks. Manche Verträge bleiben
auch bei einem funktionierenden Feed ungeeignet.

Anbieterdokumentation:
- [Alpaca Optionskette und Feedunterschiede](https://docs.alpaca.markets/us/reference/optionchain)
- [OIC: Gamma](https://www.optionseducation.org/advancedconcepts/gamma)
- [yfinance als bestehende Datenbibliothek](https://github.com/ranaroussi/yfinance)

## Modell und Einheiten

Python verwendet ein dividendenbereinigtes Black–Scholes–Merton-Modell ohne neue
Rechenbibliothek. Für amerikanische US-Aktien-/ETF-Optionen ist das eine europäische
Näherung. Vorzeitige Ausübung und diskrete Dividenden werden nicht modelliert.
Keine 0DTE; angenommener Verfall um 16:00 America/New_York mit Sommerzeitbehandlung.

Der Modellzins kommt aus `OPTIONS_RISK_FREE_RATE` (Dezimalzahl, selbst aktuell
halten) oder aus der bereits gespeicherten Fed-Zielband-Mitte, höchstens zehn Tage
alt. Die Fed-Mitte ist ein dokumentierter Proxy, keine Options-Laufzeitenkurve.
Fehlt beides, bleibt die Berechnung unverfügbar. Die erste normale Gemini-Analyse
lädt üblicherweise die vorhandenen Wirtschaftsdaten; danach erneut den Quant-Knopf
verwenden. Es wird kein konstanter Zins als angeblich aktueller Wert erfunden.

`OPTIONS_DIVIDEND_YIELD` ist optional eine kontinuierliche Modellrendite als
Dezimalzahl. Ohne Angabe gilt die explizite Annahme q=0, nicht eine gemessene
Dividendenrendite. Beide Annahmen erscheinen in der Website und bei freigegebenen
Werten im KI-Kontext. Die Options-IV stammt aus dem Feed; fehlende/ungültige IV
wird nicht durch historische Aktienvolatilität ersetzt.

Alle Werte gelten **je Optionsanteil**, nicht je 100er-Kontrakt:

| Wert | Einheit |
|---|---|
| Delta | Änderung Optionspreis in USD je 1 USD Basiswertbewegung |
| Gamma | Änderung Delta je 1 USD Basiswertbewegung |
| Theta | USD je Kalendertag, 365 Tage/Jahr |
| Vega | USD je einem Prozentpunkt IV, z.B. 30 auf 31 Prozent |
| Rho | USD je einem Prozentpunkt Zins, z.B. 4 auf 5 Prozent |

## Speicherung und Aktualität

Zwei additive SQLite-Tabellen in der bestehenden Datenbank: `options_context_cache`
speichert die Rohkette und den aktuellen berechneten Stand; `options_context_snapshots`
archiviert die berechneten Ergebnisse. Bestehende Quant-Kerzen und Handelshistorien
bleiben erhalten. Es muss kein Backtest durchgeführt werden.

Der Cache gilt aktienbezogen über alle Chartzeiträume. Innerhalb von 60 Sekunden
wird ein gespeicherter Abruf wiederverwendet. Bei einer Kursänderung über 0,5 Prozent
zieht ein Lesezugriff die KI-Freigabe zurück; ein neuer manueller Knopfdruck darf
den kurzen Cache dann vorzeitig erneuern. Fehlgeschlagene Anfragen werden frühestens
nach 60 Sekunden erneut versucht. Nach zwei Minuten verlieren alte Quotes die
KI-Freigabe, nach fünf Minuten erscheinen Modellwerte als ARCHIVIERT. Ein Fehler
beim erneuten Abruf archiviert den vorherigen Stand sofort. Keine automatischen
Feedabrufe und keine automatischen Neuberechnungen im Hintergrund.

Providerfehler, alte Daten, unpassende Kontrakte und Annahmenänderungen sind eigene
Zustände. Fehlende Greeks werden nicht als Nullwerte oder negative Richtung
gewertet. Nicht freigegebene numerische Greeks fehlen vollständig im Gemini-JSON;
der Datenstatus und der Grund werden weiterhin übergeben. Laufende Alterszähler
gelangen nicht in die KI-Anfrage und erzwingen keine unnötig neuen Cache-Schlüssel.
