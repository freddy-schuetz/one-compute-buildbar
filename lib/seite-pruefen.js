#!/usr/bin/env node
/* seite-pruefen: prüft die Oberfläche einer App im Browser (buildbar_ auf one.intelligence).
   Aufruf über seite-pruefen (/usr/local/bin); der Browser-Baustein liegt unter /opt/buildbar. */
'use strict';
const fs = require('fs');

const HILFE = `seite-pruefen <adresse> [--schritte <datei.json>] [--name <kurzname>]

Öffnet die Seite in Chromium, führt optional Schritte aus (Formular ausfüllen, absenden, warten),
macht danach ein Bild am Rechner (1280 px) und eines auf dem Handy (390 px) und meldet:
seitlich zu breite Seite, Tabellenspalten, die nur durch Wischen sichtbar sind, schwachen Kontrast
und Fehler im Browser (Konsole, gescheiterte Anfragen, HTTP-Fehler).

Schritte als JSON-Liste in einer Datei, zum Beispiel /tmp/schritte.json:
[{"fuellen": "#name", "text": "Café Sperl"}, {"fuellen": "#ort", "text": "Wien"},
 {"waehlen": "#land", "wert": "AT"}, {"klicken": "button[type=submit]"},
 {"warten_bis_weg": ".lade", "sekunden": 180}]
Weitere Schritte: {"warten": 5} (Sekunden), {"warten_auf": "Text oder CSS-Selektor", "sekunden": 60}.
Die Bilder liegen danach unter /tmp/<kurzname>-rechner.png und /tmp/<kurzname>-handy.png.`;

