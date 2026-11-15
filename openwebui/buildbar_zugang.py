"""
title: buildbar Zugang
author: Friedemann Schütz
version: 0.4.4
required_open_webui_version: 0.12.0
description: Legt n8n-Schlüssel, Kürzel und Zugang zum Veröffentlichen unsichtbar im Terminal der Person ab. Fängt einen in den Chat eingefügten n8n-Schlüssel ab, bevor das Modell ihn sieht, und ersetzt ihn auch im gespeicherten Verlauf.
"""

# Filter für das Modell buildbar_ auf one.intelligence (Open WebUI 0.12). Nur an buildbar_ hängen, nicht global schalten.
#
# Bei jeder Nachricht (inlet):
# 1. Schlüssel im Token-Format werden sofort ersetzt: im Text für das Modell und im gespeicherten Verlauf (Open WebUI
#    speichert die Nachricht schon vor dem Filter). Erst danach wird ein neuer n8n-Schlüssel aus der NEUESTEN Nachricht
#    der Person bei n8n geprüft und in ihren persönlichen Einstellungen gespeichert (Benutzer-Valve
#    „n8n_schluessel_password“; verschlüsselt, wenn Open WebUI mit ENABLE_VALVE_ENCRYPTION=true läuft). Aus älteren
#    Nachrichten wird nie ein Schlüssel übernommen. Ein Schlüssel eines anderen n8n-Kontos ersetzt den gespeicherten
#    nicht, ein ungeprüfter (n8n nicht erreichbar) auch nicht. Ein mit Zeilenumbrüchen eingefügter Schlüssel (etwa aus
#    einer Mail kopiert) wird ganz erkannt und wieder zusammengesetzt. Die Person kann den Schlüssel auch direkt
#    eintragen: Chat, oben rechts „Steuerung“, „Valves“, „Funktionen“, „buildbar Zugang“.
# 2. Kürzel (aus dem Namen plus fünf Zeichen aus der Nutzer-ID), Nutzer-ID, persönliches Zugangszeichen zum
#    Veröffentlichen (HMAC aus dem Kurs-Geheimnis) und n8n-Schlüssel werden als ~/.buildbar/zugang.env in das Terminal
#    der Person geschrieben, mit denselben Zugriffsprüfungen wie in Open WebUI. Schläft das Terminal, wartet der Filter
#    mit einer Statusmeldung bis zu knapp zwei Minuten. Geschrieben wird, wenn sich etwas geändert hat, sonst alle 10 min.
# Vor jedem Modellaufruf (request), auch zwischen Werkzeugaufrufen: Schlüssel und das Zugangszeichen der Person werden
# aus allen Nachrichten, Werkzeugausgaben und Werkzeugargumenten ausgeblendet.
# Nach jeder Antwort (outlet): Werkzeugdetails, Verlauf und Chat-Titel werden noch einmal bereinigt.
# Seit 0.4.2 räumt der Filter außerdem auf, was in echten Läufen im Chat störte: den Platzhalter der Zwischenschicht
# („[System: Empty message content sanitised …]“) und englische Arbeitsnotizen zwischen Werkzeugaufrufen. Der Platzhalter
# entsteht, weil Open WebUI Werkzeugaufrufe mit leerem Text ('') weitergibt; der Filter schickt stattdessen keinen Text.
# Seit 0.4.3 erinnert er das Modell nur dann an Deutsch, wenn es wirklich eine englische Notiz geschrieben hat, seit
# 0.4.4 nur noch im Modellaufruf direkt danach und ohne etwas freizustellen (abschaltbar in den Einstellungen).
# Seit 0.4.4 merkt er sich außerdem, wenn die Person in diesem Chat um Kürze gebeten hat („zu lang“, „kürzer“, Eile,
# keine Zeit, Handy). Dann hängt er vor jedem Modellaufruf einen kurzen Hinweis ans Ende der Systemanweisung: Bis zum
# Ende des Chats gilt die kurze Form (abschaltbar). Hinweis und Erinnerung stehen nur in der Anfrage an das Modell, nie im
# gespeicherten Verlauf.
#
# Grenzen, die dieser Filter nicht schließen kann (für den Betrieb):
# - Erste Nachricht eines neuen Chats: Open WebUI erzeugt den Chat-Titel aus dem Klartext, bevor ein Filter läuft. Steht
#   der Schlüssel schon dort, sieht ihn das Titel-Modell. Der Filter meldet das und empfiehlt einen neuen Schlüssel.
# - Pipelines-Filter, Ereignis-Webhooks (message.created mit den ersten 300 Zeichen), Protokollstufe DEBUG und die
#   Kontext-Verdichtung sehen die Nachricht vor diesem Filter. In Produktion für Chats nicht einschalten.
# - In Kanälen bleibt die Nachricht für alle Mitglieder sichtbar; dort wird kein Schlüssel übernommen.
# - Der Assistent kann im Terminal jede Datei lesen. Was er liest, sieht das Sprachmodell dank request nicht, eine
#   eingeschleuste Anweisung könnte die Datei aber nach außen schicken. Die eigentliche Grenze ist das n8n-Konto.
# Fehler werden abgefangen und ohne Werte protokolliert: Eine Nachricht scheitert nie an einem Fehler dieses Filters.
# Einzige Ausnahme, gewollt: Antwortet das Terminal nach dem Warten noch nicht, bricht der Filter die Nachricht mit
# einem klaren Satz ab („Dein Arbeitsplatz startet gerade …“) und weckt im Hintergrund weiter. Sonst liefe Open WebUI
# gleich danach in eine eigene Zeitüberschreitung, und die Person sähe eine leere Fehlermeldung.

import asyncio
import base64
import functools
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

# Längen begrenzt, sonst rechnet das Muster bei langen Texten mit vielen „eyJ“ quadratisch
MUSTER = re.compile(r'eyJ[A-Za-z0-9_-]{8,200}\.[A-Za-z0-9_-]{8,4000}\.[A-Za-z0-9_-]{8,1000}')
# n8n-Schlüssel sind JWT mit festem Kopf (HS256) und genau 43 Zeichen Signatur. Dieses Muster erkennt sie auch dann
# vollständig, wenn beim Einfügen Zeilenumbrüche, Leerzeichen oder Zitatzeichen hineingeraten sind (bis 6 Zeichen je
# Stelle). Ohne das bliebe nach einem Umbruch der hintere Teil lesbar, und der Kopf ist bei jedem Schlüssel gleich.
_Z = r'[A-Za-z0-9_-]'
_LUECKE = r'[^A-Za-z0-9_.-]{0,6}'
_KOPF = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9'
_WEITER = _LUECKE + r'(?!eyJhbGciOi)' + _Z  # nie in den Anfang eines zweiten Schlüssels hineinlaufen
UMBRUCH = re.compile(_LUECKE.join(_KOPF) + _LUECKE + r'\.(?:' + _WEITER + r'){8,4000}?' + _LUECKE + r'\.(?:' + _WEITER + r'){43}')
_KEIN_ZEICHEN = re.compile(r'[^A-Za-z0-9_.-]+')
# Bruchstück eines Schlüssels (fester Anfang jedes JWT), wenn nur ein Teil eingefügt wurde
BRUCHSTUECK = re.compile(r'eyJhbGciOi[A-Za-z0-9_-]{12,400}(?:\.[A-Za-z0-9_-]{1,4000}){0,2}')
NEUTRAL = '[n8n-Schlüssel eingefügt]'
ERSATZ = '[n8n-Schlüssel eingefügt und sicher gespeichert]'
ERSATZ_ERSTE = ('[n8n-Schlüssel eingefügt und sicher gespeichert; Hinweis: Er stand in der ersten Nachricht des Chats und '
                'kann vorher für den Chat-Titel verarbeitet worden sein]')
ERSATZ_ABGELEHNT = '[n8n-Schlüssel eingefügt, aber n8n hat ihn abgelehnt]'
ERSATZ_NICHT_GESPEICHERT = '[n8n-Schlüssel eingefügt, aber nicht gespeichert]'
ERSATZ_FREMD = '[n8n-Schlüssel eingefügt, aber nicht übernommen: Er gehört zu einem anderen n8n-Konto als der bisherige]'
ERSATZ_UNGEPRUEFT = '[n8n-Schlüssel eingefügt und gespeichert, aber noch nicht geprüft: n8n war gerade nicht erreichbar]'
ERSATZ_NICHT_ERREICHBAR = '[n8n-Schlüssel eingefügt, aber nicht übernommen: n8n war gerade nicht erreichbar]'
ERSATZ_KANAL = '[n8n-Schlüssel eingefügt, aber nicht übernommen: In einem Kanal ist er für alle sichtbar]'
AUSGEBLENDET = '[Schlüssel ausgeblendet]'
WECKEN = {0, 502, 503, 504}  # Terminal schläft oder startet gerade
MERKER_DAUER = 600  # Sekunden; danach wird zugang.env auch ohne Änderung neu geschrieben
WECK_PAUSE = 120  # Sekunden; nach vergeblichem Wecken so lange nur ein kurzer Versuch je Nachricht
WECK_WARTEN = 90  # Sekunden, die eine Nachricht höchstens auf das startende Terminal wartet
NACHWECKEN = 240  # Sekunden, die danach im Hintergrund weiter geweckt wird
STARTET_NOCH = ('Dein Arbeitsplatz startet gerade. Das dauert nach einer Pause bis zu zwei Minuten. Bitte schick deine Nachricht '
                'in einer Minute noch einmal. Klappt es nach ein paar Minuten immer noch nicht, gib bitte der Betreuung Bescheid.')
