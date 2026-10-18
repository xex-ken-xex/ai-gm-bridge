# Irodori-TTS を WSL に入れる手順

AI GM の読み上げを edge-tts から [Irodori-TTS](https://github.com/Aratako/Irodori-TTS) に切り替えるための手順です。
Irodori-TTS は、声の説明文 (VoiceDesign) から声を作れる日本語の音声合成モデルです。
[Irodori-TTS-Server](https://github.com/Aratako/Irodori-TTS-Server) は、それを OpenAI 互換の API (`/v1/audio/speech`) で動かすサーバーです。

AI GM のサーバー (`ai_gm_server.py`) は、Irodori-TTS-Server に繋がらないときや音声の作成に失敗したときは、edge-tts で作り直します。
失敗したあと 60 秒 (`AI_GM_IRODORI_RETRY_AFTER`) は edge-tts だけを使い、そのあとまた Irodori を試します。
切り替わった最初の 1 回だけ、GM の画面に「edge-tts で読み上げています」と通知が出ます。
描写のチャットは音声ができてから送るため、Irodori の待ち上限は普段 20 秒 (`AI_GM_IRODORI_TIMEOUT`) と短めです。最初の 1 回だけはモデルの読み込みを待つため 180 秒 (`AI_GM_IRODORI_FIRST_TIMEOUT`) 待ちます。この 1 回は、起動時に裏で短いあいさつを作らせて済ませるので、実際の読み上げで長く待つことはありません。

## 0. 音声用の WSL を用意する

Irodori は今の Ubuntu (AI GM のサーバーと同じ環境) に入れても、別のフォルダに収まるので問題ありません。
音声用に分けたいときは、`Irodori` という名前の Ubuntu を別に作ります。WSL2 の Linux どうしはネットワークを共有するので、別の Ubuntu で動かしても AI GM のサーバーから `http://127.0.0.1:8088` で繋がります。

### Windows 側 (PowerShell を「管理者として実行」)

最初に `wsl -l -v` で、今入っている Ubuntu の名前を確かめます (`Ubuntu` か `Ubuntu-24.04` などで、人によって違います)。

```powershell
wsl --update                                   # --name が使える新しい WSL にする
wsl --install -d Ubuntu-24.04 --name Irodori   # -d (--distribution) は入れる Linux、--name は付ける名前
```

- 再起動を求められたら再起動し、開いた Ubuntu でユーザー名とパスワードを決めます。
- `--name` が使えないと言われたら、普通に入れてから名前を付けて写します (`C:\wsl` は先に作っておく)。

  ```powershell
  wsl --install -d Ubuntu-24.04
  wsl --export Ubuntu-24.04 C:\wsl\ubuntu.tar
  wsl --import Irodori C:\wsl\Irodori C:\wsl\ubuntu.tar
  ```

  - AI GM 用の Ubuntu がもともと `Ubuntu-24.04` だと、`--install` は「入っている」で終わり、`--export` は今の環境をまるごと写します。その場合は `--install` に `Ubuntu-22.04` など別の版を指定し、その名前で export してください。
  - `--import` で取り込んだ `Irodori` は root で開きます。ふだんのユーザーで開くよう、次のように設定します (`<ユーザー名>` は Ubuntu を入れたときに決めた名前)。

    ```powershell
    wsl -d Irodori -u root -- bash -c "printf '[user]\ndefault=<ユーザー名>\n' >> /etc/wsl.conf"
    wsl --terminate Irodori
    ```

- 開くときは `wsl -d Irodori`、一覧は `wsl -l -v` です。

### GPU ドライバー (Windows 側)

NVIDIA のサイトから、普段の Windows 用 GPU ドライバーを入れます。WSL の中にドライバーを入れる必要はありません。

### 毎回の起動

Irodori-TTS-Server は、セッションで遊ぶときだけ起動します。Windows の起動時に自動で立ち上げるのはおすすめしません。動かしているあいだは、モデルの分だけ GPU のメモリ (数 GB) と WSL のメモリを使い続けるためです。

デスクトップに次の 2 つのバッチファイルを置いておくと、ダブルクリックで起動と停止ができます (`Irodori` は `wsl -l -v` に出る名前)。

`Irodori起動.bat`

```bat
@echo off
title Irodori-TTS
wsl -d Irodori -- bash -lc "cd ~/Irodori-TTS-Server && uv run --no-sync python -m irodori_openai_tts --host 127.0.0.1 --port 8088"
pause
```

`Irodori停止.bat`

```bat
@echo off
wsl --terminate Irodori
```

- 起動したウィンドウは開いたままにします。閉じるか Ctrl+C で止まります。
- 停止のバッチは Irodori 用の WSL ごと止めるので、使っていたメモリがすぐに Windows に戻ります。AI GM と同じ Ubuntu に入れた場合は、`wsl --terminate` だと AI GM のサーバーも止まります。ウィンドウを閉じるだけにしてください。
- 日本語のファイル名で文字化けするときは、`start-irodori.bat` のように英語の名前にします。

## 1. 必要なもの

- WSL2 の Ubuntu (AI GM のサーバーと同じ環境か、手順 0 で作った `Irodori`)
- NVIDIA の GPU と、Windows 側の NVIDIA ドライバー (WSL から `nvidia-smi` が動けば準備できています)。GPU が無くても CPU で動きますが、1 文に数十秒かかります
- FFmpeg (mp3 で受け取るために必要)
- [uv](https://docs.astral.sh/uv/) (Python の環境を作るツール。Irodori-TTS-Server は Python 3.10 を使います)

```bash
nvidia-smi                      # GPU が見えるか確認
sudo apt update && sudo apt install -y ffmpeg git curl
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.bashrc                # uv にパスを通す
```

## 2. Irodori-TTS-Server を入れる

AI GM のモジュールフォルダとは別の場所 (例: `~/Irodori-TTS-Server`) に入れます。

```bash
cd ~
git clone https://github.com/Aratako/Irodori-TTS-Server.git
cd Irodori-TTS-Server
uv sync --extra cu128           # NVIDIA GPU。CPU だけなら --extra cpu
cp .env.example .env
```

`--extra cu128` と `--extra cpu` はどちらか 1 つだけです。以後のコマンドは、入れた PyTorch が外れないよう `uv run --no-sync` で動かします。

## 3. モデルを v4.1-Small にする

`.env` の `IRODORI_HF_CHECKPOINT` を書き換えます。初回の起動時に Hugging Face から自動でダウンロードします。

```bash
sed -i 's|^IRODORI_HF_CHECKPOINT=.*|IRODORI_HF_CHECKPOINT=Aratako/Irodori-TTS-v4.1-Small|' .env
sed -i 's|^IRODORI_PRELOAD=.*|IRODORI_PRELOAD=true|' .env   # 起動時にモデルを読み込む (1 回目の読み上げが遅くならない)
```

- [v4.1-Small](https://huggingface.co/Aratako/Irodori-TTS-v4.1-Small) は v4-Small の長さの予測だけを学習し直したもので、文の長さの見積もりの誤りが減っています。使い方は v4-Small と同じです。
- GPU のメモリが足りないときは、量子化版 (`Aratako/Irodori-TTS-v4.1-Small-Quantized` の int8 など) を使います。サブフォルダは `リポジトリ名/フォルダ名` の形で指定します。
- 声の説明文だけで声を作るため、`IRODORI_ALLOW_NO_REF_VOICE=true` (既定) のままにします。

## 4. 起動して確かめる

```bash
cd ~/Irodori-TTS-Server
uv run --no-sync python -m irodori_openai_tts --host 127.0.0.1 --port 8088
```

Windows の PowerShell で確かめます (WSL の中で動くサーバーには、Windows から `127.0.0.1` で繋がります)。

```powershell
curl.exe http://127.0.0.1:8088/health

$body = @{
  model = "irodori-tts"; input = "冒険者たちよ、よく来た。"; voice = "none"; response_format = "mp3"
  irodori = @{ caption = "落ち着いた大人の女性の声。物語を語り聞かせるように、穏やかで聞き取りやすい話し方。" }
} | ConvertTo-Json
$out = Join-Path ([Environment]::GetFolderPath("Desktop")) "irodori_test.mp3"
Invoke-WebRequest http://127.0.0.1:8088/v1/audio/speech -Method Post `
  -ContentType "application/json; charset=utf-8" -Body ([Text.Encoding]::UTF8.GetBytes($body)) -OutFile $out
Start-Process $out
```

デスクトップに `irodori_test.mp3` ができて声が鳴れば準備完了です。

- PowerShell の `curl` は別のコマンドの別名なので、`curl.exe` と書きます。
- 日本語が文字化けしないよう、本文は UTF-8 のバイト列にして送っています。
- `curl.exe` が「接続できません」になるときは、WSL の localhost の転送が切れています。手順 4 の起動を `--host 0.0.0.0` にして、もう一度試してください。

## 5. AI GM のサーバーで使う

AI GM のサーバーを起動する端末で、音声エンジンを `irodori` にします。

```bash
export AI_GM_TTS_ENGINE=irodori
python3 ai_gm_server.py
```

起動時に `音声エンジン: Irodori-TTS (http://127.0.0.1:8088。繋がらないときは edge-tts)` に続けて `Irodori-TTS に繋がりました (裏でモデルを読み込みます)` と出れば切り替わっています。少しあとに `Irodori-TTS の準備ができました` と出れば、モデルの読み込みも終わっています。`※ Irodori-TTS に繋がりません` と出たら、Irodori-TTS-Server が起動しているか確かめてください (繋がるまでは edge-tts で読み上げます)。
`~/.bashrc` に `export AI_GM_TTS_ENGINE=irodori` を書いておくと毎回の指定は要りません。edge-tts に戻すときは `AI_GM_TTS_ENGINE=edge` にします。

- 声の説明文は、ナレーションと女性の NPC が `AI_GM_IRODORI_CAPTION_FEMALE`、男性の NPC (ボイスが Keita) が `AI_GM_IRODORI_CAPTION_MALE` です。NPC の声の高さが ±10Hz 以上ずれていれば「やや高めの声」「やや低めの声」を足します。
- 話す速さ (NPC の `+10%` など) は Irodori の `speed` (1.1 など) に直して渡します。声の高さの数値は Irodori には渡せないため、説明文の一言だけで表します。
- 他の設定は `docs/specification.md` の「環境変数」を見てください。

## 6. ナレーターの声を好みの声にする

ナレーターの声は、参照音声 (`AI_GM_IRODORI_NARRATOR_VOICE`) → ナレーターの説明文 (`AI_GM_IRODORI_CAPTION_NARRATOR`) → 女性の説明文 (`AI_GM_IRODORI_CAPTION_FEMALE`) の順に、設定されているものを使います。乱数の種はいつも `AI_GM_IRODORI_SEED` (既定 `1234`) です。説明文だけだと作るたびに声が少し揺れるので、種を固定しています。NPC の声はここの設定では変わりません (種を変えたときだけ NPC の声も変わります)。

次の順に進めると、好みの声に寄せていけます。どれも Irodori-TTS-Server を起動した状態 (手順 4) で、Windows の PowerShell で行います。

### 6-1. 声の説明文を聴き比べる

`$caption` を書き換えて何度か試します。性別・年齢・声の高さ・話し方を書くと伝わりやすいです。

```powershell
$caption = "若い男性の声。低めで落ち着いた、ささやくような語り口。"
$body = @{
  model = "irodori-tts"; input = "冒険者たちよ、よく来た。この先は古い遺跡だ。"; voice = "none"; response_format = "mp3"
  irodori = @{ caption = $caption; seed = 1234 }
} | ConvertTo-Json
$out = Join-Path ([Environment]::GetFolderPath("Desktop")) "narrator_test.mp3"
Invoke-WebRequest http://127.0.0.1:8088/v1/audio/speech -Method Post `
  -ContentType "application/json; charset=utf-8" -Body ([Text.Encoding]::UTF8.GetBytes($body)) -OutFile $out
Start-Process $out
```

### 6-2. 種を聴き比べる

決めた説明文で、種だけを変えた音声をデスクトップの `narrator_seeds` フォルダに作ります。ファイル名の数字が種です。

```powershell
$caption = "若い男性の声。低めで落ち着いた、ささやくような語り口。"
$dir = Join-Path ([Environment]::GetFolderPath("Desktop")) "narrator_seeds"
New-Item -ItemType Directory -Force $dir | Out-Null
foreach ($seed in 1, 7, 42, 100, 1234, 2024, 5555, 9999) {
  $body = @{
    model = "irodori-tts"; input = "冒険者たちよ、よく来た。この先は古い遺跡だ。"; voice = "none"; response_format = "mp3"
    irodori = @{ caption = $caption; seed = $seed }
  } | ConvertTo-Json
  Invoke-WebRequest http://127.0.0.1:8088/v1/audio/speech -Method Post `
    -ContentType "application/json; charset=utf-8" -Body ([Text.Encoding]::UTF8.GetBytes($body)) `
    -OutFile (Join-Path $dir "seed_$seed.mp3")
}
Start-Process $dir
```

気に入った説明文と種を、AI GM のサーバーを起動する端末で設定して再起動します。

```bash
export AI_GM_IRODORI_CAPTION_NARRATOR="若い男性の声。低めで落ち着いた、ささやくような語り口。"
export AI_GM_IRODORI_SEED=42
```

### 6-3. 参照音声にする (いちばん確実)

種を固定しても、文の長さが違うと声は少し揺れます。いちばん安定させたいときは、気に入った 1 本 (または自分で録った声、数秒〜2 分) を参照音声にします。Irodori-TTS-Server の `voices` フォルダに置くと、ファイル名から拡張子を除いた部分が ID になります。置いたらすぐ使われます (Irodori の再起動は不要)。

```bash
# Irodori の WSL で。6-2 で作った seed_42.mp3 をナレーターの声にする例
mkdir -p ~/Irodori-TTS-Server/voices
cp /mnt/c/Users/<Windows のユーザー名>/Desktop/narrator_seeds/seed_42.mp3 ~/Irodori-TTS-Server/voices/narrator.mp3
```

AI GM のサーバーを起動する端末で `export AI_GM_IRODORI_NARRATOR_VOICE=narrator` にして再起動すると、ナレーターだけがこの声で話します。参照音声と説明文が食い違うと声が不安定になるため、このときナレーターには説明文を付けません。名前が `voices` フォルダのファイル名と合わないと edge-tts の声になります。そのときはサーバーのログに名前を確かめるよう出ます。

## 7. 困ったとき

| 症状 | 見るところ |
| :--- | :--- |
| AI GM のログに「Irodori-TTS で音声を作れませんでした (Connection refused …)」 | Irodori-TTS-Server が起動しているか、ポートが `AI_GM_IRODORI_URL` と同じか |
| 「HTTP 4xx / 5xx」 | Irodori-TTS-Server の端末に出るエラー。mp3 の変換で失敗しているなら FFmpeg が入っているか |
| 「timed out」 | 最初の音声ならモデルの読み込みに時間がかかっている (`IRODORI_PRELOAD=true` にするか `AI_GM_IRODORI_FIRST_TIMEOUT` を延ばす)。2 回目以降なら GPU が遅い・長い文 (`AI_GM_IRODORI_TIMEOUT` を延ばす) |
| GPU のメモリ不足 (CUDA out of memory) | 量子化版のモデルにする。他に GPU を使うソフトを閉じる |
| `IRODORI_API_KEY` を設定した | AI GM 側にも同じ値を `AI_GM_IRODORI_API_KEY` に設定する |
