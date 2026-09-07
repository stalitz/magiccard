# magicCard (sxyy, 2016) 源码解读

> 目的：把这个能真正炼卡的开源实现**读懂**，明确哪些能直接复用、哪些必须重写。
> 结论先行：**程序本体不能直接搬，但协议知识已 100% 吸收进本项目。**

---

## 0. 项目性质（先定性，避免误用）

| 项 | 实际情况 |
|---|---|
| 语言 | **Python 2**（`print` 语句、`xrange`、`urllib2`、`cookielib`、`md5`、`0xffffffffL`、`long()`） |
| 界面 | **wxPython 桌面程序**，Windows 专用（还用到 `win32api.SetSystemTime`，虽然被注释） |
| 登录 | **账号密码 + 验证码**（`Tea.getTEAPass` 做 MD5→RSA→TEA 加密） |
| 能炼卡吗 | 能，是完整可用的挂机工具 |

**所以它和我们要做的东西（跨平台 web + 扫码登录）在「程序形态」上完全不同。**

---

## 1. 请求层 `commonlib/myhttp.py` —— 关键，只有 30 行

```python
self.cj = cookielib.CookieJar()
self.opener = urllib2.build_opener(urllib2.HTTPCookieProcessor(self.cj))

def get_response(self, url, data=None, customCookies=None):
    user_agent = 'Mozilla/4.0 (compatible; MSIE 5.5; Windows NT)'
    if data is not None:
        req = urllib2.Request(url, urllib.urlencode(data))   # ← 带 data 即 POST
    else:
        req = urllib2.Request(url)                            # ← 否则 GET
    req.add_header('User-Agent', user_agent)
    req.add_header('Accept-Encoding', '*')
    response = self.opener.open(req)
    return response
```

**这 30 行解释了我们卡了很久的 -1100**：
- `urllib2.Request(url, data)` 带 data 就是 **POST（application/x-www-form-urlencoded）**；
- 所有业务调用都传了 `postData`（如 `getMagicInfo` 传 `{'code':'','uin':...}`），所以**全部是 POST**；
- 我们之前一直用 GET → 服务端一律 `-1100`。
- Cookie 由 `CookieJar` 自动携带；UA 伪装成 IE5.5（腾讯老接口对 UA 不敏感，我们没抄这个）。

---

## 2. 签名 `commonlib/Tea.py` —— g_tk 算法（逐行一致，已复用）

```python
def getGTK(skey):
    myhash = 5381
    for i in skey:
        myhash += (myhash << 5) + ord(i)
    return myhash & 0x7fffffff
```

即 `hash = 5381; hash += (hash<<5) + ord(c)`，最后 `& 0x7fffffff`（DJB 变体，腾讯十年未变）。

**本项目 `backend/constants.py::calc_gtk` 与此逐行等价，实测可用。**

同文件还有 `getTEAPass(q, p, v)`：
1. 密码 `md5` → 16 字节摘要 `p`
2. `m = md5(p + uin的16进制字符串)` 作为 TEA key
3. RSA 公钥（腾讯硬编码 1024 位公钥，指数 3）加密 `p`
4. 拼 `长度 + RSA结果 + salt(uin) + 验证码长度 + 验证码大写`
5. 用 TEA 加密 → base64 → 把 `/ + =` 替换成 `- * _`

→ **这是密码登录用的，我们用扫码，这段代码完全不需要。**（且现在腾讯对密码登录几乎必出验证码/风控）

---

## 3. 端点表 `commonlib/constant.py` —— 已整体移植

真实基址 `http://card.show.qq.com/cgi-bin/`（我们实测 https 同样可用），端点形如：

| 常量 | 端点 | 用途 |
|---|---|---|
| `CARDLOGINURL` | `card_user_mainpage` | 主页面/全量状态（**核心**） |
| `STOVEREFINEURL` | `card_stove_refine` | 开始炼卡 |
| `REFINEDCARD` | `card_stove_refinedcard_get` | 取炼好的卡 |
| `DRAWCARDURL` | `card_user_random_get` | 抽卡 |
| `SALECARD` | `card_market_npc_sell` | 卖卡 |
| `BUYCARDURL` | `card_market_npc_buy` | 买卡 |
| `CARDINPUTSTOREBOX` | `card_user_storage_exchange` | 换卡箱↔保险箱 |
| `MIAOKAURL` | `card_stove_accelerate` | 加速（魔火） |
| `REGISTERMAGICCARD` | `card_user_register` | 开通（我们已开通，返回 -1005） |
| `MONEYDRAW` / `FANPAIURL` / `THEMELISTURL` | … | 礼包 / 翻牌 / 主题列表 |

