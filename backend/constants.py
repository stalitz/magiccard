# 魔法卡片接口协议常量
# 来源：开源项目 mfkp (2014) 逆向参考值。
# 警告：2026 年真实域名/端点大概率已迁移，上线前必须抓包确认。

# 接口基址（探测确认：https://card.show.qq.com/cgi-bin/ 在 2026 仍存活，返回标准 <QQHOME> XML）
# 注意：该接口需要「玩吧(wanba)游戏会话」，仅用空间 skey 会返回 -1100（未登录到该应用）。
MC_BASE_URL = "https://card.show.qq.com/cgi-bin/"

# 你提供的登录链接里的空间 appid
QZONE_APPID = "549000929"
# ptlogin 扫码用的 appid（TODO: 抓包确认是否为游戏专属 appid）
PTLOGIN_APPID = "549000929"

# ---- 魔法卡片接口端点（来自 mfkp/js/page/*，2014 参考）----
EP_REFINE       = "card_stove_refine"            # TODO 抓包确认：开始炼卡（素材卡放入炉子）
EP_REFINED_GET  = "card_stove_refinedcard_get"   # 取炼制完成的卡
EP_CANCEL       = "card_stove_cancel"            # 取消炼卡
EP_FLASH_FIRE   = "card_elf_flash_fire"          # 秒卡（立即完成）
EP_ACCELERATE   = "card_stove_accelerate"        # 加速炼卡
EP_RANDOM_GET   = "card_user_random_get"         # 抽机会卡 type=1单抽 / type=2全抽
EP_SELL         = "card_market_npc_sell"          # 卖卡
EP_MOVE         = "card_user_storage_exchange"    # 移动卡（换卡箱 <-> 保险箱）
EP_STOVE_BOX    = "card_stove_box"               # TODO 抓包确认：查询炉子状态


def calc_gtk(skey: str) -> int:
    """腾讯 g_tk 标准算法（来自 mfkp base.js hash(skey)），十年未变。"""
    h = 5381
    for ch in skey:
        h += (h << 5) + ord(ch)
    return h & 0x7fffffff


# 错误码表（来自 mfkp base.js getMsgByCode，节选常用）
ERR_CODES = {
    -1001: "请重新登陆！",
    -1002: "系统繁忙，请稍后再试!",
    -32001: "您的消耗卡面值低于目标卡，请重新选择消耗卡。",
    -32002: "找不到相应的记录。",
    -33004: "箱子满了！",
    -33011: "卡片尚未炼制完。",
    -33023: "您选择的卡片类型并不是您的好友所需要的!",
    -33033: "卡片放入集卡册成功，但QQ秀奖励发放失败，24小时内补发。",
    -33060: "您只能和好友交换等数量等面值的卡片哦",
    -33064: "您的魔力值不足，请补充魔力后再进行变卡。",
    -33084: "您在此好友处偷炉的卡片尚未取出，请先取卡后再偷炉~",
    -41002: "系统繁忙，请稍后再试！",
    -801: "购买失败!您的帐户余额不足",
    -802: "密保验证失败",
    -1102: "操作的号码和登录的号码不一致，请重新登录！",
    -1106: "对不起，您可能不是Ta的好友!",
}


def err_msg(code: int) -> str:
    return ERR_CODES.get(code, f"未知错误码 {code}")
