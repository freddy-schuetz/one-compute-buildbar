#!/usr/bin/env node
/* seite-pruefen: prüft die Oberfläche einer App im Browser (buildbar_ auf one.intelligence).
   Aufruf über seite-pruefen (/usr/local/bin); der Browser-Baustein liegt unter /opt/buildbar.
   Ablauf: Adresse prüfen (nie interne Adressen), Seite am Rechner laden, Schritte ausführen, Bild und Befund. Danach
   dasselbe auf einem nachgestellten Handy mit eigenem Browser-Speicher: neu geladen, Schritte noch einmal.
   Netz: Der Browser erreicht das Netz nur über die Schleuse in diesem Prozess (schleuseStarten). Sie prüft jedes Ziel
   und verbindet nur mit geprüften Adressen. Die Wache (wacheEinrichten) meldet gesperrte Anfragen und bricht ab, wenn
   die Seite selbst auf eine interne Adresse wechselt.
   Mit --nur-vorab als erster Angabe prüft der Befehl nur Adresse, Schritte und Dateien und startet keinen Browser
   (so prüft bin/seite-pruefen, bevor es auf die Bausperre wartet). */
'use strict';
const fs = require('fs');
const os = require('os');
const path = require('path');
const dns = require('dns');
const net = require('net');
const { domainToASCII } = require('url');

// Domain des Veröffentlichungsdienstes. Nur Seiten darunter (und 127.0.0.1, localhost) bekommen im Schritt „datei“
// eine Datei. Die Umgebungsvariable BUILDBAR_APP_DOMAIN kann sie nur enger fassen (etwa apps.friedemann-schuetz.eu),
// nie auf eine andere Domain legen: Sonst könnte ein eingeschleuster Aufruf sie gleich mit umstellen.
const APP_DOMAIN = '.friedemann-schuetz.eu';
// Schritt „datei“: nur diese Endungen, höchstens so groß, nur unter diesen Ordnern, nie unter den gesperrten
const DATEI_ENDUNGEN = ['.xlsx', '.xlsm', '.csv', '.tsv', '.txt', '.json', '.pdf', '.png', '.jpg', '.jpeg', '.webp'];
const DATEI_MAX_MB = 30;
const DATEI_WURZELN = ['/home/user', '/tmp'];
const DATEI_GESPERRT = ['/proc', '/etc', '/opt', '/usr'];
// Namen, die nie ins offene Netz führen (dazu jeder Name ohne Punkt)
const NAMEN_INTERN = ['.internal', '.local', '.localhost', '.lan', '.home.arpa', '.svc'];
// Ist ein Proxy eingestellt, geht der Browser über ihn ins Netz und nicht über die Schleuse (der Proxy löst Namen auf)
const PROXY_VARIABLEN = ['HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy'];
const RECHNER = { width: 1280, height: 900 };
const HANDY = { width: 390, height: 844, deviceScaleFactor: 2, isMobile: true, hasTouch: true };
const BILD_HOEHE_MAX = 6000; // so viele Bildpunkte hoch ist ein Bild höchstens
const BILD_BREITE_MAX = 3000; // so viele Bildpunkte breit (eine zu breite Seite wird rechts abgeschnitten)
const WARTEN_MAX = 600; // so viele Sekunden wartet ein Schritt höchstens („sekunden“, „warten“)
const SCHRITTE_MAX = 1000 * 1000; // so groß darf die Datei mit den Schritten höchstens sein (Bytes), wie bei „app“
const FRIST_OHNE_SCHRITTE = 4 * 60; // so lange darf ein Lauf ohne Schritte dauern (Sekunden), danach bricht er ab
const FRIST_MIT_SCHRITTEN = 14 * 60; // mit Schritten (die laufen am Rechner und am Handy)
const NACHFRIST = 10; // so viele Sekunden hat der Lauf nach einem Abbruch, um von selbst zu enden; danach endet er hart
const SCHLIESSEN_MAX = 8; // so viele Sekunden wartet der Befehl auf das Ende des Browsers, danach beendet er ihn hart
const VERBINDEN_MAX = 20; // so viele Sekunden darf die Schleuse für den Aufbau einer Verbindung brauchen
const LAUF_ORDNER = 'seite-pruefen-lauf-'; // Anfang des Ordners für einen Lauf (Profil des Browsers, Kopien von Dateien)
const RESTE_ALTER = 3 * 3600; // Ordner früherer Läufe (nach einem harten Ende liegen geblieben) ab diesem Alter löschen

const HILFE = `seite-pruefen <adresse> [--schritte <datei.json>] [--name <kurzname>]

Öffnet die Seite in Chromium, führt optional Schritte aus (Formular ausfüllen, Datei wählen, absenden, warten),
macht danach ein Bild am Rechner (1280 px) und eines auf dem Handy (390 px) und meldet:
seitlich zu breite Seite, Tabellenspalten, die nur durch Wischen sichtbar sind, schwachen Kontrast
und Fehler im Browser (Konsole, gescheiterte Anfragen, HTTP-Fehler).

Schritte als JSON-Liste in einer Datei, zum Beispiel /tmp/schritte.json:
[{"fuellen": "#name", "text": "Café Sperl"}, {"fuellen": "#ort", "text": "Wien"},
 {"waehlen": "#land", "wert": "AT"}, {"klicken": "button[type=submit]"},
 {"warten_bis_weg": ".lade", "sekunden": 180}]
Weitere Schritte: {"warten": 5} (Sekunden), {"warten_auf": "Text oder CSS-Selektor", "sekunden": 60}.
Wartezeiten sind höchstens 600 Sekunden lang. Nach 14 Minuten (ohne Schritte nach 4) bricht der Befehl ab.
Datei wählen: {"datei": "input[type=file]", "pfad": "/home/user/apps/<app>/daten/umsatz.xlsx"}. Das geht nur auf
deiner eigenen App und nur mit Dateien unter /home/user oder /tmp: höchstens 30 MB, Endung .xlsx, .xlsm, .csv,
.tsv, .txt, .json, .pdf, .png, .jpg, .jpeg oder .webp, nie aus versteckten Ordnern (Name mit Punkt am Anfang).
Die Bilder liegen danach unter /tmp/<kurzname>-rechner.png und /tmp/<kurzname>-handy.png.

Das Handy ist nachgestellt (390 x 844, Berührung, mobiler Browser). Dafür lädt der Befehl die Seite neu,
und die Schritte laufen ein zweites Mal. Was zugeklappt oder ausgeblendet ist, zählt nicht. Zum Prüfen
vorher aufklappen, zum Beispiel mit {"klicken": "summary"}.
Adressen: nur http und https, keine internen Adressen. Deine App auf diesem Rechner (127.0.0.1, localhost)
siehst du mit „app ansehen“ an.`;

const NUR_LOKAL = 'Adressen auf diesem Rechner (localhost, 127.0.0.1) prüft der Befehl nur über „app ansehen“.';
const ENDUNGEN_TEXT = `${DATEI_ENDUNGEN.slice(0, -1).join(', ')} und ${DATEI_ENDUNGEN[DATEI_ENDUNGEN.length - 1]}`;

const schlaf = (ms) => new Promise((r) => setTimeout(r, ms));
function wert(args, name) {
  const i = args.indexOf(name);
  return i >= 0 ? args[i + 1] : undefined;
}

// Ablehnung aus Sicherheitsgründen: Der Schritt läuft nicht, der Befehl endet mit Rückgabewert 2.
function abgelehnt(satz) {
  const e = new Error(satz);
  e.abgelehnt = true;
  return e;
}

function hostVon(adresse) {
  try {
    return new URL(adresse).hostname || String(adresse);
  } catch (e) {
    return String(adresse).slice(0, 80);
  }
}

function portVon(x) {
  return x.port || (x.protocol === 'https:' || x.protocol === 'wss:' ? '443' : '80');
}

// Ziel einer Adresse als „name:port“, so wie die Schleuse es meldet
function zielText(adresse) {
  try {
    const x = new URL(adresse);
    return `${x.hostname}:${portVon(x)}`;
  } catch (e) {
    return '';
  }
}

/* ---------- Adressen: interne Netze erkennen ---------- */

function ipv4Zahlen(s) {
  const m = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.exec(String(s));
  if (!m) return null;
  const z = m.slice(1).map(Number);
  return z.every((n) => n <= 255) ? z : null;
}

function ipv4Grund([a, b, c]) {
  if (a === 0) return 'Netz 0.x';
  if (a === 10) return 'Netz 10.x';
  if (a === 100 && b >= 64 && b <= 127) return 'Netz 100.64.x bis 100.127.x';
  if (a === 127) return 'dieser Rechner';
  if (a === 169 && b === 254) return 'Netz 169.254.x';
  if (a === 172 && b >= 16 && b <= 31) return 'Netz 172.16.x bis 172.31.x';
  if (a === 192 && b === 168) return 'Netz 192.168.x';
  if (a === 192 && b === 0 && c === 0) return 'Netz 192.0.0.x';
  if (a === 198 && (b === 18 || b === 19)) return 'Netz 198.18.x';
  if (a >= 224) return 'keine Adresse im offenen Netz';
  return '';
}

// IPv6 als acht Zahlen (16 Bit), auch mit eingebetteter IPv4 am Ende; null, wenn keine gültige Adresse
function ipv6Gruppen(s) {
  let t = String(s).toLowerCase().replace(/^\[|\]$/g, '');
  const zone = t.indexOf('%');
  if (zone >= 0) t = t.slice(0, zone);
  if (!net.isIPv6(t)) return null;
  const haelften = t.split('::');
  const teile = (x) => (x ? x.split(':') : []);
  const v4weg = (liste) => {
    const letzte = liste[liste.length - 1];
    if (!letzte || !letzte.includes('.')) return liste;
    const v4 = ipv4Zahlen(letzte);
    return v4 ? [...liste.slice(0, -1), ((v4[0] << 8) | v4[1]).toString(16), ((v4[2] << 8) | v4[3]).toString(16)] : null;
  };
  let vorn = teile(haelften[0]);
  let hinten = haelften.length === 2 ? teile(haelften[1]) : [];
  if (haelften.length === 2) hinten = v4weg(hinten);
  else vorn = v4weg(vorn);
  if (!vorn || !hinten) return null;
  const luecke = haelften.length === 2 ? 8 - vorn.length - hinten.length : 0;
  const alle = [...vorn, ...Array(Math.max(luecke, 0)).fill('0'), ...hinten];
  if (alle.length !== 8 || alle.some((g) => !/^[0-9a-f]{1,4}$/.test(g))) return null;
  return alle.map((g) => parseInt(g, 16));
}