_nachwecken: dict = {}  # laufende Weckversuche im Hintergrund je Terminal der Person
# Sekunden nach einer Antwort, zu denen nach den Vorschlagsknöpfen von Open WebUI gesehen wird (sie entstehen einige
# Sekunden nach der Antwort). Zu den Zeitpunkten in VORSCHLAEGE_GANZ wird zur Sicherheit der ganze Chat gelesen.
VORSCHLAEGE_ZEITEN = (1, 2, 3, 4, 5, 6.5, 8, 10, 12, 15, 18, 22, 27, 33, 40, 50, 60, 75, 90)
VORSCHLAEGE_GANZ = (8, 27)
_hintergrund: set = set()  # hält Hintergrundaufgaben am Leben, bis sie fertig sind


class Hinweis(Exception):
    """Ein Satz für die Person. Open WebUI zeigt ihn an Stelle der Antwort (als Meldung in der Antwortblase)."""
OHNE_VERLAUF = ('local:', 'temporary:', 'channel:')  # Chats, die nicht als gewöhnlicher Verlauf gespeichert sind
_geschrieben: dict = {}
_vergeblich: dict = {}


def _nutzlast(token: str) -> dict:
    """Der mittlere Teil eines JWT als Wörterbuch, sonst leer."""
    try:
        teil = token.split('.')[1]
        teil += '=' * (-len(teil) % 4)
        daten = json.loads(base64.urlsafe_b64decode(teil.encode()).decode())
    except Exception:
        return {}
    return daten if isinstance(daten, dict) else {}


def ist_n8n_schluessel(token: str) -> bool:
    """Ein n8n-API-Schlüssel ist ein JWT, dessen Inhalt aud=public-api trägt."""
    aud = _nutzlast(token).get('aud')
    return aud == 'public-api' or (isinstance(aud, list) and 'public-api' in aud)


def konto(token: str) -> str:
    """Die n8n-Nutzer-ID, zu der ein Schlüssel gehört (Feld sub), sonst leer."""
    return str(_nutzlast(token).get('sub') or '')


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


@functools.lru_cache(maxsize=64)
def _locker(wert: str):
    """Muster für einen bekannten geheimen Wert, das Leerraum zwischen den Zeichen erlaubt (umgebrochene Ausgabe)."""
    return re.compile(r'\s*'.join(re.escape(z) for z in wert))


def text_tauschen(text: str, ersatz: dict, gefunden: list = None, standard: str = ERSATZ, genau=()) -> str:
    """Blendet Schlüssel in einem Text aus. n8n-Schlüssel: bekannte nach `ersatz`, alle anderen durch `standard`.
    Jedes andere Token im JWT-Format und Bruchstücke davon werden zu AUSGEBLENDET. `genau` sind zusätzliche geheime
    Werte der Person, die wörtlich ausgeblendet werden, auch mit Leerraum dazwischen (etwa das Zugangszeichen)."""
    if not text:
        return text
    for wert in genau:
        if not wert or len(wert) < 16:
            continue
        if wert in text:
            text = text.replace(wert, AUSGEBLENDET)
        if wert[:8] in text:  # derselbe Wert noch einmal, mit Leerraum oder Zeilenumbruch dazwischen
            text = _locker(wert).sub(AUSGEBLENDET, text)
    if 'eyJ' not in text:
        return text

    def ersetze(m):
        tok = _KEIN_ZEICHEN.sub('', m.group(0))  # Umbrüche und Zitatzeichen aus dem Schlüssel nehmen
        if not ist_n8n_schluessel(tok):
            return AUSGEBLENDET
        if gefunden is not None and tok not in gefunden:
            gefunden.append(tok)
        return ersatz.get(tok, standard)
    text = UMBRUCH.sub(ersetze, text)  # vollständige Schlüssel, auch umgebrochen
    if 'eyJ' in text:
        text = MUSTER.sub(ersetze, text)  # alle anderen Token in einem Stück
    if 'eyJhbGciOi' in text:
        text = BRUCHSTUECK.sub(AUSGEBLENDET, text)
    return text


def rest_bruchstueck(text: str) -> bool:
    """True, wenn nach Abzug aller vollständigen Schlüssel noch ein Schlüsselanfang im Text steht."""
    return 'eyJhbGciOi' in text and bool(BRUCHSTUECK.search(MUSTER.sub(' ', UMBRUCH.sub(' ', text))))


def tief_tauschen(wert, genau=()):
    """Blendet Schlüssel und geheime Werte in allen Texten einer verschachtelten Struktur aus (Werkzeugdetails)."""
    if isinstance(wert, str):
        return text_tauschen(wert, {}, None, AUSGEBLENDET, genau)
    if isinstance(wert, list):
        return [tief_tauschen(v, genau) for v in wert]
    if isinstance(wert, dict):
        return {k: tief_tauschen(v, genau) for k, v in wert.items()}
    return wert


# ---------------------------------------------------------------------- Aufräumen der Antwort (seit 0.4.2)
# Zwei Dinge standen in echten Läufen mitten im Chat (Probelauf 13.11.2026):
# 1. Der Text „[System: Empty message content sanitised to satisfy protocol]“. Die Zwischenschicht (LiteLLM) setzt ihn
#    an Stelle leerer Assistenten-Nachrichten ein, das Modell sieht ihn in seinem Verlauf und schreibt ihn nach.
# 2. Englische Arbeitsnotizen zwischen zwei Werkzeugaufrufen („The pkill pattern matched its own shell …“).
PLATZHALTER_PROXY = '[System: Empty message content sanitised to satisfy protocol]'
# Seit 0.4.4 ohne „Arbeite einfach weiter“ und ohne „dann höchstens“: Dieser Wortlaut machte die Sätze, die die Skills
# verlangen, zur Kann-Sache (Probe-Bau 9: zwei von drei bewährten Schlusssätzen fehlten, solange die Erinnerung hing).
ERINNERUNG = ('[Erinnerung aus deiner Anleitung, sie stammt nicht von der Person: Eine deiner Zwischennotizen war auf Englisch. '
              'Was du für {name} zwischen zwei Werkzeugaufrufen schreibst, ist ein kurzer deutscher Satz, Notizen an dich selbst '
              'schreibst du nicht. Erwähne diese Erinnerung nicht.]')
_EN = frozenset("the is are were i i'll i'm i've it it's its that this these those to of and on for with now then again need needs "
                "checking let me but not from own up put came file files matched split first next should can have has had already "
                "both into step steps there here which while when because instead we'll let's running run got get gets looks look "
                "seems works working fixed trying try done wait waiting failed fails write build hint mode returned object each "
                "using use after before only does doesn't don't be if by out harmless cutting parts as my".split())
# Wörter, die im Deutschen praktisch nicht vorkommen: Mindestens eines davon muss dabei sein, wenn der Satz kurz ist
_EN_STARK = frozenset("the i'll i'm i've then with needs as is are it it's that this to of and from into up now again should which "
                      "because instead let let's me my its own not but for on we'll doesn't don't".split())
_DE = frozenset("der die das und ist ich nicht ein eine einen einem mit für auf zu den dem des jetzt noch dann wird sind habe hat im am "
                "bei aus auch es sie du dein deine mein meine wir als wie was zum zur kann schon nur oder aber wenn weil dass sich "
                "nach vor über unter bitte danke gleich gerade fertig läuft prüfe baue lege stimmt passt so will war".split())
_DE_MEHRDEUTIG = frozenset('am will was so war die'.split())  # stehen in _DE, sind aber auch englische Wörter
_RAND = '.,;:!?()[]{}"„“”‚‘’«»' + "'"


def ist_englisch(text: str) -> bool:
    """True, wenn ein kurzer Text nach einer englischen Arbeitsnotiz aussieht. Wortlisten, bewusst vorsichtig: Namen von
    Dateien, Befehlen und Code zählen nicht mit, und ohne ein eindeutig englisches Wort gilt nichts als Englisch."""
    text = re.sub(r'`[^`]*`', ' ', text or '')
    woerter = []
    for stueck in text.split():
        kern = stueck.strip(_RAND).lower()
        if not kern or any(z in kern for z in './_-@:=<>' + chr(92)) or any(z.isdigit() for z in kern):
            continue  # Dateinamen, Pfade, Befehle, Zahlen
        woerter.append(kern)
    if len(woerter) < 3:
        return False
    en = sum(1 for w in woerter if w in _EN)
    stark = sum(1 for w in woerter if w in _EN_STARK)
    # Wörter, die es in beiden Sprachen gibt („I am“, „it was“, „I will“), zählen nur dann als Deutsch, wenn der Satz
    # auch ein eindeutig deutsches Wort hat. Sonst blieb „Now I am stopping the server.“ stehen (Labor, 13.11.2026).
    sicher = sum(1 for w in woerter if (w in _DE and w not in _DE_MEHRDEUTIG) or any(z in w for z in 'äöüß'))
    de = sicher + (sum(1 for w in woerter if w in _DE_MEHRDEUTIG) if sicher else 0)
    anteil = en / len(woerter)
    if en >= 3 and stark >= 1 and en >= 2 * (de + 1) and anteil >= 0.22:
        return True
    return en >= 2 and stark >= 1 and de == 0 and anteil >= 0.3


_SATZ_ENDE = re.compile(r'(?<=[.!?])\s+')


