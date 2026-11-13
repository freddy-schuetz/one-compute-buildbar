# one.compute (buildbar_)

Terminal-Image und Zusatz für den Assistenten **buildbar_** auf one.intelligence. Das Image ist das Open-Terminal-Image,
das auch one.compute (Advanced) nutzt, plus die Werkzeuge, die buildbar_ braucht. Der Zusatz „buildbar Zugang“ ist eine
Filterfunktion für Open WebUI. Er legt je Person die Zugänge unsichtbar im Terminal ab. Geheimnisse liegen weder im Image
noch in diesem Repo.

Image: `ghcr.io/freddy-schuetz/open-terminal:buildbar`

## Einrichten in one.intelligence (Admin)

### 1. Terminal-Verbindung

Admin, Einstellungen, Integrationen, Terminal-Verbindungen: neue Verbindung anlegen oder „one.compute (Advanced)“ kopieren.

| Feld | Wert |
|---|---|
| Name, ID | `one.compute (buildbar_)`, `one-compute-buildbar` |
| Bild | `ghcr.io/freddy-schuetz/open-terminal:buildbar` |
| Chat-Uploads | `Dateisystem` (angehängte Dateien landen im Terminal) |
| Speicher | dauerhaft, mindestens 2Gi (eine App braucht rund 600 MB, mit 1Gi ist nach zwei Apps Schluss) |
| CPU, Arbeitsspeicher | 2 und 2Gi (Next.js-Builds) |
| Inaktivitäts-Timeout | an Kurstagen 120 Minuten (sonst startet das Terminal nach jeder Pause neu, rund 90 Sekunden) |
| Umgebungsvariablen | keine |

Speichern, danach „Terminals aktualisieren“. Zugriff für die Gruppen, die buildbar_ nutzen.

Bekannte Falle: Open WebUI lädt die Werkzeugliste der Terminals beim Start und beim Speichern der Verbindungen mit
`AIOHTTP_CLIENT_TIMEOUT_TOOL_SERVER_DATA` (Standard 10 s). Die erste Abfrage eines schlafenden Terminals dauert 20 bis
30 s. Mit 120 s ist das Terminal zuverlässig in der Liste.

### 2. Zusatz „buildbar Zugang“

Admin, Funktionen, neue Funktion. Code: [`openwebui/buildbar_zugang.py`](openwebui/buildbar_zugang.py).

- ID `buildbar_zugang`, Name `buildbar Zugang`, aktivieren. **Nicht global schalten:** Er gehört nur an das Modell buildbar_.
- Einstellungen (Valves, nur für Admins sichtbar):

| Valve | Wert |
|---|---|
| `n8n_url` | `https://n8n.oi.destination.one` |
| `hub_url` | `https://oi-labor-hub.friedemann-schuetz.eu` (Bereitstellungsdienst für die Apps) |
| `hub_kurs_geheimnis` | Kurs-Geheimnis des Bereitstellungsdienstes, kommt von Friedemann auf sicherem Weg |
| `nur_terminal` | `one-compute-buildbar` (Standard; schreibt die Zugänge nur in dieses Terminal) |
| `priority` | `-100` (Standard; läuft vor anderen Filtern) |

- **Open WebUI mit `ENABLE_VALVE_ENCRYPTION=true` betreiben** (und auf allen Instanzen derselbe `WEBUI_SECRET_KEY`). Ohne diese
  Einstellung speichert Open WebUI persönliche Einstellungen im Klartext und schickt sie an den Browser, also auch den
  n8n-Schlüssel. Wer Funktionen oder Werkzeuge anlegen darf, führt Code auf dem Server aus und kommt damit an alle
  Einstellungen: dieses Recht nur der Betreuung geben.
- Am Modell buildbar_ den Filter „buildbar Zugang“ einschalten. Das Modell pflegt Friedemann per Schnittstelle. Bitte
  buildbar_ nicht in der Oberfläche speichern: Dabei geht die Einstellung „Subagents aus“ verloren, weil der Editor
  diesen Schalter nicht zeigt.

### 3. Modell buildbar_

Standard-Terminal `one-compute-buildbar`. Die Gruppen, die buildbar_ nutzen, brauchen Leserecht auf das Modell und auf
die 16 Skills `buildbar-*` (Arbeitsbereich, Skills).

## Was je Person passiert

Das Home ist am Anfang leer. Bei jeder Nachricht an buildbar_ schreibt der Zusatz die Datei `~/.buildbar/zugang.env`
ins Terminal der Person, falls sich etwas geändert hat. Darin stehen Kürzel, Nutzer-ID, die Adressen von n8n und dem
Bereitstellungsdienst, ein persönliches Zugangszeichen zum Veröffentlichen (HMAC aus dem Kurs-Geheimnis, Kürzel und
Nutzer-ID) und, sobald vorhanden, der n8n-Schlüssel der Person. Der Ordner ist versteckt, das Dateifenster blendet ihn
aus, und buildbar_ öffnet ihn nie.

**Die Person tut nur eins: ihren n8n-Schlüssel einmal hinterlegen.** buildbar_ sagt beim ersten Kontakt, wie
(n8n, Einstellungen, n8n API, Schlüssel mit allen Rechten erstellen). Zwei Wege:

1. In den Chat einfügen. Der Zusatz erkennt den Schlüssel, ersetzt ihn, bevor das Sprachmodell die Nachricht bekommt,
   prüft ihn bei n8n und speichert ihn in den persönlichen Einstellungen. Auch im gespeicherten Chatverlauf und in der
   Anzeige steht danach nur ein Platzhalter. Übernommen wird nur ein Schlüssel aus der neuesten Nachricht, nie aus
   einem älteren oder geteilten Verlauf.
2. Direkt eintragen: im Chat oben rechts „Steuerung“, „Valves“, „Funktionen“, „buildbar Zugang“.

Das Kürzel bildet der Zusatz aus dem Namen: erster Buchstabe des Vornamens und Nachname (höchstens 9 Zeichen, ohne
Umlaute) plus drei Zeichen aus der Nutzer-ID, zum Beispiel `bhillerbd5`. Es steht in Pfaden, Tabellennamen und
App-Adressen.

Vor jedem Modellaufruf, auch zwischen zwei Werkzeugaufrufen, blendet der Zusatz n8n-Schlüssel und das Zugangszeichen
der Person aus allen Nachrichten und Werkzeugausgaben aus. Nach der Antwort bereinigt er die gespeicherten
Werkzeugdetails. Liest der Assistent also doch einmal eine Zugangsdatei, sieht das Sprachmodell nur einen Platzhalter.
Schläft das Terminal beim ersten Kontakt, wartet der Zusatz mit einer Statusmeldung bis zu knapp zwei Minuten.

## Zugänge und Bausteine

buildbar_ nutzt die Zugänge (Credentials) im n8n-Konto der Person: eigene und solche, die für das Konto freigegeben
sind. `buildbar-status` listet sie mit Name, Typ und ID. Dafür braucht der n8n-Schlüssel das Recht, Zugänge aufzulisten
(`credential:list`). KI läuft über den n8n-Node von one.intelligence (`CUSTOM.lmChatOneIntelligence`, Zugang vom Typ
`oneIntelligenceApi`), getestet mit `claude-sonnet-5-5`, `claude-haiku-5-5` und `local/gemma-4-31B`.

Die fünf Bausteine (Google, Tripadvisor, OpenStreetMap, Website, Webseite lesen) sind ein Angebot, keine Pflicht. Sie
liegen in einem n8n-Konto und sind für jeden Workflow der Instanz aufrufbar (`callerPolicy: any`). Sie laufen mit den
Zugängen des Kontos, in dem sie liegen.

## Was im Image liegt

| Pfad | Zweck |
|---|---|
| `/usr/local/bin/buildbar-status` | zeigt, was eingerichtet ist und was fehlt, ohne Schlüssel auszugeben |
| `/usr/local/bin/n8n-werkzeug` | ruft die n8n-mcp-Werkzeuge im n8n-Konto der Person auf |
| `/usr/local/bin/n8n-mcp-start` | startet n8n-mcp 2.91.0 (ohne Löschen, Zugänge und Instanz-Audit) |
| `/usr/local/bin/app` | App anlegen, auflisten, veröffentlichen (über den Bereitstellungsdienst) |
| `/usr/local/bin/seite-pruefen` | prüft eine Oberfläche im Browser: Bilder Rechner und Handy, Überbreite, Kontrast, Fehler |
| `/opt/buildbar/` | `seite-pruefen.js`, `puppeteer-core`, `schluessel-laden.sh` |
| aus dem Basis-Image | Node 22, Python 3.12, Git, Chromium, curl, sudo |

## Bekannte Grenzen

- **Erste Nachricht eines neuen Chats:** Open WebUI erzeugt den Chat-Titel, bevor der Zusatz läuft. Steht der Schlüssel
  schon im allerersten Satz eines neuen Chats, bekommt ihn das Modell für den Titel einmal zu sehen. buildbar_ fragt
  erst nach der ersten Nachricht nach dem Schlüssel, im normalen Ablauf passiert das also nicht.
- **Schlüssel eines Admin-Kontos** sehen alle Konten der n8n. `buildbar-status` warnt dann. Für Teilnehmende nur
  Schlüssel normaler Konten.
- **Der Assistent arbeitet als derselbe Nutzer** wie die Programme im Terminal. Dass er `~/.buildbar` nicht öffnet,
  regelt seine Anleitung, nicht ein Dateirecht. Eine eingeschleuste Anweisung auf einer Webseite könnte ihn dazu
  bringen, die Datei hinauszuschicken, ohne dass das Sprachmodell den Schlüssel je sieht. Die eigentliche Grenze ist
  das n8n-Konto der Person: ein Schlüssel eines normalen Kontos, kein Admin-Schlüssel.
- **Ereignisse und Protokolle:** Open WebUI schickt die ersten 300 Zeichen jeder neuen Nachricht an eingerichtete
  Webhooks und Ereignis-Funktionen, bevor der Zusatz läuft. Auf der Protokollstufe DEBUG steht die Anfrage im Log.
  Beides in Produktion nicht für Chats einschalten.

## Bauen und aktualisieren

Jeder Push auf `main` baut das Image über GitHub Actions und schiebt es nach GHCR (`:buildbar` und `:buildbar-<sha>`).
Lokal: `docker build -t one-compute-buildbar .` und `docker run --rm -it --entrypoint bash one-compute-buildbar`, dann
`buildbar-status`. Versionen: n8n-mcp 2.91.0, puppeteer-core 23.11.1, Basis open-terminal 0.17.7 (im Dockerfile).
Der Zusatz liegt unter `openwebui/` und wird in Open WebUI aktualisiert, nicht über das Image.
