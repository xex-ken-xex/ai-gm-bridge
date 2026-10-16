"""
gm_server - AI GM Bridge バックエンドのモジュール群

ai_gm_server.py から機能単位で分割したパッケージです。

- config       : 環境変数・定数
- text         : HTML 除去・TTS パラメータ正規化などの純粋関数
- protocol     : FVTT へ送る action メッセージの組み立て
- tts          : edge-tts による音声合成
- llm          : Gemini / agy CLI への問い合わせ
- dungeon_scene: ダンジョン生成とシーン構築命令
- handlers     : event ごとのハンドラ
- router       : event 名 → ハンドラの振り分け
- app          : WebSocket サーバー本体
"""