另有移动端域 `mfkp.qzapp.z.qq.com/qshow/cgi-bin/wl_card_*`（带 `sid`），**我们不用**——PC 域已够。

⚠️ 注意常量里的 `REGISTERMAGICCARD` 带 `&timestamp=...` 这类硬编码随机串，是当年抓包留下的，无意义。

---

## 4. 各操作真实参数（来自 `commons.py` / `main.py` 调用点）

全部 `POST`，`g_tk` 在 URL 上，业务参数在 body：

```python
# 主页面（最常用，读全量状态）
postData = {'code': '', 'uin': uin}                       # getMagicInfo()

# 抽卡：type=1 单抽 / type=2 全抽
{'type': 1, 'iSlotNo': slotId}                            # main.py drawCard()
{'type': types, 'fetchnum': fetchnum}                     # commons.drawCard()

# 卖卡
{'slot_no': slotNo, 'cardid': cardId, 'type': types, 'uin': account}

# 买卡
{'card_id': cardId, 'theme_id': themeId}

# 换卡箱 ↔ 保险箱：type=0 存保险箱 / type=1 回换卡箱
{'dest': dest, 'src': src, 'type': types, 'uin': account}

# 每日礼包 / 翻牌
{'uin': uin}                                              # MONEYDRAW
{'type': 1}                                               # FANPAIURL
```

**取卡参数**由 mfkp (2014) 的 `js/page/stove_box.js` 交叉验证：

```javascript
card_stove_refinedcard_get?uin=..&slotid=<炉位>&slottype=1&ver=1&code=
```

与我们实测一致（返回 `-32002 找不到记录` 即该炉位无卡，属正常业务码）。

> ⚠️ `ver=1` 与 `code=` 是**必填**：缺失时服务端返回非数字 code（`versionError`），
> 本项目 `parse_response()` 已对非数字 code 做容错（原先 `int()` 会抛异常）。

---

## 5. 状态解析：主页面 XML 是什么结构

`card_user_mainpage` 返回 `<QQHOME code="0">`，内含：

| 标签 | 含义 |
|---|---|
| `<user nick uin money exp randchance …>` | 昵称/金币/经验/**抽卡机会** |
| `<stove id endtime lv strong_lv>` | 每个炼卡炉（lv=炉等级） |
| `<stovebox cur max stovenum>` + 子 `<card slot stove btime locktime id flag>` | 炼卡位（`flag=2` 表示炼制完成可取） |
| `<changebox cur max>` + 子 `<card slot id type status>` | 换卡箱 |
| `<storebox cur max nor>` + 子 `<card>` | 保险箱 |
| `<elf>` `<zcg>` `<vip_data>` `<new_data>` | 精灵 / 种子罐 / VIP / 魔钻金粉等 |

本项目已能完整解析（实测 181 张卡）。

---

## 6. 复用结论（不造轮子，但也不黑盒）

| 部分 | 处理方式 |
|---|---|
| g_tk 算法 | ✅ **直接等价实现**（`constants.calc_gtk`） |
| 端点表 | ✅ **整体移植**（`constants.EP_*`） |
| 各操作参数 | ✅ **照抄**并实测验证 |
| XML 结构与解析 | ✅ 按上述标签解析 |
| 请求方式（POST 表单 + g_tk 在 URL） | ✅ 按 `myhttp.py` 语义用 httpx 重写 |
| 登录（密码+TEA+RSA） | ❌ **不搬**：要扫码，且 Py2 + 风控 |
| wxPython 界面 | ❌ **不搬**：要跨平台 web |
| Python 2 语法 | ❌ **不搬**：本项目 Python 3.13 |
| 开始炼卡 `card_stove_refine` 参数 | ⚠️ **源码里没有**（`commons.py` 无炼卡实现），仍需定位 |

**一句话：它的「协议知识」我们已经全部吃下并实测通过；它的「程序本体」一个文件都没复制——因为形态、语言、登录方式全不匹配，复制过来只会是负担。**