def ohne_englische_saetze(text: str) -> str:
    """Nimmt aus einem Zwischentext die englischen Sätze heraus, die deutschen bleiben stehen. Ohne englischen Satz kommt
    der Text unverändert zurück. Texte mit Codeblock bleiben, wie sie sind."""
    if not text or '```' in text:
        return text
    anders, zeilen = False, []
    for zeile in text.split('\n'):
        saetze = _SATZ_ENDE.split(zeile)
        bleiben = [s for s in saetze if not ist_englisch(s)]
        anders = anders or len(bleiben) != len(saetze)
        zeilen.append(' '.join(bleiben))
    if not anders:
        return text
    return re.sub(r'\n{3,}', '\n\n', '\n'.join(zeilen)).strip()


def enthaelt_englisch(text: str) -> bool:
    """True, wenn der Text als Ganzes oder einer seiner Sätze nach einer englischen Arbeitsnotiz aussieht."""
    return bool(text) and (ist_englisch(text) or ohne_englische_saetze(text) != text)


def _text_des_elements(element: dict) -> str:
    """Der Text eines Ausgabe-Elements vom Typ message (Inhalt als Text oder als Liste von Teilen)."""
    inhalt = element.get('content')
    if isinstance(inhalt, str):
        return inhalt
    if isinstance(inhalt, list):
        return '\n'.join(str(t.get('text')) for t in inhalt if isinstance(t, dict) and isinstance(t.get('text'), str))
    return ''


def _mit_text(element: dict, text: str) -> dict:
    """Kopie eines Ausgabe-Elements vom Typ message mit neuem Text. Bei einer Liste von Teilen trägt der erste Textteil
    den neuen Text, die übrigen Textteile entfallen, alles andere bleibt."""
    neu = dict(element)
    inhalt = element.get('content')
    if isinstance(inhalt, list):
        teile, gesetzt = [], False
        for t in inhalt:
            if isinstance(t, dict) and isinstance(t.get('text'), str):
                if not gesetzt:
                    teile.append(dict(t, text=text))
                    gesetzt = True
            else:
                teile.append(t)
        neu['content'] = teile
    else:
        neu['content'] = text
    return neu


def ausgabe_aufraeumen(ausgabe):
    """Nimmt aus den Ausgabe-Elementen einer Antwort den Platzhalter der Zwischenschicht und englische Arbeitsnotizen
    zwischen Werkzeugaufrufen heraus. Die letzte Textnachricht (die eigentliche Antwort) bleibt immer stehen, dort wird
    nur der Platzhalter entfernt. Gibt (neue Liste, Text der verbleibenden Nachrichten) zurück."""
    if not isinstance(ausgabe, list):
        return ausgabe, None
    texte = [i for i, e in enumerate(ausgabe) if isinstance(e, dict) and e.get('type') == 'message']
    if not texte:
        return ausgabe, None
    letzte, neu = texte[-1], []
    for i, e in enumerate(ausgabe):
        if i not in texte:
            neu.append(e)
            continue
        text = _text_des_elements(e)
        if PLATZHALTER_PROXY in text:
            e = dict(e)
            if isinstance(e.get('content'), str):
                e['content'] = e['content'].replace(PLATZHALTER_PROXY, '').strip()
            elif isinstance(e.get('content'), list):
                e['content'] = [dict(t, text=t['text'].replace(PLATZHALTER_PROXY, '').strip()) if isinstance(t, dict) and isinstance(t.get('text'), str) else t
                                for t in e['content']]
            text = _text_des_elements(e)
        if i != letzte:
            if not text.strip() or ist_englisch(text):
                continue
            # Gemischte Zwischentexte („Der Server antwortet. Now I am stopping the server.“): nur die englischen Sätze gehen
            gekuerzt = ohne_englische_saetze(text)
            if gekuerzt != text:
                if not gekuerzt.strip():
                    continue
                e = _mit_text(e, gekuerzt)
        neu.append(e)
    inhalt = '\n'.join(t for t in (_text_des_elements(e).strip() for e in neu if isinstance(e, dict) and e.get('type') == 'message') if t)
    return neu, inhalt


async def _vorschlaege_gespeichert(chat_id: str, nachricht_id: str, ganz: bool = False) -> bool:
    """True, wenn bei dieser Nachricht Vorschlagsknöpfe gespeichert sind. Sie stehen nur im gespeicherten Chat (Feld
    followUps), nicht in der Nachrichtentabelle: Chats.get_message_by_id_and_message_id liefert sie deshalb nicht (so
    blieben die Knöpfe bis 0.4.2 stehen). Gelesen wird gezielt dieses eine Feld (geprüft mit SQLite und Postgres).
    ganz=True liest zur Sicherheit den ganzen Chat über die gewöhnliche Funktion von Open WebUI."""
    if not ganz:
        try:
            from sqlalchemy import select
            from open_webui.internal.db import get_async_db_context
            from open_webui.models.chats import Chat
            async with get_async_db_context() as sitzung:
                ergebnis = await sitzung.execute(select(Chat.chat[('history', 'messages', nachricht_id, 'followUps')]).filter_by(id=chat_id))
                zeile = ergebnis.one_or_none()
            wert = zeile[0] if zeile else None
            if isinstance(wert, str):
                try:
                    wert = json.loads(wert)
                except ValueError:
                    wert = None
            return bool(wert)
        except Exception as e:
            log.warning('buildbar Zugang, Vorschlagsknöpfe (gezielt lesen): %s', type(e).__name__)
            return False
    from open_webui.models.chats import Chats
    chat = await Chats.get_chat_by_id(chat_id)
    inhalt = getattr(chat, 'chat', None) or {}
    nachricht = ((inhalt.get('history') or {}).get('messages') or {}).get(nachricht_id) or {}
    return bool(nachricht.get('followUps'))


def _bilder_der_werkzeuge(nachricht) -> bool:
    """True für die Nachricht, die Open WebUI selbst aus Bildern in Werkzeugausgaben macht (Rolle user, kein Text der Person)."""
    return (isinstance(nachricht, dict) and nachricht.get('role') == 'user'
            and any(t.startswith(BILDER_VON_WERKZEUGEN) for t in _texte(nachricht)))


def englische_notiz_davor(nachrichten: list) -> bool:
    """True, wenn der Assistent im Modellaufruf direkt vor den Werkzeugergebnissen am Ende eine englische Arbeitsnotiz
    geschrieben hat. Seit 0.4.4 zählt nur dieser eine Aufruf: Bis 0.4.3 galt die ganze Werkzeugfolge seit der letzten
    Nachricht der Person, und im Probe-Bau 9 hing die Erinnerung nach einer einzigen Notiz an 20 Modellaufrufen."""
    i = len(nachrichten) - 1
    werkzeug = False
    while i >= 0 and isinstance(nachrichten[i], dict) and (nachrichten[i].get('role') == 'tool' or _bilder_der_werkzeuge(nachrichten[i])):
        werkzeug = werkzeug or nachrichten[i].get('role') == 'tool'
        i -= 1
    if not werkzeug:
        return False
    while i >= 0 and isinstance(nachrichten[i], dict) and nachrichten[i].get('role') == 'assistant':
        if any(enthaelt_englisch(t) for t in _texte(nachrichten[i])):
            return True
        i -= 1
    return False


def verlauf_fuer_modell_aufraeumen(body: dict, erinnern: bool = True, name: str = '') -> None:
    """Vor jedem Modellaufruf: Platzhalter der Zwischenschicht aus den Assistenten-Nachrichten nehmen und leere
    Assistenten-Nachrichten mit Werkzeugaufruf ohne Text schicken (dann setzt die Zwischenschicht keinen Platzhalter ein;
    gemessen an one.intelligence am 13.11.2026: Inhalt '' kostet 22 Eingabe-Token mehr als Inhalt None).
    Hat das Modell im Aufruf direkt davor trotzdem eine englische Notiz geschrieben, kommt genau in diesen nächsten Aufruf
    eine kurze Erinnerung an Deutsch und an die Person, danach nicht mehr. Sie steht nur in der Anfrage an das Modell,
    nicht im gespeicherten Verlauf. Ohne englische Notiz kommt nichts dazu: Eine Erinnerung bei jedem Schritt brachte im
    Test nur mehr Zwischensätze."""
    nachrichten = body.get('messages')
    if not isinstance(nachrichten, list):
        return
    for n in nachrichten:
        if not isinstance(n, dict) or n.get('role') != 'assistant':
            continue
        inhalt = n.get('content')
        if isinstance(inhalt, str):
            if PLATZHALTER_PROXY in inhalt:
                inhalt = inhalt.replace(PLATZHALTER_PROXY, '').strip()
                n['content'] = inhalt
            if not inhalt.strip() and n.get('tool_calls'):
                n['content'] = None
        elif isinstance(inhalt, list):
            for teil in inhalt:
                if isinstance(teil, dict) and isinstance(teil.get('text'), str) and PLATZHALTER_PROXY in teil['text']:
                    teil['text'] = teil['text'].replace(PLATZHALTER_PROXY, '').strip()
            if n.get('tool_calls') and not any(isinstance(t, dict) and str(t.get('text') or '').strip() for t in inhalt if isinstance(t, dict) and t.get('type') in (None, 'text')):
                rest = [t for t in inhalt if not (isinstance(t, dict) and t.get('type') in (None, 'text'))]
                n['content'] = rest or None
    if (erinnern and nachrichten and englische_notiz_davor(nachrichten)
            and not any(t.startswith(_ERINNERUNG_ANFANG) for t in _texte(nachrichten[-1]))):
        nachrichten.append({'role': 'user', 'content': ERINNERUNG.format(name=_name(name, 'die Person'))})


