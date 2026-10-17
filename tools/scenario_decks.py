"""
シナリオビルダーの「発想カード」(マンネリ防止)

AI に毎回ゼロから考えさせると、霧・古代の魔力機関・行方不明の子供のような定番に寄っていく。
そこでサーバー側で段階ごとに数本の「軸」からカードを 1 枚ずつ引き、プロンプトに制約として渡す。

- 引き方は seed で再現できる (同じ seed なら同じカード)
- 最近の地図帳エントリで使ったカード (recent) は、その軸に他の候補が残っている限り引かない
- カードは字面どおりでなく「この世界に合わせて解釈する」前提の短い発想の種。
  ワールド資料や確定事項と衝突したら AI が読み替える (プロンプト側のルール)
"""

import random
from typing import Dict, Iterable, List, Optional

# stage -> [(軸 id, 軸の表示名, [(カード id, 文面), ...]), ...]
DECKS: Dict[str, List[tuple]] = {
    "region": [
        ("terrain", "地形の主役", [
            ("salt_flat", "白い塩の平原と、点在する塩湖"),
            ("karst", "雨水に穿たれた石灰岩の台地と陥没穴"),
            ("ash_forest", "火山灰に埋もれかけた森と温い地面"),
            ("delta", "流れを変え続ける河口の三角州"),
            ("cliff_coast", "崩れ続ける断崖の海岸線"),
            ("highland_bog", "高原に広がる泥炭の湿地"),
            ("red_canyon", "赤土の峡谷と段丘"),
            ("glacier_valley", "氷河が削り残した U 字の谷"),
            ("dune_grass", "草原を少しずつ呑み込む砂丘"),
            ("giant_wood", "幹の内側に道が通るほどの巨木の樹海"),
            ("sunken_basin", "湖底に村や道が沈んだ盆地"),
            ("terraces", "人の手で切られた段々畑が山肌を覆う丘陵"),
        ]),
        ("climate", "気候の癖", [
            ("timed_wind", "毎日ほぼ同じ時刻に吹き始める強い風"),
            ("late_frost", "初夏にも降りる季節外れの霜"),
            ("two_seasons", "はっきり分かれた長い雨季と乾季"),
            ("day_night", "昼と夜の激しい寒暖差"),
            ("dust", "霧ではなく、黄色い砂塵が視界を奪う"),
            ("thunder", "雷がよく落ち、落雷跡が地名になる"),
            ("tide_road", "潮の満ち引きで現れたり消えたりする道"),
            ("geothermal", "地熱で冬でも雪が積もらない一帯"),
            ("long_dusk", "日没後も長く空が明るい白夜に近い夏"),
            ("pollen", "季節ごとに空気を黄や赤に染める花粉"),
        ]),
        ("rupture", "歴史の断層 (何が変わったか)", [
            ("plague", "疫病で人が去り、別の人々が住み着いた"),
            ("river_shift", "川の流れが変わり、栄えた町が干上がった"),
            ("treaty", "講和条約で国境が引き直され、住民が二つに分かれた"),
            ("vein_dry", "鉱脈が尽き、産業が丸ごと別のものに置き換わった"),
            ("reform", "信仰の改革で古い神殿が別の用途に転用された"),
            ("magic_accident", "魔法の実験事故の跡が、今では日常の風景になっている"),
            ("migration", "遠方からの大移住で言葉と料理が混ざり合った"),
            ("civil_war", "内戦の講和の条件が今も守られている (あるいは破られかけている)"),
            ("route_move", "交易路が移って宿場が寂れ、別の場所が栄えた"),
            ("dynasty_end", "領主の家系が途絶え、合議や代官の統治に変わった"),
            ("flood", "百年に一度の大洪水が地形と村の配置を変えた"),
        ]),
        ("economy", "暮らしを支える産物", [
            ("salt", "塩"), ("dye", "染料 (茜・藍・貝紫など)"), ("wool", "羊毛と毛織物"),
            ("herb", "薬草"), ("amber", "琥珀"), ("stone", "切り出し石材"),
            ("beeswax", "蜜蝋と蜂蜜"), ("iron", "鉄と木炭"), ("tea", "茶葉"),
            ("paper", "紙 (樹皮や草から漉く)"), ("leather", "皮革と膠"), ("fish_sauce", "魚醤と干物"),
            ("glass", "砂から作るガラス"), ("resin", "樹脂と松脂"),
        ]),
        ("oddity", "ひとつだけの異物", [
            ("giant_bones", "地面から突き出た、何のものか分からない巨大な骨"),
            ("singing_stones", "風が吹くと鳴る穴だらけの石"),
            ("leaning_plants", "草木がすべて同じ方角に傾いて育つ"),
            ("unmapped_village", "地図に載らないのに、誰もが場所を知っている村"),
            ("night_lights", "夜にだけ谷の底に灯る、誰も点けていない明かり"),
            ("upstream_fall", "季節によって逆向きに水が落ちる滝"),
            ("silent_birds", "鳥がこの一帯でだけ鳴かない"),
            ("old_road", "どこにも続かない、異様に保存状態の良い舗装路"),
            ("standing_ships", "海から遠い丘の上に並ぶ古い船の竜骨"),
            ("warm_lake", "冬でも凍らず湯気を立てる湖"),
        ]),
    ],
    "settlement": [
        ("governance", "共同体のかたち", [
            ("council", "長老ではなく、くじで選ばれた合議"),
            ("guild", "職能ギルドが実権を握っている"),
            ("matriarch", "女系の家長たちが土地と水を管理している"),
            ("mixed", "移住者と先住の家々が通りを挟んで暮らす"),
            ("vow", "宗教的な戒律で一日の時間割が決まっている"),
            ("debt", "一つの商家への借金で村中が結ばれている"),
            ("exodus", "若者が出ていき、残った者が仕事を兼ねている"),
            ("garrison", "駐屯兵と住民がぎこちなく同居している"),
            ("refuge", "よそで居場所を失った人々が作った新しい村"),
        ]),
        ("custom", "習わし・祭り", [
            ("lantern", "亡くなった人の数だけ灯籠を流す夜"),
            ("swap", "年に一度、家の仕事を交換する日"),
            ("silent_market", "言葉を交わさずに取引する無言の市"),
            ("first_cut", "最初に収穫したものを外の誰かに贈る習慣"),
            ("name_stone", "生まれた子の名を石に刻んで道に敷く"),
            ("contest", "職人たちが腕を競う年に一度の品評会"),
            ("borrowed_saint", "よその土地の聖人を勝手に守護者にしている"),
            ("curfew_song", "日没に全員で歌い、歌が止んだら外に出ない"),
            ("feast_debt", "宴の費用を翌年の宴で返す仕組み"),
        ]),
        ("sorrow", "悲しみの型 (毎回「魔物に襲われた」にしない)", [
            ("absence", "帰ってこない誰か (失踪・出稼ぎ・徴兵)"),
            ("injustice", "見て見ぬふりをされている不正"),
            ("decline", "看板の産業が静かに衰えていく"),
            ("sickness", "原因の分からない病や不作"),
            ("broken_promise", "守られなかった昔の約束"),
            ("generation", "親と子の世代で信じるものが違う"),
            ("nature", "毎年少しずつ迫る自然 (浸食・砂・水位)"),
            ("guilt", "村ぐるみで隠している過去の罪"),
            ("rival", "隣の集落との終わらない諍い"),
        ]),
        ("bearer", "意外な担い手", [
            ("children", "子供たちが欠かせない仕事を担っている"),
            ("elders", "引退した老人だけが昔の技を覚えている"),
            ("veterans", "元兵士たちが畑と見張りを兼ねている"),
            ("outsider", "よそ者の職人が看板の品を作っている"),
            ("animals", "人ではなく、訓練された動物が仕事の要"),
            ("night_shift", "夜にしか働かない人々がいる"),
            ("one_family", "たった一家族が技を独占している"),
        ]),
    ],
    "wilderness": [
        ("road", "道の性格", [
            ("pilgrim", "巡礼者が石を積んで道標にした巡礼路"),
            ("salt_road", "荷役の獣が踏み固めた古い交易の道"),
            ("game_trail", "人ではなく獣が作った道"),
            ("waterway", "道の半分が水路や浅瀬"),
            ("ridge", "風の強い尾根伝いの道"),
            ("rail", "使われなくなった運搬路やトロッコの軌道"),
            ("ferry", "渡し場を何度も使う川沿いの道"),
            ("military", "軍が作った、まっすぐすぎる道"),
        ]),
        ("pressure", "旅の圧力", [
            ("deadline", "期限がある (祭り・潮・月の満ち欠け)"),
            ("weather", "天候が急変しやすい区間がある"),
            ("supply", "水や食料の補給が難しい"),
            ("pursuer", "誰かに後をつけられている"),
            ("signs_lost", "道標が壊され、あるいはずらされている"),
            ("toll", "通行料や許可を求める者がいる"),
            ("burden", "運ばなければならない荷や人がいる"),
        ]),
        ("meeting", "道中の出会い", [
            ("travelers", "反対方向から来る旅人"),
            ("lost", "道に迷った者"),
            ("deserter", "敵側から逃げてきた者"),
            ("wreck", "荷を残して消えた隊商の跡"),
            ("hermit", "道の途中に住み着いた変わり者"),
            ("beast_young", "親とはぐれた獣の仔"),
            ("rival_party", "同じ目的地を目指す別の一行"),
        ]),
    ],
    "site_lore": [
        ("mission", "ミッションの型", [
            ("stop_ritual", "儀式や装置の起動を阻止する"),
            ("divide", "敵の勢力を分断し、同士討ちさせる"),
            ("rescue", "囚われた者を救出して脱出する"),
            ("weak_point", "無敵に見える相手の弱点を仕掛けで暴く"),
            ("expose", "黒幕の陰謀の証拠を持ち帰る"),
            ("negotiate", "戦わずに交渉で決着をつける道がある"),
            ("reclaim", "奪われたものを取り返す"),
            ("investigate", "何が起きたのかを調べて真相を確かめる"),
            ("hold_out", "時間まで持ちこたえ、退路を確保する"),
            ("escort", "誰か (何か) を安全に運び出す"),
            ("race", "別の一行より先に目的を果たす"),
        ]),
        ("motive", "主な敵の動機", [
            ("survival", "生き延びるため (飢え・寒さ・追われている)"),
            ("misunderstanding", "誤解からこちらを敵だと思っている"),
            ("revenge", "昔の仕打ちへの復讐"),
            ("faith", "信仰と使命感"),
            ("orders", "上からの命令に縛られている"),
            ("guardian", "この場所を守ることが役目だと信じている"),
            ("salvation", "誰かを救うつもりでやっている"),
            ("profit", "金と取引"),
            ("curiosity", "知りたい、試したいという欲"),
        ]),
        ("twist", "ひねり", [
            ("client_lied", "依頼した側が何かを隠している"),
            ("enemy_victim", "敵とされている者も被害者だった"),
            ("cursed_prize", "求めていたものに代償がある"),
            ("time_shift", "時間 (昼夜・潮・季節) で中の様子が変わる"),
            ("truce", "対立する二勢力が一時休戦している"),
            ("alive_place", "場所そのものが意志や癖を持っている"),
            ("already_done", "目的のことは既に誰かが半分やり遂げている"),
            ("insider", "中に協力者がいるが、信用しきれない"),
            ("wrong_map", "伝わっている間取りが実際と違う"),
        ]),
        ("purpose", "建物の元の用途", [
            ("mill", "水車小屋や製粉所"), ("prison", "監獄"), ("observatory", "天文台"),
            ("granary", "穀物倉庫"), ("monastery", "修道院"), ("bathhouse", "公衆浴場"),
            ("foundry", "鋳造所"), ("mausoleum", "霊廟"), ("theater", "劇場"),
            ("academy", "学院"), ("lighthouse", "灯台"), ("dam", "堤防や水門"),
            ("mint", "造幣所"), ("hospital", "施療院"), ("customs", "関所や税関"),
        ]),
    ],
}

STAGES = tuple(DECKS.keys())


def draw_cards(stage: str, rng: random.Random, recent: Optional[Iterable[str]] = None) -> List[Dict[str, str]]:
    """
    stage の各軸から 1 枚ずつ引く。返り値は [{"id", "axis", "text"}]。
    recent に含まれるカード id は、その軸に別の候補が残っている限り引かない。
    """
    recent_ids = set(recent or [])
    cards = []
    for axis_id, axis_label, entries in DECKS.get(stage, []):
        pool = [(cid, text) for cid, text in entries if f"{stage}.{axis_id}.{cid}" not in recent_ids]
        if not pool:
            pool = list(entries)
        cid, text = rng.choice(pool)
        cards.append({"id": f"{stage}.{axis_id}.{cid}", "axis": axis_label, "text": text})
    return cards


def format_cards(cards: List[Dict[str, str]]) -> str:
    if not cards:
        return "(なし)"
    return "\n".join(f"- {c['axis']}: {c['text']}" for c in cards)
