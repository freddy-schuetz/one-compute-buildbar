"""
title: buildbar Zugang
author: Friedemann Schütz
version: 0.3.0
required_open_webui_version: 0.12.0
description: Legt n8n-Schlüssel, Kürzel und Zugang zum Veröffentlichen unsichtbar im Terminal der Person ab. Fängt einen in den Chat eingefügten n8n-Schlüssel ab, bevor das Modell ihn sieht, und ersetzt ihn auch im gespeicherten Verlauf.
"""

# Filter für das Modell buildbar_ auf one.intelligence (Open WebUI 0.12). Nur an buildbar_ hängen, nicht global schalten.
#
# Bei jeder Nachricht (inlet):
# 1. Steht in der neuesten Nachricht der Person ein n8n-API-Schlüssel (ein JWT mit aud=public-api), wird er bei n8n
#    geprüft und in den persönlichen Einstellungen der Person gespeichert (Benutzer-Valve „n8n_schluessel_password“;
#    verschlüsselt, wenn Open WebUI mit ENABLE_VALVE_ENCRYPTION läuft). Schlüssel in allen Nachrichten werden durch
#    einen Platzhalter ersetzt, bevor das Modell sie bekommt, und ebenso im gespeicherten Verlauf und in der Anzeige:
#    Open WebUI speichert die Nachricht schon vor dem Filter. Aus älteren Nachrichten wird nie ein Schlüssel übernommen.
#    Die Person kann ihn auch direkt eintragen: Chat, oben rechts „Steuerung“, „Valves“, Funktionen, buildbar Zugang.
# 2. Kürzel (aus dem Namen plus fünf Zeichen aus der Nutzer-ID), Nutzer-ID, persönliches Zugangszeichen zum
#    Veröffentlichen (HMAC aus dem Kurs-Geheimnis) und n8n-Schlüssel werden als ~/.buildbar/zugang.env in das Terminal
#    der Person geschrieben, mit denselben Zugriffsprüfungen wie in Open WebUI. Schläft das Terminal, wartet der Filter
#    mit einer Statusmeldung bis zu knapp zwei Minuten. Geschrieben wird, wenn sich etwas geändert hat, sonst alle 10 min.
# Vor jedem Modellaufruf (request), auch zwischen Werkzeugaufrufen: n8n-Schlüssel und das Zugangszeichen der Person
# werden aus allen Nachrichten, Werkzeugausgaben und Werkzeugargumenten ausgeblendet.
# Fehler werden abgefangen und ohne Werte protokolliert: Eine Nachricht scheitert nie an diesem Filter.

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import re
import time
import unicodedata

import aiohttp
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

MUSTER = re.compile(r'eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}')
ERSATZ = '[n8n-Schlüssel eingefügt und sicher gespeichert]'
ERSATZ_ABGELEHNT = '[n8n-Schlüssel eingefügt, aber n8n hat ihn abgelehnt]'
ERSATZ_NICHT_GESPEICHERT = '[n8n-Schlüssel eingefügt, aber nicht gespeichert]'
AUSGEBLENDET = '[Schlüssel ausgeblendet]'
WECKEN = {0, 502, 503, 504}  # Terminal schläft oder startet gerade
MERKER_DAUER = 600  # Sekunden; danach wird zugang.env auch ohne Änderung neu geschrieben
_geschrieben: dict = {}


def ist_n8n_schluessel(token: str) -> bool:
    """Ein n8n-API-Schlüssel ist ein JWT, dessen Inhalt aud=public-api trägt."""
    try:
        teil = token.split('.')[1]
        teil += '=' * (-len(teil) % 4)
        daten = json.loads(base64.urlsafe_b64decode(teil.encode()).decode())
    except Exception:
        return False
    aud = daten.get('aud') if isinstance(daten, dict) else None
    return aud == 'public-api' or (isinstance(aud, list) and 'public-api' in aud)


def gueltiger_schluessel(wert: str) -> str:
    """Gibt den Wert zurück, wenn er genau ein n8n-Schlüssel ist (nichts davor, nichts danach, keine Zeilenumbrüche)."""
    wert = (wert or '').strip()
    return wert if MUSTER.fullmatch(wert) and ist_n8n_schluessel(wert) else ''