function ipv6Grund(g) {
  const v4 = (h, l) => [h >> 8, h & 255, l >> 8, l & 255];
  if (g.every((x) => x === 0)) return 'keine Adresse im offenen Netz';
  if (g.slice(0, 7).every((x) => x === 0) && g[7] === 1) return 'dieser Rechner';
  if (g.slice(0, 5).every((x) => x === 0) && (g[5] === 0xffff || g[5] === 0)) return ipv4Grund(v4(g[6], g[7]));
  if (g[0] === 0x64 && g[1] === 0xff9b && g.slice(2, 6).every((x) => x === 0)) return ipv4Grund(v4(g[6], g[7]));
  if (g[0] === 0x2002) return ipv4Grund(v4(g[1], g[2]));
  if ((g[0] & 0xfe00) === 0xfc00) return 'internes Netz fc00::';
  if ((g[0] & 0xffc0) === 0xfe80 || (g[0] & 0xffc0) === 0xfec0) return 'internes Netz fe80::';
  if ((g[0] & 0xff00) === 0xff00) return 'keine Adresse im offenen Netz';
  return '';
}

// Grund, warum eine IP-Adresse intern ist; leer für eine Adresse im offenen Netz
function ipGrund(adresse) {
  const v4 = ipv4Zahlen(adresse);
  if (v4) return ipv4Grund(v4);
  const g = ipv6Gruppen(adresse);
  return g ? ipv6Grund(g) : 'keine gültige Adresse';
}

// Urteil über den Namen oder die IP-Adresse einer Adresse, nur nach dem Text (ohne Namensauflösung)
function hostUrteil(hostname, { lokal = false } = {}) {
  let host = String(hostname || '').toLowerCase();
  if (host.startsWith('[') && host.endsWith(']')) host = host.slice(1, -1);
  host = host.replace(/\.+$/, '');
  if (host === 'localhost' || host === '127.0.0.1') return lokal ? { host, lokal: true } : { host, nurLokal: true };
  if (ipv4Zahlen(host) || host.includes(':')) {
    const g = ipGrund(host);
    return g ? { host, ip: true, intern: g } : { host, ip: true };
  }
  if (!host.includes('.')) return { host, intern: 'Name ohne Punkt' };
  const endung = NAMEN_INTERN.find((e) => host.endsWith(e));
  if (endung) return { host, intern: `Name auf ${endung}` };
  return { host, name: true };
}

function internSatz(u) {
  return u.ip ? `${u.host} ist eine interne Adresse (${u.intern}), solche Adressen prüft der Befehl nicht.`
    : `„${u.host}“ ist ein interner Name (${u.intern}), solche Adressen prüft der Befehl nicht.`;
}

// IPv4 zuerst: In einem Netz ohne IPv6 kostet sonst jeder Versuch dorthin Zeit
function adressListe(liste) {
  const a = liste.map((x) => x.address);
  return [...a.filter((x) => net.isIPv4(x)), ...a.filter((x) => !net.isIPv4(x))];
}

// Sieht einen Namen nach. Rückgabe: { adressen }, oder { grund, adresse }, wenn eine der Adressen intern ist.
async function nachsehen(host, lookup) {
  const liste = await lookup(host, { all: true, verbatim: true });
  if (!liste || !liste.length) throw Object.assign(new Error('keine Adresse'), { code: 'ENOTFOUND' });
  for (const { address } of liste) {
    const g = ipGrund(address);
    if (g) return { grund: g, adresse: address };
  }
  return { adressen: adressListe(liste) };
}

// Startadresse: Text und Namensauflösung. Rückgabe bei einem Namen auch die geprüften IP-Adressen (adressen): Nur mit
// diesen verbindet die Schleuse den Browser später. Lässt sich der Name nicht nachsehen, prüft der Befehl die Seite
// nicht, außer ein Proxy ist eingestellt (dann löst der Proxy auf).
async function adressePruefen(adresse, { lokal = false, lookup = dns.promises.lookup, proxy = false } = {}) {
  const u = hostUrteil(adresse.hostname, { lokal });
  if (u.nurLokal) return { ok: false, grund: NUR_LOKAL };
  if (u.intern) return { ok: false, grund: internSatz(u) };
  if (!u.name) return { ok: true };
  let n;
  try {
    n = await nachsehen(u.host, lookup);
  } catch (e) {
    if (proxy) return { ok: true, unaufgeloest: true };
    return { ok: false, grund: `Die Adresse ${u.host} ist im Netz nicht zu finden (${e.code || 'Fehler'}). Prüfe sie auf Tippfehler, oder versuche es später noch einmal.` };
  }
  if (n.grund) return { ok: false, grund: `${u.host} führt auf die interne Adresse ${n.adresse} (${n.grund}), solche Adressen prüft der Befehl nicht.` };
  return { ok: true, host: u.host, adressen: n.adressen };
}

/* Urteil über das Ziel einer Verbindung (Name oder IP-Adresse und Port). Die Wache und die Schleuse fragen dieselbe
   Stelle: Was die Wache erlaubt, verbindet die Schleuse, und zwar nur mit den hier geprüften Adressen.
   Rückgabe: { adressen } (erlaubt; null heißt: ein eingestellter Proxy löst den Namen auf) oder { grund, intern }.
   intern false heißt: Der Name ließ sich nicht nachsehen. Jeder Name wird je Lauf einmal nachgesehen (bekannt: schon
   geprüfte Namen mit ihren Adressen, etwa die Startadresse); nur ein gescheitertes Nachsehen wird wiederholt.
   Mit SEITE_PRUEFEN_LOKAL=1 (lokal) ist auf diesem Rechner nur die geprüfte App erlaubt: 127.0.0.1 oder localhost,
   so wie es in der Adresse steht, mit dem Port der Startadresse (lokalPort). */
function zielPruefer({ lokal = false, lookup = dns.promises.lookup, proxy = false, lokalPort = null, bekannt = {} } = {}) {
  const namen = new Map(Object.entries(bekannt).map(([h, adressen]) => [h, Promise.resolve({ adressen })]));
  return async (hostname, port) => {
    const u = hostUrteil(hostname, { lokal });
    if (u.nurLokal) return { grund: 'dieser Rechner', intern: true };
    if (u.lokal) {
      if (!lokalPort || String(port) !== String(lokalPort)) return { grund: 'anderer Dienst auf diesem Rechner', intern: true };
      return { adressen: u.host === 'localhost' ? ['127.0.0.1', '::1'] : ['127.0.0.1'] };
    }
    if (u.intern) return { grund: u.intern, intern: true };
    if (!u.name) return { adressen: [u.host] };
    if (!namen.has(u.host)) {
      const p = nachsehen(u.host, lookup).then((n) => (n.grund ? { grund: `${n.adresse}, ${n.grund}`, intern: true } : n));
      namen.set(u.host, p);
      p.catch(() => namen.delete(u.host));
    }
    return namen.get(u.host).catch(() => (proxy ? { adressen: null } : { grund: 'Name nicht zu finden', intern: false }));
  };
}

/* Prüft die Adresse jeder Anfrage der Seite (für die Wache). Rückgabe: null (erlaubt) oder { grund, intern }.
   o: wie zielPruefer, oder ziel: eine schon vorhandene Zielprüfung. */
function anfragePruefer(o = {}) {
  const ziel = o.ziel || zielPruefer(o);
  return async (adresse) => {
    let x;
    try {
      x = new URL(adresse);
    } catch (e) {
      return null;
    }
    if (x.protocol === 'file:') return { grund: 'Datei auf dem Rechner', intern: true };
    if (!['http:', 'https:', 'ws:', 'wss:'].includes(x.protocol)) return null;
    const z = await ziel(x.hostname, portVon(x));
    return z.grund ? { grund: z.grund, intern: z.intern } : null;
  };
}

/* ---------- Schleuse: der einzige Weg des Browsers ins Netz ---------- */

/* Kleiner SOCKS5-Dienst auf 127.0.0.1. Chromium baut jede Verbindung über ihn auf (--proxy-server), auch die zu diesem
   Rechner, auch WebSockets und alles andere, was die Wache nicht sieht. Für jedes Ziel fragt er pruefen(host, port)
   und verbindet nur mit den Adressen aus dieser Antwort. Chromium sieht selbst keinen Namen nach: Ein Name kann also
   nicht bei der Prüfung öffentlich und beim Verbinden intern sein.
   melden(ziel, urteil): gesperrtes Ziel ({ grund, intern }) oder gescheiterte Verbindung ({ grund, verbindung: true }).
   umleiten (nur für Tests): { name: IP-Adresse } verbindet einen geprüften Namen mit einer anderen Adresse. */
