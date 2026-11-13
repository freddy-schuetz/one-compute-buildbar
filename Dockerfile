# one.compute (buildbar_): Terminal-Image für buildbar_ auf one.intelligence.
# Basis ist das Open-Terminal-Image, das auch one.compute (Advanced) nutzt. Dazu kommen die Werkzeuge,
# die der Assistent buildbar_ braucht. Das Home (/home/user) wird beim Start als dauerhafter Speicher
# darübergelegt, deshalb liegt hier nichts im Home: Programme unter /usr/local/bin und /opt/buildbar.
# Geheimnisse sind nicht im Image. Je Person schreibt der Zusatz „buildbar Zugang“ in one.intelligence die Datei
# ~/.buildbar/zugang.env (siehe README.md).
FROM ghcr.io/open-webui/open-terminal:0.17.7

LABEL org.opencontainers.image.title="one.compute (buildbar_)" \
      org.opencontainers.image.description="Open Terminal mit n8n-mcp, Browser-Prüfung und den buildbar-Befehlen" \
      org.opencontainers.image.source="https://github.com/freddy-schuetz/one-compute-buildbar"

USER root

# Standardwerte für alle Personen (ohne Geheimnisse); ~/.buildbar/zugang.env kann sie überschreiben
ENV N8N_API_URL=https://n8n.oi.destination.one \
    OIL_HUB_URL=https://oi-labor-hub.friedemann-schuetz.eu \
    NPM_CONFIG_CACHE=/var/tmp/buildbar/npm-cache \
    BB_WERK=/var/tmp/buildbar

# n8n-mcp (Werkzeuge für n8n, fest auf die geprüfte Version) und der Browser-Baustein für seite-pruefen
RUN npm install -g --no-audit --no-fund n8n-mcp@2.91.0 \
 && mkdir -p /opt/buildbar /var/tmp/buildbar \
 && cd /opt/buildbar && npm install --no-audit --no-fund --no-save puppeteer-core@23.11.1 \
 && (npm cache clean --force 2>/dev/null || true) \
 && chown -R user:user /var/tmp/buildbar

COPY bin/ /usr/local/bin/
COPY lib/ /opt/buildbar/
RUN chmod 755 /usr/local/bin/app /usr/local/bin/n8n-werkzeug /usr/local/bin/n8n-mcp-start \
              /usr/local/bin/seite-pruefen /usr/local/bin/buildbar-status /usr/local/bin/buildbar-merken \
 && chmod 644 /opt/buildbar/seite-pruefen.js /opt/buildbar/schluessel-laden.sh \
 && node -e "require('/opt/buildbar/node_modules/puppeteer-core/package.json')" \
 && command -v n8n-mcp >/dev/null \
 && rm -rf /usr/local/bin/__pycache__

USER user
WORKDIR /home/user
