#!/usr/bin/env bash
# ==============================================================================
# start_server.sh - AI GM サーバー起動スクリプト (対話式)
#
# 起動前に、どのコードで動かすかを選べます。
#   Enter / 0 : 最新の main を取ってきて起動
#   1〜N      : 検証したいブランチ (PR) を番号で選んで起動
#   c         : 今のブランチのまま (git を触らずに) 起動
#
# ブランチを切り替えると FVTT 側のモジュール (bridge.js など) も同じ版になるので、
# ブラウザの FVTT を再読み込み (F5) してください。
# ==============================================================================

# git の切り替えでこのファイル自体が書き換わっても途中から壊れないよう、
# 全体を関数にして最後まで読み込んでから実行する
main() {
  set -uo pipefail

  local script_dir
  script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  cd "$script_dir" || exit 1

  echo "====================================================="
  echo "  AI GM サーバー起動"
  echo "====================================================="

  local current old_head
  current="$(git rev-parse --abbrev-ref HEAD 2>/dev/null)"
  old_head="$(git rev-parse HEAD 2>/dev/null)"

  echo "リモートの最新情報を取得しています..."
  if ! git fetch --prune origin; then
    echo "[WARN] git fetch に失敗しました。手元にある情報だけで続けます。"
  fi

  # 編集中のファイルがあると切り替えで消える・失敗するため、切り替えは止める
  local dirty=""
  if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    dirty="1"
  fi

  # main 以外のリモートブランチを新しい順に並べる
  local -a branches=()
  local line
  while IFS= read -r line; do
    [ -n "$line" ] && branches+=("$line")
  done < <(git for-each-ref --sort=-committerdate --format='%(refname:lstrip=3)' refs/remotes/origin \
             | grep -v -x -e 'HEAD' -e 'main')

  echo ""
  echo "今のブランチ: ${current}"
  echo ""
  echo "  0) 最新の main を取ってきて起動 (Enter でもこれ)"
  local i b info
  for i in "${!branches[@]}"; do
    b="${branches[$i]}"
    info="$(git log -1 --format='%cd  %s' --date=format:'%m/%d %H:%M' "origin/$b" 2>/dev/null)"
    printf '  %d) %s\n       %s\n' "$((i + 1))" "$b" "$info"
  done
  echo "  c) 今のブランチのまま起動 (git を触らない)"
  echo "  q) やめる"
  echo ""

  local choice target=""
  while true; do
    read -r -p "番号を入力してください: " choice
    choice="${choice// /}"
    case "$choice" in
      ""|0) target="main"; break ;;
      c|C) target=""; break ;;
      q|Q) echo "起動をやめました。"; return 0 ;;
      *)
        if [[ "$choice" =~ ^[0-9]+$ ]] && [ "$choice" -ge 1 ] && [ "$choice" -le "${#branches[@]}" ]; then
          target="${branches[$((choice - 1))]}"
          break
        fi
        echo "  0〜${#branches[@]}、c、q のどれかを入力してください。"
        ;;
    esac
  done

  if [ -n "$target" ]; then
    if [ -n "$dirty" ]; then
      echo ""
      echo "[ERROR] 編集中のファイルがあるため、ブランチを切り替えられません。"
      git status --short --untracked-files=no
      echo "変更が不要なら 'git restore .' で元に戻してから、もう一度実行してください。"
      echo "(今のまま起動したいときは c を選んでください)"
      return 1
    fi

    if [ "$target" = "main" ]; then
      git switch -q main || return 1
      if ! git merge -q --ff-only origin/main; then
        echo "[ERROR] 手元の main が origin/main と食い違っているため、更新できませんでした。"
        echo "手元の main で作業していなければ 'git reset --hard origin/main' で揃えられます。"
        return 1
      fi
    else
      # 手元に同名ブランチがあり、リモートに無いコミットがある場合だけ確認する
      # (Squash マージ後に同じ名前が使い回された場合など。中身は main に入っているので通常は捨てて問題ない)
      if git show-ref --verify --quiet "refs/heads/$target"; then
        local ahead
        ahead="$(git rev-list --count "origin/$target..$target")"
        if [ "$ahead" -gt 0 ]; then
          echo ""
          echo "[注意] 手元の $target に、リモートに無いコミットが ${ahead} 件あります。"
          git log --oneline "origin/$target..$target" | head -10
          read -r -p "これらを捨ててリモートの内容に揃えますか？ [y/N]: " yn
          case "$yn" in
            y|Y) ;;
            *) echo "起動をやめました。"; return 1 ;;
          esac
        fi
      fi
      git checkout -q -B "$target" "origin/$target" || return 1
    fi
  fi

  local new_head
  new_head="$(git rev-parse HEAD)"
  echo ""
  echo "起動するコード: $(git rev-parse --abbrev-ref HEAD) ($(git log -1 --format='%h %s'))"

  if [ "$old_head" != "$new_head" ]; then
    echo "[INFO] コードが切り替わりました。FVTT を開いているブラウザは再読み込み (F5) してください。"
    if ! git diff --quiet "$old_head" "$new_head" -- requirements.txt; then
      echo "[INFO] requirements.txt が変わっています。ライブラリを更新します..."
      python3 -m pip install -q -r requirements.txt --user 2>/dev/null \
        || python3 -m pip install -q -r requirements.txt --user --break-system-packages \
        || echo "[WARN] ライブラリの更新に失敗しました。./Setup.sh を実行してください。"
    fi
  fi

  if [ -z "${GEMINI_API_KEY:-}" ]; then
    echo "[WARN] GEMINI_API_KEY が設定されていません。AI の応答が動きません。"
  fi

  echo "-----------------------------------------------------"
  echo "サーバーを起動します (止めるときは Ctrl+C)"
  echo "-----------------------------------------------------"
  exec python3 ai_gm_server.py
}

main "$@"
exit $?
