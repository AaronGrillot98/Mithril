#!/usr/bin/env bash
# Mithril — one-line installer for Linux and macOS.
#
#   curl -fsSL https://raw.githubusercontent.com/AaronGrillot98/mithril/main/install.sh | bash
#
# Installs Mithril into ~/.mithril/venv and adds a `mithril` launcher to
# ~/.local/bin (which is on $PATH for most shells).

set -euo pipefail

REPO="${MITHRIL_REPO:-https://github.com/AaronGrillot98/mithril}"
REF="${MITHRIL_REF:-main}"
INSTALL_DIR="${MITHRIL_HOME:-$HOME/.mithril}"
BIN_DIR="${MITHRIL_BIN:-$HOME/.local/bin}"

bold()  { printf '\033[1m%s\033[0m\n'  "$*"; }
info()  { printf '  \033[36m›\033[0m %s\n' "$*"; }
ok()    { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn()  { printf '  \033[33m!\033[0m %s\n' "$*"; }
die()   { printf '  \033[31m✗\033[0m %s\n' "$*" >&2; exit 1; }

bold "Installing Mithril"

# --- Find a usable Python 3.10+ -----------------------------------------------
PY=""
for cand in python3.13 python3.12 python3.11 python3.10 python3 python; do
  if command -v "$cand" >/dev/null 2>&1; then
    if "$cand" -c 'import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)' >/dev/null 2>&1; then
      PY="$cand"; break
    fi
  fi
done
[ -n "$PY" ] || die "Python 3.10+ is required but was not found on PATH."
ok "Using $($PY -c 'import sys;print(sys.executable, sys.version.split()[0])')"

# --- Create venv --------------------------------------------------------------
info "Creating virtual environment at $INSTALL_DIR/venv"
mkdir -p "$INSTALL_DIR"
"$PY" -m venv "$INSTALL_DIR/venv"
ok "venv ready"

# --- Install mithril from the repo -------------------------------------------
info "Installing mithril-llm from $REPO@$REF"
"$INSTALL_DIR/venv/bin/pip" install --quiet --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install --quiet "git+${REPO}.git@${REF}"
ok "package installed"

# --- Drop launcher into ~/.local/bin -----------------------------------------
mkdir -p "$BIN_DIR"
LAUNCHER="$BIN_DIR/mithril"
cat > "$LAUNCHER" <<EOF
#!/usr/bin/env bash
exec "$INSTALL_DIR/venv/bin/mithril" "\$@"
EOF
chmod +x "$LAUNCHER"
ok "launcher installed: $LAUNCHER"

if ! command -v mithril >/dev/null 2>&1; then
  case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *)  warn "$BIN_DIR is not on PATH. Add this line to your shell profile:"
        printf '\n      export PATH="%s:$PATH"\n\n' "$BIN_DIR"
        ;;
  esac
fi

bold ""
bold "Done."
printf "\n  Start the proxy:    \033[36mmithril serve\033[0m\n"
printf   "  One-shot scan:      \033[36mmithril scan \"ignore previous instructions\"\033[0m\n"
printf   "  Dashboard:          \033[36mhttp://localhost:8080\033[0m\n\n"