def kuerzel(name: str, user_id: str) -> str:
    """Erster Buchstabe des Vornamens plus Nachname, Kleinbuchstaben ohne Umlaute, höchstens 7 Zeichen, dazu fünf
    Zeichen aus der Nutzer-ID gegen Doppelungen. Beispiel: Bastian Hiller -> bhiller + 5 Zeichen (höchstens 12)."""
    t = (name or '').lower()
    for alt, neu in (('ä', 'ae'), ('ö', 'oe'), ('ü', 'ue'), ('ß', 'ss')):
        t = t.replace(alt, neu)
    t = unicodedata.normalize('NFKD', t).encode('ascii', 'ignore').decode()
    teile = [p for p in re.split(r'[^a-z0-9]+', t) if p]
    k = (teile[0][0] + teile[-1]) if len(teile) >= 2 else (teile[0] if teile else 'person')
    k = re.sub(r'[^a-z0-9]', '', k)
    if not k or not k[0].isalpha():
        k = 'p' + k
    return k[:7] + hashlib.sha256(user_id.encode()).hexdigest()[:5]


def zugangszeichen(geheimnis: str, k: str, user_id: str) -> str:
    return hmac.new(geheimnis.encode(), f'{k}|{user_id}'.encode(), hashlib.sha256).hexdigest()


def text_tauschen(text: str, ersatz: dict, gefunden: list = None, standard: str = ERSATZ, genau=()) -> str:
    """Ersetzt n8n-Schlüssel in einem Text: bekannte nach `ersatz`, alle anderen durch `standard`. `genau` sind
    zusätzliche geheime Werte, die wörtlich ausgeblendet werden (etwa das Zugangszeichen)."""
    for wert in genau:
        if wert and len(wert) >= 16 and wert in text:
            text = text.replace(wert, AUSGEBLENDET)

    def ersetze(m):
        tok = m.group(0)
        if not ist_n8n_schluessel(tok):
            return tok
        if gefunden is not None and tok not in gefunden:
            gefunden.append(tok)
        return ersatz.get(tok, standard)
    return MUSTER.sub(ersetze, text)


def tief_tauschen(wert, genau=()):
    """Blendet n8n-Schlüssel und geheime Werte in allen Texten einer verschachtelten Struktur aus (Werkzeugdetails)."""
    if isinstance(wert, str):
        return text_tauschen(wert, {}, None, AUSGEBLENDET, genau)
    if isinstance(wert, list):
        return [tief_tauschen(v, genau) for v in wert]
    if isinstance(wert, dict):
        return {k: tief_tauschen(v, genau) for k, v in wert.items()}
    return wert


def _texte(nachricht: dict) -> list:
    """Alle Texte einer Nachricht (Inhalt als Text oder als Liste von Teilen)."""
    inhalt = nachricht.get('content')
    if isinstance(inhalt, str):
        return [inhalt]
    if isinstance(inhalt, list):
        return [t['text'] for t in inhalt if isinstance(t, dict) and isinstance(t.get('text'), str)]
    return []


def schluessel_finden(body: dict, nur_letzte: bool = False) -> list:
    """Findet n8n-Schlüssel in Nachrichten der Person, ohne etwas zu ändern (neuester zuletzt).
    nur_letzte: nur in der neuesten Nachricht der Person suchen."""
    nachrichten = [n for n in (body.get('messages') or []) if isinstance(n, dict) and n.get('role') == 'user']
    if nur_letzte:
        nachrichten = nachrichten[-1:]
    gefunden = []
    for n in nachrichten:
        for text in _texte(n):
            text_tauschen(text, {}, gefunden)
    return gefunden