function schleuseStarten(pruefen, melden = () => {}, umleiten = {}) {
  const offen = new Set();
  const merken = (s) => {
    offen.add(s);
    s.on('error', () => {});
    s.once('close', () => offen.delete(s));
    return s;
  };
  // Verbindung zu einer der geprüften Adressen, der Reihe nach. An das System geht dabei nie ein Name.
  const aufbauen = (adressen, port) => new Promise((ok, nein) => {
    const liste = adressen.filter((a) => net.isIP(a));
    const ende = Date.now() + VERBINDEN_MAX * 1000;
    const naechste = (fehler) => {
      const a = liste.shift();
      const rest = ende - Date.now();
      if (!a || rest <= 0) {
        nein(fehler || Object.assign(new Error('keine Adresse'), { code: 'ENOTFOUND' }));
        return;
      }
      const s = merken(net.connect({ host: a, port }));
      const uhr = setTimeout(() => s.destroy(Object.assign(new Error('Zeit abgelaufen'), { code: 'ETIMEDOUT' })), liste.length ? Math.min(rest, 5000) : rest);
      const scheitern = (e) => {
        clearTimeout(uhr);
        s.destroy();
        naechste(e);
      };
      s.once('error', scheitern);
      s.once('connect', () => {
        clearTimeout(uhr);
        s.removeListener('error', scheitern);
        ok(s);
      });
    };
    naechste(null);
  });
  const srv = net.createServer((c) => {
    merken(c);
    let puffer = Buffer.alloc(0);
    let begruesst = false;
    const absage = (code) => c.end(Buffer.from([5, code, 0, 1, 0, 0, 0, 0, 0, 0]), () => c.destroy());
    const lesen = (daten) => {
      puffer = Buffer.concat([puffer, daten]);
      if (puffer.length > 1024) {
        c.destroy(); // Begrüßung und Auftrag sind zusammen viel kürzer
        return;
      }
      if (!begruesst) {
        if (puffer.length < 2 || puffer.length < 2 + puffer[1]) return;
        if (puffer[0] !== 5) {
          c.destroy();
          return;
        }
        puffer = puffer.subarray(2 + puffer[1]);
        c.write(Buffer.from([5, 0])); // ohne Anmeldung
        begruesst = true;
      }
      if (puffer.length < 5) return;
      const art = puffer[3];
      const laenge = art === 1 ? 4 : art === 4 ? 16 : art === 3 ? 1 + puffer[4] : -1;
      if (puffer[0] !== 5 || puffer[1] !== 1 || laenge < 0) {
        absage(7); // nur der Auftrag „verbinden“
        return;
      }
      if (puffer.length < 6 + laenge) return;
      const roh = puffer.subarray(4, 4 + laenge);
      const port = puffer.readUInt16BE(4 + laenge);
      const rest = puffer.subarray(6 + laenge);
      c.removeListener('data', lesen);
      c.pause();
      // Schreibweise wie in einer Adresse (klein, Zahlenformen aufgelöst). Chromium schickt auch IP-Adressen als Text.
      let host = art === 1 ? roh.join('.') : art === 3 ? roh.subarray(1).toString('latin1') : roh.toString('hex').match(/.{4}/g).join(':');
      try {
        host = new URL(`http://${host.includes(':') && !host.startsWith('[') ? `[${host}]` : host}/`).hostname.replace(/^\[|\]$/g, '');
      } catch (e) {
        absage(4);
        return;
      }
      const wohin = `${host.includes(':') ? `[${host}]` : host}:${port}`;
      Promise.resolve().then(() => pruefen(host, port)).then((urteil) => {
        if (!urteil || !urteil.adressen) {
          melden(wohin, urteil || { grund: 'nicht erlaubt', intern: false });
          absage(2); // nicht erlaubt
          return undefined;
        }
        const name = host.replace(/[.]+$/, '');
        const umgeleitet = Object.prototype.hasOwnProperty.call(umleiten, name) ? umleiten[name] : null;
        return aufbauen(umgeleitet ? [umgeleitet] : urteil.adressen, port).then((z) => {
          if (c.destroyed) {
            z.destroy();
            return;
          }
          c.write(Buffer.from([5, 0, 0, 1, 0, 0, 0, 0, 0, 0]));
          if (rest.length) z.write(rest);
          c.once('close', () => z.destroy());
          z.once('close', () => c.destroy());
          c.pipe(z);
          z.pipe(c);
        }, (e) => {
          melden(wohin, { grund: e.code || 'Fehler', verbindung: true });
          absage(e.code === 'ECONNREFUSED' ? 5 : 4); // abgelehnt oder nicht erreichbar
        });
      }).catch(() => c.destroy());
    };
    c.on('data', lesen);
  });
  return new Promise((ok, nein) => {
    srv.once('error', nein);
    srv.listen(0, '127.0.0.1', () => {
      srv.on('error', () => {}); // ein Fehler beim Annehmen einer Verbindung beendet den Befehl nicht
      ok({
        port: srv.address().port,
        schliessen() {
          srv.close();
          for (const s of offen) s.destroy();
        },
      });
    });
  });
}

/* ---------- Schritt „datei“ ---------- */

// Nur enger als die eingebaute Domain: Ein Wert, der nicht auf APP_DOMAIN endet, gilt nicht.
function appDomain(env = process.env) {
  const roh = String(env.BUILDBAR_APP_DOMAIN || '').trim().toLowerCase().replace(/^\.+|\.+$/g, '');
  const d = roh ? domainToASCII(roh) : '';
  if (!d || !/^[a-z0-9-]+(\.[a-z0-9-]+)+$/.test(d)) return APP_DOMAIN;
  return ('.' + d).endsWith(APP_DOMAIN) ? '.' + d : APP_DOMAIN;
}

// Gehört die Adresse zur eigenen App? (Domain des Veröffentlichungsdienstes, 127.0.0.1 oder localhost)
function istEigeneApp(adresse, domain = appDomain()) {
  let x;
  try {
    x = new URL(adresse);
  } catch (e) {
    return false;
  }
  if (x.protocol !== 'http:' && x.protocol !== 'https:') return false;
  const host = x.hostname.toLowerCase().replace(/\.+$/, '');
  return host === '127.0.0.1' || host === 'localhost' || host.endsWith(domain);
}

/* Prüft den Pfad einer Datei, ohne sie zu lesen: aufgelöst (Verknüpfungen folgen), normale Datei ohne zweiten Namen,
   höchstens 30 MB, erlaubte Endung, lesbar, unter /home/user oder /tmp, kein Teil mit Punkt am Anfang, nie /proc,
   /etc, /opt, /usr.
   o (nur für Tests): pfad (path.posix oder path.win32), fs, wurzeln, gesperrt, home, cwd. */
function dateiPruefen(roh, o = {}) {
  const p = o.pfad || path;
  const dfs = o.fs || fs;
  const wurzeln = o.wurzeln || DATEI_WURZELN;
  const gesperrt = o.gesperrt || DATEI_GESPERRT;
  const nein = (grund) => ({ ok: false, grund });
  const aufloesen = (x) => (dfs.realpathSync.native || dfs.realpathSync)(x);
  const trenner = p.sep === '\\' ? /[\\/]+/ : /\/+/;
  const punkt = (x) => x.split(trenner).find((t) => t.startsWith('.') && t !== '.');
  const unter = (x, w) => {
    const r = p.relative(w, x);
    return r === '' || (r.split(trenner)[0] !== '..' && !p.isAbsolute(r));
  };
  const bereich = (x) => gesperrt.find((g) => unter(x, g));
  const orte = wurzeln.join(' oder ');
  if (typeof roh !== 'string' || !roh.trim()) return nein('Im Schritt fehlt der Pfad der Datei („pfad“).');
  if (roh.includes('\0')) return nein('Der Pfad enthält ein ungültiges Zeichen.');
  let s = roh;
  if (s === '~' || s.startsWith('~/')) s = (o.home || os.homedir()) + s.slice(1);
  // jeder Teil zählt, so wie er dasteht: „..“ und Teile mit Punkt am Anfang, ehe „..“ sie beim Auflösen verdeckt
  if (s.split(trenner).includes('..')) return nein('Der Pfad enthält „..“, gib ihn ohne „..“ an.');
  const abs = p.resolve(o.cwd || process.cwd(), s);
  const versteckt = punkt(s) || punkt(abs);
  if (versteckt) return nein(`Der Pfad führt in einen versteckten Ordner oder zu einer versteckten Datei (${versteckt}), solche Pfade sind gesperrt.`);
  let b = bereich(abs);
  if (b) return nein(`Dateien unter ${b} sind gesperrt.`);
  if (!wurzeln.some((w) => unter(abs, w))) return nein(`Die Datei liegt nicht unter ${orte}, nur von dort nimmt der Befehl Dateien.`);
  let echt;
  try {
    echt = aufloesen(abs);
  } catch (e) {
    return nein(['ENOENT', 'ENOTDIR'].includes(e.code) ? `Die Datei ${abs} gibt es nicht.` : `Die Datei ${abs} lässt sich nicht öffnen (${e.code || 'Fehler'}).`);
  }
  const echtVersteckt = punkt(echt);
  if (echtVersteckt) return nein(`Der Pfad zeigt über eine Verknüpfung in einen versteckten Ordner oder auf eine versteckte Datei (${echtVersteckt}), solche Pfade sind gesperrt.`);
  b = bereich(echt);
  if (b) return nein(`Der Pfad zeigt über eine Verknüpfung nach ${b}, dort sind Dateien gesperrt.`);
  const echteWurzeln = wurzeln.map((w) => {
    try {
      return aufloesen(w);
    } catch (e) {
      return w;
    }
  });
  if (!echteWurzeln.some((w) => unter(echt, w))) return nein(`Der Pfad zeigt über eine Verknüpfung nach ${echt}, das liegt nicht unter ${orte}.`);
  let st;
  try {
    st = dfs.statSync(echt, { bigint: true });
  } catch (e) {
    return nein(`Die Datei ${abs} lässt sich nicht öffnen (${e.code || 'Fehler'}).`);
  }
  if (st.isDirectory()) return nein(`${abs} ist ein Ordner, keine Datei.`);
  if (!st.isFile()) return nein(`${abs} ist keine normale Datei.`);
  // Eine harte Verknüpfung hat denselben Inhalt unter zwei Namen; realpath sieht den zweiten Namen nicht
  if (Number(st.nlink) > 1) return nein(`${abs} hat noch einen zweiten Namen an anderer Stelle (harte Verknüpfung), solche Dateien wählt der Befehl nicht aus.`);
  for (const x of new Set([abs, echt])) {
    const endung = p.extname(x).toLowerCase();
    if (DATEI_ENDUNGEN.includes(endung)) continue;
    const was = x === abs ? `Die Datei hat ${endung ? `die Endung ${endung}` : 'keine Endung'}`
      : `Der Pfad zeigt über eine Verknüpfung auf eine Datei ${endung ? `mit der Endung ${endung}` : 'ohne Endung'}`;
    return nein(`${was}, erlaubt sind nur ${ENDUNGEN_TEXT}.`);
  }
  if (Number(st.size) > DATEI_MAX_MB * 1024 * 1024) {
    return nein(`Die Datei ist ${(Number(st.size) / 1048576).toFixed(1).replace('.', ',')} MB groß, erlaubt sind höchstens ${DATEI_MAX_MB} MB.`);
  }
  // Leserecht prüfen, ohne zu lesen (access): Ohne Leserecht ist das ein klarer Satz, kein vermeintlicher Austausch
  try {
    dfs.accessSync(echt, fs.constants.R_OK);
  } catch (e) {
    return nein(`Die Datei ${abs} lässt sich nicht lesen (keine Leserechte).`);
  }
  return { ok: true, echt, name: p.basename(abs), groesse: Number(st.size), dev: st.dev, ino: st.ino };
}

