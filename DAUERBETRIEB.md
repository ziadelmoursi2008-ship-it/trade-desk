# Dauerbetrieb ohne geoeffnete Website

## Was die zwei Backtest-Knoepfe tun

`Python neu berechnen` startet einen einmaligen historischen Test der ausgewaehlten
Aktie/des Timeframes. `Gemini + Quant auswerten` startet eine einmalige Auswertung
bereits aufgezeichneter KI-Aktivierungen. Diese beiden Knoepfe allein schalten
keinen Dauerbetrieb ein. Ein gestarteter Auftrag kann bei geschlossenem Browser
weiterlaufen, solange das Backend laeuft. Die Ergebnisse bleiben gespeichert.

Der neue, separate **Dauerbetrieb** sammelt Daten und erzeugt neue Analysen auch
ohne Browser. Die historische Auswertung ist ein weiterer, periodischer Schritt.
Es werden keine echten Broker-Orders gesendet.

## Einmal einrichten

1. Backend/Frontend beenden und das gepruefte Dauerbetrieb-Update installieren.
2. Im Projektordner `Autostart-einrichten.cmd` ausfuehren. Das richtet eine eigene
   Windows-Aufgabe fuer deinen angemeldeten Benutzer ein und startet das Backend
   ohne sichtbares Terminal. Eine Pruefung verhindert einen zweiten Server auf
   Port 8000. Kein fremder Portbesitzer wird beendet.
3. `http://127.0.0.1:8000` im Browser oeffnen. Das Backend liefert jetzt auch die
   fertig gebaute Website aus; der Vite-/Frontend-Prozess muss nicht weiterlaufen.
   Die bisherige Entwicklungsadresse auf Port 5173 bleibt nutzbar.
4. Im Bereich **Dauerbetrieb** Aktie, 5m/15m, Profile und Limits pruefen. Danach
   `Dauerbetrieb einschalten` klicken. Erst dieser Klick aktiviert die Automatik.
5. Browser schliessen. Das Backend arbeitet weiter. Die Einstellungen und Archive
   bleiben bei einem Neustart erhalten; der eingeschaltete Modus wird fortgesetzt.

Voreinstellung: **AUS**, eine Aktie (NVDA), 5m und 15m, beide Profile, Berichte alle
60 Minuten. Die Aktie kann geaendert werden; maximal zehn Katalog-Aktien.
Es werden nicht ungefragt alle Aktien dauerhaft an Gemini geschickt.

## Was wann laeuft

- Der Sammler beobachtet waehrend der Handelszeit regelmaessig Kurse, normalerweise
  etwa alle 25 Sekunden zuzueglich Feed-/Verarbeitungszeit. Ausserhalb der
  Handelszeit wird seltener geprueft. Kein Anspruch auf einen Echtzeit-Kursfeed.
- Abgeschlossene 1m-/5m-/15m-Kerzen werden dauerhaft in SQLite gespeichert.
  Vollstaendigkeit richtet sich nach dem Beobachtungszeitpunkt des Abrufs. Eine
  damals unvollstaendige Cache-Kerze wird nicht nachtraeglich nur durch Zeitablauf
  als vollstaendig eingestuft. Ueberlappende Daten werden auf Korrekturen geprueft.
- Pro frischer abgeschlossener 5m-/15m-Kerze wird jedes eingeschaltete Profil
  hoechstens einmal eingeplant. Die Python-Analyse ist unabhaengig von Gemini.
  Gemini berechnet vorher Quant und verwendet den bestehenden gemeinsamen
  Analyseaufruf inklusive Nachrichten, Makro und gegebenenfalls Optionsrisiko.
- Gemini-Anfragen laufen in einer eigenen Warteschlange. Langsame Antworten
  blockieren die Kursbeobachtung nicht. Zu alte Auftraege und inzwischen
  gewechselte Kerzen werden verworfen; es gibt keine nachtraeglich erfundenen
  KI-Entscheidungen fuer Zeiten ohne Beobachtung.
- Backtest-Berichte werden standardmaessig stuendlich aktualisiert, bei einem
  beobachteten Boersenschluss zusaetzlich abschliessend. Unveraenderte Schlussdaten
  fuehren nachts nicht zu staendigen Wiederholungen. Neue Aufzeichnungen und
  Folgekurse muessen trotzdem erst vorliegen, bevor der kombinierte Test ein
  Ergebnis zeigen kann.
- Kurs-, Kontext-, Aktivierungs- und Ergebnisarchive bleiben gespeichert. Die
  regulaeren Backtest-Eingaben nutzen bis zu 30 Kalendertage des gesammelten
  Kursarchivs, soweit vorhanden; je Timeframe ist weiterhin Warmup notwendig.
  Die dauerhaften Originalkurse werden dadurch nicht geloescht.

Jeder historische Bericht beginnt wie bisher separat mit 5.000 EUR und wertet
Aktienkaeufe mit Vollausstieg an TP1 oder Stop inklusive Modellkosten aus. Es ist
kein dauerhaft finanziertes Echtdepot und kein vollstaendiger Replay aller
KI-Planveraenderungen. Die beiden Konten/Zeitraeume duerfen nicht addiert werden.