def schluessel_ersetzen(body: dict, ersatz: dict, standard: str = ERSATZ, genau=()) -> list:
    """Ersetzt n8n-Schlüssel in allen Nachrichten (Person, Assistent, System, Werkzeuge), auch in den Argumenten von
    Werkzeugaufrufen. Gibt die gefundenen Schlüssel zurück (neuester zuletzt)."""
    gefunden = []
    for nachricht in body.get('messages') or []:
        if not isinstance(nachricht, dict):
            continue
        inhalt = nachricht.get('content')
        if isinstance(inhalt, str):
            nachricht['content'] = text_tauschen(inhalt, ersatz, gefunden, standard, genau)
        elif isinstance(inhalt, list):
            for teil in inhalt:
                if isinstance(teil, dict) and isinstance(teil.get('text'), str):
                    teil['text'] = text_tauschen(teil['text'], ersatz, gefunden, standard, genau)
        for aufruf in nachricht.get('tool_calls') or []:
            funktion = aufruf.get('function') if isinstance(aufruf, dict) else None
            if isinstance(funktion, dict) and isinstance(funktion.get('arguments'), str):
                funktion['arguments'] = text_tauschen(funktion['arguments'], ersatz, gefunden, standard, genau)
    return gefunden


class Filter:
    class Valves(BaseModel):
        priority: int = Field(default=-100, description='Reihenfolge unter mehreren Filtern (kleiner = früher)')
        n8n_url: str = Field(default='https://n8n.oi.destination.one', description='Adresse der n8n')
        hub_url: str = Field(default='https://oi-labor-hub.friedemann-schuetz.eu',
                             description='Bereitstellungsdienst für die Apps')
        hub_kurs_geheimnis: str = Field(default='', description='Kurs-Geheimnis des Bereitstellungsdienstes (nie weitergeben)',
                                        json_schema_extra={'input': {'type': 'password'}})
        nur_terminal: str = Field(default='one-compute-buildbar',
                                  description='Nur in dieses Terminal schreiben (ID der Terminal-Verbindung). Leer: jedes')

    class UserValves(BaseModel):
        # Der Name endet auf „password“, damit Open WebUI den Wert in Prüfprotokollen schwärzt
        n8n_schluessel_password: str = Field(default='', title='n8n-Schlüssel',
                                             description='Dein n8n-Schlüssel (n8n: unten links dein Name, Settings, n8n API, '
                                             'Create an API key, alle Rechte). Du kannst ihn auch einfach in den Chat einfügen.',
                                             json_schema_extra={'input': {'type': 'password'}})

    def __init__(self):
        self.valves = self.Valves()

    # ------------------------------------------------------------------ Haken von Open WebUI

    async def inlet(self, body: dict, __user__: dict = None, __request__=None, __metadata__: dict = None,
                    __event_emitter__=None, __id__: str = None) -> dict:
        try:
            await self._arbeiten(body, __user__ or {}, __request__, __metadata__ or {}, __event_emitter__, __id__)
        except Exception as e:  # nie die Nachricht scheitern lassen, nie Werte protokollieren
            log.warning('buildbar Zugang: %s', type(e).__name__)
            try:  # Notfall: Ein Schlüssel erreicht das Modell auch dann nicht, wenn oben etwas schiefging
                schluessel_ersetzen(body, {})
            except Exception:
                pass
        return body

    async def request(self, body: dict, __user__: dict = None) -> dict:
        """Läuft vor jedem Modellaufruf, auch zwischen Werkzeugaufrufen: blendet Schlüssel und Zugangszeichen aus allen
        Nachrichten aus, auch aus Werkzeugausgaben (etwa wenn der Assistent doch eine Zugangsdatei gelesen hat)."""
        try:
            schluessel_ersetzen(body, {}, standard=AUSGEBLENDET, genau=self._genaue_werte(__user__))
        except Exception as e:
            log.warning('buildbar Zugang, request: %s', type(e).__name__)
        return body

    async def outlet(self, body: dict, __user__: dict = None) -> dict:
        """Läuft nach der Antwort: blendet Schlüssel und Zugangszeichen in den gespeicherten Werkzeugdetails aus
        (Feld output der Antworten). Den Text der Nachrichten lässt er unverändert, sonst legt Open WebUI den alten
        Wortlaut als originalContent ab; das Modell hat dank `request` ohnehin nie einen Schlüssel gesehen."""
        try:
            genau = self._genaue_werte(__user__)
            for nachricht in body.get('messages') or []:
                if isinstance(nachricht, dict) and nachricht.get('output'):
                    nachricht['output'] = tief_tauschen(nachricht['output'], genau)
        except Exception as e:
            log.warning('buildbar Zugang, outlet: %s', type(e).__name__)
        return body

    def _genaue_werte(self, user) -> list:
        """Die geheimen Werte dieser Person, die wörtlich ausgeblendet werden: n8n-Schlüssel und Zugangszeichen."""
        user = user or {}
        genau = []
        schluessel = gueltiger_schluessel(getattr(user.get('valves'), 'n8n_schluessel_password', ''))
        if schluessel:
            genau.append(schluessel)
        user_id = str(user.get('id') or '')
        if user_id and self.valves.hub_kurs_geheimnis:
            genau.append(zugangszeichen(self.valves.hub_kurs_geheimnis, kuerzel(str(user.get('name') or ''), user_id), user_id))
        return genau

    # ------------------------------------------------------------------ Ablauf

    async def _melden(self, emitter, text, fertig=True):
        if emitter:
            try:
                await emitter({'type': 'status', 'data': {'description': text, 'done': fertig}})
            except Exception:
                pass

    async def _n8n_pruefen(self, schluessel: str) -> int:
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10), trust_env=True) as s:
                async with s.get(self.valves.n8n_url.strip().rstrip('/') + '/api/v1/workflows?limit=1',
                                 headers={'X-N8N-API-KEY': schluessel, 'Accept': 'application/json'},
                                 allow_redirects=False) as r:
                    return r.status
        except Exception:
            return 0

    async def _arbeiten(self, body, user, request, metadata, emitter, funktion_id):
        user_id = str(user.get('id') or '')
        if not user_id:
            return
        # Ein Wert im Feld, der kein n8n-Schlüssel ist (Tippfehler, Zeilenumbrüche), wird nie ins Terminal geschrieben
        schluessel = gueltiger_schluessel(getattr(user.get('valves'), 'n8n_schluessel_password', ''))

        # 1. Eingefügte Schlüssel abfangen, bevor das Modell sie sieht. Gespeichert wird nur ein neuer Schlüssel aus der
        #    neuesten Nachricht; ältere Nachrichten werden nur geschwärzt. Ersetzt wird einmal, mit dem endgültigen Text.
        ersatz = {}
        neu = [t for t in schluessel_finden(body, nur_letzte=True) if t != schluessel]
        if neu:
            kandidat = neu[-1]
            status = await self._n8n_pruefen(kandidat)
            if status in (401, 403):
                ersatz[kandidat] = ERSATZ_ABGELEHNT
                await self._melden(emitter, 'n8n hat den eingefügten Schlüssel abgelehnt')
            else:
                from open_webui.models.functions import Functions
                gespeichert = await Functions.update_user_valves_by_id_and_user_id(
                    funktion_id, user_id, {'n8n_schluessel_password': kandidat})
                schluessel = kandidat  # gilt für diese Nachricht auch dann, wenn das Speichern scheiterte
                if gespeichert is None:
                    ersatz[kandidat] = ERSATZ_NICHT_GESPEICHERT
                    await self._melden(emitter, 'Der n8n-Schlüssel ließ sich nicht speichern. Bitte die Betreuung fragen.')
                elif status == 200:
                    await self._melden(emitter, 'n8n-Schlüssel sicher gespeichert')
                else:
                    await self._melden(emitter, 'n8n-Schlüssel gespeichert; n8n war gerade nicht erreichbar, geprüft wird später')
        if schluessel_ersetzen(body, ersatz):
            um = metadata.get('user_message')
            if isinstance(um, dict) and isinstance(um.get('content'), str):
                um['content'] = text_tauschen(um['content'], ersatz)
            # Open WebUI hat die Nachricht schon vor diesem Filter gespeichert: dort und in der Anzeige ebenfalls ersetzen
            try:
                await self._verlauf_saeubern(metadata, emitter, ersatz)
            except Exception as e:
                log.warning('buildbar Zugang, Verlauf: %s', type(e).__name__)

        # 2. Zugang ins Terminal der Person schreiben
        terminal_id = str(body.get('terminal_id') or metadata.get('terminal_id') or '')
        nur = self.valves.nur_terminal.strip()
        if not terminal_id or (nur and terminal_id != nur):
            return
        ziel = await self._verbindung(user_id, terminal_id, metadata)
        if not ziel:
            return
        verbindung, nutzer, kontext = ziel
        k = kuerzel(str(user.get('name') or ''), user_id)
        einzeilig = lambda w: re.sub(r'[\r\n]', '', str(w or '')).strip().rstrip('/')  # noqa: E731
        # ZUSATZ=1 sagt buildbar-status, dass ein in den Chat eingefügter Schlüssel hier abgefangen wird
        zeilen = ['# Zugänge für buildbar_, geschrieben vom Zusatz „buildbar Zugang“. Nicht öffnen, nicht ausgeben.',
                  'ZUSATZ=1', f'KUERZEL={k}', f'PERSON_ID={einzeilig(user_id)}',
                  f'N8N_API_URL={einzeilig(self.valves.n8n_url)}', f'OIL_HUB_URL={einzeilig(self.valves.hub_url)}']
        if self.valves.hub_kurs_geheimnis:
            zeilen.append(f'HUB_TOKEN={zugangszeichen(self.valves.hub_kurs_geheimnis, k, user_id)}')
        if schluessel:
            zeilen.append(f'N8N_API_KEY={schluessel}')
        inhalt = '\n'.join(zeilen) + '\n'
        schluessel_merker = (user_id, terminal_id, kontext or '')
        merker = hashlib.sha256(inhalt.encode()).hexdigest()
        alt = _geschrieben.get(schluessel_merker)
        if alt and alt[0] == merker and time.monotonic() - alt[1] < MERKER_DAUER:
            return

        status = await self._schreiben(request, verbindung, nutzer, kontext, metadata, inhalt)
        if status in WECKEN:
            # Erster Kontakt oder nach einer Pause: Das Terminal startet erst (bis zu rund 90 s)
            await self._melden(emitter, 'Dein Arbeitsplatz startet gerade. Beim ersten Mal dauert das bis zu zwei Minuten.',
                               fertig=False)
            ende = time.monotonic() + 110
            while status in WECKEN and time.monotonic() < ende:
                await asyncio.sleep(10)
                status = await self._schreiben(request, verbindung, nutzer, kontext, metadata, inhalt)
            await self._melden(emitter, 'Dein Arbeitsplatz ist bereit.' if status == 200 else
                               'Dein Arbeitsplatz antwortet noch nicht.')
        if status == 200:
            _geschrieben[schluessel_merker] = (merker, time.monotonic())

    async def _verbindung(self, user_id, terminal_id, metadata):
        """Dieselben Prüfungen wie Open WebUI beim Lesen aus einem Terminal (utils/terminals.py, get_terminal_json):
        Verbindung eingeschaltet, Chat-Kontext erlaubt, Zugriffsrecht der Person. Gibt (Verbindung, Nutzer, Kontext)
        zurück oder None."""
        from open_webui.models.config import Config
        from open_webui.models.groups import Groups
        from open_webui.models.users import Users
        from open_webui.utils.access_control import has_connection_access
        from open_webui.utils.terminals import terminal_context_available, terminal_context_config, terminal_context_id

        verbindungen = await Config.get('terminal_server.connections', []) or []
        verbindung = next((v for v in verbindungen if v.get('id') == terminal_id), None)
        if not verbindung or not verbindung.get('enabled', True) or not terminal_context_available(verbindung, 'chat'):
            return None
        kontext = terminal_context_id(verbindung, metadata, 'chat')
        config = terminal_context_config(verbindung, 'chat')
        if isinstance(config, dict) and config.get('context_id') in {'chat_id', 'automation_id'} and not kontext:
            return None
        nutzer = await Users.get_user_by_id(user_id)
        if not nutzer:
            return None
        try:
            gruppen_liste = await Groups.get_groups_by_member_id(nutzer.id, include_inherited=True)
        except TypeError:  # ältere 0.12-Fassungen kennen include_inherited nicht
            gruppen_liste = await Groups.get_groups_by_member_id(nutzer.id)
        gruppen = {g.id for g in gruppen_liste or []}
        if not await has_connection_access(nutzer, verbindung, gruppen):
            return None
        return verbindung, nutzer, kontext

    async def _schreiben(self, request, verbindung, nutzer, kontext, metadata, inhalt) -> int:
        """Schreibt die Datei über die Datei-Schnittstelle von Open Terminal. Gibt den HTTP-Status zurück, 0 bei
        Netzfehlern oder Zeitüberschreitung."""
        from open_webui.utils.terminals import TERMINAL_CONTEXT_HEADER, get_terminal_server_url
        from open_webui.utils.tools import build_tool_server_headers
        try:
            from open_webui.env import AIOHTTP_CLIENT_SESSION_TOOL_SERVER_SSL as ssl_wert
        except Exception:
            ssl_wert = True

        kopf, kekse = await build_tool_server_headers(verbindung, request, nutzer, metadata=metadata)
        kopf['Accept'] = 'application/json'
        kopf['X-User-Id'] = nutzer.id
        if metadata.get('chat_id'):
            kopf['X-Session-Id'] = metadata['chat_id']
        if kontext:
            kopf[TERMINAL_CONTEXT_HEADER] = kontext
        basis = get_terminal_server_url(verbindung)
        optionen = {'headers': kopf, 'cookies': kekse or None, 'ssl': ssl_wert, 'allow_redirects': False}
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15), trust_env=True) as s:
                heim = '/home/user'
                async with s.get(basis + '/files/cwd', **optionen) as r:
                    if r.status in WECKEN:
                        return r.status
                    if r.status == 200:
                        heim = (await r.json()).get('home') or heim
                optionen['headers'] = {**kopf, 'Content-Type': 'application/json'}
                async with s.post(basis + '/files/write', json={'path': heim.rstrip('/') + '/.buildbar/zugang.env',
                                                                 'content': inhalt}, **optionen) as r:
                    return r.status
        except Exception:
            return 0

    async def _verlauf_saeubern(self, metadata, emitter, ersatz):
        """Open WebUI 0.12 speichert die Nachricht der Person schon zu Beginn der Anfrage (Verlauf im Chat, Tabelle
        chat_message, alte Liste chat.messages). Ohne diesen Schritt stünde ein eingefügter Schlüssel dort im Klartext,
        auch in Exporten und geteilten Chats. Danach gleicht das Ereignis chat:outlet die Anzeige an; originalContent
        wird mit überschrieben, sonst behielte die Oberfläche den alten Wortlaut dort."""
        chat_id = str(metadata.get('chat_id') or '')
        if not chat_id or chat_id.startswith(('local:', 'channel:')):
            return
        from open_webui.models.chats import Chats

        geaendert = []
        verlauf = await Chats.get_messages_map_by_chat_id(chat_id) or {}
        for mid, nachricht in verlauf.items():
            if not isinstance(nachricht, dict):
                continue
            felder = {}
            for feld in ('content', 'originalContent'):
                wert = nachricht.get(feld)
                if isinstance(wert, str) and MUSTER.search(wert):
                    neu = text_tauschen(wert, ersatz)
                    if neu != wert:
                        felder[feld] = neu
            if felder:
                inhalt = felder.get('content', nachricht.get('content'))
                felder['originalContent'] = inhalt
                await Chats.upsert_message_to_chat_by_id_and_message_id(chat_id, mid, felder, touch=False)
                geaendert.append({'id': mid, 'content': inhalt, 'originalContent': inhalt})

        chat = await Chats.get_chat_by_id(chat_id)
        daten = getattr(chat, 'chat', None) if chat else None
        if isinstance(daten, dict):
            felder, liste, anders = {}, [], False
            for m in daten.get('messages') or []:
                if isinstance(m, dict) and isinstance(m.get('content'), str) and MUSTER.search(m['content']):
                    neu = text_tauschen(m['content'], ersatz)
                    if neu != m['content']:
                        m, anders = {**m, 'content': neu}, True
                liste.append(m)
            if anders:
                felder['messages'] = liste
            titel = daten.get('title')
            if isinstance(titel, str) and MUSTER.search(titel):
                felder['title'] = text_tauschen(titel, ersatz)
            if felder:
                await Chats.update_chat_by_id(chat_id, felder, touch=False)

        if geaendert and emitter:
            try:
                await emitter({'type': 'chat:outlet', 'data': {'messages': geaendert}})
            except Exception:
                pass