// Kopiert die geprüfte Datei in einen eigenen Ordner. Gelesen wird über dieselbe geöffnete Datei, die geprüft ist:
// Wurde sie seit der Prüfung ausgetauscht, bricht der Schritt ab. Der Browser bekommt nur die Kopie.
function kopieren(g, ziel) {
  const grenze = DATEI_MAX_MB * 1024 * 1024;
  const geaendert = 'Die Datei hat sich seit der Prüfung geändert, der Befehl wählt sie nicht aus.';
  let ein = null;
  let aus = null;
  try {
    try {
      ein = fs.openSync(g.echt, fs.constants.O_RDONLY | (fs.constants.O_NOFOLLOW || 0));
    } catch (e) {
      // Fehlt nur das Leserecht, ist das kein Austausch, sondern ein normaler Fehlschlag des Schritts
      if (e.code === 'EACCES' || e.code === 'EPERM') throw new Error('Die Datei lässt sich nicht lesen (keine Leserechte).');
      throw abgelehnt(geaendert);
    }
    const st = fs.fstatSync(ein, { bigint: true });
    if (!st.isFile() || st.dev !== g.dev || st.ino !== g.ino || Number(st.nlink) > 1) throw abgelehnt(geaendert);
    aus = fs.openSync(ziel, 'wx', 0o600);
    const puffer = Buffer.alloc(1 << 20);
    let summe = 0;
    for (let n = fs.readSync(ein, puffer, 0, puffer.length, null); n > 0; n = fs.readSync(ein, puffer, 0, puffer.length, null)) {
      summe += n;
      if (summe > grenze) throw abgelehnt(`Die Datei ist größer als ${DATEI_MAX_MB} MB.`);
      fs.writeSync(aus, puffer, 0, n);
    }
    fs.futimesSync(aus, st.atime, st.mtime);
  } finally {
    if (aus !== null) fs.closeSync(aus);
    if (ein !== null) fs.closeSync(ein);
  }
}

// Ablage der Kopien: je Pfad einmal geprüft und kopiert (Rechner und Handy nehmen dieselbe Kopie), am Ende gelöscht.
function dateiAblage(o = {}) {
  let ordner = null;
  const fertig = new Map();
  return {
    holen(s) {
      const schluessel = String(s.pfad);
      if (fertig.has(schluessel)) return fertig.get(schluessel);
      const g = dateiPruefen(s.pfad, o);
      if (!g.ok) throw abgelehnt(g.grund);
      if (!ordner) ordner = fs.mkdtempSync(path.join(o.kopieOrdner || os.tmpdir(), 'seite-pruefen-kopie-'));
      const ziel = path.join(fs.mkdtempSync(path.join(ordner, 'datei-')), g.name);
      kopieren(g, ziel);
      fertig.set(schluessel, ziel);
      return ziel;
    },
    aufraeumen() {
      if (!ordner) return;
      try {
        fs.rmSync(ordner, { recursive: true, force: true });
      } catch (e) {
        /* bleibt liegen, ist nur für diesen Nutzer lesbar */
      }
    },
  };
}

// Ordner früherer Läufe (Profil des Browsers, Kopien gewählter Dateien), die nach einem harten Ende liegen blieben.
// Nur eigene Ordner mit genau dem Namen, den ein Lauf anlegt (LAUF_ORDNER und sechs Zeichen), älter als RESTE_ALTER:
// Ein laufender Lauf ist nie so alt (Zeitgrenze). Andere Ordner und Dateien unter /tmp bleiben, wie sie sind.
function resteLoeschen(ordner = os.tmpdir(), alter = RESTE_ALTER) {
  let namen = [];
  try {
    namen = fs.readdirSync(ordner);
  } catch (e) {
    return;
  }
  const grenze = Date.now() - alter * 1000;
  const ich = typeof process.getuid === 'function' ? process.getuid() : null;
  for (const n of namen) {
    if (!n.startsWith(LAUF_ORDNER) || !/^[A-Za-z0-9]{6}$/.test(n.slice(LAUF_ORDNER.length))) continue;
    const p = path.join(ordner, n);
    try {
      const st = fs.lstatSync(p);
      if (!st.isDirectory() || st.mtimeMs > grenze || (ich !== null && st.uid !== ich)) continue;
      fs.rmSync(p, { recursive: true, force: true });
    } catch (e) {
      /* bleibt liegen */
    }
  }
}

/* ---------- Befund im Browser ---------- */

async function befund(page) {
  return page.evaluate(() => {
    const cw = document.documentElement.clientWidth;
    const sw = document.documentElement.scrollWidth;
    const name = (el) => {
      let s = el.tagName.toLowerCase();
      if (el.id) s += '#' + el.id;
      else if (typeof el.className === 'string' && el.className.trim()) s += '.' + el.className.trim().split(/\s+/).slice(0, 2).join('.');
      return s;
    };
    const stile = new Map();
    const stil = (el) => {
      let s = stile.get(el);
      if (!s) {
        s = getComputedStyle(el);
        stile.set(el, s);
      }
      return s;
    };
    const eingefangen = (el) => {
      for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) {
        const o = stil(p).overflowX;
        if (o === 'auto' || o === 'scroll' || o === 'hidden' || o === 'clip') return true;
      }
      return false;
    };
    // Was zugeklappt (details ohne open), ausgeblendet (display: none, hidden), unsichtbar (visibility, opacity 0)
    // oder in einem Bereich steckt, der es ganz abschneidet (zugeklappt mit overflow: hidden), zählt nicht.
    const mitPruefung = typeof Element.prototype.checkVisibility === 'function';
    const zugeklappt = (el) => {
      for (let e = el; e.parentElement; e = e.parentElement) {
        const d = e.parentElement;
        if (d.tagName === 'DETAILS' && !d.open && !(e.tagName === 'SUMMARY' && d.querySelector(':scope > summary') === e)) return true;
      }
      return false;
    };
    const gerendert = (el) => (mitPruefung ? el.checkVisibility() : el.getClientRects().length > 0 && !zugeklappt(el));
    const stilSichtbar = (el) => {
      if (mitPruefung) return el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true });
      if (!gerendert(el) || stil(el).visibility !== 'visible') return false;
      for (let e = el; e; e = e.parentElement) if (stil(e).opacity === '0') return false;
      return true;
    };
    // Der Rahmen, an dem ein Element hängt: Ein absolut gesetztes Element hängt am nächsten gesetzten Vorfahren, ein
    // festes am Fenster. Bereiche dazwischen schneiden es nicht ab.
    const rahmenVon = (e) => {
      const pos = stil(e).position;
      if (pos !== 'absolute' && pos !== 'fixed') return e.parentElement;
      for (let a = e.parentElement; a; a = a.parentElement) {
        const s = stil(a);
        if ((pos === 'absolute' && s.position !== 'static') || s.transform !== 'none' || s.filter !== 'none'
          || s.perspective !== 'none' || /paint|layout|strict|content/.test(s.contain)) return a;
      }
      return null;
    };
    // Schneidet ein Bereich mit overflow hidden oder clip das Element ganz ab? Jeder Bereich schneidet am Innenrand
    // seines Rahmens ab (Innenabstand zählt mit). Bleibt höchstens 1 px übrig, ist nichts zu sehen.
    const abgeschnitten = (el) => {
      const r = el.getBoundingClientRect();
      let [links, oben, rechts, unten] = [r.left, r.top, r.right, r.bottom];
      for (let p = rahmenVon(el); p && p !== document.body && p !== document.documentElement; p = rahmenVon(p)) {
        const st = stil(p);
        if (st.display === 'contents' || st.display === 'inline') continue;
        const x = /hidden|clip/.test(st.overflowX);
        const y = /hidden|clip/.test(st.overflowY);
        if (!x && !y) continue;
        const pr = p.getBoundingClientRect();
        if (x) {
          links = Math.max(links, pr.left + (parseFloat(st.borderLeftWidth) || 0));
          rechts = Math.min(rechts, pr.right - (parseFloat(st.borderRightWidth) || 0));
        }
        if (y) {
          oben = Math.max(oben, pr.top + (parseFloat(st.borderTopWidth) || 0));
          unten = Math.min(unten, pr.bottom - (parseFloat(st.borderBottomWidth) || 0));
        }
        if (rechts - links <= 1 || unten - oben <= 1) return true;
      }
      return false;
    };
    // Tabellen und Text: sichtbar nach CSS, größer als 1 px und nicht ganz abgeschnitten
    const sichtbar = (el) => {
      if (!stilSichtbar(el)) return false;
      const r = el.getBoundingClientRect();
      return r.width > 1 && r.height > 1 && !abgeschnitten(el);
    };
    // „zu breit“: sichtbar nach CSS und nicht ohne Höhe (auch eine Linie von 1 px zählt)
    const sichtbarBreit = (el) => stilSichtbar(el) && el.getBoundingClientRect().height > 0;
    // Elemente, die über den rechten Rand ragen und von keinem wischbaren Rahmen eingefangen werden. Unsichtbare
    // nennt die Meldung nur, wenn kein sichtbares Element die Ursache ist.
    const zuBreit = [];
    const unsichtbarBreit = [];
    for (const el of document.body.querySelectorAll('*')) {
      const r = el.getBoundingClientRect();
      if (!(r.width > 1 && r.right > cw + 1) || eingefangen(el)) continue;
      const eintrag = `${name(el)} (rechter Rand bei ${Math.round(r.right)} px`;
      if (sichtbarBreit(el)) {
        zuBreit.push(eintrag + ')');
        if (zuBreit.length >= 5) break;
      } else if (unsichtbarBreit.length < 5 && gerendert(el)) unsichtbarBreit.push(eintrag + ', unsichtbar)');
    }
    if (!zuBreit.length) zuBreit.push(...unsichtbarBreit);
    // Sichtbare Tabellen, deren Spalten rechts außerhalb liegen
    const tabellen = [];
    for (const t of document.querySelectorAll('table')) {
      if (!sichtbar(t)) continue;
      const rahmen = (t.parentElement || t).getBoundingClientRect();
      const rechts = Math.min(rahmen.right, cw);
      const kopf = t.querySelectorAll('thead th').length ? t.querySelectorAll('thead th') : t.querySelectorAll('tr:first-child th, tr:first-child td');
      const verborgen = [...kopf].filter((th) => {
        const r = th.getBoundingClientRect();
        return gerendert(th) && r.width > 1 && r.left >= rechts - 4;
      }).map((th) => th.innerText.trim() || '(ohne Titel)');
      if (verborgen.length) tabellen.push(`Tabelle: nur durch Wischen sichtbar sind die Spalten ${verborgen.join(', ')}`);
    }
    // Kontrast von Text zu seinem Hintergrund (WCAG: 4,5:1, große Schrift 3:1)
    // Jede CSS-Farbe (auch oklch, wie Tailwind 4 sie liefert) vom Browser selbst in RGB umrechnen lassen
    const leinwand = document.createElement('canvas');
    leinwand.width = 1;
    leinwand.height = 1;
    const pinsel = leinwand.getContext('2d', { willReadFrequently: true });
    const farbe = (s) => {
      if (!s || s === 'transparent') return { rgb: [0, 0, 0], a: 0 };
      pinsel.clearRect(0, 0, 1, 1);
      pinsel.fillStyle = 'rgba(0, 0, 0, 0)';
      pinsel.fillStyle = String(s);
      pinsel.fillRect(0, 0, 1, 1);
      const d = pinsel.getImageData(0, 0, 1, 1).data;
      return { rgb: [d[0], d[1], d[2]], a: d[3] / 255 };
    };
    const lum = (rgb) => {
      const [r, g, b] = rgb.map((v) => {
        v /= 255;
        return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
      });
      return 0.2126 * r + 0.7152 * g + 0.0722 * b;
    };
    const grund = (el) => {
      for (let e = el; e; e = e.parentElement) {
        const st = stil(e);
        if (st.backgroundImage && st.backgroundImage !== 'none') return null;
        const f = farbe(st.backgroundColor);
        if (f && f.a > 0.5) return f.rgb;
      }
      return [255, 255, 255];
    };
    const schwach = [];
    for (const el of document.body.querySelectorAll('h1,h2,h3,h4,p,li,label,a,button,span,td,th,div,strong,small')) {
      if (![...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim())) continue;
      const st = stil(el);
      const r = el.getBoundingClientRect();
      if (st.display === 'none' || st.visibility === 'hidden' || r.width <= 1 || r.height <= 1 || r.right <= 0 || r.bottom <= 0) continue;
      if (!sichtbar(el)) continue;
      const vf = farbe(st.color);
      const hg = grund(el);
      if (!vf || vf.a < 0.1 || !hg) continue;
      const l1 = lum(vf.rgb);
      const l2 = lum(hg);
      const k = (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
      const px = parseFloat(st.fontSize);
      const gross = px >= 24 || (px >= 18.66 && parseInt(st.fontWeight, 10) >= 700);
      if (k < (gross ? 3 : 4.5)) {
        schwach.push(`${name(el)} „${el.innerText.trim().replace(/\s+/g, ' ').slice(0, 50)}“: Kontrast ${k.toFixed(1)}:1, nötig ${gross ? 3 : 4.5}:1`);
        if (schwach.length >= 6) break;
      }
    }
    const text = (document.querySelector('main') || document.body).innerText.replace(/\n{3,}/g, '\n\n').slice(0, 2500);
    return { cw, sw, zuBreit, tabellen, schwach, text };
  });
}

