#!/usr/bin/env bash
# ==============================================================================
# Setup.sh - AI GM Bridge セットアップスクリプト
#
# このスクリプトは何度実行しても安全（冪等）に環境構築および検証を行えます。
# 依存ライブラリの自動確認・インストール、権限設定、FVTT連携環境の診断を実施します。
# ==============================================================================

set -euo pipefail

# 端末カラー設定
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  C_RESET="\033[0m"
  C_BOLD="\033[1m"
  C_GREEN="\033[32m"
  C_YELLOW="\033[33m"
  C_BLUE="\033[34m"
  C_RED="\033[31m"
else
  C_RESET=""
  C_BOLD=""
  C_GREEN=""
  C_YELLOW=""
  C_BLUE=""
  C_RED=""
fi

log_info()  { echo -e "${C_BLUE}[INFO]${C_RESET} $*"; }
log_ok()    { echo -e "${C_GREEN}[OK]${C_RESET}   $*"; }
log_warn()  { echo -e "${C_YELLOW}[WARN]${C_RESET} $*"; }
log_error() { echo -e "${C_RED}[ERROR]${C_RESET} $*"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo -e "${C_BOLD}=====================================================================${C_RESET}"
echo -e "${C_BOLD}          AI GM Bridge (Foundry VTT 連携) セットアップ               ${C_RESET}"
echo -e "${C_BOLD}=====================================================================${C_RESET}"
echo ""

# ------------------------------------------------------------------------------
# 1. Python 3 実行環境の確認
# ------------------------------------------------------------------------------
log_info "1. Python 実行環境の確認中..."

if ! command -v python3 &>/dev/null; then
  log_error "python3 が見つかりません。Python 3.10以上をインストールしてください。"
  exit 1
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PYTHON_MAJOR=$(python3 -c 'import sys; print(sys.version_info.major)')
PYTHON_MINOR=$(python3 -c 'import sys; print(sys.version_info.minor)')

if [ "$PYTHON_MAJOR" -lt 3 ] || { [ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 10 ]; }; then
  log_error "Python のバージョンが古すぎます (検出: v${PYTHON_VERSION})。Python 3.10以上が必要です。"
  exit 1
fi
log_ok "Python 3 検出: $(python3 --version) (${PYTHON_VERSION})"

# ------------------------------------------------------------------------------
# 2. Python 依存パッケージの確認 & 自動インストール (冪等)
# ------------------------------------------------------------------------------
log_info "2. Python 依存パッケージの確認中..."

REQUIRED_MODULES=("websockets" "edge_tts" "google.genai")
MISSING_MODULES=()

for mod in "${REQUIRED_MODULES[@]}"; do
  if ! python3 -c "import $mod" &>/dev/null; then
    MISSING_MODULES+=("$mod")
  fi
done

if [ ${#MISSING_MODULES[@]} -eq 0 ]; then
  log_ok "すべての依存パッケージがインストール済みです (websockets, edge-tts, google-genai)。"
else
  log_warn "未インストールのパッケージを検出: ${MISSING_MODULES[*]}"
  log_info "requirements.txt からパッケージをインストールします..."

  # pip3 の存在確認
  if ! python3 -m pip --version &>/dev/null; then
    log_error "pip が見つかりません。'sudo apt install python3-pip' などで pip を導入してください。"
    exit 1
  fi

  # PEP 668 (externally-managed-environment) 対策を含む安全なインストール試行
  if ! python3 -m pip install -r "$SCRIPT_DIR/requirements.txt" --user 2>/dev/null; then
    log_info "標準インストールに失敗したため、--break-system-packages フラグを適用して再試行します..."
    python3 -m pip install -r "$SCRIPT_DIR/requirements.txt" --user --break-system-packages
  fi

  # インストール後の再検証
  for mod in "${REQUIRED_MODULES[@]}"; do
    if ! python3 -c "import $mod" &>/dev/null; then
      log_error "パッケージ '$mod' のインストール検証に失敗しました。"
      exit 1
    fi
  done
  log_ok "依存パッケージのインストールが完了しました。"
fi

# ------------------------------------------------------------------------------
# 3. agy (Antigravity CLI) / Gemini 連携環境の診断
# ------------------------------------------------------------------------------
log_info "3. AI エンジン連携の診断中..."

AGY_FOUND=false
if command -v agy &>/dev/null; then
  AGY_PATH=$(command -v agy)
  AGY_FOUND=true
elif [ -x "$HOME/.local/bin/agy" ]; then
  AGY_PATH="$HOME/.local/bin/agy"
  AGY_FOUND=true
fi

if [ "$AGY_FOUND" = true ]; then
  log_ok "agy (Antigravity CLI) 検出: $AGY_PATH (サポートGM 連携有効)"
else
  log_warn "agy コマンドが PATH に見つかりません。"
  log_warn "  ※ サポートGM (チャット内ダンジョン自動生成等) を利用する場合は"
  log_warn "     agy がインストールされ PATH に通っていることを確認してください。"
fi

if [ -n "${GEMINI_API_KEY:-}" ]; then
  log_ok "GEMINI_API_KEY 環境変数が設定されています (Gemini 直接連携可能)"
else
  log_info "GEMINI_API_KEY は未設定です (agy 経由のサポートGM機能が優先利用されます)"
fi

# ------------------------------------------------------------------------------
# 4. Foundry VTT Data ディレクトリ書き込み権限の確認
# ------------------------------------------------------------------------------
log_info "4. 音声・マップ保存先ディレクトリの確認中..."

DATA_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
if [ -d "$DATA_DIR" ] && [ -w "$DATA_DIR" ]; then
  log_ok "FVTT Data ディレクトリへの書き込み権限を確認: $DATA_DIR"
else
  log_warn "FVTT Data ディレクトリ ($DATA_DIR) への書き込み権限が確認できませんでした。"
  log_warn "音声ファイル (voice_*.mp3) やマップ画像 (dungeon_*.png) の生成時に権限エラーとなる可能性があります。"
fi

# ------------------------------------------------------------------------------
# 5. スクリプト実行権限の設定 (冪等)
# ------------------------------------------------------------------------------
log_info "5. スクリプトの実行権限を設定中..."

chmod +x "$SCRIPT_DIR/Setup.sh" 2>/dev/null || true
chmod +x "$SCRIPT_DIR/ai_gm_server.py" 2>/dev/null || true
if [ -d "$SCRIPT_DIR/tools" ]; then
  chmod +x "$SCRIPT_DIR/tools"/*.py 2>/dev/null || true
fi
log_ok "実行権限を設定しました。"

# ------------------------------------------------------------------------------
# 6. 完了サマリー & 起動案内
# ------------------------------------------------------------------------------
echo ""
echo -e "${C_BOLD}=====================================================================${C_RESET}"
echo -e "${C_GREEN}${C_BOLD}  セットアップが正常に完了しました！ (準備完了)${C_RESET}"
echo -e "${C_BOLD}=====================================================================${C_RESET}"
echo ""
echo "■ サーバーの起動方法:"
echo -e "   ${C_BOLD}python3 ai_gm_server.py${C_RESET}"
echo ""
echo "   バックグラウンドで常駐起動する場合:"
echo -e "   ${C_BOLD}nohup python3 ai_gm_server.py > ai_gm_server.log 2>&1 &${C_RESET}"
echo ""
echo "■ Foundry VTT での利用手順:"
echo "   1. Foundry VTT をブラウザで開き、ワールドを起動（GMとしてログイン）"
echo "   2. ワールド設定 > 「モジュールの管理」で「AI GM Bridge」を有効化"
echo "   3. チャット欄でコマンドを入力:"
echo -e "      ${C_BOLD}/ai ダンジョン生成！！${C_RESET}  → 規模選択肢カードが表示されます"
echo ""