## Gemini-Kosten begrenzen

Eine Aktie auf 5m und 15m kann regulaer etwa **16 neue Analysen pro Handelsstunde**
ausloesen. Zehn Aktien koennen etwa 160 ausloesen. Fehlende Bewertungen oder
ungueltige Plaene koennen zusaetzliche Vervollstaendigungs-/Reparaturaufrufe
erfordern. Das Auswerten des Backtest-Berichts selbst ruft Gemini nicht auf.

Standardgrenzen: **48 Hintergrund-API-Versuche in einer rollenden Stunde** und
**256 pro UTC-Kalendertag**. Grenzen und beide Analyseprofile sind einstellbar.
Jeder erste Aufruf und jeder Reparaturaufruf wird vor dem Senden atomar reserviert.
Fehlgeschlagene Versuche zaehlen mit; automatische SDK-Netzwerk-Retries sind fuer
diese Hintergrundaufrufe ausgeschaltet. Zaehler ueberstehen Backend-Neustarts.

Die Grenzen zaehlen Aufrufe, nicht Euro oder Token. Sie sind keine Geldkostengarantie.
Manuelle Website-Analysen sind zusaetzliche, separat gestartete Aufrufe. Kleine
Limits koennen die Abdeckung der ausgewaehlten Aktien unvollstaendig machen.
Beim Erreichen der Grenze laufen Python und Kursbeobachtung weiter; neue
Hintergrund-Gemini-Aufrufe werden bis zur erneuten Verfuegbarkeit unterlassen.

## Unabhaengigkeit und Grenzen

Der manuelle Chartmodus und sein Quant-Schalter werden nicht umgestellt.
Der Hintergrund verwendet eigene Signalzustaende und eigene KI-Kontexte.
Manuelle und automatische Aktivierungen werden nicht zu einem gemischten
Backtest zusammengerechnet. Sobald passende Hintergrundkontexte vorliegen,
verwendet der kombinierte Bericht diese getrennte Quelle; andernfalls die
passenden manuellen Aufzeichnungen. Die Quelle steht in der Website.

Bei ausgeschalteter Automatik starten keine weiteren Anfragen. Eine bereits
gesendete Anfrage kann noch zurueckkehren; nach einer Einstellungsaenderung wird
ihr Ergebnis nicht als neue Hintergrundfreigabe uebernommen. Beim Abschalten des
Backends werden wartende Auftraege beendet; alte Auftraege werden nach einem
Neustart nicht als historische KI-Entscheidungen nachgeholt.

**Der PC muss eingeschaltet, wach und mit dem Internet verbunden sein.**
Die Windows-Aufgabe nutzt deine angemeldete Sitzung, funktioniert bei gesperrtem
Bildschirm, benoetigt aber kein geoeffnetes Browserfenster. Bei Ausschalten,
Energiesparen oder Abmelden gibt es keine kontinuierliche Beobachtung. Keine
systemweite Energieeinstellung wird geaendert. Fuer Betrieb unabhaengig vom PC
waere ein dauerhaft laufender Server erforderlich; dieses Update mietet oder
bezahlt keinen Server.

Die Aufgabe startet das Backend nach der Windows-Anmeldung und prueft regelmaessig,
ob ein Start noetig ist. Mehrere Instanzen werden zusaetzlich durch eine
SQLite-Ausfuehrungssperre am doppelten Sammeln gehindert. Der Dienst bindet nur
an 127.0.0.1; er wird nicht ins Internet veroeffentlicht.

## Beenden, Autostart und Diagnose

- `Dauerbetrieb ausschalten` stoppt die Automatik; die Website bleibt erreichbar.
- `Hintergrund-Backend-starten.cmd` startet den eigenen Server einmal unsichtbar.
- `Hintergrund-Backend-stoppen.cmd` beendet nur einen anhand von Programmdatei,
  Kommandozeile und Erstellungszeit verifizierten eigenen Prozess und deaktiviert
  seinen Autostart. Ein manuell im Terminal gestarteter Prozess wird nicht anhand
  einer fremden Portnummer beendet. Danach kann `Autostart-einrichten.cmd` den
  automatischen Start erneut einrichten.
- `Autostart-entfernen.cmd` entfernt nur die eigene Windows-Aufgabe. Ein bereits
  laufender Server wird dadurch nicht beendet.
- Protokoll: `backend/logs/background-backend.log`, mit begrenzter Dateigroesse
  und Rotation. Keine API-Schluessel im Prozessnachweis oder in den Einstellungen.
- Die Windows-Aufgabe wird durch das Update allein nicht registriert. Die
  mitgelieferten Skripte werden unter deinem Benutzer ausgefuehrt; Windows kann
  die Registrierung je nach lokalen Richtlinien ablehnen und zeigt den Fehler.

Neue Tabellen: background_runs, background_runtime, background_api_attempts,
background_price_bars und getrennte background_signal_states/-events. Bestehende
Archive werden additiv um ihre Aufzeichnungsquelle ergaenzt und bleiben erhalten.
Das Update ersetzt weder .env noch Datenbank und aendert keine Dateirechte.