function ansicht(titel, bild, b, zusatz = [], keinBild = '') {
  const z = [keinBild ? `${titel}: kein Bild (${keinBild})` : `${titel}: Bild ${bild}`];
  z.push(b.sw > b.cw + 1 ? `  Seite seitlich zu breit: JA, ${b.sw} px statt ${b.cw} px. Zu breit: ${b.zuBreit.join('; ') || 'unklar'}`
    : '  Seite seitlich zu breit: nein');
  for (const t of b.tabellen) z.push('  ' + t);
  for (const t of zusatz) z.push('  ' + t);
  return z.join('\n');
}

/* ---------- Bilder ---------- */

// Ausschnitt und Maßstab eines Bildes: höchstens BILD_HOEHE_MAX Bildpunkte hoch und BILD_BREITE_MAX breit. Ist die
// Seite zu hoch, wird das Bild bis auf einfache Pixeldichte verkleinert und erst danach unten abgeschnitten; ist sie
// zu breit, wird es rechts abgeschnitten. breite, hoehe: Größe der Seite in CSS-Pixeln.
function bildRahmen(breite, hoehe, dichte) {
  const b = Math.max(1, Math.ceil(breite || 0));
  const h = Math.max(1, Math.ceil(hoehe || 0));
  const massstab = h * dichte > BILD_HOEHE_MAX ? Math.max(BILD_HOEHE_MAX / (h * dichte), 1 / dichte) : 1;
  const px = dichte * massstab;
  return {
    massstab,
    gezeigt: Math.min(h, Math.floor(BILD_HOEHE_MAX / px + 1e-6)),
    gezeigtBreite: Math.min(b, Math.floor(BILD_BREITE_MAX / px + 1e-6)),
  };
}

// Größe der Seite in CSS-Pixeln. Am nachgestellten Handy dehnt Chromium den Rahmen einer zu breiten Seite auf die
// verkleinerte Ansicht aus (etwa 1560 x 3376 statt 390 x 844), und scrollHeight wächst mit. Dann zählt die Höhe des
// Inhalts, mindestens eine Bildschirmhöhe.
async function seitenMasse(page) {
  let rahmen = null;
  try {
    const sitzung = await page.createCDPSession();
    const m = await sitzung.send('Page.getLayoutMetrics');
    await sitzung.detach().catch(() => {});
    rahmen = m.cssLayoutViewport || m.layoutViewport || null;
  } catch (e) {
    /* ohne Rahmen: scrollHeight */
  }
  const d = await page.evaluate(() => {
    const html = document.documentElement;
    const body = document.body;
    let inhalt = html.getBoundingClientRect().height;
    if (body) {
      const r = body.getBoundingClientRect();
      const st = getComputedStyle(body);
      inhalt = Math.max(inhalt, r.top + window.scrollY + (parseFloat(st.borderTopWidth) || 0) + body.scrollHeight
        + (parseFloat(st.borderBottomWidth) || 0) + (parseFloat(st.marginBottom) || 0));
    }
    return { sw: html.scrollWidth, sh: html.scrollHeight, ch: html.clientHeight, inhalt: Math.ceil(inhalt) };
  });
  let hoehe = d.sh;
  if (rahmen && rahmen.clientHeight > d.ch + 1 && d.sh <= Math.ceil(rahmen.clientHeight) + 1) {
    hoehe = Math.min(d.sh, Math.max(d.ch, d.inhalt));
  }
  return { breite: d.sw, hoehe };
}

// Bild der Seite (siehe bildRahmen). Geht das nicht (zu groß für den Speicher), dann nur der sichtbare Teil; geht auch
// das nicht, kein Bild. Der Befund läuft in jedem Fall weiter.
async function bildMachen(page, datei, dichte) {
  let breite = 0;
  let hoehe = 0;
  try {
    ({ breite, hoehe } = await seitenMasse(page));
    const r = bildRahmen(breite, hoehe, dichte);
    await page.screenshot({ path: datei, clip: { x: 0, y: 0, width: r.gezeigtBreite, height: r.gezeigt, scale: r.massstab } });
    return { breite, hoehe, gezeigt: r.gezeigt, gezeigtBreite: r.gezeigtBreite };
  } catch (e) {
    try {
      await page.screenshot({ path: datei });
      return { breite, hoehe, gezeigt: hoehe, gezeigtBreite: breite, ausschnitt: true };
    } catch (e2) {
      fs.rmSync(datei, { force: true });
      return { keinBild: String(e2.message || e2).split('\n')[0].slice(0, 120) };
    }
  }
}

function bildZeilen(masse) {
  const z = [];
  if (masse.keinBild) return z;
  if (masse.ausschnitt) z.push('Bild zeigt nur den sichtbaren Teil der Seite: Für ein Bild der ganzen Seite war sie zu groß.');
  if (masse.gezeigt < masse.hoehe) z.push(`Bild gekürzt: Es zeigt die obersten ${masse.gezeigt} px, die Seite ist ${masse.hoehe} px hoch.`);
  if (masse.gezeigtBreite < masse.breite) z.push(`Bild gekürzt: Es zeigt die linken ${masse.gezeigtBreite} px, die Seite ist ${masse.breite} px breit.`);
  return z;
}

/* ---------- Abbruch und Zeitgrenze ---------- */

// Abbruch des ganzen Laufs: interne Adresse (art „intern“, Rückgabewert 2) oder Zeitgrenze (art „frist“, 1)
function abbruchSignal() {
  let melden;
  const s = { grund: '', art: '', versprechen: new Promise((r) => { melden = r; }) };
  s.setzen = (grund, art) => {
    if (s.grund) return;
    s.grund = grund;
    s.art = art;
    melden();
  };
  return s;
}

const ABGEBROCHEN = Symbol('abgebrochen');
// Wartet auf p, aber nur bis zum Abbruch: Dann kommt ABGEBROCHEN zurück, und p läuft ins Leere.
function bisAbbruch(p, signal) {
  const q = Promise.resolve(p);
  q.catch(() => {});
  if (signal.grund) return Promise.resolve(ABGEBROCHEN);
  return Promise.race([q, signal.versprechen.then(() => ABGEBROCHEN)]);
}

// Wartet auf p, aber höchstens ms Millisekunden. Rückgabe: true, wenn p in der Zeit fertig war (auch mit Fehler).
// Für alles, was der Browser schuldig bleiben kann (Seite schließen, Browser beenden).
function hoechstens(p, ms) {
  let uhr;
  const fertig = Promise.resolve(p).then(() => true, () => true);
  return Promise.race([fertig, new Promise((r) => { uhr = setTimeout(() => r(false), ms); })]).finally(() => clearTimeout(uhr));
}

function dauerText(sekunden) {
  return sekunden >= 120 ? `${Math.round(sekunden / 60)} Minuten` : `${sekunden} Sekunden`;
}

/* ---------- Schritte ---------- */

function zahlVon(roh) {
  if (typeof roh === 'number') return roh;
  return typeof roh === 'string' && roh.trim() ? Number(roh) : NaN;
}

function sekundenVon(s) {
  return s.sekunden === undefined || s.sekunden === null ? 120 : zahlVon(s.sekunden);
}

// Wartezeiten vorab prüfen: Eine kaputte Angabe („zwei“) hieße sonst Warten ohne Ende.
function wartezeitFehler(schritte) {
  const fehler = [];
  for (const [i, s] of schritte.entries()) {
    if (!s || typeof s !== 'object' || 'datei' in s || s.fuellen || s.waehlen || s.klicken) continue;
    let feld = '';
    if (s.warten) feld = 'warten';
    else if ((s.warten_bis_weg || s.warten_auf) && s.sekunden !== undefined && s.sekunden !== null) feld = 'sekunden';
    if (!feld) continue;
    const n = zahlVon(s[feld]);
    if (!(n > 0 && n <= WARTEN_MAX)) {
      fehler.push(`FEHLER: Schritt ${i + 1} hat eine ungültige Wartezeit („${feld}“: ${String(JSON.stringify(s[feld])).slice(0, 30)}). Erlaubt ist eine Zahl größer als 0 und höchstens ${WARTEN_MAX} (Sekunden).`);
    }
  }
  return fehler;
}

