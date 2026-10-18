"""A. GMからの /ai 指示コマンド (agy 連携) と B. チャット内ボタン選択結果 (ai_choice)"""

from gm_server import deadline, dungeon_scene, protocol, skills
from gm_server.handlers import adventure_log
from gm_server.config import SPEAKER_SUPPORT
from gm_server.llm import SUPPORT_GM_CHARACTER
from gm_server.text import newlines_to_br


# スキル判定の聞き返しカードのボタンの値 (後ろに選択肢の番号が付く)
SKILL_CHOICE_PREFIX = "ai_skill:"
NO_REPLY_MESSAGE = "⚠️ ごめんね、Geminiちゃんの返事が届かなかったみたい…(応答がありませんでした) サーバーのログを見てもらえるかな？"


async def _say(ctx, text: str) -> None:
    if text:
        await ctx.send(protocol.chat(newlines_to_br(text), speaker=SPEAKER_SUPPORT))


async def _run_generate_dungeon(ctx, params: dict, prompt: str, say: str, text: str) -> None:
    pending = ctx.state.pending_dungeon
    pending["user_prompt"] = str(params.get("request") or prompt)
    scene_mode = params.get("scene_mode")
    if scene_mode in dungeon_scene.SCENE_MODE_INFO:
        # 舞台が読み取れたらモード選択を飛ばしてサイズ選択から
        pending["scene_mode"] = scene_mode
        await ctx.send(dungeon_scene.size_card(dungeon_scene.scene_mode_label(scene_mode), scene_mode))
    else:
        pending["scene_mode"] = dungeon_scene.DEFAULT_SCENE_MODE
        await ctx.send(dungeon_scene.mode_card())
    print("ダンジョン生成ウィザードのカードをFVTTへ送信しました。")


def _builder_runner(skill_name: str):
    async def run(ctx, params: dict, prompt: str, say: str, text: str) -> None:
        label = skills.SKILLS_BY_NAME[skill_name].label
        await _say(ctx, say or f"{label}だね、任せて！")
        await ctx.send(protocol.open_builder(skills.BUILDER_TABS[skill_name], str(params.get("request") or prompt)))
    return run


async def _run_adventure_log(ctx, params: dict, prompt: str, say: str, text: str) -> None:
    mode = params.get("mode") if params.get("mode") in adventure_log.MODES else adventure_log.log_mode(prompt)
    await adventure_log.request_log(ctx, prompt, mode=mode)


async def _run_help(ctx, params: dict, prompt: str, say: str, text: str) -> None:
    await _say(ctx, skills.help_text())


async def _run_not_ready(ctx, skill: skills.Skill, text: str) -> None:
    notice = f"🚧 {skill.label}はまだ準備中なんだ、ごめんね！"
    await _say(ctx, notice + ("今は文章で手伝うね。\n\n" + text if text else ""))


# skill 名 → 実行内容 (準備中のスキルは載せない)
SKILL_RUNNERS = {
    "generate_dungeon": _run_generate_dungeon,
    **{name: _builder_runner(name) for name in skills.BUILDER_TABS},
    "adventure_log": _run_adventure_log,
    "help": _run_help,
}


async def run_skill(ctx, name: str, params: dict, prompt: str, say: str = "", text: str = "") -> None:
    skill = skills.SKILLS_BY_NAME[name]
    print(f"スキルを実行します: {skills.display_name(skill)} {params}")
    runner = SKILL_RUNNERS.get(name)
    if runner is None or not skill.ready:
        await _run_not_ready(ctx, skill, text)
        return
    await runner(ctx, params, prompt, say, text)


async def _keyword_fallback(ctx, prompt: str) -> None:
    """agy から返事が無いとき: 今までのキーワード判定で動けるものだけ動かす"""
    if adventure_log.is_adventure_log_intent(prompt):
        await adventure_log.request_log(ctx, prompt)
    elif prompt and dungeon_scene.is_dungeon_intent(prompt):
        await _run_generate_dungeon(ctx, {}, prompt, "", "")
    else:
        await _say(ctx, NO_REPLY_MESSAGE)


async def handle_command(ctx, data: dict) -> None:
    user = data.get("user", "GM")
    prompt = (data.get("prompt") or "").strip()
    print(f"\n[FVTT GM指示] {user}: /ai {prompt}")

    # `/ai` だけならできることの一覧
    if not prompt:
        await run_skill(ctx, "help", {}, prompt)
        return

    # スキル判定: 判定プロンプト + スキル一覧 + GM の文を agy に1回だけ送る (判定と回答をまとめる)
    persona = "" if getattr(ctx.llm, "conversation_id", None) else SUPPORT_GM_CHARACTER
    print(f"agyへスキル判定を依頼中: {prompt}")
    resp = await ctx.llm.ask_support_gm(skills.build_router_prompt(prompt, persona), raw=True)
    if not resp:
        await _keyword_fallback(ctx, prompt)
        return

    result = skills.parse_router_response(resp)
    if result is None:
        # JSON として読めなければ、返ってきた文をそのまま回答として表示する (今までと同じ)
        await _say(ctx, resp)
    elif result["type"] == "skill":
        await run_skill(ctx, result["skill"], result["params"], prompt, result["say"], result["text"])
    elif result["type"] == "clarify":
        ctx.state.pending_skill_options = [dict(o, prompt=prompt) for o in result["options"]]
        await ctx.send(protocol.interactive_card(
            title="サポートGM Geminiちゃん",
            message=result["question"],
            choices=[{"label": o["label"], "value": f"{SKILL_CHOICE_PREFIX}{i}"} for i, o in enumerate(result["options"])],
        ))
    else:
        await _say(ctx, result["text"])


async def _handle_skill_choice(ctx, value: str) -> None:
    options = ctx.state.pending_skill_options
    try:
        option = options[int(value[len(SKILL_CHOICE_PREFIX):])]
    except (ValueError, IndexError):
        print(f"古い選択肢か、範囲外の選択肢です: {value}")
        return
    ctx.state.pending_skill_options = []
    await run_skill(ctx, option["skill"], option["params"], option.get("prompt", ""))


async def handle_choice(ctx, data: dict) -> None:
    value = data.get("value", "")
    label = data.get("label", "")
    print(f"\n[FVTT GM選択] {label} ({value})")

    # タイムアウト延長確認カードの回答 (router が受信キューを待たずに渡してくる)
    if deadline.is_timeout_choice(value):
        deadline.resolve_timeout_choice(ctx, value)
        return

    # スキル判定の聞き返しカードの回答
    if str(value).startswith(SKILL_CHOICE_PREFIX):
        await _handle_skill_choice(ctx, value)
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
        await ctx.send(dungeon_scene.size_card(dungeon_scene.scene_mode_label(choice.scene_mode), choice.scene_mode))
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