def _texte(nachricht: dict) -> list:
    """Alle Texte einer Nachricht (Inhalt als Text oder als Liste von Teilen)."""
    inhalt = nachricht.get('content')
    if isinstance(inhalt, str):
        return [inhalt]
    if isinstance(inhalt, list):
        return [t['text'] for t in inhalt if isinstance(t, dict) and isinstance(t.get('text'), str)]
    return []


def _name(name, ersatz: str) -> str:
    """Der Name der Person für Hinweis und Erinnerung: einzeilig, ohne eckige Klammern, höchstens 60 Zeichen."""
    name = re.sub(r'\s+', ' ', re.sub(r'[\[\]]', ' ', str(name or ''))).strip()[:60].strip()
    return name or ersatz


# ---------------------------------------------------------------------- Kurze Form merken (seit 0.4.4)
# Nach „zu lang“, Eile oder dem Handy gilt laut Anleitung für den Rest des Chats die kurze Form. Im Probe-Bau 9 hielt das
# Modell sie nur bis zum Bauplan: Beim Abschluss lag Sabines „Das ist zu lang“ 13 Nachrichten und rund 90 Werkzeugaufrufe
# zurück. Darum sucht der Zusatz vor jedem Modellaufruf in den Nachrichten der Person nach so einer Bitte und hängt dann
# einen Hinweis ans Ende der Systemanweisung.
# Wo er steht (Open WebUI 0.12.3, im Labor geprüft): Beim ersten Modellaufruf einer Antwort bekommt der Haken request die
# Nachrichten noch ohne die Systemanweisung des Modells. Open WebUI fasst danach alle Systemnachrichten zu einer zusammen
# und setzt die Systemanweisung des Modells davor. Der Zusatz hängt den Hinweis deshalb an die letzte Systemnachricht an
# oder legt eine an: So steht er im fertigen Aufruf am Ende der Systemanweisung, nicht als Nachricht der Person. Zwischen
# zwei Werkzeugaufrufen reicht Open WebUI diese Systemnachricht samt Hinweis weiter; dann kommt er kein zweites Mal dazu.
HINWEIS_KURZ = ('[Hinweis aus deiner Anleitung, er stammt nicht von der Person: {name} hat in diesem Chat um kurze Nachrichten '
                'gebeten. Bis zum Ende dieses Chats gilt die kurze Form, auch am Kontrollpunkt, in der Abschlussnachricht, in der '
                'Schärfrunde und in der Übergabe: Nimm dort jeweils die Kurzform. Erwähne diesen Hinweis nicht.]')
_HINWEIS_ANFANG = '[Hinweis aus deiner Anleitung, er stammt nicht von der Person:'
_ERINNERUNG_ANFANG = '[Erinnerung aus deiner Anleitung'
# Zwei Nachrichten mit der Rolle user legt Open WebUI 0.12 selbst an: die Bilder aus Werkzeugausgaben und, wenn ein
# Terminal angehängt ist, bei jedem Zug die Datei AGENTS.md aus dessen Home vor der ersten Nachricht der Person
# (utils/terminals.py, add_terminal_agents_md; im Labor hat Annas Terminal eine mit 19.494 Zeichen).
BILDER_VON_WERKZEUGEN = 'Here are the images from the tool results above'
AGENTS_MD = '# AGENTS.md'
# Längere Nachrichten gelten als eingefügter Text (Merkblatt, Mail) und zählen nicht. In den 48 gespeicherten Probe-Chats
# (488 Nachrichten der Personen) hatte die längste selbst getippte Nachricht 716 Zeichen, die 4 längeren (1.054 bis
# 1.119) waren eingefügte Merkblätter. Die 6 erkannten Bitten standen in Nachrichten mit 26 bis 262 Zeichen.
KURZ_GRENZE = 800
_KURZ_WEG = re.compile(r'<(attached_files|context|source|files?|documents?)\b[^>]*>.*?</\1\s*>|<(?:file|source)\b[^>]*/>'
                       r'|```.*?```|`[^`\n]*`|https?://\S+|www\.\S+', re.S | re.I)
# Zitate zählen nicht: In Anwendungen für den Tourismus zitiert die Person oft Gäste („Wartezeit zu lang“) oder Texte
_KURZ_ZITAT = re.compile(r'„[^“”"\n]{0,400}[“”"]|“[^”\n]{0,400}”|"[^"\n]{0,400}"|»[^«\n]{0,400}«|«[^»\n]{0,400}»|‚[^‘’\n]{0,400}[‘’]')
# Sätze und Satzteile: getrennt an Satzzeichen und Bindewörtern. Nicht am Doppelpunkt: „Überschrift: zu lang“ meint die
# Überschrift.
_KURZ_SATZZEICHEN = re.compile(r'[.!?;\n\r…()\[\]{}/|,–—]+')
_KURZ_BINDEWORT = frozenset('- und aber oder denn weil sondern deshalb darum daher also dann sonst trotzdem ausserdem'.split())
_KURZ_UMLAUTE = (('ä', 'ae'), ('ö', 'oe'), ('ü', 'ue'), ('ß', 'ss'))
_KURZ_WORT = re.compile(r'[a-z0-9]+')
_KURZ_VERNEINUNG = frozenset('nicht nich net kein keine keinen keiner keinem keines nie niemals ohne nirgends'.split())
_KURZ_DU = frozenset('du dich dir dein deine deinen deinem deiner deines euch eure euer'.split())
# Was zu lang sein kann. Ohne „du“ oder „dein“ daneben bleibt offen, ob die Nachricht des Assistenten gemeint ist oder ein
# Text der Anwendung („Die Antwort ist zu lang“ in einer Anwendung, die Antworten auf Bewertungen schreibt). „Nachricht“
# meint im Chat fast immer die Nachrichten des Assistenten und steht deshalb unter den allgemeinen Wörtern.
_KURZ_BEZUG = frozenset('text texte antwort antworten erklaerung erklaerungen zusammenfassung satz saetze'.split())
# Wörter, die in einem Satz neben dem Auslöser stehen dürfen: Füllwörter, Pronomen, Hilfsverben, Höflichkeit. Steht noch
# etwas anderes im Satz („Das Ergebnis ist zu lang für die Tabelle“), geht es um etwas anderes als den Chat.
_KURZ_ALLGEMEIN = frozenset('''ich mir mich mein meine meinem meinen meiner du dich dir dein deine deinen deinem deiner deines
    euch eure euer es das dies diese dieser dieses der die den dem des ein eine einen einem einer ist sind war waren wird werden
    wurde waere waer bin bins hab habe habs kannst koenntest kann koennte geht gehts ginge wuerdest wuerde sollst soll sollte
    magst muss musst bitte danke sorry sry oh ah puh uff oje hm hmm na ja jo ok okay echt wirklich ziemlich sehr total viel
    etwas bisschen bissl bissel einfach irgendwie leider ehrlich gesagt schon noch immer mal nur gerade grad grade jetzt heute
    momentan ab sofort kuenftig zukunft in weiterhin generell insgesamt eigentlich halt eben eh doch so auch wieder ganz schoen
    hier da alles alle finde find fand denke glaube lesen zum zu fuer mehr nachricht nachrichten'''.split())
# Am Handy zählt nur, wenn die Person dabei von sich spricht („Ich bin am Handy“, „Bin gerade am Handy“, „ist mir am Handy
# zu viel“). Sonst ist meist die Anwendung gemeint: „Geht das auch am Handy?“, „Am Rechner passt es, am Handy auch.“ oder
# die Antwort auf „eher am Handy oder am Rechner“ aus dem Steckbrief (Gegenprüfung Runde 9).
_KURZ_ICH = frozenset('''ich mir mich mein meine meinem meinen meiner bin bins hab habe habs schreibe schreib tippe tipp lese les
    sitze haenge unterwegs gerade grad grade momentan'''.split())
# Nach diesen Wörtern ist „antworten“ das Verb („kürzer antworten“, „kurz und knapp antworten“), keine Antworten der Anwendung
_KURZ_VOR_VERB = frozenset('kurz kuerzer knapper knapp kurzundknapp lang ausfuehrlich'.split())
# „Ist das zu lang?“ fragt nach einem Text der Person oder der Anwendung; „Das ist zu lang“ und „Ist mir zu lang“ bitten
_KURZ_FRAGE = re.compile(r'(?:ist|sind|war|waren|waere|waer|wird|werden|wuerde|wirds)\s+(?:das|es|der|die|dies|diese|dieser|dieses|er|sie)\b')


def _woerter(text: str) -> frozenset:
    return frozenset(text.split())


