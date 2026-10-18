"""F. 声のテスト (Geminiちゃんのメニューから。今の音声エンジンで見本のセリフを GM にだけ鳴らす)

FVTT (GM) が voice_test を送る。サーバーは見本のセリフを読み上げ、GM のクライアントでだけ鳴る play_sound と、
どのエンジン (Irodori-TTS / edge-tts) で鳴ったかを知らせる progress を返す。
"""

from gm_server import protocol
from gm_server.progress import log_step
from gm_server.tts import support_voice, voice_filename

# 声の種類 → (表示名, 見本のセリフ, synthesize に渡す声の指定を返す関数)
SAMPLES = {
    "narrator": ("ナレーター", "冒険者たちよ、よく来た。この先は古い遺跡だ。足元に気をつけて進むがいい。", dict),
    "support": ("Geminiちゃん", "やっほー、Geminiちゃんだよ！声のテスト、ちゃんと聞こえてるかな？", support_voice),
}


def result_message(label: str, tts, voice: dict) -> str:
    """どのエンジンで鳴ったかを GM に知らせる文"""
    if tts.last_engine == "irodori":
        detail = tts.describe_voice(**voice)
        return f"声のテスト ({label}): Irodori-TTS で読み上げました" + (f" ({detail})" if detail else "")
    if tts.last_engine == "edge":
        if tts.engine != "irodori":
            return f"声のテスト ({label}): edge-tts で読み上げました (AI_GM_TTS_ENGINE が edge です)"
        reason = tts.last_irodori_error or "理由はサーバーのログにあります"
        return f"声のテスト ({label}): Irodori-TTS で作れなかったため、edge-tts で読み上げました ({reason})"
    return f"声のテスト ({label}): 音声を作れませんでした (サーバーのログに理由があります)"


async def handle(ctx, data: dict) -> None:
    kind = data.get("voice") if data.get("voice") in SAMPLES else "narrator"
    label, text, make_voice = SAMPLES[kind]
    voice = make_voice()
    log_step(f"[声のテスト] {label}の声で見本のセリフを読み上げます")
    # 繋がらなかった直後でも、テストでは Irodori をもう一度試す (起動し直した直後に押すことが多いため)
    ctx.tts.resume_irodori()
    file_name = voice_filename("voice_test")
    if await ctx.tts.synthesize(text, file_name, **voice):
        await ctx.send(protocol.play_sound(file_name, 1.0, subtitle=text, gm_only=True))
    message = result_message(label, ctx.tts, voice)
    log_step(message)
    await ctx.send(protocol.progress(message))
