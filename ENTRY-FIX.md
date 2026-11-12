# Entry und aktueller Kurs

Ein vorbereiteter Limit-, Stop- oder Bestätigungs-Entry ist ein geplanter Einstiegspreis. Er muss nicht dem aktuellen Kurs entsprechen. Ein aktiver Entry bleibt dagegen der ursprüngliche simulierte Einstieg; er wird nicht mit dem Kurs verschoben.

Dieses Update korrigiert die laufende Prüfung und die Darstellung für alle Instrumente:

- Gemini-Pläne werden gegen den zuletzt beobachteten Kurs validiert. Indikatoren verwenden weiterhin abgeschlossene Kerzen. Der Gemini-Kontext und seine bestehenden Cache-Schlüssel bleiben unverändert; Kursänderungen erzeugen keine zusätzlichen KI-Aufrufe.
- Die lokale Kursbeobachtung prüft auch wartende Pläne: Bei mehr als 2 ATR Abstand läuft ein unberührter Entry ab. Die ATR stammt aus abgeschlossenen Kerzen des gewählten Timeframes. Aktive Pläne werden nicht wegen dieses Abstands verschoben oder beendet.
- Die festgelegte Lebensdauer wird auch bei einer wiederverwendeten Gemini-Antwort und über den Quote-WebSocket geprüft. Gezählt werden abgeschlossene Kurskerzen, keine leeren Stunden am Wochenende.
- Bestehende gespeicherte Pläne erhalten die Prüfung ohne Datenbankmigration. Die ursprünglichen Level bleiben in der Historie erhalten.
- Der Chart beschriftet wartende Level mit „Geplanter Entry“ und „Entry ausstehend“. Chart und Infokarte zeigen den aktuellen Kurs und den Abstand getrennt an. Die Infokarte nutzt dabei denselben Kurs wie der Chart.

Installation: Windows + R, dann `"%TEMP%\AI-Trade-Entry-Fix\Installieren.cmd"`.

Der Installer prüft die Dateien, sichert betroffene Dateien und die SQLite-Datenbank und startet ausschließlich das identifizierte Projekt-Backend neu. Er installiert keine Pakete, trainiert keine Modelle und startet keine Backtests. Gespeicherte Modelle, Kurse und Einstellungen werden erhalten.

Prüfung: 9 gezielte Backend-Regressionen, darunter das Gold-Beispiel mit Entry 4041,50 und Kurs 4030, sowie 4 Prüfungen der Chart- und Infokartenanzeige. TypeScript und Produktionsbuild erfolgreich. Keine historischen Strategie-Backtests und keine externen API-Aufrufe für diese Prüfungen.