// Grund, warum die Schritt-Datei nicht passt, ohne Text aus der Datei: JSON.parse schreibt Teile davon in seine
// Meldung, also steht hier nur die Stelle.
function jsonGrund(err, inhalt) {
  if (!(err instanceof SyntaxError)) return String((err && err.message) || err).split('\n')[0].slice(0, 200);
  const m = String(err.message);
  const zs = /line (\d+) column (\d+)/.exec(m);
  const pos = /position (\d+)/.exec(m);
  let zeile = zs && zs[1];
  let spalte = zs && zs[2];
  if (!zeile && pos) {
    const vorher = String(inhalt || '').slice(0, Number(pos[1]));
    zeile = vorher.split('\n').length;
    spalte = vorher.length - vorher.lastIndexOf('\n');
  }
  if (zeile) return `Fehler in Zeile ${zeile}, Spalte ${spalte}`;
  if (/end of JSON input/i.test(m)) return 'die Datei ist leer oder hört mitten in einem Eintrag auf';
  return 'unerwartetes Zeichen';
}

async function dateiWaehlen(page, s, z) {
  const hier = page.url();
  if (!istEigeneApp(hier, z.domain)) {
    throw abgelehnt(`Die Seite ist inzwischen auf ${hostVon(hier)}, das gehört nicht zu deiner App, dort wählt der Befehl keine Datei aus.`);
  }
  if (typeof s.datei !== 'string' || !s.datei.trim()) throw new Error('Es fehlt das Dateifeld („datei“)');
  const feld = await page.$(s.datei);
  if (!feld) throw new Error(`Kein Element für den Selektor ${s.datei}`);
  const istDateifeld = await feld.evaluate((e) => e.tagName === 'INPUT' && String(e.type).toLowerCase() === 'file');
  if (!istDateifeld) throw new Error(`${s.datei} ist kein Dateifeld (input type="file")`);
  await feld.uploadFile(z.ablage.holen(s)); // prüft den Pfad noch einmal und gibt dem Feld nur die Kopie
}

async function schrittAusfuehren(page, s, z) {
  if (s && typeof s === 'object' && 'datei' in s) await dateiWaehlen(page, s, z);
  else if (s.fuellen) {
    await page.click(s.fuellen, { clickCount: 3 });
    await page.type(s.fuellen, String(s.text ?? ''));
  } else if (s.waehlen) await page.select(s.waehlen, String(s.wert));
  else if (s.klicken) await (z.handy ? page.tap(s.klicken) : page.click(s.klicken));
  else if (s.warten) await schlaf(zahlVon(s.warten) * 1000);
  else if (s.warten_bis_weg) {
    await schlaf(1000);
    await page.waitForFunction((sel) => !document.querySelector(sel), { timeout: sekundenVon(s) * 1000, polling: 1000 }, s.warten_bis_weg);
  } else if (s.warten_auf) {
    await page.waitForFunction((t) => {
      if (document.body.innerText.includes(t)) return true;
      try { return !!document.querySelector(t); } catch (e) { return false; }
    }, { timeout: sekundenVon(s) * 1000, polling: 1000 }, s.warten_auf);
  } else throw new Error('unbekannter Schritt');
}

// Führt die Schritte aus. Kommt ein Abbruch (interne Adresse, Zeitgrenze), endet sie sofort, auch mitten im Warten.
async function schritteAusfuehren(page, schritte, z) {
  const gescheitert = [];
  let verweigert = '';
  for (const [i, s] of schritte.entries()) {
    if (z.signal.grund) break;
    try {
      if ((await bisAbbruch(schrittAusfuehren(page, s, z), z.signal)) === ABGEBROCHEN) break;
    } catch (e) {
      const kopf = `Schritt ${i + 1} ${JSON.stringify(s).slice(0, 80)}`;
      if (e.abgelehnt) {
        verweigert = e.message;
        gescheitert.push(`${kopf}: nicht ausgeführt. ${e.message}`);
        if (i + 1 < schritte.length) gescheitert.push(`Schritte ab ${i + 2}: nicht ausgeführt, weil Schritt ${i + 1} abgelehnt ist`);
        break;
      }
      gescheitert.push(`${kopf}: ${String(e.message || e).split('\n')[0].slice(0, 120)}`);
    }
  }
  return { gescheitert, verweigert };
}

/* ---------- eine Ansicht: Rechner oder Handy ---------- */

function handyKennung(version) {
  const voll = (String(version).match(/\/([\d.]+)/) || [])[1] || '130.0.0.0';
  const haupt = voll.split('.')[0];
  return {
    ua: `Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/${haupt}.0.0.0 Mobile Safari/537.36`,
    daten: {
      brands: [{ brand: 'Chromium', version: haupt }, { brand: 'Not.A/Brand', version: '99' }],
      fullVersionList: [{ brand: 'Chromium', version: voll }, { brand: 'Not.A/Brand', version: '99.0.0.0' }],
      fullVersion: voll, platform: 'Android', platformVersion: '10.0.0', architecture: '', model: 'K', mobile: true, bitness: '', wow64: false,
    },
  };
}

/* Wache über alle Anfragen des Browsers, auch aus neuen Fenstern, Rahmen und Workern: Anfragen an interne Adressen
   scheitern und stehen im Bericht. Wechselt die geprüfte Seite selbst auf eine interne Adresse, löst die Wache den
   Abbruch aus; danach geht keine Anfrage mehr hinaus. Neue Fenster der Seite schließt sie sofort (sie halten die
   Seite sonst an). Was die Wache nicht sieht (WebSockets), hält die Schleuse auf. */
async function wacheEinrichten(browser, anfrageGrund, signal) {
  const wache = { hauptRahmen: '', hauptZiel: '', gesperrt: new Map(), eigeneSeite: false };
  const sitzung = await browser.target().createCDPSession();
  sitzung.on('Fetch.requestPaused', (ev) => {
    // Ein Name, der sich nicht nachsehen lässt, scheitert so, wie der Browser es selbst melden würde
    const sperren = (g) => {
      if (g && g.intern) wache.gesperrt.set(ev.request.url, g.grund);
      return sitzung.send('Fetch.failRequest', { requestId: ev.requestId, errorReason: g && !g.intern ? 'NameNotResolved' : 'BlockedByClient' });
    };
    if (signal.grund) {
      sperren(null).catch(() => {});
      return;
    }
    const seiteSelbst = ev.resourceType === 'Document' && ev.frameId === wache.hauptRahmen;
    if (seiteSelbst) wache.hauptZiel = zielText(ev.request.url); // wohin die geprüfte Seite gerade geht, auch nach Weiterleitungen
    anfrageGrund(ev.request.url).then((g) => {
      if (signal.grund) return sperren(null);
      if (!g) return sitzung.send('Fetch.continueRequest', { requestId: ev.requestId });
      if (g.intern && seiteSelbst) {
        signal.setzen(`Die Seite leitet auf eine interne Adresse weiter (${hostVon(ev.request.url)}, ${g.grund}), deshalb bricht der Befehl ab.`, 'intern');
      }
      return sperren(g);
    }).catch(() => {});
  });
  await sitzung.send('Fetch.enable', { patterns: [{ urlPattern: '*' }] });
  browser.on('targetcreated', (t) => {
    if (t.type() !== 'page') return;
    if (wache.eigeneSeite) {
      wache.eigeneSeite = false; // die Seite, die der Befehl gerade selbst öffnet
      return;
    }
    t.page().then((p) => p && p.close()).catch(() => {});
  });
  return wache;
}

// Der Browser benennt gescheiterte Verbindungen nach der Schleuse (ERR_SOCKS_…). Im Bericht steht der Name, den er
// ohne sie genannt hätte: gesperrt, abgelehnt, Zeit abgelaufen, nicht erreichbar.
const NETZFEHLER = {
  ECONNREFUSED: 'ERR_CONNECTION_REFUSED', ETIMEDOUT: 'ERR_CONNECTION_TIMED_OUT', ECONNRESET: 'ERR_CONNECTION_RESET',
  EHOSTUNREACH: 'ERR_ADDRESS_UNREACHABLE', ENETUNREACH: 'ERR_ADDRESS_UNREACHABLE', ENOTFOUND: 'ERR_NAME_NOT_RESOLVED',
};
function netzfehlerText(text, ort, z) {
  if (!/ERR_SOCKS_/.test(text)) return text;
  const adresse = (/'((?:wss?|https?):\/\/[^']+)'/.exec(text) || [])[1] || ort;
  const wohin = zielText(adresse);
  const name = z.gesperrt.has(wohin) ? 'ERR_BLOCKED_BY_CLIENT' : NETZFEHLER[z.verbindung.get(wohin)] || 'ERR_CONNECTION_FAILED';
  return text.replace(/ERR_SOCKS_[A-Z_]+/g, name);
}

// Warum die Seite nicht lädt: Scheitert schon die Verbindung (zur Startadresse oder dorthin, wohin sie weiterleitet),
// steht hier ein klarer Satz statt der Meldung des Browsers.
function ladeGrund(e, z) {
  const roh = String(e.message || e).split('\n')[0];
  const wohin = z.wache.hauptZiel || z.start;
  const code = /ERR_SOCKS_|ERR_PROXY_/.test(roh) && z.verbindung.get(wohin);
  return code ? `Die Seite ist nicht erreichbar: keine Verbindung zu ${wohin} (${code}).` : netzfehlerText(roh, z.url, z);
}

// Text einer Meldung der Konsole aus ihren Werten, so wie puppeteer ihn bildet (ConsoleMessage.text()): einfache Werte
// als Text, Objekte als „JSHandle@<Art>“, mit Leerzeichen dazwischen.
function konsolenText(werte) {
  return werte.map((r) => {
    if (!r) return '';
    if (r.objectId) return 'JSHandle@' + (r.subtype || r.type);
    const u = r.unserializableValue;
    if (u) return r.type === 'bigint' ? u.replace('n', '') : u === '-0' ? '0' : u;
    return r.value;
  }).join(' ');
}