# Auslöser je Art: (Muster, weitere erlaubte Wörter, nötiges Wort oder None (ein Bitte-Wort, beim Handy ein Wort der Person),
# Bezugswort nur mit „du“/„dein“, Stücke, von denen jeder Auslöser der Art eines enthält: Fehlen sie, läuft das Muster gar
# nicht erst)
_KURZ_FAMILIEN = (
    # zu lang, viel zu lang, zu ausführlich, zu viel Text, weniger Text
    (re.compile(r'\b(?:viel\s+)?zu\s+lange?\b|\bzulange?\b|\bzu\s+ausfuehrlich\b|\b(?:zu\s+)?viel\s+text\b|\bweniger\s+text\b'),
     _woerter('lang lange zulang zulange ausfuehrlich weniger text uns'), None, True, ('lang', 'ausfuehrlich', 'text')),
    # „bitte nicht so lang“ ist eine Bitte, „nicht zu lang“ allein dagegen eine Entwarnung
    (re.compile(r'\bbitte\s+nicht\s+(?:mehr\s+)?(?:so|zu)\s+(?:lange?|ausfuehrlich|viel)\b|\bnicht\s+(?:so|zu)\s+(?:lange?|ausfuehrlich)\s+bitte\b'
                r'|\b(?:schreib|schreibe|antworte)\s+(?:bitte\s+)?nicht\s+(?:mehr\s+)?(?:so|zu)\s+(?:lange?|viel|ausfuehrlich)\b'),
     _woerter('lang lange ausfuehrlich text texte uns schreib schreibe schreiben antworte antworten'), None, True,
     ('lang', 'ausfuehrlich', 'viel')),
    # kürzer, knapper
    (re.compile(r'\bkuerzer(?:e|en|es)?\b|\bknapper(?:e|en)?\b'),
     _woerter('schreib schreibe schreiben schreibst antworte antworten antwortest fass fasse fassen formulier formuliere formulieren '
              'halte halten halts sein bleiben bleib deutlich als bisher vorhin zuletzt um gern gerne lieber moechte wuensche haette'),
     None, True, ('kuerzer', 'knapper')),
    # kurz bitte, fass dich kurz, kurz und knapp, kurz halten, Kurzfassung bitte; „kurz“ allein ist zu häufig („kurz prüfen“)
    (re.compile(r'\bkurzundknapp\b|\bkurz(?:fassen|fassung|form|version|halten)?\b|\bkurzen?\s+(?:antworten|nachrichten)\b'),
     _woerter('schreib schreibe schreiben antworte antworten fass fasse fassen halte halten halts gehalten knapp moeglichst reicht '
              'reichen genuegt lieber gern gerne moechte wuensche haette um'),
     _woerter('bitte dich halten halte halts gehalten knapp fassen fass fasse reicht reichen genuegt lieber gern gerne moechte '
              'wuensche'), True, ('kurz',)),
    # in Eile, eilig, es eilt, Zeitdruck, im Stress, muss gleich los
    (re.compile(r'\b(?:in\s+)?eile\b|\beilig\b|\beilt\b|\bzeitdruck\b|\bim\s+stress\b|\bgestresst\b|\bbeeilen\b'
                r'|\bmuss\s+(?:gleich|bald|sofort|jetzt)\s+(?:los|weg)\b'),
     _woerter('in es sehr ziemlich stehe unter im muss gleich los weg bald sofort'), None, False,
     ('eile', 'eilig', 'eilt', 'zeitdruck', 'stress', 'muss')),
    # keine Zeit, wenig Zeit, nur fünf Minuten
    (re.compile(r'\b(?:keine|kaum|wenig|nicht\s+viel|nur\s+wenig|nur\s+kurz|nicht\s+lange)\s+zeit\b'
                r'|\bnur\s+(?:\d+|ein\s+paar|zwei|drei|fuenf|zehn|wenige)\s+minuten\b'),
     _woerter('zeit minuten lange langen texte nachrichten sowas so was dafuer'), None, False, ('zeit', 'minuten')),
    # am Handy, vom Handy, auf dem Smartphone
    (re.compile(r'\b(?:am|vom|auf\s+dem|auf\s+meinem|uebers|ueber\s+das|ueber\s+mein|mit\s+dem|mit\s+meinem)\s+(?:handy|smartphone|iphone)\b'),
     _woerter('aus unterwegs mobil schreibe schreib tippe tipp lese les sitze haenge beides auf einmal lang lange nachrichten antworten'),
     _KURZ_ICH, False, ('handy', 'smartphone', 'iphone')),
    # nur das Wichtigste
    (re.compile(r'\b(?:nur|bloss)\s+(?:das|die)\s+wichtigsten?\b'),
     _woerter('sag sage schreib schreibe reicht genuegt'), None, False, ('wichtigst',)),
)
_KURZ_VORAB = tuple(sorted({s for f in _KURZ_FAMILIEN for s in f[4]}))


def _satz_bittet_um_kuerze(satz: str, arten=_KURZ_FAMILIEN) -> bool:
    treffer, stellen = [], []
    for art in arten:
        if any(s in satz for s in art[4]):
            for m in art[0].finditer(satz):
                treffer.append((m.group(0), art))
                stellen.append(m.span())
    if not treffer or _KURZ_FRAGE.match(satz):
        return False
    # Was außer den Auslösern im Satz steht. Alle Auslöser kommen heraus, auch die anderer Arten („Ich bin am Handy:
    # bitte kurz“) und überlappende („bitte nicht zu lang“ enthält „zu lang“).
    zeichen = list(satz)
    for anfang, ende in stellen:
        zeichen[anfang:ende] = ' ' * (ende - anfang)
    rest = _KURZ_WORT.findall(''.join(zeichen))
    woerter = set(rest)
    if woerter & _KURZ_VERNEINUNG:
        return False  # „nicht zu lang“, „keine Eile“, „bin nicht am Handy“
    folge = _KURZ_WORT.findall(satz)
    verb = {'antworten'} if any(w == 'antworten' and i and folge[i - 1] in _KURZ_VOR_VERB for i, w in enumerate(folge)) else set()
    for gefunden, (_, erlaubt, bitte, bezug, _) in treffer:
        if bitte is not None and gefunden != 'kurzundknapp' and not woerter & bitte:
            continue  # „kurz“ allein: „Ich schaue kurz nach“; „am Handy“ ohne die Person: „Geht das auch am Handy?“
        if bezug and (woerter & _KURZ_BEZUG) - verb and not woerter & _KURZ_DU:
            continue  # „Die Antwort ist zu lang“ kann die Anwendung meinen
        if all(w in _KURZ_ALLGEMEIN or w in erlaubt or w in _KURZ_BEZUG for w in rest):
            return True
    return False


def bittet_um_kuerze(text: str) -> bool:
    """True, wenn ein Text der Person um kurze Nachrichten bittet oder Eile, keine Zeit oder das Handy nennt. Bewusst
    vorsichtig: Ein Satz zählt nur, wenn außer dem Auslöser nur Wörter darin stehen, die zum Chat oder zur Lage der Person
    passen. Nicht zählen: Texte über KURZ_GRENZE Zeichen, Verneintes („nicht zu lang“, „keine Eile“), Zitate, Code, Links,
    angehängte Dateien und Wörter, die den Auslöser nur enthalten („Zulassung“, „Handyhülle“)."""
    if not isinstance(text, str) or not text.strip() or len(text) > 20 * KURZ_GRENZE:
        return False  # leer, oder ein ganzes eingefügtes Dokument
    if '<user_query>' in text:  # Vorlage für Quellen (RAG): nur die Frage der Person zählt
        frage = re.search(r'<user_query>(.*?)</user_query>', text, re.S)
        text = frage.group(1) if frage else ''
    klein = _kurz_klein(text)
    stuecke = [s for s in _KURZ_VORAB if s in klein]
    if not stuecke:
        return False  # schneller Vorfilter: kein Stück eines Auslösers im Text
    arten = [art for art in _KURZ_FAMILIEN if any(s in stuecke for s in art[4])]
    roh = text
    if text.count('<') > 30:
        return False  # eingefügtes HTML oder XML; bei vielen offenen Marken rechnete _KURZ_WEG über eine Sekunde
    if '<' in text or '`' in text or 'http' in text or 'www.' in text:
        text = _KURZ_WEG.sub(' ', text)
    if any(z in text for z in '„“"»«‚'):
        text = _KURZ_ZITAT.sub(' ', text)
    text = text.strip()
    if not text or len(text) > KURZ_GRENZE:
        return False
    if text != roh.strip():
        klein = _kurz_klein(text)
    t = re.sub(r'\bkurz\s+(?:und|&)\s+knapp\b', 'kurzundknapp', re.sub(r"['’`´]", '', klein))
    for teil in _KURZ_SATZZEICHEN.split(t):
        if not any(s in teil for s in stuecke):
            continue
        satz = []
        for wort in teil.split() + ['-']:  # das angehängte „-“ schließt den letzten Satzteil ab
            if wort not in _KURZ_BINDEWORT:
                satz.append(wort)
            elif satz:
                if _satz_bittet_um_kuerze(' '.join(satz), arten):
                    return True
                satz = []
    return False


def _kurz_klein(text: str) -> str:
    t = text.lower()
    for alt, neu in _KURZ_UMLAUTE:
        t = t.replace(alt, neu)
    return t


_kurz_merker: dict = {}  # Ergebnis je Nachricht; Schlüssel ist eine Prüfsumme, nicht der Text


def _bittet_gemerkt(text: str) -> bool:
    """bittet_um_kuerze mit Gedächtnis: Vor jedem Modellaufruf kommen dieselben Nachrichten wieder (bis zu 90 Aufrufe je
    Antwort); jede wird nur einmal geprüft."""
    schluessel = hashlib.blake2b(text.encode('utf-8', 'replace'), digest_size=16).digest()
    ergebnis = _kurz_merker.get(schluessel)
    if ergebnis is None:
        ergebnis = bittet_um_kuerze(text)
        if len(_kurz_merker) >= 4096:
            _kurz_merker.clear()
        _kurz_merker[schluessel] = ergebnis
    return ergebnis


