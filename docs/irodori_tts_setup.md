# Irodori-TTS を WSL に入れる手順

AI GM の読み上げを edge-tts から [Irodori-TTS](https://github.com/Aratako/Irodori-TTS) に切り替えるための手順です。
Irodori-TTS は、声の説明文 (VoiceDesign) から声を作れる日本語の音声合成モデルです。
[Irodori-TTS-Server](https://github.com/Aratako/Irodori-TTS-Server) は、それを OpenAI 互換の API (`/v1/audio/speech`) で動かすサーバーです。

AI GM のサーバー (`ai_gm_server.py`) は、Irodori-TTS-Server に繋がらないときや音声の作成に失敗したときは、edge-tts で作り直します。
失敗したあと 60 秒 (`AI_GM_IRODORI_RETRY_AFTER`) は edge-tts だけを使い、そのあとまた Irodori を試します。

## 1. 必要なもの

- WSL2 の Ubuntu (AI GM のサーバーと同じ環境)
- NVIDIA の GPU と、Windows 側の NVIDIA ドライバー (WSL から `nvidia-smi` が動けば準備できています)。GPU が無くても CPU で動きますが、1 文に数十秒かかります
- FFmpeg (mp3 で受け取るために必要)
- [uv](https://docs.astral.sh/uv/) (Python の環境を作るツール。Irodori-TTS-Server は Python 3.10 を使います)

```bash
nvidia-smi                      # GPU が見えるか確認
sudo apt update && sudo apt install -y ffmpeg git
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

別の端末で確かめます。

```bash
curl http://127.0.0.1:8088/health
curl http://127.0.0.1:8088/v1/audio/speech \
  -H "Content-Type: application/json" \
  -d '{"model":"irodori-tts","input":"冒険者たちよ、よく来た。","voice":"none","response_format":"mp3",
       "irodori":{"caption":"落ち着いた大人の女性の声。物語を語り聞かせるように、穏やかで聞き取りやすい話し方。"}}' \
  --output test.mp3
```

`test.mp3` ができれば準備完了です。Windows のエクスプローラーで `\\wsl$\Ubuntu\home\<ユーザー名>\Irodori-TTS-Server` を開くと再生できます。

## 5. AI GM のサーバーで使う

AI GM のサーバーを起動する端末で、音声エンジンを `irodori` にします。

```bash
export AI_GM_TTS_ENGINE=irodori
python3 ai_gm_server.py
```

起動時に `音声エンジン: Irodori-TTS (http://127.0.0.1:8088。繋がらないときは edge-tts)` と出れば切り替わっています。
`~/.bashrc` に `export AI_GM_TTS_ENGINE=irodori` を書いておくと毎回の指定は要りません。edge-tts に戻すときは `AI_GM_TTS_ENGINE=edge` にします。

- 声の説明文は、ナレーションと女性の NPC が `AI_GM_IRODORI_CAPTION_FEMALE`、男性の NPC (ボイスが Keita) が `AI_GM_IRODORI_CAPTION_MALE` です。NPC の声の高さが ±10Hz 以上ずれていれば「やや高めの声」「やや低めの声」を足します。
- 話す速さ (NPC の `+10%` など) は Irodori の `speed` (1.1 など) に直して渡します。声の高さの数値は Irodori には渡せないため、説明文の一言だけで表します。
- 他の設定は `docs/specification.md` の「環境変数」を見てください。

## 6. 困ったとき

| 症状 | 見るところ |
| :--- | :--- |
| AI GM のログに「Irodori-TTS で音声を作れませんでした (Connection refused …)」 | Irodori-TTS-Server が起動しているか、ポートが `AI_GM_IRODORI_URL` と同じか |
| 「HTTP 4xx / 5xx」 | Irodori-TTS-Server の端末に出るエラー。mp3 の変換で失敗しているなら FFmpeg が入っているか |
| 「timed out」 | 初回のモデル読み込みに時間がかかっている。`IRODORI_PRELOAD=true` にするか、`AI_GM_IRODORI_TIMEOUT` を延ばす |
| GPU のメモリ不足 (CUDA out of memory) | 量子化版のモデルにする。他に GPU を使うソフトを閉じる |
| `IRODORI_API_KEY` を設定した | AI GM 側にも同じ値を `AI_GM_IRODORI_API_KEY` に設定する |
