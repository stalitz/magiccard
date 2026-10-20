"""七雄Q传(tid=290) 配方静态数据 —— 硬编码，零依赖 auto_refine。

卡 id / 面值 / 素材表（来自项目已核实配方树）：
  李牧4251 / 王翦4252 / 孙膑4253 / 吕不韦4254 / 蒙恬4255  (40面，素材=底卡，买即来)
  信陵君4256 ← 吕不韦+孙膑+王翦
  白起4257   ← 孙膑+王翦+李牧
  管仲4258   ← 蒙恬+吕不韦+孙膑
  西施4259   ← 管仲+白起+信陵君   (顶层, 540面)

炼时（theme_compose_list[c][6]，单位秒）：40面=3600 / 150面=14400 / 540面=54000。
底卡(10面) 买即来，占 0 炉时，无限供应。
"""

# cid -> (名称, 面值, [素材cid...])
CARDS = {
    4251: ("李牧", 40, []),
    4252: ("王翦", 40, []),
    4253: ("孙膑", 40, []),
    4254: ("吕不韦", 40, []),
    4255: ("蒙恬", 40, []),
    4256: ("信陵君", 150, [4254, 4253, 4252]),
    4257: ("白起", 150, [4253, 4252, 4251]),
    4258: ("管仲", 150, [4255, 4254, 4253]),
    4259: ("西施", 540, [4258, 4257, 4256]),
}

TOP = 4259  # 顶层收藏卡

FACE_DUR = {40: 3600, 150: 14400, 540: 54000}
DUR = {cid: FACE_DUR[face] for cid, (_, face, _) in CARDS.items()}


def depth_of(cid, _cache={}):
    """配方树深度：底卡=0，向上每层+1。带缓存。"""
    if cid in _cache:
        return _cache[cid]
    mats = CARDS[cid][2]
    d = 0 if not mats else 1 + max(depth_of(m) for m in mats)
    _cache[cid] = d
    return d


def compute_runs(sets):
    """复刻 ThemePlan.net_plan 的净需求口径（含每套卡的集卡册 +1 收藏需求）。

    返回 {cid: 需炼次数}（仅非底卡；底卡走 buy 不进 smelt）。
    与 auto_refine 的 net_plan 同算法：每个 id 初值=sets（收藏），按 depth 降序
    展开，net=需求-(箱存0+在炉0) 全为正 → runs[cid]=net，并把 net 累加到各素材 need。
    """
    ids = list(CARDS.keys())
    depths = {c: depth_of(c) for c in ids}
    need = {cid: max(1, sets) for cid in ids}  # 每张套内卡都按 sets 算收藏需求
    runs = {}
    for cid in sorted(ids, key=lambda c: -depths[c]):
        n = need.pop(cid, 0)
        if n <= 0:
            continue
        mats = CARDS[cid][2]
        runs[cid] = runs.get(cid, 0) + n  # 同一 cid 不会被多次 pop，这里直接赋值即可
        for m in mats:
            need[m] = need.get(m, 0) + n
    # 底卡(mats 为空)不进 smelt；但上面循环对底卡也设了 runs（其 need 不会再被消费）。
    # 真实 net_plan 里底卡走 buy 分支不设 runs——这里底卡 mats 空，下面构造 job 时跳过即可。
    return runs


def mats_count(cid):
    """素材需求 {mat_cid: 数量}——同类型素材可互换（游戏真实语义）。"""
    out = {}
    for m in CARDS[cid][2]:
        out[m] = out.get(m, 0) + 1
    return out


def ess_work(cid, _cache={}):
    """生产 1 张 cid 卡（含其全部素材，递归）所需炉时（下界用）。"""
    if cid in _cache:
        return _cache[cid]
    v = DUR[cid] + sum(k * ess_work(m) for m, k in mats_count(cid).items())
    _cache[cid] = v
    return v


def recipe_meta():
    return {
        "name": "七雄Q传",
        "top": TOP,
        "cards": CARDS,
        "dur": DUR,
        "face_dur": FACE_DUR,
    }


# ---------------------------------------------------------------------------
# 泛化：从 data/card_lib.json 读取任意主题的配方树
# ---------------------------------------------------------------------------
import json as _json                       # noqa: E402
from pathlib import Path as _Path         # noqa: E402

_LIB_DEFAULT = (_Path(__file__).resolve().parent.parent.parent
                / "data" / "card_lib.json")