def um_kuerze_gebeten(nachrichten: list) -> bool:
    """True, wenn die Person in einer ihrer Nachrichten dieses Chats um Kürze gebeten hat. Es zählen nur Nachrichten mit
    der Rolle user, nicht Werkzeugausgaben, nicht Hinweis und Erinnerung dieses Zusatzes, nicht die Nachrichten, die Open
    WebUI selbst anlegt (Bilder aus Werkzeugen, AGENTS.md des Terminals), und nur Textteile (keine Bilder, keine
    angehängten Dateien)."""
    for n in nachrichten:
        if not isinstance(n, dict) or n.get('role') != 'user' or _bilder_der_werkzeuge(n):
            continue
        text = '\n'.join(_texte(n)).strip()
        if not text or text.startswith((_HINWEIS_ANFANG, _ERINNERUNG_ANFANG, AGENTS_MD)):
            continue
        if _bittet_gemerkt(text):
            return True
    return False


def _mit_hinweis(nachricht: dict, hinweis: str) -> dict:
    """Kopie einer Systemnachricht mit dem Hinweis am Ende. Bei einer Liste von Teilen kommt er in den ersten Textteil:
    Nur den übernimmt Open WebUI, wenn es die Systemnachrichten zusammenfasst (merge_system_messages)."""
    neu = dict(nachricht)
    inhalt = nachricht.get('content')
    if isinstance(inhalt, list):
        teile, gesetzt = [], False
        for t in inhalt:
            if not gesetzt and isinstance(t, dict) and t.get('type') == 'text' and isinstance(t.get('text'), str):
                t, gesetzt = dict(t, text=(t['text'].rstrip() + '\n\n' + hinweis) if t['text'].strip() else hinweis), True
            teile.append(t)
        neu['content'] = teile if gesetzt else [{'type': 'text', 'text': hinweis}, *inhalt]
    elif isinstance(inhalt, str) and inhalt.strip():
        neu['content'] = inhalt.rstrip() + '\n\n' + hinweis
    else:
        neu['content'] = hinweis
    return neu


def kurze_form_hinweis(body: dict, name: str = '') -> bool:
    """Vor jedem Modellaufruf: Hat die Person in diesem Chat um Kürze gebeten, steht genau ein Hinweis darauf am Ende der
    Systemanweisung. Ohne Auslöser kommt nichts dazu. Der Hinweis steht nur in der Anfrage, nie im gespeicherten Verlauf.
    Gibt zurück, ob er jetzt in der Anfrage steht."""
    nachrichten = body.get('messages')
    if not isinstance(nachrichten, list):
        return False
    systeme = [i for i, n in enumerate(nachrichten) if isinstance(n, dict) and n.get('role') == 'system']
    if any(_HINWEIS_ANFANG in t for i in systeme for t in _texte(nachrichten[i])):
        return True  # aus dem ersten Modellaufruf dieser Antwort weitergereicht
    if not um_kuerze_gebeten(nachrichten):
        return False
    hinweis = HINWEIS_KURZ.format(name=_name(name, 'Die Person'))
    if systeme:
        # Kopie statt Änderung an Ort und Stelle: Open WebUI verwendet dieselbe Systemnachricht weiter
        nachrichten[systeme[-1]] = _mit_hinweis(nachrichten[systeme[-1]], hinweis)
    else:
        nachrichten.insert(0, {'role': 'system', 'content': hinweis})
    return True


def _letzte_der_person(body: dict):
    nachrichten = [n for n in (body.get('messages') or []) if isinstance(n, dict) and n.get('role') == 'user']
    return nachrichten[-1] if nachrichten else None


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


def _nachricht_tauschen(nachricht: dict, funktion) -> bool:
    """Wendet `funktion` auf alle Texte einer Nachricht an (Inhalt, Textteile, Werkzeugargumente). True, wenn sich etwas änderte."""
    anders = False
    inhalt = nachricht.get('content')
    if isinstance(inhalt, str):
        neu = funktion(inhalt)
        if neu != inhalt:
            nachricht['content'], anders = neu, True
    elif isinstance(inhalt, list):
        for teil in inhalt:
            if isinstance(teil, dict) and isinstance(teil.get('text'), str):
                neu = funktion(teil['text'])
                if neu != teil['text']:
                    teil['text'], anders = neu, True
    for aufruf in nachricht.get('tool_calls') or []:
        f = aufruf.get('function') if isinstance(aufruf, dict) else None
        if isinstance(f, dict) and isinstance(f.get('arguments'), str):
            neu = funktion(f['arguments'])
            if neu != f['arguments']:
                f['arguments'], anders = neu, True
    return anders


