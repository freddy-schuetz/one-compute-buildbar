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
| `n8n_url` | `https://n8n.oi.destination.one` (diese n8n rufen auch neue Apps auf) |
| `hub_url` | `https://oi-labor-hub.friedemann-schuetz.eu` (Bereitstellungsdienst für die Apps) |
| `hub_kurs_geheimnis` | Kurs-Geheimnis des Bereitstellungsdienstes, kommt von Friedemann auf sicherem Weg |
| `nur_terminal` | `one-compute-buildbar` (Standard; schreibt die Zugänge nur in dieses Terminal) |
| `priority` | `-100` (Standard; läuft vor anderen Filtern) |
| `kurze_form_merken`, `erinnerung_deutsch`, `antwort_aufraeumen`, `vorschlagsknoepfe_aus` | an (Standard; was sie tun, steht direkt unter dieser Liste) |

- Was der Zusatz außer den Zugängen am Chat tut (Fassung 0.4.4, jeder Punkt mit seinem Schalter abschaltbar):
  - **Kurze Form merken** (`kurze_form_merken`): Hat die Person in diesem Chat „zu lang“, „kürzer“ oder „kurz bitte“
    geschrieben oder Eile, keine Zeit oder das Handy erwähnt, hängt der Zusatz vor jedem Modellaufruf einen kurzen
    Hinweis ans Ende der Systemanweisung: Bis zum Ende des Chats gilt die kurze Form, auch am Kontrollpunkt, in der
    Abschlussnachricht, in der Schärfrunde und in der Übergabe. Es zählen nur Nachrichten der Person bis 800 Zeichen
    und darin nur Sätze, die sich auf den Chat oder ihre Lage beziehen. Nicht zählen etwa „Das Ergebnis ist zu lang für
    die Tabelle“, „nicht zu lang“, „bin am Handy erreichbar“, Fragen wie „Ist das zu lang?“ oder „Geht das auch am
    Handy?“ (am Handy zählt nur, wenn die Person von sich spricht), Zitate, eingefügte Texte, angehängte Dateien und die
    Datei `AGENTS.md` des Terminals, die Open WebUI selbst als Nachricht mitschickt.
  - **Erinnerung an Deutsch** (`erinnerung_deutsch`): Schreibt das Modell zwischen zwei Werkzeugaufrufen eine englische
    Notiz, bekommt der nächste Modellaufruf einmal eine kurze Erinnerung: Was es für die Person schreibt, ist ein kurzer
    deutscher Satz.
  - **Aufräumen** (`antwort_aufraeumen`): Englische Notizen und den Platzhalter der Zwischenschicht nimmt er aus der
    fertigen Antwort.
  - **Vorschlagsknöpfe** (`vorschlagsknoepfe_aus`): Die automatisch erzeugten Folgefragen unter jeder Antwort entfernt
    er wieder.

  Hinweis und Erinnerung stehen nur in der Anfrage an das Modell, nie im gespeicherten Verlauf.

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
ins Terminal der Person, wenn sich etwas geändert hat oder das letzte Schreiben länger als 10 Minuten her ist. Darin
stehen Kürzel, Nutzer-ID, die Adressen von n8n und dem Bereitstellungsdienst, ein persönliches Zugangszeichen zum
Veröffentlichen (HMAC aus dem Kurs-Geheimnis, Kürzel und Nutzer-ID) und, sobald vorhanden, der n8n-Schlüssel der
Person. Der Ordner ist versteckt, das Dateifenster blendet ihn aus, und buildbar_ öffnet ihn nie.

`buildbar-status` hält den Zusatz nur für aktiv, wenn diese Datei jünger als 30 Minuten ist. Ist sie älter, läuft der
Zusatz vermutlich nicht mehr (abgeschaltet oder nicht mehr am Modell). Fehlt dann der n8n-Schlüssel, schickt
`buildbar-status` die Person zur Betreuung, statt zum Einfügen in den Chat zu raten: Ohne den Zusatz fängt den
Schlüssel dort niemand ab. Die Betreuung prüft in diesem Fall, ob der Zusatz aktiv ist und am Modell buildbar_ hängt.
Die 10 Minuten stehen im Zusatz (`MERKER_DAUER`) und müssen unter den 30 Minuten bleiben. Wer den Zusatz so ändert,
dass er die Datei seltener schreibt, schaltet sonst die Anleitung zum Einfügen für alle ab.

**Die Person tut nur eins: ihren n8n-Schlüssel einmal hinterlegen.** buildbar_ sagt beim ersten Kontakt, wie
(n8n, Einstellungen, n8n API, Schlüssel mit allen Rechten erstellen). Zwei Wege:

1. In den Chat einfügen. Der Zusatz erkennt den Schlüssel, ersetzt ihn, bevor das Sprachmodell die Nachricht bekommt,
   prüft ihn bei n8n und speichert ihn in den persönlichen Einstellungen. Auch im gespeicherten Chatverlauf und in der
   Anzeige steht danach nur ein Platzhalter. Übernommen wird nur ein Schlüssel aus der neuesten Nachricht, nie aus
   einem älteren oder geteilten Verlauf.
2. Direkt eintragen: im Chat oben rechts „Steuerung“, „Valves“, „Funktionen“, „buildbar Zugang“.

Das Kürzel bildet der Zusatz aus dem Namen: erster Buchstabe des Vornamens und Nachname (zusammen höchstens 7 Zeichen,
ohne Umlaute), dazu 5 Zeichen aus der Nutzer-ID. Es ist damit höchstens 12 Zeichen lang, zum Beispiel `bhiller3fa9c`.
Es steht in Pfaden, Tabellennamen und App-Adressen.

Vor jedem Modellaufruf, auch zwischen zwei Werkzeugaufrufen, blendet der Zusatz n8n-Schlüssel und das Zugangszeichen
der Person aus allen Nachrichten und Werkzeugausgaben aus. Nach der Antwort bereinigt er die gespeicherten
Werkzeugdetails. Liest der Assistent also doch einmal eine Zugangsdatei, sieht das Sprachmodell nur einen Platzhalter.
Schläft das Terminal beim ersten Kontakt, wartet der Zusatz mit einer Statusmeldung bis zu knapp zwei Minuten.

## Zugänge und Bausteine

Ein n8n-Schlüssel kann die Zugänge (Credentials) eines Kontos nicht auflisten: Unter seinen Rechten gibt es dafür
keines. buildbar_ kommt deshalb auf zwei Wegen an Zugänge:

- **Eigene Zugänge der Person**, zum Beispiel für die KI: buildbar_ baut den Node ohne Zugang, die Person wählt ihren
  Zugang dort einmal aus und speichert. `buildbar-merken aus-workflow <Workflow-ID>` liest Name, Typ und Nummer aus dem
  Workflow und merkt sie sich (`~/.config/buildbar/zugaenge.json`, keine Schlüssel). `buildbar-status` zeigt die
  gemerkten Zugänge. KI läuft über den n8n-Node von one.intelligence (`CUSTOM.lmChatOneIntelligence`, Zugang vom Typ
  `oneIntelligenceApi`), getestet mit `claude-sonnet-5-5`, `claude-haiku-5-5` und `local/gemma-4-31B`.
- **DataForSEO und Apify** über zwei flexible Bausteine. Das sind n8n-Workflows im Konto der Betreuung mit deren
  Zugängen. „Baustein: DataForSEO“ nimmt jede Abfrage an (Pfad und Anfrage frei), „Baustein: Apify“ startet jeden
  Actor aus dem Apify Store. Beide haben ein Tageslimit für alle Aufrufer zusammen.

Dazu kommen fünf fertige Bausteine für Öffnungszeiten und Webseiten (Google, Tripadvisor, OpenStreetMap, Website,
Webseite lesen). Alle Bausteine sind ein Angebot, keine Pflicht, und für jeden Workflow der Instanz aufrufbar
(`callerPolicy: any`). Sie laufen mit den Zugängen des Kontos, in dem sie liegen.

## Was im Image liegt

