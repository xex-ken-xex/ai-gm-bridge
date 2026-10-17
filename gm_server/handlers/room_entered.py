"""C. トークン部屋進入検知 (room_entered 自動音声読み上げ)"""

from gm_server import protocol
from gm_server.tts import voice_filename


async def handle(ctx, data: dict) -> None:
    room_name = data.get("room_name", "部屋")
    flavor = data.get("flavor", "")
    token_name = data.get("token_name", "プレイヤー")
    print(f"\n[部屋進入検知] {token_name} -> {room_name}: {flavor[:40]}...")
    if not flavor:
        return

    file_name = voice_filename("voice_room")
    speech_text = f"{room_name}。{flavor}"
    if await ctx.tts.synthesize(speech_text, file_name):
        subtitle = f"📍 {room_name}: {flavor[:45]}{'...' if len(flavor) > 45 else ''}"
        await ctx.send(protocol.play_sound(file_name, 0.95, subtitle=subtitle))
        print(f"部屋進入音声再生指示送信: {file_name}")