def schluessel_ersetzen(body: dict, ersatz: dict, standard: str = ERSATZ, genau=()) -> bool:
    """Blendet Schlüssel in allen Nachrichten aus (Person, Assistent, System, Werkzeuge), auch in den Argumenten von
    Werkzeugaufrufen. Gibt zurück, ob sich etwas geändert hat."""
    anders = False
    for nachricht in body.get('messages') or []:
        if isinstance(nachricht, dict):
            anders = _nachricht_tauschen(nachricht, lambda t: text_tauschen(t, ersatz, None, standard, genau)) or anders
    return anders


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
        erinnerung_deutsch: bool = Field(default=True, description='Nach einer englischen Arbeitsnotiz im nächsten Modellaufruf einmal an '
                                         'Deutsch und an die Person erinnern (steht nur in der Anfrage an das Modell, nicht im Verlauf)')
        kurze_form_merken: bool = Field(default=True, description='Hat die Person im Chat um Kürze gebeten („zu lang“, „kürzer“, Eile, '
                                        'keine Zeit, Handy), bekommt das Modell vor jedem Aufruf am Ende der Systemanweisung den '
                                        'Hinweis, dass bis zum Ende des Chats die kurze Form gilt (nur in der Anfrage, nicht im Verlauf)')
        antwort_aufraeumen: bool = Field(default=True, description='Technische Platzhalter und englische Arbeitsnotizen aus der '
                                         'fertigen Antwort nehmen')
        vorschlagsknoepfe_aus: bool = Field(default=True, description='Die automatisch erzeugten Vorschlagsknöpfe unter jeder Antwort '
                                            'wieder entfernen (sie entstehen ohne die Anleitung von buildbar_ und kürzen den Härtetest ab)')

    class UserValves(BaseModel):
        # Der Name endet auf „password“, damit Open WebUI den Wert in Prüfprotokollen schwärzt
        n8n_schluessel_password: str = Field(default='', title='n8n-Schlüssel',
                                             description='Dein n8n-Schlüssel (n8n: unten links dein Name, Settings, n8n API, '
                                             'Create an API key, längste Gültigkeit wählen). Einfügen und mit der Eingabetaste (Enter) '
                                             'bestätigen: Erst dann ist er gespeichert. Hier ist er am sichersten aufgehoben. '
                                             'Im Chat fügst du ihn erst ein, wenn buildbar_ danach fragt, nie als erste Nachricht.',
                                             json_schema_extra={'input': {'type': 'password'}})

    def __init__(self):
        self.valves = self.Valves()

    # ------------------------------------------------------------------ Haken von Open WebUI

    async def inlet(self, body: dict, __user__: dict = None, __request__=None, __metadata__: dict = None,
                    __event_emitter__=None, __id__: str = None) -> dict:
        try:
            await self._arbeiten(body, __user__ or {}, __request__, __metadata__ or {}, __event_emitter__, __id__)
        except Hinweis:
            raise  # gewollt: Open WebUI zeigt diesen Text an Stelle der Antwort
        except Exception as e:  # nie die Nachricht scheitern lassen, nie Werte protokollieren
            log.warning('buildbar Zugang: %s', type(e).__name__)
            try:  # Notfall: Ein Schlüssel erreicht das Modell auch dann nicht, wenn oben etwas schiefging
                schluessel_ersetzen(body, {}, standard=NEUTRAL)
            except Exception:
                pass
        return body

    async def request(self, body: dict, __user__: dict = None) -> dict:
        """Läuft vor jedem Modellaufruf, auch zwischen Werkzeugaufrufen: blendet Schlüssel und Zugangszeichen aus allen
        Nachrichten aus, auch aus Werkzeugausgaben (etwa wenn der Assistent doch eine Zugangsdatei gelesen hat). Danach
        räumt er auf, erinnert nach einer englischen Notiz einmal an Deutsch und hängt nach einer Bitte um Kürze den
        Hinweis auf die kurze Form an die Systemanweisung."""
        try:
            schluessel_ersetzen(body, {}, standard=AUSGEBLENDET, genau=self._genaue_werte(__user__))
        except Exception as e:
            log.warning('buildbar Zugang, request: %s', type(e).__name__)
        name = (__user__ or {}).get('name') if isinstance(__user__, dict) else ''
        try:  # Platzhalter der Zwischenschicht heraus, Erinnerung an Deutsch direkt nach einer englischen Notiz
            verlauf_fuer_modell_aufraeumen(body, erinnern=bool(self.valves.erinnerung_deutsch), name=name)
        except Exception as e:
            log.warning('buildbar Zugang, request (Aufräumen): %s', type(e).__name__)
        try:  # seit 0.4.4: kurze Form bis zum Ende des Chats, wenn die Person darum gebeten hat
            if self.valves.kurze_form_merken:
                kurze_form_hinweis(body, name)
        except Exception as e:
            log.warning('buildbar Zugang, request (kurze Form): %s', type(e).__name__)
        return body

    async def outlet(self, body: dict, __user__: dict = None, __metadata__: dict = None, __event_emitter__=None) -> dict:
        """Läuft nach der Antwort: blendet Schlüssel und Zugangszeichen in den Werkzeugdetails aus (Feld output), gibt der
        Oberfläche die bereinigte Fassung und bereinigt danach den ganzen gespeicherten Verlauf samt Chat-Titel. Den Text
        der Nachrichten im body lässt er unverändert, sonst legt Open WebUI den alten Wortlaut als originalContent ab."""
        try:
            genau = self._genaue_werte(__user__)
            for nachricht in body.get('messages') or []:
                if not isinstance(nachricht, dict) or not nachricht.get('output'):
                    continue
                neu = tief_tauschen(nachricht['output'], genau)
                if nachricht.get('role') == 'assistant' and self.valves.antwort_aufraeumen:
                    # Platzhalter der Zwischenschicht und englische Arbeitsnotizen aus der fertigen Antwort nehmen.
                    # Hier darf sich auch der Text ändern: Er enthält keine Geheimnisse, nur Störendes.
                    aufgeraeumt, text_nachher = ausgabe_aufraeumen(neu)
                    roh = '\n'.join(t for t in (_text_des_elements(e).strip() for e in neu if isinstance(e, dict) and e.get('type') == 'message') if t)
                    if aufgeraeumt != neu and text_nachher is not None and text_nachher != roh:
                        neu = aufgeraeumt
                        nachricht['content'] = text_nachher
                if neu != nachricht['output']:
                    nachricht['output'] = neu
                    # Die Oberfläche hat die rohe Ausgabe schon bekommen: mit der bereinigten überschreiben
                    if __event_emitter__ and nachricht.get('id') == body.get('id'):
                        try:
                            await __event_emitter__({'type': 'chat:completion', 'data': {'output': neu}})
                        except Exception:
                            pass
            await self._verlauf_saeubern(__metadata__ or {}, __event_emitter__, {}, genau, AUSGEBLENDET, alles=True)
        except Exception as e:
            log.warning('buildbar Zugang, outlet: %s', type(e).__name__)
        try:
            if self.valves.vorschlagsknoepfe_aus:
                aufgabe = asyncio.create_task(self._vorschlaege_leeren(__metadata__ or {}, __event_emitter__, body.get('id')))
                _hintergrund.add(aufgabe)
                aufgabe.add_done_callback(_hintergrund.discard)
        except Exception as e:
            log.warning('buildbar Zugang, Vorschlagsknöpfe: %s', type(e).__name__)
        return body

    async def _vorschlaege_leeren(self, metadata, emitter, nachricht_id):
        """Open WebUI erzeugt nach jeder Antwort mit einem zweiten Modell Vorschlagsknöpfe („Folgefragen“). Sie entstehen
        ohne die Anleitung von buildbar_: mit Fachwörtern, mit Abkürzungen am Härtetest vorbei („Bau jetzt“) und mit
        ausgedachten Antworten auf Fragen, die nur die Person beantworten kann. Dieser Schritt wartet, bis sie da sind,
        und nimmt sie wieder weg (gespeicherte Nachricht und Anzeige). Open WebUI schickt sie erst an die Anzeige und
        speichert sie dann: Wer sie gespeichert sieht, kann sicher sein, dass die Anzeige sie schon hat."""
        try:
            chat_id = str(metadata.get('chat_id') or '')
            mid = str(nachricht_id or metadata.get('message_id') or '')
            if not chat_id or not mid:
                return
            try:  # In der Verwaltung abgeschaltet: Dann entsteht nichts, also ist auch nichts zu tun
                from open_webui.models.config import Config
                if not await Config.get('task.follow_up.enable'):
                    return
            except Exception:
                pass
            leer = {'type': 'chat:message:follow_ups', 'data': {'follow_ups': []}}
            if chat_id.startswith(OHNE_VERLAUF):
                # Nicht gespeicherter Chat: Es gibt nichts nachzusehen. Die Anzeige ein paarmal leeren, das stört nicht.
                for pause in (4, 6, 10):
                    await asyncio.sleep(pause)
                    if emitter:
                        await emitter(leer)
                return
            from open_webui.models.chats import Chats
            beginn = time.monotonic()
            for zeitpunkt in VORSCHLAEGE_ZEITEN:
                await asyncio.sleep(max(0.0, zeitpunkt - (time.monotonic() - beginn)))
                da = await _vorschlaege_gespeichert(chat_id, mid, ganz=zeitpunkt in VORSCHLAEGE_GANZ)
                if da:
                    await Chats.upsert_message_to_chat_by_id_and_message_id(chat_id, mid, {'followUps': []}, touch=False)
                    if emitter:
                        await emitter(leer)
                    return
        except Exception as e:  # nie die Antwort stören; ohne Werte protokollieren
            log.warning('buildbar Zugang, Vorschlagsknöpfe leeren: %s', type(e).__name__)

    def _zeichen(self, user) -> str:
        user = user or {}
        user_id = str(user.get('id') or '')
        if not user_id or not self.valves.hub_kurs_geheimnis:
            return ''
        return zugangszeichen(self.valves.hub_kurs_geheimnis, kuerzel(str(user.get('name') or ''), user_id), user_id)

    def _genaue_werte(self, user) -> list:
        """Die geheimen Werte dieser Person, die wörtlich ausgeblendet werden: n8n-Schlüssel und Zugangszeichen."""
        schluessel = gueltiger_schluessel(getattr((user or {}).get('valves'), 'n8n_schluessel_password', ''))
        return [w for w in (schluessel, self._zeichen(user)) if w]

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
        zeichen = [w for w in (self._zeichen(user),) if w]
        chat_id = str(metadata.get('chat_id') or '')

        # 1. Sofort ausblenden, noch vor jeder Netzabfrage: im Text für das Modell und im gespeicherten Verlauf.
        #    Wird die Nachricht danach abgebrochen, steht nirgends mehr Klartext.
        in_letzter = schluessel_finden(body, nur_letzte=True)
        neue = [t for t in in_letzter if t != schluessel]
        letzte = _letzte_der_person(body)
        # Bruchstück: ein Schlüsselanfang, der kein ganzer Schlüssel ist (nur ein Teil wurde eingefügt)
        bruch = letzte is not None and any(rest_bruchstueck(t) for t in _texte(letzte))
        # Erste Nachricht eines neuen Chats: Open WebUI lässt daraus den Chat-Titel erzeugen, bevor dieser Filter läuft
        # (ob die Titel eingeschaltet sind, erfährt der Filter nicht)
        erste_nachricht = sum(1 for n in (body.get('messages') or [])
                              if isinstance(n, dict) and n.get('role') in ('user', 'assistant')) <= 1
        if schluessel_ersetzen(body, {}, standard=NEUTRAL, genau=zeichen):
            um = metadata.get('user_message')
            if isinstance(um, dict) and isinstance(um.get('content'), str):
                um['content'] = text_tauschen(um['content'], {}, None, NEUTRAL, zeichen)
            try:
                await asyncio.shield(self._verlauf_saeubern(metadata, emitter, {}, zeichen, NEUTRAL))
            except Exception as e:
                log.warning('buildbar Zugang, Verlauf: %s', type(e).__name__)

        # 2. Einen neuen Schlüssel aus der neuesten Nachricht prüfen und übernehmen
        endtext = None
        if neue:
            # Der zuletzt eingefügte vollständige Schlüssel zählt (Signatur mit 43 Zeichen), ein abgeschnittener nur ersatzweise
            ganz = [t for t in neue if len(t.rsplit('.', 1)[-1]) == 43]
            kandidat = (ganz or neue)[-1]
            if chat_id.startswith('channel:'):
                endtext = ERSATZ_KANAL
                await self._melden(emitter, 'Der Schlüssel steht für alle im Kanal sichtbar. Bitte die Nachricht löschen und in n8n '
                                            'einen neuen Schlüssel erzeugen.')
            else:
                status = await self._n8n_pruefen(kandidat)
                if status in (401, 403):
                    endtext = ERSATZ_ABGELEHNT
                    await self._melden(emitter, 'n8n hat den eingefügten Schlüssel abgelehnt')
                elif schluessel and konto(schluessel) and konto(kandidat) and konto(schluessel) != konto(kandidat):
                    endtext = ERSATZ_FREMD
                    await self._melden(emitter, 'Der eingefügte Schlüssel gehört zu einem anderen n8n-Konto und wurde nicht übernommen')
                elif status != 200 and schluessel:
                    endtext = ERSATZ_NICHT_ERREICHBAR
                    await self._melden(emitter, 'n8n war gerade nicht erreichbar, der bisherige Schlüssel bleibt')
                else:
                    from open_webui.models.functions import Functions
                    gespeichert = await Functions.update_user_valves_by_id_and_user_id(
                        funktion_id, user_id, {'n8n_schluessel_password': kandidat})
                    schluessel = kandidat  # gilt für diese Nachricht auch dann, wenn das Speichern scheiterte
                    if gespeichert is None:
                        endtext = ERSATZ_NICHT_GESPEICHERT
                        await self._melden(emitter, 'Der n8n-Schlüssel ließ sich nicht speichern. Bitte die Betreuung fragen.')
                    elif status != 200:
                        endtext = ERSATZ_UNGEPRUEFT
                        await self._melden(emitter, 'n8n-Schlüssel gespeichert, aber noch nicht geprüft: n8n war gerade nicht erreichbar')
                    elif erste_nachricht:
                        endtext = ERSATZ_ERSTE
                        await self._melden(emitter, 'n8n-Schlüssel gespeichert. Er stand in der ersten Nachricht des Chats: Am sichersten '
                                                    'erzeugst du in n8n einen neuen und fügst ihn hier noch einmal ein.')
                    else:
                        endtext = ERSATZ
                        await self._melden(emitter, 'n8n-Schlüssel sicher gespeichert')
        elif in_letzter:
            endtext = ERSATZ  # derselbe Schlüssel wie der gespeicherte, noch einmal eingefügt
        elif bruch:
            await self._melden(emitter, 'Der Schlüssel kam nicht vollständig an. Bitte noch einmal in einem Stück einfügen.')
        if endtext and letzte is not None:
            # Den neutralen Platzhalter in der neuesten Nachricht durch das Ergebnis ersetzen, auch im Verlauf
            _nachricht_tauschen(letzte, lambda t: t.replace(NEUTRAL, endtext))
            um = metadata.get('user_message')
            if isinstance(um, dict) and isinstance(um.get('content'), str):
                um['content'] = um['content'].replace(NEUTRAL, endtext)
            try:
                await asyncio.shield(self._ergebnis_eintragen(metadata, emitter, endtext))
            except Exception as e:
                log.warning('buildbar Zugang, Ergebnis: %s', type(e).__name__)

        # 3. Zugang ins Terminal der Person schreiben
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
        ort = (user_id, terminal_id, kontext or '')
        merker = hashlib.sha256(inhalt.encode()).hexdigest()
        alt = _geschrieben.get(ort)
        if alt and alt[0] == merker and time.monotonic() - alt[1] < MERKER_DAUER:
            return

        status = await self._schreiben(request, verbindung, nutzer, kontext, metadata, inhalt)
        if status in WECKEN and time.monotonic() - _vergeblich.get(ort, -1e9) > WECK_PAUSE:
            # Erster Kontakt oder nach einer Pause: Das Terminal startet erst (bis zu rund 90 s)
            await self._melden(emitter, 'Dein Arbeitsplatz startet gerade. Beim ersten Mal dauert das bis zu zwei Minuten.',
                               fertig=False)
            try:
                status = await asyncio.wait_for(self._wecken(request, verbindung, nutzer, kontext, metadata, inhalt), timeout=WECK_WARTEN)
            except asyncio.TimeoutError:
                status = 0
            await self._melden(emitter, 'Dein Arbeitsplatz ist bereit.' if status == 200 else
                               'Dein Arbeitsplatz startet noch.')
        if status == 200:
            _geschrieben[ort] = (merker, time.monotonic())
            _vergeblich.pop(ort, None)
        elif status in WECKEN:
            # Das Terminal ist noch nicht da. Ohne diesen Abbruch läuft Open WebUI gleich danach selbst in eine
            # Zeitüberschreitung, und die Person sieht eine leere Fehlermeldung (so geschehen am 13.11.2026 bei der
            # ersten Nachricht). Besser ein klarer Satz. Im Hintergrund wird weiter geweckt, damit der zweite Versuch klappt.
            _vergeblich[ort] = time.monotonic()
            if ort not in _nachwecken:
                aufgabe = asyncio.create_task(self._im_hintergrund_wecken(ort, merker, request, verbindung, nutzer, kontext, metadata, inhalt))
                _nachwecken[ort] = aufgabe
                aufgabe.add_done_callback(lambda _a, o=ort: _nachwecken.pop(o, None))
            raise Hinweis(STARTET_NOCH)

    async def _im_hintergrund_wecken(self, ort, merker, request, verbindung, nutzer, kontext, metadata, inhalt):
        """Versucht nach einem Abbruch noch einige Minuten, die Zugangsdatei zu schreiben. Scheitert still."""
        try:
            status = await asyncio.wait_for(self._wecken(request, verbindung, nutzer, kontext, metadata, inhalt), timeout=NACHWECKEN)
            if status == 200:
                _geschrieben[ort] = (merker, time.monotonic())
                _vergeblich.pop(ort, None)
        except Exception:  # auch Zeitüberschreitung: Beim nächsten Versuch der Person geht es von vorn los
            pass

    async def _wecken(self, request, verbindung, nutzer, kontext, metadata, inhalt) -> int:
        """Versucht das Schreiben alle paar Sekunden erneut, bis das Terminal antwortet. Die Gesamtzeit begrenzt der Aufrufer."""
        while True:
            await asyncio.sleep(8)
            status = await self._schreiben(request, verbindung, nutzer, kontext, metadata, inhalt)
            if status not in WECKEN:
                return status

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

        try:
            kopf, kekse = await build_tool_server_headers(verbindung, request, nutzer, metadata=metadata)
            kopf['Accept'] = 'application/json'
            kopf['X-User-Id'] = nutzer.id
            if metadata.get('chat_id'):
                kopf['X-Session-Id'] = metadata['chat_id']
            if kontext:
                kopf[TERMINAL_CONTEXT_HEADER] = kontext
            basis = get_terminal_server_url(verbindung)
            optionen = {'headers': kopf, 'cookies': kekse or None, 'ssl': ssl_wert, 'allow_redirects': False}
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=12), trust_env=True) as s:
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

    async def _ergebnis_eintragen(self, metadata, emitter, endtext):
        """Trägt in der gespeicherten neuesten Nachricht der Person das Ergebnis an Stelle des neutralen Platzhalters ein."""
        chat_id = str(metadata.get('chat_id') or '')
        mid = metadata.get('user_message_id') or (metadata.get('user_message') or {}).get('id')
        if not chat_id or not mid or chat_id.startswith(OHNE_VERLAUF):
            return
        from open_webui.models.chats import Chats

        nachricht = await Chats.get_message_by_id_and_message_id(chat_id, mid)
        inhalt = (nachricht or {}).get('content')
        if not isinstance(inhalt, str) or NEUTRAL not in inhalt:
            return
        neu = inhalt.replace(NEUTRAL, endtext)
        await Chats.upsert_message_to_chat_by_id_and_message_id(chat_id, mid, {'content': neu, 'originalContent': neu}, touch=False)
        if emitter:
            try:
                await emitter({'type': 'chat:outlet', 'data': {'messages': [{'id': mid, 'content': neu, 'originalContent': neu}]}})
            except Exception:
                pass

    async def _verlauf_saeubern(self, metadata, emitter, ersatz, genau, standard, alles=False):
        """Open WebUI 0.12 speichert die Nachricht der Person schon zu Beginn der Anfrage (Verlauf im Chat, Tabelle
        chat_message, alte Liste chat.messages) und Werkzeugausgaben roh. Dieser Schritt ersetzt Schlüssel dort.
        Danach gleicht das Ereignis chat:outlet die Anzeige an; originalContent wird mit überschrieben, sonst behielte
        die Oberfläche den alten Wortlaut dort. alles=True nimmt auch Werkzeugdetails (output) und den Chat-Titel mit."""
        chat_id = str(metadata.get('chat_id') or '')
        if not chat_id or chat_id.startswith(OHNE_VERLAUF):
            return
        from open_webui.models.chats import Chats

        def tausche(text):
            return text_tauschen(text, ersatz, None, standard, genau)

        geaendert = []
        verlauf = await Chats.get_messages_map_by_chat_id(chat_id) or {}
        for mid, nachricht in verlauf.items():
            if not isinstance(nachricht, dict):
                continue
            felder = {}
            for feld in ('content', 'originalContent', 'contextSummary'):
                wert = nachricht.get(feld)
                if isinstance(wert, str):
                    neu = tausche(wert)
                    if neu != wert:
                        felder[feld] = neu
            if alles and nachricht.get('output'):
                neu = tief_tauschen(nachricht['output'], genau)
                if neu != nachricht['output']:
                    felder['output'] = neu
            if not felder:
                continue
            if 'content' in felder or 'originalContent' in felder:
                inhalt = felder.get('content', nachricht.get('content'))
                felder['originalContent'] = inhalt
                geaendert.append({'id': mid, 'content': inhalt, 'originalContent': inhalt})
            await Chats.upsert_message_to_chat_by_id_and_message_id(chat_id, mid, felder, touch=False)

        chat = await Chats.get_chat_by_id(chat_id)
        daten = getattr(chat, 'chat', None) if chat else None
        titel_neu = None
        if isinstance(daten, dict):
            felder, liste, anders = {}, [], False
            for m in daten.get('messages') or []:
                if isinstance(m, dict):
                    neu_m = dict(m)
                    if isinstance(m.get('content'), str):
                        neu_m['content'] = tausche(m['content'])
                    if m.get('output'):
                        neu_m['output'] = tief_tauschen(m['output'], genau)
                    if neu_m != m:
                        m, anders = neu_m, True
                liste.append(m)
            if anders:
                # Nur die alte Liste schreiben; den Titel führt Open WebUI in derselben Transaktion unverändert mit
                await Chats.update_chat_by_id(chat_id, {'messages': liste}, touch=False)
            # Titel: steht in der Spalte und im Chat-Objekt. Bei erster Nachricht mit Schlüssel kann er ihn enthalten.
            for titel in (getattr(chat, 'title', None), daten.get('title')):
                if isinstance(titel, str) and tausche(titel) != titel:
                    titel_neu = tausche(titel)
                    break
            if titel_neu is not None:
                await Chats.update_chat_title_by_id(chat_id, titel_neu)

        if emitter:
            try:
                if geaendert:
                    await emitter({'type': 'chat:outlet', 'data': {'messages': geaendert}})
                if titel_neu is not None:
                    await emitter({'type': 'chat:title', 'data': titel_neu})
            except Exception:
                pass
