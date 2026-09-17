# 魔法卡片助手（自研版）

替代「金鱼」这类收费助手的免费自研工具：纯文字 / 列表 / 按钮界面，走魔法卡片
后端接口，QQ 扫码登录。

## 已确认的协议（来自开源项目 mfkp 逆向，2014 参考值）

| 项 | 内容 |
|---|---|
| 接口基址 | `http://card.show.qq.com/cgi-bin/`（游戏页面在 `appimg.qq.com`） |
| 认证 | cookie 里的 `skey` + URL 参数 `g_tk = hash(skey)` |
| g_tk 算法 | DJB hash：`h=5381; for c: h+=(h<<5)+ord(c); return h & 0x7fffffff` |
| 返回格式 | XML：`<QQHOME code=.. message=..><card .../></QQHOME>` |
| 错误码 | 见 `backend/constants.py` 的 `ERR_CODES` |

### 已确认的端点
- `card_stove_refinedcard_get` 取炼制完成的卡（uin, slotid, slottype=1, ver=1, code）
- `card_stove_cancel` 取消炼卡
- `card_elf_flash_fire` 秒卡（立即完成）
- `card_stove_accelerate` 加速炼卡
- `card_user_random_get` 抽机会卡（type=1 单抽 / type=2 全抽）
- `card_market_npc_sell` 卖卡
- `card_user_storage_exchange` 移动卡（换卡箱 <-> 保险箱）

## ⚠️ 抓包确认项（上线前必须做）

这些是 **2014 年** 的参考值，2026 年魔法卡片接口大概率已迁移（域名 / HTTPS /
appid / 端点名都可能变）。**第一步必须抓一次真实请求**：

1. 用 Flash 中心 / 大厅版客户端进入游戏，手动炼一次卡。
2. 同时抓包（Charles / mitmproxy），重点看：
   - 请求域名是不是还叫 `card.show.qq.com`，还是已迁到 `qzs.qq.com` 之类
   - **开始炼卡** 的端点名和参数（本项目暂用 `card_stove_refine` 占位，需确认）
   - 服务端要的 `skey` 是 qq.com 域还是 qzs 域（影响 g_tk 从哪个 cookie 算）
3. 把确认后的值填进 `backend/constants.py` 的 `MC_BASE_URL` / 端点常量。

## 运行

```bash
cd /Users/stalitz/www/魔法卡片
python3 -m venv backend/.venv
source backend/.venv/bin/activate
pip install -r backend/requirements.txt
uvicorn backend.main:app --port 8000 --reload
```

浏览器打开 http://localhost:8000 ，手机 QQ 扫码登录后即为操作面板。

## 现状
- ✅ 扫码登录（ptlogin2 标准 QR 协议，真实可跑）
- ✅ 接口层结构（g_tk 算法 + XML 解析 + 错误码）
- ✅ 文字 / 列表 / 按钮前端骨架
- 🚧 开始炼卡、取卡等接口待抓包填真实端点后联调

## 奖励编号格式（2026-09-16 定案）

游戏里所有奖励串（翻牌 `card_pai_open` 的 `content`、灵宠关卡 `register_gift`、
活动礼包 `package`、猫咪旅行 `giftInfo`）都是同一个三段式：

```
大类id _ 类内编号id _ 数量        例：92_32_1 = 猫咪旅行类第32号 ×1 = 红宝石×1
```

**第 1 段是玩法/品类 id，不是道具 id**（所以「翻牌能出气球」并不矛盾——
气球是 `48_1415`，大类 48 = 通用/活动道具）。名字表与实证链见
`backend/item_dict.py`。

## 数据文件（`data/` 不入库，需联网重建）

`data/` 被 `.gitignore` 忽略，新环境需重建这几个文件：

| 文件 | 重建方式 |
|---|---|
| `data/item_dict.json` | `python -m backend.item_dict`（拉官方静态配置，约 470 条） |
| `data/prop_dict.json` `data/card_lib.json` | 见各自模块注释 |

官方静态配置都在 `appimg2.qq.com/card/...`，**免登录直取**；版本号会随游戏更新，
最新地址可用登录态调一次 `card_run_base` 的 `version_list` 取（见
`item_dict.parse_version_list`）。
