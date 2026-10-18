"""A. GMからの /ai 指示コマンド (agy 連携) と B. チャット内ボタン選択結果 (ai_choice)"""

from gm_server import deadline, dungeon_scene, protocol
from gm_server.config import SPEAKER_SUPPORT
from gm_server.text import newlines_to_br


async def handle_command(ctx, data: dict) -> None:
    user = data.get("user", "GM")
    prompt = (data.get("prompt") or "").strip()
    print(f"\n[FVTT GM指示] {user}: /ai {prompt}")

    if dungeon_scene.is_dungeon_intent(prompt):
        ctx.state.pending_dungeon["user_prompt"] = prompt
        ctx.state.pending_dungeon["scene_mode"] = dungeon_scene.DEFAULT_SCENE_MODE
        # ステップ1: 舞台 (シーンモード) 選択肢カードを返送
        await ctx.send(dungeon_scene.mode_card())
        print("モード選択肢カードをFVTTへ送信しました。")
        return

    # その他の自然言語指示は agy CLI へルーティング (NPC操作・相談・設定構築など)
    print(f"agyへプロンプトを委譲中: {prompt}")
    agy_resp = await ctx.llm.ask_support_gm(prompt)
    if not agy_resp:
        agy_resp = "⚠️ ごめんね、Geminiちゃんの返事が届かなかったみたい…(応答がありませんでした) サーバーのログを見てもらえるかな？"
    await ctx.send(protocol.chat(newlines_to_br(agy_resp), speaker=SPEAKER_SUPPORT))


async def handle_choice(ctx, data: dict) -> None:
    value = data.get("value", "")
    label = data.get("label", "")
    print(f"\n[FVTT GM選択] {label} ({value})")

    # タイムアウト延長確認カードの回答 (router が受信キューを待たずに渡してくる)
    if deadline.is_timeout_choice(value):
        deadline.resolve_timeout_choice(ctx, value)
        return

    choice = dungeon_scene.parse_dungeon_choice(value)
    if choice is None:
        print(f"未対応の選択肢です: {value}")
        return

    pending = ctx.state.pending_dungeon
    scene_mode = dungeon_scene.normalize_scene_mode(pending.get("scene_mode"))

    # ステップ1.5: モード選択完了 -> サイズ選択肢カード
    if choice.kind == "mode":
        pending["scene_mode"] = choice.scene_mode
        await ctx.send(dungeon_scene.size_card(dungeon_scene.scene_mode_label(choice.scene_mode)))
        return

    # ステップ2: サイズ選択完了 -> 部屋の密度選択肢カード (野外は地点数固定のためループ率へ)
    if choice.kind == "size":
        if scene_mode == "wilderness":
            await ctx.send(dungeon_scene.loop_card(choice.size_key, dungeon_scene.DEFAULT_DENSITY, label, prev_kind="サイズ"))
        else:
            await ctx.send(dungeon_scene.density_card(choice.size_key, label))
        return

    # ステップ3: 部屋密度選択完了 -> ループ率選択肢カード
    if choice.kind == "density":
        await ctx.send(dungeon_scene.loop_card(choice.size_key, choice.density, label))
        return

    # ステップ4: ループ率選択完了 -> 実際のダンジョン生成 & シーン作成
    user_prompt = pending.get("user_prompt", "")
    await dungeon_scene.generate_and_create_dungeon_scene(
        ctx, choice.size_key, choice.loop_ratio, choice.density, user_prompt=user_prompt, scene_mode=scene_mode
    )
