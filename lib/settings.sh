# Reads settings.env WITHOUT running it as shell code (used by ./jb and bin/run-service.sh),
# so values work exactly as typed: a password like  Pa$$w0rd!  or one with spaces needs no
# quotes. Quotes are still allowed ('...' "..." and TextEdit's curly ones), text after
# " #" is a comment, and Windows line endings are fine.   Works with macOS's bash 3.2.
jb_load_settings() {
  local f="$1" line key val sq="'" dq='"'
  [ -f "$f" ] || return 0
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%$'\r'}"
    line="${line#$'\xef\xbb\xbf'}"                        # byte-order mark some editors add
    line="${line#"${line%%[![:space:]]*}"}"                # leading blanks
    case "$line" in ''|'#'*) continue ;; esac
    case "$line" in *=*) ;; *) continue ;; esac
    key="${line%%=*}"
    val="${line#*=}"
    key="${key%"${key##*[![:space:]]}"}"                   # trailing blanks
    key="${key#export }"
    case "$key" in ''|[0-9]*|*[!A-Za-z0-9_]*) continue ;; esac
    val="${val#"${val%%[![:space:]]*}"}"
    case "$val" in
      \'*)  val="${val#\'}";  val="${val%%\'*}" ;;
      \"*)  val="${val#\"}";  val="${val%%\"*}" ;;
      $'\xe2\x80\x98'*) val="${val#$'\xe2\x80\x98'}"; val="${val%%$'\xe2\x80\x99'*}" ;;
      $'\xe2\x80\x9c'*) val="${val#$'\xe2\x80\x9c'}"; val="${val%%$'\xe2\x80\x9d'*}" ;;
      *)    val="${val%%[[:space:]]#*}"
            val="${val%"${val##*[![:space:]]}"}" ;;
    esac
    # TextEdit may have turned ' and " typed inside a value into curly quotes: undo that
    # (the replacements come from variables: bash 3.2 keeps a backslash-escaped quote literally)
    val="${val//$'\xe2\x80\x98'/$sq}"; val="${val//$'\xe2\x80\x99'/$sq}"
    val="${val//$'\xe2\x80\x9c'/$dq}"; val="${val//$'\xe2\x80\x9d'/$dq}"
    export "$key=$val"
  done < "$f"
}
