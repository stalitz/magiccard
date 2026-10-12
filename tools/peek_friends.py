import sys, re, json, asyncio
sys.path.insert(0, ".")
from backend.main import _session
from backend.constants import EP_USER_LIST

async def main():
    s = _session
    r = await s.post(EP_USER_LIST)
    raw = r.get("raw", "")
    # 抓所有 <user ...> 标签，打印其全部属性
    users = re.findall(r"<user\b([^>]*)>", raw)
    print(f"好友总数(标签数): {len(users)}")
    print("=== 前 6 个 <user> 原始属性 ===")
    for u in users[:6]:
        print("  ", u.strip())
    print("=== 后 6 个 <user> 原始属性 ===")
    for u in users[-6:]:
        print("  ", u.strip())
    # 是否有 level / lv / 等级 字段
    attrs = set()
    for u in users:
        for k in re.findall(r'(\w+)\s*=', u):
            attrs.add(k)
    print("=== <user> 标签出现的属性名 ===", sorted(attrs))
    # uin 顺序
    uins = re.findall(r'<user\s+uin="(\d+)"', raw)
    print(f"=== uin 序列长度 {len(uins)} ===")
    print("  前 5:", uins[:5])
    print("  后 5:", uins[-5:])
    print("  3934268479 索引:", uins.index("3934268479") if "3934268479" in uins else "不在列表")

asyncio.run(main())
