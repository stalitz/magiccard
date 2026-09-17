# 魔法卡片接口协议常量
# 来源：开源项目 mfkp (2014) 逆向参考值。
# 警告：2026 年真实域名/端点大概率已迁移，上线前必须抓包确认。

# 接口基址（探测确认：https://card.show.qq.com/cgi-bin/ 在 2026 仍存活，返回标准 <QQHOME> XML）
# 注意：该接口需要游戏侧会话；仅用 qq.com 的 skey 会返回 -1100（未登录到该应用）。
MC_BASE_URL = "https://card.show.qq.com/cgi-bin/"

# 你提供的登录链接里的空间 appid
QZONE_APPID = "549000929"
# ptlogin 扫码用的 appid（TODO: 抓包确认是否为游戏专属 appid）
PTLOGIN_APPID = "549000929"

# ---- 魔法卡片接口端点 ----
# 主来源：sxyy/magicCard (2016) commonlib/constant.py 的真实字符串常量
# （该项目是可真正炼卡的 Python 实现，端点名比 mfkp 2014 更完整、可信）
EP_MAINPAGE     = "card_user_mainpage"           # 主页面/卡箱入口：POST code='' uin=<uin>
EP_REGISTER     = "card_user_register"           # 开通魔法卡片（未开通时须先调，见 -1005）
EP_REFINE       = "card_stove_refine"            # 开始炼卡（素材卡放入炉子）★ 已由 magicCard 确认
EP_REFINED_GET  = "card_stove_refinedcard_get"   # 取炼制完成的卡
EP_CANCEL       = "card_stove_cancel"            # 取消炼卡
EP_FLASH_FIRE   = "card_elf_flash_fire"          # 秒卡（立即完成）
EP_ACCELERATE   = "card_stove_accelerate"        # 加速炼卡
EP_STEAL_GET    = "card_stove_stealcard_get"     # 查询可偷卡
EP_STEAL        = "card_stove_steal"             # 偷卡
EP_RANDOM_GET   = "card_user_random_get"         # 抽机会卡 POST type=2
EP_SELL         = "card_market_npc_sell"         # 卖卡
EP_BUY          = "card_market_npc_buy"          # 买卡
EP_MOVE         = "card_user_storage_exchange"   # 移动卡（换卡箱 <-> 保险箱）
EP_THEME_LIST   = "card_user_theme_list"         # 套卡主题列表
EP_COLLECTION_ADD = "card_collection_add"        # 卡片放入集卡册
EP_EXCHANGE_CARD  = "card_user_exchangecard"     # 变卡（换卡）
EP_TRANSFER     = "card_user_transfer_card"      # 赠卡
EP_MONEY_DRAW   = "card_user_money_draw"         # 每日登陆礼包
EP_PAI_OPEN     = "card_pai_open"                # 翻牌
EP_MISSION_COMPLETE = "card_mission_complete"    # 完成任务
EP_GUIDE_REPORT = "card_user_guide_report"       # 新手引导上报
EP_LEVELUP_BONUS = "card_user_levelup_bonus"     # 升级奖励
EP_TASK_ACTIVITY  = "card_task_activity"         # 活动任务
EP_ACT_GIFTS    = "card_act_get_gifts"           # 活动领奖
EP_ACT_FREE     = "card_act_get_free"            # 活动免费次数
EP_ACT_PAY      = "card_act_pay"                 # 活动付费
EP_ACT_DIG      = "card_act_dig"                 # 挖宝
EP_SPIRITE      = "card_user_spirite"            # 宠物探险
EP_ZCG          = "card_user_zcg"                # 种子罐/杂物柜
EP_SHARE_INFO   = "card_user_share_info"         # 分享信息
EP_SHARE_GET    = "card_user_share_get"          # 分享领奖

# ---- 2026-09-14 新探明端点 ----
EP_USER_LIST    = "card_user_list"               # **好友列表**（uin/nick/exp/vip）
                                                 # 不是「全服」：固定返回 194 人＝登录者的 QQ 好友中
                                                 # 玩过本游戏的人（含自己 + 若干 exp=0 的僵尸号），
                                                 # 忽略分页参数、与请求者参数无关；可作换卡补充池
EP_MINI_GET     = "card_mini_get"                # 集卡册进度：<Node theme_id num lv>，覆盖全库套卡
EP_PROPS_INFO   = "card_market_props_info"       # 道具持有清单（2026-09-15 抓包发现）
                                                 # 返回 <prop type="1" subtype="N" count="X">，
                                                 # subtype 即道具卡 id（见 propCard_v_26.xml）
                                                 # 主界面加载时必调，无参数即可
# 注：EP_SPIRITE（精灵/猫咪探险）的 act 是"操作码"而非活动号：
#   act=1 查询（可带 opuin）｜act=3 提交探险（section_id+ids+pass_id 必须齐全）｜act=4 收获宝箱


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
    -1005: "用户未开通魔法卡片（需先调 card_user_register 开通）",
    # -1100：magicCard/mfkp 的错误码表均未收录。实测现象：带有效 skey + 正确 g_tk
    # 调用仍返回它。当前推测为「未开通/未初始化该应用」，待用 card_user_register
    # 开通后复测确认（尚未验证，勿当定论）。
    -1100: "未开通或未初始化该应用（推测，待 register 后复测）",
    -32001: "您的消耗卡面值低于目标卡，请重新选择消耗卡。",
    -32002: "找不到相应的记录。",
    -33002: "合成失败，可能是卡片被其他魔法师换走了（素材槽位已变/失效），请刷新后重试。",
    # -33003：实测来源 = card_stove_refine 在自己 5 个主炉全占时返回，服务端 message
    # 原文即「您的炼卡炉满了」（2026-09-17 偷炉实测）。
    -33003: "您的炼卡炉满了",
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