// Die Einträge für „Fehler im Browser“: vorn, was nur die Schleuse gesperrt hat (WebSockets und anderes, das die Wache
// nicht sieht). Meldet die Wache dasselbe Ziel schon mit seiner vollen Adresse, entfällt der Eintrag der Schleuse:
// Chromium baut Verbindungen oft schon auf, bevor die Anfrage losgeht, sonst stünde dasselbe zweimal da.
function mitVerbindungen(fehler, verbindungen, wache) {
  const gemeldet = new Set([...wache.gesperrt.keys()].map(zielText));
  const zeilen = [...verbindungen].filter(([wohin]) => !gemeldet.has(wohin))
    .map(([wohin, grund]) => `Gesperrt, weil intern (${grund}): Verbindung zu ${wohin}`);
  return new Set([...zeilen, ...fehler]);
}

async function ansichtLaufen(browser, art, z) {
  const handy = art === 'handy';
  const { wache, signal } = z;
  const abgebrochen = () => ({ abbruch: signal.grund });
  if (signal.grund) return abgebrochen();
  const kontext = handy ? await browser.createBrowserContext() : null;
  wache.eigeneSeite = true;
  const page = handy ? await kontext.newPage() : await browser.newPage();
  wache.eigeneSeite = false;
  const fehler = new Set();
  const verbindungen = new Map(); // Ziele, die die Schleuse in dieser Ansicht sperrt, mit dem Grund
  z.melder.gesperrt = verbindungen;
  try {
    // Meldungen der Konsole kommen auf zwei Wegen an: über puppeteer und über eine eigene Sitzung (Runtime.enable).
    // puppeteer allein verliert über die Leitung (pipe) manchmal ein console.error, das im selben Datenpaket kommt wie
    // der Start der Skripte der Seite. Derselbe Text steht trotzdem nur einmal im Bericht (fehler ist eine Menge).
    const konsole = (text, ort) => {
      if (!/favicon/i.test(ort)) fehler.add('Konsole: ' + netzfehlerText(text, ort, z).slice(0, 160));
    };
    page.on('console', (m) => {
      if (m.type() !== 'error') return;
      const werte = m.args();
      konsole(werte.length ? konsolenText(werte.map((w) => w.remoteObject())) : m.text(), (m.location && m.location() && m.location().url) || '');
    });
    const sitzung = await page.createCDPSession();
    wache.hauptRahmen = (await sitzung.send('Page.getFrameTree')).frameTree.frame.id;
    sitzung.on('Runtime.consoleAPICalled', (ev) => {
      if (ev.type !== 'error') return;
      const stelle = ev.stackTrace && ev.stackTrace.callFrames && ev.stackTrace.callFrames[0];
      konsole(konsolenText(ev.args || []), (stelle && stelle.url) || '');
    });
    await sitzung.send('Runtime.enable').catch(() => {}); // ohne sie bleibt der Weg über puppeteer
    page.on('pageerror', (e) => fehler.add('Skriptfehler: ' + String(e.message || e).slice(0, 160)));
    page.on('response', (r) => {
      if (r.status() >= 400 && !/favicon/i.test(r.url())) fehler.add(`HTTP ${r.status()}: ${r.url().slice(0, 120)}`);
    });
    page.on('requestfailed', (r) => {
      const g = wache.gesperrt.get(r.url());
      fehler.add(g ? `Gesperrt, weil intern (${g}): ${r.url().slice(0, 120)}` : 'Anfrage gescheitert: ' + r.url().slice(0, 120));
    });
    await page.setBypassServiceWorker(true);
    if (handy) {
      await page.setUserAgent(z.handyKennung.ua, z.handyKennung.daten);
      await page.setViewport(HANDY);
    } else await page.setViewport(RECHNER);
    const t0 = Date.now();
    try {
      if ((await bisAbbruch(page.goto(z.url, { waitUntil: 'networkidle2', timeout: 60000 }), signal)) === ABGEBROCHEN) return abgebrochen();
    } catch (e) {
      if (signal.grund) return abgebrochen();
      if (!handy) throw new Error(ladeGrund(e, z));
      return { ladeFehler: ladeGrund(e, z).slice(0, 160), fehler: mitVerbindungen(fehler, verbindungen, wache) };
    }
    if (signal.grund) return abgebrochen();
    const { gescheitert, verweigert } = await schritteAusfuehren(page, z.schritte, { handy, domain: z.domain, ablage: z.ablage, signal });
    if (signal.grund || (await bisAbbruch(schlaf(800), signal)) === ABGEBROCHEN) return abgebrochen();
    const sekunden = Math.round((Date.now() - t0) / 1000);
    const bild = path.join(z.bildOrdner, `${z.kurz}-${art}.png`);
    const masse = await bisAbbruch(bildMachen(page, bild, handy ? HANDY.deviceScaleFactor : 1), signal);
    if (masse === ABGEBROCHEN) return abgebrochen();
    const b = await bisAbbruch(befund(page), signal);
    if (b === ABGEBROCHEN || signal.grund) return abgebrochen();
    return { bild, b, gescheitert, verweigert, sekunden, fehler: mitVerbindungen(fehler, verbindungen, wache), masse };
  } finally {
    // Nie ohne Grenze auf das Schließen warten: Mitten in einem Seitenwechsel verliert Chromium den Auftrag manchmal,
    // und die Antwort kommt nie. Nach einem Abbruch endet gleich der ganze Browser, dann wartet hier niemand.
    const zu = page.close().catch(() => {}).then(() => (kontext ? kontext.close().catch(() => {}) : undefined));
    if (!signal.grund) await hoechstens(zu, 5000);
  }
}

/* ---------- Ablauf ---------- */

// Beendet einen Prozess hart, unter Linux samt seiner Gruppe (dort startet puppeteer Chromium in einer eigenen).
function prozessBeenden(p) {
  if (!p || !p.pid || p.exitCode !== null || p.signalCode) return;
  try {
    if (process.platform === 'win32') p.kill();
    else process.kill(-p.pid, 'SIGKILL');
  } catch (e) {
    try {
      p.kill('SIGKILL');
    } catch (e2) {
      /* schon weg */
    }
  }
}

// Beendet den Browser: erst auf dem normalen Weg, nach SCHLIESSEN_MAX Sekunden hart. So endet der Befehl auch dann,
// wenn Chromium nicht mehr antwortet. sofort: nach einem Abbruch oder Fehler gleich hart (mitten in einem Seitenwechsel
// lässt sich Chromium mit dem normalen Weg viel Zeit, und zu retten ist dann nichts mehr).
async function browserBeenden(browser, sofort = false) {
  const p = browser.process();
  if (!sofort && (await hoechstens(browser.close(), SCHLIESSEN_MAX * 1000))) return;
  prozessBeenden(p);
  if (p) await hoechstens(new Promise((r) => (p.exitCode !== null || p.signalCode ? r() : p.once('exit', r))), 3000);
}

/* e (nur für Tests): env, lookup, bildOrdner, modul, chromium, browserArgs, fristSekunden, verbinden (Schleuse:
   { name: IP-Adresse }) und datei (Einstellungen für dateiPruefen und die Ablage der Kopien: wurzeln, gesperrt, home,
   kopieOrdner). Gibt den Rückgabewert zurück. */
