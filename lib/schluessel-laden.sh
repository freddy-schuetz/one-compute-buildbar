# Lädt die Schlüssel der Person in die Umgebung, ohne sie auszuwerten (kein eval, kein source).
# Quellen: /home/user/schluessel.txt (Zeilen NAME=wert) und, falls vorhanden, ~/.buildbar/geheim.env.
# Werte, die schon in der Umgebung stehen, bleiben. HUB_TOKEN ist ein Zweitname für OIL_HUB_TOKEN.
for bb_datei in "$HOME/schluessel.txt" "$HOME/.buildbar/geheim.env"; do
  [ -f "$bb_datei" ] || continue
  while IFS='=' read -r bb_k bb_v || [ -n "$bb_k" ]; do
    bb_k="${bb_k%%[[:space:]]*}"
    case "$bb_k" in ''|'#'*) continue ;; esac
    bb_v="${bb_v%$'\r'}"
    bb_v="${bb_v#"${bb_v%%[![:space:]]*}"}"
    bb_v="${bb_v%"${bb_v##*[![:space:]]}"}"
    [ "$bb_k" = "HUB_TOKEN" ] && bb_k="OIL_HUB_TOKEN"
    case "$bb_k" in *[!A-Z0-9_]*) continue ;; esac
    bb_alt="${!bb_k:-}"
    [ -n "$bb_alt" ] && continue
    export "$bb_k=$bb_v"
  done < "$bb_datei"
done
unset bb_datei bb_k bb_v bb_alt
