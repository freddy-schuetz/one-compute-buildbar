# one.compute (buildbar_)

Terminal-Image für den Assistenten **buildbar_** auf one.intelligence. Es ist das Open-Terminal-Image, das auch
one.compute (Advanced) nutzt, plus die Werkzeuge, die buildbar_ braucht. Geheimnisse sind nicht im Image.

Image: `ghcr.io/freddy-schuetz/open-terminal:buildbar`

## Einrichten in one.intelligence (Admin)

1. Admin, Einstellungen, Integrationen, Terminal-Verbindungen: neue Verbindung anlegen oder die vorhandene
   „one.compute (Advanced)“ kopieren.
   - Name: `one.compute (buildbar_)`, ID: `one-compute-buildbar`
   - Bild: `ghcr.io/freddy-schuetz/open-terminal:buildbar`
   - Chat-Uploads: `Dateisystem` (angehängte Dateien landen im Terminal)
   - Speicher: dauerhaft, mindestens 2Gi (eine App braucht rund 600 MB; 1Gi reicht für eine App)
   - CPU: 2, Arbeitsspeicher: 2Gi (Next.js-Builds)
   - Inaktivitäts-Timeout: für Kurstage 120 Minuten (Pods starten sonst nach jeder Pause neu, rund 90 Sekunden)
   - Umgebungsvariablen: keine nötig
2. Speichern, danach „Terminals aktualisieren“.
3. Am Modell buildbar_ als Standard-Terminal `one-compute-buildbar` eintragen (Arbeitsbereich, Modelle, buildbar_, Terminal).
4. Die Gruppen, die buildbar_ nutzen, brauchen Zugriff auf die Verbindung („Zugriff“) und Leserecht auf die
   16 Skills `buildbar-*` (Arbeitsbereich, Skills).

Bekannte Falle: Open WebUI lädt die Werkzeugliste der Terminals beim Start und beim Speichern der Verbindungen mit
`AIOHTTP_CLIENT_TIMEOUT_TOOL_SERVER_DATA` (Standard 10 s). Die erste `openapi.json`-Abfrage eines schlafenden Pods
dauert 20 bis 30 s. Mit 120 s ist das Terminal zuverlässig in der Liste.

## Was im Image liegt

| Pfad | Zweck |
|---|---|
| `/usr/local/bin/buildbar-status` | zeigt, was eingerichtet ist und was fehlt (ohne Schlüssel auszugeben) |
| `/usr/local/bin/n8n-werkzeug` | ruft die n8n-mcp-Werkzeuge im n8n-Konto der Person auf |
| `/usr/local/bin/n8n-mcp-start` | startet n8n-mcp 2.91.0 (ohne Löschen, Zugänge, Instanz-Audit) |
| `/usr/local/bin/app` | App anlegen, auflisten, veröffentlichen (über den Bereitstellungsdienst) |
| `/usr/local/bin/seite-pruefen` | prüft eine Oberfläche im Browser: Bilder Rechner/Handy, Überbreite, Kontrast, Fehler |
| `/opt/buildbar/` | `seite-pruefen.js`, `puppeteer-core`, `schluessel-laden.sh` |
| aus dem Basis-Image | Node 22, Python 3.12, Git, Chromium, curl, sudo |

Nicht im Image: OpenCode (nur für den späteren Brücken-Betrieb), Bausteine und Zugänge (liegen in n8n).

## Je Person: eine Datei mit Schlüsseln

Das Home ist am Anfang leer. buildbar_ ruft beim ersten Kontakt `buildbar-status` auf und sagt der Person, was
fehlt. Die Person legt dann rechts im Terminal (Reiter Dateien) die Datei `/home/user/schluessel.txt` an:

```
KUERZEL=bhil
N8N_API_KEY=<aus n8n: Einstellungen, n8n API, Schlüssel erstellen>
HUB_TOKEN=<Zugang zum Veröffentlichen, kommt von der Betreuung>
```

Optional, falls im eigenen n8n-Konto Zugänge liegen: `CRED_KI=<id>`, `CRED_DATAFORSEO=<id>`, `CRED_APIFY=<id>`.
Die Schlüssel gehen nie durch den Chat. `~/.buildbar/geheim.env` wird weiter gelesen (alte Einrichtung).

## Bausteine

Die fünf Bausteine (Google, Tripadvisor, OpenStreetMap, Website, Webseite lesen) liegen in einem n8n-Konto und sind
für jeden Workflow der Instanz aufrufbar (`callerPolicy: any`). Die IDs stehen im Systemprompt von buildbar_. Zugänge
für DataForSEO, Apify und KI stecken in den Bausteinen, die Personen brauchen keine eigenen.

## Bauen und aktualisieren

Jeder Push auf `main` baut das Image über GitHub Actions und schiebt es nach GHCR (`:buildbar` und `:buildbar-<sha>`).
Lokal: `docker build -t one-compute-buildbar .` und `docker run --rm -it one-compute-buildbar bash`, dann
`buildbar-status`. Versionen: n8n-mcp 2.91.0, puppeteer-core 23.11.1, Basis open-terminal 0.17.7 (im Dockerfile).