def _load_lib(lib_path=None):
    p = _Path(lib_path) if lib_path else _LIB_DEFAULT
    return _json.loads(p.read_text(encoding="utf-8"))


class RecipeSet:
    """任意 tid 的配方集，接口与模块级常量（CARDS/DUR/compute_runs/...）一致，
    供 instance.build_instance 与 run_compare / run_prod_compare 直接替换使用。

    底卡 cut 规则（与七雄Q传硬编码版同语义）：一张卡的素材若**有任何一个不在
    本主题的可炼卡集合里**（即买即来的底卡），则该卡整体当作底卡（mats=[]），
    其炉时仍照常计入——等价于「素材无限供应、0 约束」。
    """

    def __init__(self, tid, lib_path=None):
        lib = _load_lib(lib_path)
        tcl = lib["theme_card_list"]
        tco = lib["theme_compose_list"]
        cl = lib["card_list"]
        face_map = {int(c[0]): int(c[3]) for c in cl}
        name_map = {int(c[0]): str(c[2]) for c in cl}

        th = next((t for t in tcl if int(t[0]) == int(tid)), None)
        self.tid = int(tid)
        self.name = str(th[1]).strip() if th else f"tid{tid}"
        self.star = int(th[2] or 0) if th else 0
        self.theme_ids = ([int(x) for x in (th[11] or []) if x] if th else [])

        comp = {}
        for c in tco:
            if int(c[0]) == int(tid):
                cid = int(c[2])
                comp[cid] = {"mats": [int(x) for x in c[3:6] if x],
                             "dur": int(c[6])}
        card_ids = set(comp)
        cards = {}
        for cid, info in comp.items():
            used = [m for m in info["mats"] if m in card_ids]
            cards[cid] = (name_map.get(cid, f"card{cid}"),
                          face_map.get(cid, 0), used)
        # 顶层 = 深度最大的卡（可能多张并列，取其一作代表）
        self.CARDS = cards
        self.DUR = {cid: info["dur"] for cid, info in comp.items()}
        faces = sorted({f for _, f, _ in cards.values()})
        self.FACE_DUR = {f: min(self.DUR[c] for c in cards if cards[c][1] == f)
                         for f in faces}
        # 深度缓存
        self._depth = {}
        self._ess = {}

    # --- 与模块级同名的接口 ---
    def depth_of(self, cid, _seen=None):
        if cid in self._depth:
            return self._depth[cid]
        mats = self.CARDS.get(cid, (None, None, []))[2]
        if not mats:
            self._depth[cid] = 0
            return 0
        d = 1 + max(self.depth_of(m) for m in mats)
        self._depth[cid] = d
        return d

    def mats_count(self, cid):
        out = {}
        for m in self.CARDS.get(cid, (None, None, []))[2]:
            out[m] = out.get(m, 0) + 1
        return out

    def ess_work(self, cid):
        if cid in self._ess:
            return self._ess[cid]
        v = self.DUR[cid] + sum(k * self.ess_work(m)
                                for m, k in self.mats_count(cid).items())
        self._ess[cid] = v
        return v

    def compute_runs(self, sets):
        ids = list(self.CARDS.keys())
        depths = {c: self.depth_of(c) for c in ids}
        need = {cid: max(1, sets) for cid in ids}
        runs = {}
        for cid in sorted(ids, key=lambda c: -depths[c]):
            n = need.pop(cid, 0)
            if n <= 0:
                continue
            mats = self.CARDS[cid][2]
            runs[cid] = runs.get(cid, 0) + n
            for m in mats:
                need[m] = need.get(m, 0) + n
        return runs

    def top_cid(self):
        return max(self.CARDS, key=lambda c: (self.depth_of(c), c))

    def recipe_meta(self):
        return {
            "name": self.name, "top": self.top_cid(), "star": self.star,
            "cards": self.CARDS, "dur": self.DUR, "face_dur": self.FACE_DUR,
        }


def build_recipe_set(tid, lib_path=None):
    return RecipeSet(tid, lib_path)


# 模块级 name/star：TID==290 时 run_prod_compare 把本模块直接当 RECIPE 使用，
# 需提供与 RecipeSet 同名的属性（其余接口 CARDS/DUR/compute_runs/... 已在模块级定义）。
name = "七雄Q传"
star = 2
