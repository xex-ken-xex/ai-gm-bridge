"""D. 通常のプレイヤー発言 (Gemini TRPG GM 演出)"""

from gm_server import protocol
from gm_server.text import ai_text_to_html, clean_html, dice_formula_from_command
from gm_server.tts import voice_filename


async def handle(ctx, data: dict) -> None:
    user = data.get("user", "Player")
    player_input = clean_html(data.get("content", ""))
    if not player_input:
        return

    print(f"\n[FVTTプレイヤー発言] {user}: {player_input}")
    print("AI GMが思考中...")

    gm_response = await ctx.llm.query_ai_gm(player_input)
    narrative = gm_response.get("narrative") or ""
    speech = gm_response.get("speech") or ""
    formula = dice_formula_from_command(gm_response.get("dice"))

    # 音声生成
    file_name = None
    if speech:
        file_name = voice_filename()
        print(f"音声生成中 ({file_name}): 「{speech}」")
        if not await ctx.tts.synthesize(speech, file_name):
            file_name = None

    # チャット描写送信
    if narrative:
        await ctx.send(protocol.chat(ai_text_to_html(narrative)))

    # ダイスロール送信
    if formula:
        await ctx.sleep(0.3)
        await ctx.send(protocol.roll(formula))
        print(f"ダイスロール送信: {formula}")

    # 音声再生指示送信
    if file_name:
        await ctx.sleep(0.5)
        await ctx.send(protocol.play_sound(file_name, 1.0, subtitle=speech))
