# Systemstatus

Kompakte, lesende Statusseite f?r Backend, Marktdaten und Analysebetrieb.
Die Oberfl?che verwendet HTML, CSS und JavaScript ohne externe Bibliotheken.

```sh
node backend/observatory/build.mjs
```

Nach dem Backendstart steht sie unter `http://localhost:8000/` bereit.
Das Dashboard ist unter `/trading` erreichbar. Der Status wird alle 30 Sekunden
und bei R?ckkehr zum Tab gepr?ft. Eine fehlgeschlagene Pr?fung markiert Daten-
und Analysestatus als unbekannt; sie startet keine Analysen oder Kursabrufe.

Der ?ffentliche Portfolio-Build setzt ausdr?cklich den Demo-Modus und verlinkt
zur?ck zum statischen Dashboard. Das passiert in `tools/build-portfolio.mjs`.
