#!/usr/bin/env python3
"""藏传佛教法会 / 灌顶 重点信源监控

针对没有 RSS 的藏区媒体与政府站, 定时抓首页/新闻列表页,
按关键词过滤出法会相关条目, 落地存档并增量去重。

信源 (2026-10-10 实测 HTTP 200 且有法会内容):
  中国西藏网   tibet.cn         — 班禅灌顶首发渠道, 有「金瓶宝鉴」系列专题
  康巴卫视网  kangbatv.com     — 甘孜/甘南藏区本地, 塔尔寺跳欠等报道
  甘南州政府   gannan.gov.cn
  碌曲县政府   luqu.gov.cn       — 七月法会/郎木寺
  夏河县政府   xiahe.gov.cn      — 拉卜楞寺法会
  西宁市政府   xining.gov.cn     — 塔尔寺四大法会

用法:
  python fahui_monitor.py             # 跑一轮
  python fahui_monitor.py --days 7    # 顺便展示最近 7 天命中
"""
import json
import re
import ssl
import sys
import time
import argparse
import pathlib
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).parent
STORE = ROOT / "data" / "fahui_monitor"
STORE.mkdir(parents=True, exist_ok=True)
STATE = STORE / "seen.json"
TZ = timezone(timedelta(hours=8))

# ── 关键词分层 ──
# 强信号: 佛教法会专有动作词, 单独出现即可判定为相关
KW_STRONG = re.compile(
    r"灌顶|时轮金刚|跳欠|羌姆|跳神|法舞|瞻佛|晒大?佛|展佛|亮佛|亮宝|"
    r"辩经|酥油花|供灯会|祈愿大?法会|法舞会|传昭|玛尼朵美|"
    r"仁波切|堪布|受戒|传戒|出家|剃度|金瓶宝鉴|萨嘎达瓦|供僧", re.I)

# 中信号: 泛佛教词, 必须与"寺院/佛教语境"同现才算命中
KW_MID = re.compile(r"法会|法讯|放生|转经|朝圣|供灯|僧团|佛学院|法台|法王|活佛", re.I)

# 寺名专名: 具体寺院名, 出现即判定 (不会误伤)
KW_TEMPLE = re.compile(
    r"塔尔寺|拉卜楞寺|扎什伦布寺?|郎木寺|甘丹寺|大昭寺|布达拉宫|色拉寺|哲蚌寺|"
    r"扎什伦布寺|哲蚌寺|昂仁寺|桑耶寺|敏珠林寺|楚布寺|直贡寺|南迦巴则", re.I)

# 地区名: 单独出现太泛 (迪庆州/玛曲/碌曲 常出现在政务新闻里),
#         必须与佛教/寺院语境同现才判定
KW_REGION = re.compile(
    r"夏河|碌曲|玛曲|甘南州?|迪庆州?|香格里拉|玉树|果洛|黄南|海西|海北|"
    r"甘孜州?|阿坝州?|泽仁|德格|石渠|囊谦|安多|康巴|康区|安多藏区", re.I)

# 佛教语境词: 与地区名同现时起确认作用
KW_BUDDHIST = re.compile(
    r"寺|佛教|藏传|格鲁|宁玛|噶举|萨迦|僧|法会|法舞|灌顶|仁波切|活佛|堪布|"
    r"经幡|玛尼|酥油花|唐卡|坛城|护法", re.I)

# 「法会」的常见假阳性: 立法会 / 发布会 / 大会 / 商协会 …
FALSE_FA = re.compile(
    r"立法会|发布会|开幕会|闭幕会|大会|商会|协会|委员会|理事会|年会|"
    r"见面会|报告会|音乐会|追悼会|纪念会|座谈会|洽谈会", re.I)

# 黑名单: 行政/公文/灾害/医疗/商业类噪音
KW_NOISE = re.compile(
    r"办公室|预算|决算|财政|决定书|行政处罚|不予行政|注销|登记|登记公告|条例|办法|"
    r"规定|通知|公告|公示|招标|采购|中标|人事|任免|干部|述职|考核|"
    r"人大|政协|会议|召开|调研|座谈会|扶贫|乡村振兴项目|疫情|防控|地震|泥石流|"
    r"事故|坍塌|调查|报告|工程|施工|集团|有限公司|土地|征收|拆迁|医保|门诊|"
    r"医院|疾病|净土|保卫战|基层|振兴|宣传周|交流周|学院|学校|中学|小学|"
    r"招录|分数线|集团|公司有限|电视剧|系列剧|集全|第\d+集", re.I)



SOURCES = [
    ("中国西藏网",   "http://www.tibet.cn/"),
    ("康巴卫视网",  "https://www.kangbatv.com/"),
    ("甘南州政府",  "http://www.gannan.gov.cn/"),
    ("碌曲县政府",  "http://www.luqu.gov.cn/"),
    ("夏河县政府",  "http://www.xiahe.gov.cn/"),
    ("西宁市政府",  "https://www.xining.gov.cn/"),
]

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120 Safari/537.36")