async function main(args, e = {}) {
  const env = e.env || process.env;
  let nurVorab = false;
  if (args[0] === '--nur-vorab') {
    nurVorab = true;
    args = args.slice(1);
  }
  const url = args[0];
  const hilfe = ['hilfe', '-h', '--help'];
  if (!url || hilfe.includes(url) || !/^https?:\/\//i.test(url)) {
    console.log(HILFE);
    return url && !hilfe.includes(url) ? 2 : 0;
  }
  let adresse;
  try {
    adresse = new URL(url);
  } catch (err) {
    console.log(`FEHLER: ${url} ist keine gültige Adresse.`);
    return 2;
  }
  const lokal = env.SEITE_PRUEFEN_LOKAL === '1';
  const proxy = PROXY_VARIABLEN.some((k) => env[k]);
  const lookup = e.lookup || dns.promises.lookup;
  const start = await adressePruefen(adresse, { lokal, lookup, proxy });
  if (!start.ok) {
    console.log('FEHLER: ' + start.grund);
    return 2;
  }
  const lokalPort = lokal && hostUrteil(adresse.hostname, { lokal: true }).lokal ? portVon(adresse) : null;
  const kurz = (wert(args, '--name') || adresse.hostname.split('.')[0]).replace(/[^a-z0-9-]/gi, '') || 'seite';
  const datei = wert(args, '--schritte');
  let schritte = [];
  if (datei) {
    let inhalt = '';
    try {
      // Nur eine normale, kleine Datei lesen: An einer FIFO oder an /dev/zero bliebe der Befehl sonst ohne Ende hängen
      const fd = fs.openSync(datei, fs.constants.O_RDONLY | (fs.constants.O_NONBLOCK || 0));
      try {
        const st = fs.fstatSync(fd);
        if (!st.isFile()) throw new Error('keine normale Datei');
        if (st.size > SCHRITTE_MAX) throw new Error(`die Datei ist größer als ${SCHRITTE_MAX / 1000} KB`);
        inhalt = fs.readFileSync(fd, 'utf8');
      } finally {
        fs.closeSync(fd);
      }
      schritte = JSON.parse(inhalt);
      if (!Array.isArray(schritte)) throw new Error('keine Liste');
    } catch (err) {
      console.log(`FEHLER: Die Schritte in ${datei} sind kein gültiges JSON (${jsonGrund(err, inhalt)}). Format: seite-pruefen hilfe`);
      return 2;
    }
  }
  const zeiten = wartezeitFehler(schritte);
  if (zeiten.length) {
    console.log(zeiten.join('\n'));
    return 2;
  }
  // Schritt „datei“ vorab prüfen, ohne eine Datei zu lesen
  const domain = appDomain(env);
  const dateiEinstellungen = e.datei || {};
  const dateiSchritte = schritte.map((s, i) => [s, i]).filter(([s]) => s && typeof s === 'object' && 'datei' in s);
  if (dateiSchritte.length && !istEigeneApp(url, domain)) {
    console.log(`FEHLER: Schritt ${dateiSchritte[0][1] + 1} (Datei wählen) nicht ausgeführt: Dateien wählt der Befehl nur auf deiner eigenen App aus (Adresse endet auf ${domain}, oder 127.0.0.1 und localhost), ${adresse.hostname} gehört nicht dazu.`);
    return 2;
  }
  const vorab = [];
  for (const [s, i] of dateiSchritte) {
    const g = typeof s.datei !== 'string' || !s.datei.trim() ? { grund: 'Es fehlt das Dateifeld („datei“), zum Beispiel "input[type=file]".' } : dateiPruefen(s.pfad, dateiEinstellungen);
    if (!g.ok) vorab.push(`FEHLER: Schritt ${i + 1} (Datei wählen) nicht ausgeführt: ${g.grund}`);
  }
  if (vorab.length) {
    console.log(vorab.join('\n'));
    return 2;
  }
  if (nurVorab) return 0;

  // Eigener Ordner für diesen Lauf: Profil des Browsers und Kopien gewählter Dateien, am Ende gelöscht
  resteLoeschen();
  const lauf = fs.mkdtempSync(path.join(os.tmpdir(), LAUF_ORDNER));
  // Löscht den Ordner des Laufs. Geht das nicht gleich (unter Windows hält ein endender Browser noch Dateien fest),
  // versucht er es ein paar Mal; bleibt etwas liegen, räumt resteLoeschen es bei einem späteren Lauf weg.
  const weg = () => {
    try {
      fs.rmSync(lauf, { recursive: true, force: true });
      return true;
    } catch (err) {
      return false;
    }
  };
  const wegMitGeduld = async () => {
    for (let i = 0; i < 8 && !weg(); i++) await schlaf(250);
  };
  const ablage = dateiAblage({ kopieOrdner: lauf, ...dateiEinstellungen });
  const bildOrdner = e.bildOrdner || '/tmp';
  const signal = abbruchSignal();
  const melder = { gesperrt: null }; // die Ansicht, die gerade läuft, hängt hier ihre Liste ein
  const verbindung = new Map(); // Ziele, zu denen die Schleuse keine Verbindung bekam, mit dem Grund
  const gesperrt = new Set(); // Ziele, die die Schleuse gesperrt hat, weil sie intern sind
  let schleuse = null;
  let browser = null;
  let uhr = null;
  let nachUhr = null;
  let gescheitert = false;
  let vorbei = false;
  // Von außen beendet (SIGTERM von „app“, Abbruch im Terminal): Browser hart beenden, Ordner löschen, sofort enden.
  // Ohne das liefe der Befehl ohne Browser weiter, bis der laufende Schritt zu Ende gewartet hat.
  const beiSignal = (name) => {
    prozessBeenden(browser && browser.process());
    weg();
    process.exit(128 + ((os.constants.signals || {})[name] || 15));
  };
  const signale = ['SIGTERM', 'SIGHUP', 'SIGINT'];
  for (const s of signale) process.on(s, beiSignal);
  const abbrechen = () => {
    for (const a of ['rechner', 'handy']) fs.rmSync(path.join(bildOrdner, `${kurz}-${a}.png`), { force: true });
    console.log('FEHLER: ' + signal.grund);
    return signal.art === 'frist' ? 1 : 2;
  };
  try {
    // Die Zeitgrenze läuft ab jetzt, also auch für den Start des Browsers
    const frist = e.fristSekunden || (schritte.length ? FRIST_MIT_SCHRITTEN : FRIST_OHNE_SCHRITTE);
    uhr = setTimeout(() => signal.setzen(`Die Prüfung war nach ${dauerText(frist)} nicht fertig, deshalb bricht der Befehl ab. `
      + (schritte.length ? 'Kürzere Wartezeiten in den Schritten helfen („sekunden“, „warten“).' : 'Die Seite lädt zu lange oder hängt.'), 'frist'), frist * 1000);
    const ziel = zielPruefer({ lokal, lookup, proxy, lokalPort, bekannt: start.host ? { [start.host]: start.adressen } : {} });
    const startArgs = ['--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage'];
    if (!proxy) {
      schleuse = await schleuseStarten(ziel, (wohin, urteil) => {
        if (urteil.verbindung) verbindung.set(wohin, urteil.grund);
        else if (!urteil.intern) verbindung.set(wohin, 'ENOTFOUND'); // der Name ließ sich nicht nachsehen
        else {
          gesperrt.add(wohin);
          if (melder.gesperrt) melder.gesperrt.set(wohin, urteil.grund);
        }
      }, e.verbinden || {});
      // <-loopback>: Auch Verbindungen zu diesem Rechner gehen über die Schleuse (sonst nähme Chromium sie davon aus).
      // WebRTC schickte seine Pakete sonst direkt (UDP, an der Schleuse vorbei); der Schalter heißt je nach Fassung anders.
      startArgs.push(`--proxy-server=socks5://127.0.0.1:${schleuse.port}`, '--proxy-bypass-list=<-loopback>',
        '--webrtc-ip-handling-policy=disable_non_proxied_udp', '--force-webrtc-ip-handling-policy=disable_non_proxied_udp');
    }
    const puppeteer = require(e.modul || env.SEITE_PRUEFEN_MODUL || 'puppeteer-core');
    // pipe: Endet dieser Prozess, auch hart (kill -9), merkt Chromium das an der Leitung und beendet sich selbst.
    // Signale behandelt dieser Befehl selbst (beiSignal), nicht puppeteer.
    browser = await puppeteer.launch({
      executablePath: e.chromium || env.CHROMIUM || '/usr/bin/chromium',
      pipe: true,
      userDataDir: path.join(lauf, 'profil'),
      handleSIGINT: false,
      handleSIGTERM: false,
      handleSIGHUP: false,
      args: [...startArgs, ...(e.browserArgs || [])],
    });
    const ablauf = (async () => {
      const z = {
        url, schritte, kurz, bildOrdner, domain, ablage, signal, melder, verbindung, gesperrt,
        start: zielText(url),
        wache: await wacheEinrichten(browser, anfragePruefer({ ziel }), signal),
        handyKennung: handyKennung(await browser.version()),
      };
      const rechner = await ansichtLaufen(browser, 'rechner', z);
      const handy = rechner.abbruch ? {} : await ansichtLaufen(browser, 'handy', z);
      return { rechner, handy };
    })();
    ablauf.catch(() => {});
    // Nach einem Abbruch (interne Adresse, Zeitgrenze) hat der Ablauf NACHFRIST Sekunden, um von selbst zu enden.
    // Antwortet der Browser nicht mehr, wartet der Befehl nicht länger auf ihn.
    const hart = signal.versprechen.then(() => new Promise((r) => {
      if (!vorbei) nachUhr = setTimeout(() => r(null), NACHFRIST * 1000);
    }));
    const ergebnis = await Promise.race([ablauf, hart]);
    if (!ergebnis || ergebnis.rechner.abbruch || ergebnis.handy.abbruch || signal.grund) return abbrechen();
    const { rechner, handy } = ergebnis;

    const schwach = [...new Set([...rechner.b.schwach, ...(handy.b ? handy.b.schwach : [])])].slice(0, 8);
    const fehler = [...rechner.fehler];
    for (const f of handy.fehler || []) if (!rechner.fehler.has(f)) fehler.push(f + ' (nur am Handy)');
    console.log(`Seite: ${url} (geladen${schritte.length ? ' und Schritte ausgeführt' : ''} in ${rechner.sekunden} s)`);
    if (schritte.length) {
      const alle = [...rechner.gescheitert, ...(handy.gescheitert || []).map((g) => 'Am Handy: ' + g)];
      console.log(alle.length ? `Schritte gescheitert:\n  ${alle.join('\n  ')}` : `Schritte: alle ${schritte.length} ausgeführt`);
    }
    console.log(ansicht('Rechner (1280 px)', rechner.bild, rechner.b, bildZeilen(rechner.masse), rechner.masse.keinBild));
    if (handy.ladeFehler) console.log(`Handy (390 px): kein Bild, die Seite ließ sich am Handy nicht laden (${handy.ladeFehler})`);
    else {
      const zusatz = [`Wie auf einem echten Handy (Berührung, mobiler Browser): neu geladen${schritte.length ? ' und Schritte noch einmal ausgeführt' : ''} in ${handy.sekunden} s`];
      if (handy.b.cw > HANDY.width + 1) {
        zusatz.push(`Nicht fürs Handy eingerichtet: Die Seite wird ${handy.b.cw} px breit gebaut und verkleinert gezeigt. Im Kopf der Seite fehlt <meta name="viewport" content="width=device-width, initial-scale=1">.`);
      }
      console.log(ansicht('Handy (390 px)', handy.bild, handy.b, [...zusatz, ...bildZeilen(handy.masse)], handy.masse.keinBild));
    }
    console.log(schwach.length ? `Schwacher Kontrast:\n  ${schwach.join('\n  ')}` : 'Schwacher Kontrast: keiner gefunden');
    console.log(fehler.length ? `Fehler im Browser:\n  ${fehler.slice(0, 8).join('\n  ')}` : 'Fehler im Browser: keine');
    console.log(`Sichtbarer Text (Anfang):\n${rechner.b.text}`);
    console.log('\nNächster Schritt: Sieh dir beide Bilder an (read_file), behebe Auffälligkeiten und zeig das Bild vom Rechner im Chat (display_file).');
    return rechner.verweigert || handy.verweigert ? 2 : 0;
  } catch (err) {
    gescheitert = true;
    if (signal.grund) return abbrechen();
    throw err;
  } finally {
    vorbei = true;
    clearTimeout(uhr);
    clearTimeout(nachUhr);
    if (browser) await browserBeenden(browser, gescheitert || !!signal.grund);
    if (schleuse) schleuse.schliessen();
    ablage.aufraeumen();
    await wegMitGeduld();
    for (const s of signale) process.removeListener(s, beiSignal);
  }
}

module.exports = {
  main, HILFE, APP_DOMAIN, DATEI_ENDUNGEN, appDomain, istEigeneApp, hostUrteil, ipGrund, adressePruefen, zielPruefer,
  anfragePruefer, schleuseStarten, dateiPruefen, dateiAblage, kopieren, bildRahmen, jsonGrund, wartezeitFehler,
  resteLoeschen, hoechstens, netzfehlerText, mitVerbindungen, konsolenText,
};

if (require.main === module) {
  // Versprechen, die nach einem Abbruch ins Leere laufen (der Browser ist dann schon zu), beenden den Befehl nicht
  process.on('unhandledRejection', () => {});
  // Erst beenden, wenn alles ausgegeben ist; offene Verbindungen der Seite halten den Befehl nicht auf.
  const ende = (code) => process.stdout.write('', () => process.exit(code));
  main(process.argv.slice(2)).then(ende, (e) => {
    console.log('FEHLER: ' + String(e.message || e).split('\n')[0]);
    ende(1);
  });
}
