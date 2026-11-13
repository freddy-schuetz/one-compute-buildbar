# Lädt die Zugänge der Person in die Umgebung, ohne sie auszuwerten (kein eval, kein source).
# Quellen: ~/.buildbar/zugang.env (schreibt der Zusatz „buildbar Zugang“ in Open WebUI; seine Werte gelten vor den
# Standardwerten des Images) und ~/.buildbar/geheim.env (alte Einrichtung, füllt nur Lücken).
# Übernommen werden nur die bekannten Namen unten, nie Variablen wie PATH oder NODE_OPTIONS.
# HUB_TOKEN ist ein Zweitname für OIL_HUB_TOKEN. Der Ordner ~/.buildbar ist versteckt und im Dateifenster ausgeblendet.
bb_namen=" ZUSATZ KUERZEL PERSON_ID N8N_API_URL N8N_API_KEY OIL_HUB_URL OIL_HUB_TOKEN "
if [ -d "$HOME/.buildbar" ]; then
  chmod 700 "$HOME/.buildbar" 2>/dev/null
  chmod 600 "$HOME/.buildbar/"*.env 2>/dev/null
fi
for bb_datei in "$HOME/.buildbar/zugang.env" "$HOME/.buildbar/geheim.env"; do
  [ -f "$bb_datei" ] || continue
  while IFS= read -r bb_zeile || [ -n "$bb_zeile" ]; do
    bb_zeile="${bb_zeile%$'\r'}"
    case "$bb_zeile" in '#'*|*'='*) ;; *) continue ;; esac
    case "$bb_zeile" in '#'*) continue ;; esac
    bb_k="${bb_zeile%%=*}"
    bb_v="${bb_zeile#*=}"
    bb_k="${bb_k//[[:space:]]/}"
    bb_v="${bb_v#"${bb_v%%[![:space:]]*}"}"
    bb_v="${bb_v%"${bb_v##*[![:space:]]}"}"
    [ "$bb_k" = "HUB_TOKEN" ] && bb_k="OIL_HUB_TOKEN"
    case "$bb_namen" in *" $bb_k "*) ;; *) continue ;; esac
    # Die alte geheim.env füllt nur Lücken; zugang.env gilt immer
    if [ "$bb_datei" != "$HOME/.buildbar/zugang.env" ] && [ -n "${!bb_k:-}" ]; then
      continue
    fi
    export "$bb_k=$bb_v"
  done < "$bb_datei"
done
unset bb_datei bb_zeile bb_k bb_v bb_namen