def fetch(url, timeout=20):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept": "text/html,application/xhtml+xml"})
    with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
        raw = r.read()
    for enc in ("utf-8", "gb18030", "gbk", "big5"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def absolutize(base, href):
    from urllib.parse import urljoin
    return urljoin(base, href)


def is_fahui(title):
    """判定标题是否与藏传佛教法会/灌顶相关.

    规则 (黑名单优先):
      1. 行政/公文/灾害/医疗类直接否
      2. 强信号 (灌顶/羌姆/辩经/酥油花...)  -> 是
      3. 寺名专名 (塔尔寺/扎什伦布寺...)      -> 是
      4. 地区名 (香格里拉/夏河...) 且 与佛教语境同现 -> 是
      5. 泛佛教词 (法会/放生/活佛...) 且 与寺院/佛教同现 -> 是
    """
    t = title.strip()
    if KW_NOISE.search(t):
        return False, []
    # 「法会」只在不是 立法会/发布会/大会 这类词时才算佛教语境
    mid = [m.group(0) for m in KW_MID.finditer(t)
           if not FALSE_FA.match(t[max(0, m.start()-2):m.end()+2])]
    strong = [m.group(0) for m in KW_STRONG.finditer(t)]
    temple = [m.group(0) for m in KW_TEMPLE.finditer(t)]
    region = [m.group(0) for m in KW_REGION.finditer(t)]
    mid = [m.group(0) for m in KW_MID.finditer(t)]
    bud = bool(KW_BUDDHIST.search(t))

    if strong:
        return True, sorted(set(strong + temple + region))
    if temple:
        return True, sorted(set(temple + strong))
    if region and bud:
        return True, sorted(set(region))
    if mid and bud:
        return True, sorted(set(mid))
    return False, []


def extract_links(html, base, source):
    out = []
    for m in re.finditer(r'<a[^>]+href="([^"#]+)"[^>]*>(.*?)</a>', html, re.S | re.I):
        href, text = m.group(1), re.sub(r"<[^>]+>", "", m.group(2))
        text = re.sub(r"\s+", " ", text).strip()
        if not (6 <= len(text) <= 80):
            continue
        ok, matched = is_fahui(text)
        if not ok:
            continue
        out.append({
            "source": source,
            "title": text,
            "url": absolutize(base, href),
            "matched": matched,
        })
    seen, uniq = set(), []
    for it in out:
        k = it["title"][:30]
        if k in seen:
            continue
        seen.add(k)
        uniq.append(it)
    return uniq


def load_state():
    if STATE.exists():
        try:
            return json.loads(STATE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"seen": {}, "runs": []}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=0, help="展示最近 N 天命中")
    ap.add_argument("--fetch-body", action="store_true", help="抓取命中条目正文")
    args = ap.parse_args()

    state = load_state()
    seen = state.setdefault("seen", {})
    now = datetime.now(TZ)
    print("=" * 84)
    print(f"藏传佛教法会/灌顶 重点信源监控   {now.strftime('%Y-%m-%d %H:%M:%S')} (UTC+8)")
    print("=" * 84)

    all_hits = []
    src_stat = []
    for name, url in SOURCES:
        try:
            html = fetch(url)
            hits = extract_links(html, url, name)
        except Exception as e:
            print(f"  ❌ {name:12} {type(e).__name__}: {str(e)[:50]}")
            src_stat.append((name, "FAIL", 0))
            continue
        new = [h for h in hits if h["url"] not in seen]
        print(f"  ✅ {name:12} 抓到 {len(hits):2} 条相关, 其中新增 {len(new)} 条")
        for h in new:
            seen[h["url"]] = {"ts": now.isoformat(), "title": h["title"], "source": h["source"]}
        all_hits.extend(new)
        src_stat.append((name, "OK", len(hits)))
        time.sleep(1.2)

    # 抓正文(可选)
    if args.fetch_body and all_hits:
        print(f"\n  抓取 {len(all_hits)} 条正文 ...")
        for h in all_hits:
            try:
                body = fetch(h["url"], timeout=20)
                txt = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", body)
                txt = re.sub(r"<[^>]+>", " ", txt)
                txt = re.sub(r"\s+", " ", txt).strip()
                m = re.search(r"(\d{4}[-年]\d{1,2}[-月]\d{1,2})", txt[:2000])
                h["date"] = m.group(1) if m else ""
                h["excerpt"] = txt[:500]
            except Exception:
                h["excerpt"] = ""
            time.sleep(1.0)

    # 存档
    day_file = STORE / f"{now:%Y-%m-%d}.json"
    prev = json.loads(day_file.read_text(encoding="utf-8")) if day_file.exists() else []
    merged = {h["url"]: h for h in prev}
    for h in all_hits:
        merged[h["url"]] = h
    day_file.write_text(json.dumps(list(merged.values()), ensure_ascii=False, indent=2),
                        encoding="utf-8")
    state["runs"].append({"ts": now.isoformat(),
                          "sources_ok": sum(1 for s in src_stat if s[1] == "OK"),
                          "new_hits": len(all_hits)})
    state["runs"] = state["runs"][-200:]
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")

    print("\n" + "=" * 84)
    print(f"本轮新增 {len(all_hits)} 条   累计已见 {len(seen)} 条")
    print("=" * 84)
    for h in all_hits:
        print(f"\n  【{h['source']}】{h['title']}")
        print(f"    命中词: {', '.join(h['matched'][:8])}")
        print(f"    {h['url']}")
        if h.get("excerpt"):
            print(f"    {h['excerpt'][:220]}")

    if args.days:
        print("\n" + "=" * 84)
        print(f"最近 {args.days} 天全部命中")
        print("=" * 84)
        cutoff = (now - timedelta(days=args.days)).strftime("%Y-%m-%d")
        total = 0
        for i in range(args.days):
            d = (now - timedelta(days=i)).strftime("%Y-%m-%d")
            f = STORE / f"{d}.json"
            if not f.exists():
                continue
            items = json.loads(f.read_text(encoding="utf-8"))
            if not items:
                continue
            print(f"\n  ── {d} ({len(items)} 条) ──")
            for it in items:
                total += 1
                print(f"    [{it['source']}] {it['title'][:60]}")
        if not total:
            print("  (无)")

    print(f"\n存档目录: {STORE}")


if __name__ == "__main__":
    main()