const schlaf = (ms) => new Promise((r) => setTimeout(r, ms));
function wert(args, name) {
  const i = args.indexOf(name);
  return i >= 0 ? args[i + 1] : undefined;
}

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
    const eingefangen = (el) => {
      for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) {
        const o = getComputedStyle(p).overflowX;
        if (o === 'auto' || o === 'scroll' || o === 'hidden' || o === 'clip') return true;
      }
      return false;
    };
    // Elemente, die über den rechten Rand ragen und von keinem wischbaren Rahmen eingefangen werden
    const zuBreit = [];
    for (const el of document.body.querySelectorAll('*')) {
      const r = el.getBoundingClientRect();
      if (r.width > 1 && r.right > cw + 1 && !eingefangen(el)) {
        zuBreit.push(`${name(el)} (rechter Rand bei ${Math.round(r.right)} px)`);
        if (zuBreit.length >= 5) break;
      }
    }
    // Tabellen, deren Spalten rechts außerhalb liegen
    const tabellen = [];
    for (const t of document.querySelectorAll('table')) {
      const rahmen = (t.parentElement || t).getBoundingClientRect();
      const rechts = Math.min(rahmen.right, cw);
      const kopf = t.querySelectorAll('thead th').length ? t.querySelectorAll('thead th') : t.querySelectorAll('tr:first-child th, tr:first-child td');
      const verborgen = [...kopf].filter((th) => th.getBoundingClientRect().left >= rechts - 4)
        .map((th) => th.innerText.trim() || '(ohne Titel)');
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
        const st = getComputedStyle(e);
        if (st.backgroundImage && st.backgroundImage !== 'none') return null;
        const f = farbe(st.backgroundColor);
        if (f && f.a > 0.5) return f.rgb;
      }
      return [255, 255, 255];
    };
    const schwach = [];
    for (const el of document.body.querySelectorAll('h1,h2,h3,h4,p,li,label,a,button,span,td,th,div,strong,small')) {
      if (![...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim())) continue;
      const st = getComputedStyle(el);
      const r = el.getBoundingClientRect();
      if (st.display === 'none' || st.visibility === 'hidden' || r.width <= 1 || r.height <= 1 || r.right <= 0 || r.bottom <= 0) continue;
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

function ansicht(titel, bild, b) {
  const z = [`${titel}: Bild ${bild}`];
  z.push(b.sw > b.cw + 1 ? `  Seite seitlich zu breit: JA, ${b.sw} px statt ${b.cw} px. Zu breit: ${b.zuBreit.join('; ') || 'unklar'}`
    : '  Seite seitlich zu breit: nein');
  for (const t of b.tabellen) z.push('  ' + t);
  return z.join('\n');
}

(async () => {
  const args = process.argv.slice(2);
  const url = args[0];
  if (!url || ['hilfe', '-h', '--help'].includes(url) || !/^https?:\/\//i.test(url)) {
    console.log(HILFE);
    process.exit(url && !['hilfe', '-h', '--help'].includes(url) ? 2 : 0);
  }
  const kurz = (wert(args, '--name') || new URL(url).hostname.split('.')[0]).replace(/[^a-z0-9-]/gi, '') || 'seite';
  const datei = wert(args, '--schritte');
  let schritte = [];
  if (datei) {
    try {
      schritte = JSON.parse(fs.readFileSync(datei, 'utf8'));
      if (!Array.isArray(schritte)) throw new Error('keine Liste');
    } catch (e) {
      console.log(`FEHLER: Die Schritte in ${datei} sind kein gültiges JSON (${e.message}). Format: seite-pruefen hilfe`);
      process.exit(2);
    }
  }
  const puppeteer = require(process.env.SEITE_PRUEFEN_MODUL || 'puppeteer-core');
  const browser = await puppeteer.launch({
    executablePath: process.env.CHROMIUM || '/usr/bin/chromium',
    args: ['--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage'],
  });
  const fehler = new Set();
  try {
    const page = await browser.newPage();
    page.on('console', (m) => {
      const ort = (m.location && m.location() && m.location().url) || '';
      if (m.type() === 'error' && !/favicon/i.test(ort)) fehler.add('Konsole: ' + m.text().slice(0, 160));
    });
    page.on('pageerror', (e) => fehler.add('Skriptfehler: ' + String(e.message || e).slice(0, 160)));
    page.on('response', (r) => { if (r.status() >= 400 && !/favicon/i.test(r.url())) fehler.add(`HTTP ${r.status()}: ${r.url().slice(0, 120)}`); });
    page.on('requestfailed', (r) => fehler.add('Anfrage gescheitert: ' + r.url().slice(0, 120)));
    await page.setViewport({ width: 1280, height: 900 });
    const t0 = Date.now();
    await page.goto(url, { waitUntil: 'networkidle2', timeout: 60000 });
    const gescheitert = [];
    for (const [i, s] of schritte.entries()) {
      try {
        if (s.fuellen) {
          await page.click(s.fuellen, { clickCount: 3 });
          await page.type(s.fuellen, String(s.text ?? ''));
        } else if (s.waehlen) await page.select(s.waehlen, String(s.wert));
        else if (s.klicken) await page.click(s.klicken);
        else if (s.warten) await schlaf(Number(s.warten) * 1000);
        else if (s.warten_bis_weg) {
          await schlaf(1000);
          await page.waitForFunction((sel) => !document.querySelector(sel), { timeout: (s.sekunden || 120) * 1000, polling: 1000 }, s.warten_bis_weg);
        } else if (s.warten_auf) {
          await page.waitForFunction((t) => {
            if (document.body.innerText.includes(t)) return true;
            try { return !!document.querySelector(t); } catch (e) { return false; }
          }, { timeout: (s.sekunden || 120) * 1000, polling: 1000 }, s.warten_auf);
        } else throw new Error('unbekannter Schritt');
      } catch (e) {
        gescheitert.push(`Schritt ${i + 1} ${JSON.stringify(s).slice(0, 80)}: ${String(e.message || e).split('\n')[0].slice(0, 120)}`);
      }
    }
    await schlaf(800);
    const sekunden = Math.round((Date.now() - t0) / 1000);
    const bildRechner = `/tmp/${kurz}-rechner.png`;
    await page.screenshot({ path: bildRechner, fullPage: true });
    const rechner = await befund(page);
    await page.setViewport({ width: 390, height: 844 });
    await schlaf(800);
    const bildHandy = `/tmp/${kurz}-handy.png`;
    await page.screenshot({ path: bildHandy, fullPage: true });
    const handy = await befund(page);

    const schwach = [...new Set([...rechner.schwach, ...handy.schwach])].slice(0, 8);
    console.log(`Seite: ${url} (geladen${schritte.length ? ' und Schritte ausgeführt' : ''} in ${sekunden} s)`);
    if (schritte.length) console.log(gescheitert.length ? `Schritte gescheitert:\n  ${gescheitert.join('\n  ')}` : `Schritte: alle ${schritte.length} ausgeführt`);
    console.log(ansicht('Rechner (1280 px)', bildRechner, rechner));
    console.log(ansicht('Handy (390 px)', bildHandy, handy));
    console.log(schwach.length ? `Schwacher Kontrast:\n  ${schwach.join('\n  ')}` : 'Schwacher Kontrast: keiner gefunden');
    console.log(fehler.size ? `Fehler im Browser:\n  ${[...fehler].slice(0, 8).join('\n  ')}` : 'Fehler im Browser: keine');
    console.log(`Sichtbarer Text (Anfang):\n${rechner.text}`);
    console.log('\nNächster Schritt: Sieh dir beide Bilder an (read_file), behebe Auffälligkeiten und zeig das Bild vom Rechner im Chat (display_file).');
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.log('FEHLER: ' + String(e.message || e).split('\n')[0]);
  process.exit(1);
});