| Pfad | Zweck |
|---|---|
| `/usr/local/bin/buildbar-status` | zeigt, was eingerichtet ist und was fehlt, ohne Schlüssel auszugeben |
| `/usr/local/bin/buildbar-merken` | merkt sich Zugänge, die die Person in einem eigenen Workflow ausgewählt hat |
| `/usr/local/bin/n8n-werkzeug` | ruft die n8n-mcp-Werkzeuge im n8n-Konto der Person auf |
| `/usr/local/bin/n8n-mcp-start` | startet n8n-mcp 2.91.0 (ohne Löschen, Zugänge, Instanz-Audit, Agenten und Evaluations), ohne Telemetrie |
| `/usr/local/bin/app` | App anlegen, auflisten, ansehen, sichern und veröffentlichen (über den Bereitstellungsdienst). `app neu` stellt die Vorlage auf die n8n aus `N8N_API_URL` um (nur Schema und Host, ohne Angabe `https://n8n.oi.destination.one`). `app ansehen` baut die App im Arbeitsplatz, startet sie nur lokal und prüft sie im Browser, ohne etwas zu veröffentlichen. `app veroeffentlichen` prüft vorher lokal und danach die echte Adresse. `app sichern "<was>"` überträgt nur Bericht, Stand, Notizen und Workflow-Dateien (`pruefbericht.md`, `stand.md`, `mein-use-case.md`, `README.md`, `workflows/*.json`) nach Gitea und kehrt sofort zurück, ohne Bau, Vorprüfung und Warten. Hat sich seit dem letzten Stand auf Gitea noch etwas anderes geändert, überträgt es nichts und meldet `NICHT GESICHERT` mit den Dateien, dann gilt `app veroeffentlichen`. Es läuft immer nur ein Bau (Sperre `/tmp/buildbar-bau.lock`, der Arbeitsplatz hat 2 GB) |
| `/usr/local/bin/seite-pruefen` | prüft eine Oberfläche im Browser: Bilder Rechner und Handy (am Handy wie ein echtes Gerät neu geladen), Überbreite, sehr kleine Schrift (am Bildschirm unter 11 px, auch die Beschriftung einer verkleinert gezeigten Zeichnung), Kontrast, Fehler. Schritt `datei` lädt eine Datei aus dem Ordner der App oder aus `/tmp` in ein Dateifeld. Nur eigene Apps und, mit `SEITE_PRUEFEN_LOKAL=1`, die lokal gestartete App; interne Adressen sind gesperrt |
| `/usr/local/bin/workflow-pruefen` | prüft einen n8n-Workflow auf stille Fehler (Name, Ausdruck ohne „=“, Webhook und Antwort, offene Weiterleitung, Kosten ohne Tageszähler, Code, KI, Zugänge) |
| `/usr/local/bin/datei-ansehen` | liest eine angehängte Tabelle ganz (Spalten, Summen, Kontrollzeilen) und blendet Personendaten aus, bevor geplant wird |
| `/usr/local/bin/seite-ansehen` | sieht eine fremde öffentliche Webseite einmal im Browser an: Bild, Hausfarben mit Kontrast, Logo-Kandidaten, Navigation, Text |
| `/opt/buildbar/` | `seite-pruefen.js`, `seite-ansehen.js`, `puppeteer-core`, `schluessel-laden.sh` |
| aus dem Basis-Image | Node 22, Python 3.12, Git, Chromium, curl, sudo |

Die Zeitzone im Arbeitsplatz ist Europe/Berlin (`TZ` im Dockerfile): `date`, Python, Node und der Browser von
`seite-pruefen` zeigen deutsche Zeit, mit Sommerzeit. `app status` rechnet die Zeiten des letzten Builds dorthin um.

Zu `n8n-mcp-start`: Gesperrt ist auch das Löschen von Tabellenzeilen, nicht nur von ganzen Tabellen. Die Telemetrie von
n8n-mcp ist abgeschaltet (`N8N_MCP_TELEMETRY_DISABLED=true`, auch als Standardwert im Image), es gehen also keine
Nutzungsdaten und keine Inhalte von Workflows an den Hersteller. Vor dem Start wechselt der Befehl in den Ordner
`/opt/buildbar`: n8n-mcp lädt sonst eine Datei `.env` aus dem Arbeitsordner des Assistenten. Von den Zugängen der
Person bekommt n8n-mcp nur den n8n-Zugang, nicht das Zugangszeichen zum Veröffentlichen und nicht die Nutzer-ID.
Vorsorglich nimmt der Befehl auch den Schlüssel des Terminals heraus (`OPEN_TERMINAL_API_KEY`), ebenso `app` für npm,
Next.js und git. Was der Container sonst in seiner Umgebung hat, geht unverändert an diese Programme. Welche Namen
das sind, zeigt im Terminal `env | cut -d= -f1` (nur die Namen, keine Werte).

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

Jeder Push auf `main`, der das Image betrifft, baut es über GitHub Actions und schiebt es nach GHCR. Jede Fassung
bekommt zwei Namen: `:buildbar` (immer die neueste) und `:buildbar-<sha>` (genau diese Fassung). Die Terminals holen
eine neue Fassung, wenn die Verbindung auf `:buildbar` steht und das Image beim Start neu geprüft wird
(`imagePullPolicy: Always`), oder wenn im Feld „Bild“ der Name der neuen Fassung eingetragen und „Terminals
aktualisieren“ geklickt wird. Änderungen nur an dieser Beschreibung oder am Zusatz lösen keinen Bau aus.
Lokal: `docker build -t one-compute-buildbar .` und `docker run --rm -it --entrypoint bash one-compute-buildbar`, dann
`buildbar-status`. Versionen: n8n-mcp 2.91.0, puppeteer-core 23.11.1, Basis open-terminal 0.17.7 (im Dockerfile),
seite-pruefen 1.4 (`seite-pruefen hilfe` nennt die Fassung in der letzten Zeile).
Der Zusatz liegt unter `openwebui/` und wird in Open WebUI aktualisiert, nicht über das Image.
