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
(STORE / "news").mkdir(parents=True, exist_ok=True)        # 新闻报道(事后)
(STORE / "gonggao").mkdir(parents=True, exist_ok=True)     # 法讯公告(事前)
(STORE / "unreachable").mkdir(parents=True, exist_ok=True) # 需 web_search 兜底的源
STATE = STORE / "seen.json"
TZ = timezone(timedelta(hours=8))
DATE_RE = re.compile(r"(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})")
URL_DATE_RE = re.compile(r"[/t_](20\d{2})(\d{2})(\d{2})[_/.]")

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

# 佛教语境词: 供地区名/泛佛教词做"确认用"判断.
# ⚠️ 不能包含 法会/法舞/灌顶 等本身就在匹配的词 —— 否则任何含"法会"的标题
#    都会自动满足"佛教语境", 上下文检查形同虚设 (2026-10-10 踩过).
KW_BUDDHIST = re.compile(
    r"寺|佛教|藏传|格鲁|宁玛|噶举|萨迦|僧|仁波切|活佛|堪布|法王|法台|"
    r"经幡|玛尼|酥油花|唐卡|坛城|护法|拜佛|朝佛", re.I)

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
    r"招录|分数线|集团|公司有限|电视剧|系列剧|集全|第\d+集|"
    r"攻略|门票|游记|见闻|住宿|交通|怎么玩|必看|指南", re.I)



SOURCES = [
    # ── 新闻报道类: 活动结束后央媒/地方媒体发稿, 带记者署名 ──
    #    2026-10-10 实测 HTTP 200 可直抓
    ("中国西藏网",   "http://www.tibet.cn/",                "news"),
    ("康巴卫视网",  "https://www.kangbatv.com/",          "news"),
    ("甘南州政府",  "http://www.gannan.gov.cn/",           "news"),
    ("碌曲县政府",  "http://www.luqu.gov.cn/",             "news"),
    ("夏河县政府",  "http://www.xiahe.gov.cn/",            "news"),
    ("西宁市政府",  "https://www.xining.gov.cn/",          "news"),

    # ── 法讯公告类: 活动开始前发布, 附报名链接/电话, 时效性最强 ──
    #    实测可直抓
    ("创古中心(香港)", "https://www.thranguhk.org/",       "gonggao"),
    ("中央传媒(台湾)", "https://tcnmedia.net/",            "gonggao"),

    # ── 实测从本机不可达 (DNS 通但 TCP 超时, 换 http/https/UA 均失败) ──
    #    这些只能靠 web_search 走搜索引擎索引, 由 cron 任务兜底
    ("喇嘛网(台湾)",   "https://www.lama.com.tw/",        "search-only"),
    ("藏密网",         "https://edupro.org/",            "search-only"),
    ("帕平(尼泊尔)",   "https://palpung.org/",            "search-only"),
    ("桑吉佛网",       "https://www.sangjigo.net/",      "search-only"),
]

# 时效窗口
NEWS_WINDOW_DAYS = 30       # 新闻: 只收最近 30 天发布的
GONGGAO_WINDOW_DAYS = 90    # 法讯: 只收未来 90 天内要办的 (提前太久没意义)


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
    # 「法会」只在不是 立法会/发布会/大会 这类词时才算佛教语境.
    # 用 search 而非 match: 切片里该词可能不在开头 (如 "区立法会通过")
    mid = [m.group(0) for m in KW_MID.finditer(t)
           if not FALSE_FA.search(t[max(0, m.start() - 2):m.end() + 2])]
    strong = [m.group(0) for m in KW_STRONG.finditer(t)]
    temple = [m.group(0) for m in KW_TEMPLE.finditer(t)]
    region = [m.group(0) for m in KW_REGION.finditer(t)]
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


def extract_links(html, base, source, kind="news"):
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
            "kind": kind,
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


def dates_in_text(text):
    out = set()
    for m in DATE_RE.finditer(text):
        try:
            out.add(datetime(int(m.group(1)), int(m.group(2)),
                             int(m.group(3)), tzinfo=TZ).date())
        except ValueError:
            pass
    return out


def date_from_url(url):
    m = URL_DATE_RE.search(url)
    if not m:
        return None
    try:
        return datetime(int(m.group(1)), int(m.group(2)),
                         int(m.group(3)), tzinfo=TZ).date()
    except ValueError:
        return None


def classify_window(item, body_dates, now):
    """按体裁判定时效窗口, 返回 (是否收录, 状态标签, 日期列表).

    news    : 收最近 NEWS_WINDOW_DAYS 天发布的报道
    gonggao : 只收未来 GONGGAO_WINDOW_DAYS 天内要办的活动, 过期的丢弃
    """
    today = now.date()
    if item["kind"] == "news":
        d = date_from_url(item["url"])
        if d is None:
            d = min(body_dates) if body_dates else None
        if d is None:
            return True, "news-无日期", []
        age = (today - d).days
        if -3 <= age <= NEWS_WINDOW_DAYS:      # 容忍 3 天时区偏差
            return True, f"news-{d}", [d]
        return False, f"news-{d}(超出{age}天)", [d]

    # gonggao: 活动日期必须落在未来窗口内
    fut = sorted(d for d in body_dates if today <= d <= today + timedelta(days=GONGGAO_WINDOW_DAYS))
    if fut:
        return True, f"gonggao-即将{fut[0]}", fut
    past = sorted(d for d in body_dates if d < today)
    if past:
        return False, f"gonggao-已过期{past[-1]}", past
    return False, "gonggao-无法定位日期", []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=0, help="展示最近 N 天命中")
    ap.add_argument("--fetch-body", action="store_true", default=True)
    ap.add_argument("--limit-body", type=int, default=25, help="单源最多抓几条正文")
    args = ap.parse_args()

    state = load_state()
    seen = state.setdefault("seen", {})
    now = datetime.now(TZ)
    print("=" * 88)
    print(f"藏传佛教法会/灌顶 监控   {now.strftime('%Y-%m-%d %H:%M:%S')} (UTC+8)")
    print(f"  新闻窗口: 近 {NEWS_WINDOW_DAYS} 天    法讯窗口: 未来 {GONGGAO_WINDOW_DAYS} 天")
    print("=" * 88)

    kept, stats, unreachable = [], [], []
    for name, url, kind in SOURCES:
        if kind == "search-only":
            unreachable.append(name)
            print(f"  ⏭ {name:16} 本机不可达, 需 web_search 兜底")
            continue
        try:
            html = fetch(url)
        except Exception as e:
            print(f"  ❌ {name:16} {type(e).__name__}: {str(e)[:46]}")
            continue
        cands = extract_links(html, url, name, kind)
        print(f"  ✅ {name:16} 候选 {len(cands):>2} 条")
        for it in cands[: args.limit_body]:
            body_dates = set()
            try:
                body = fetch(it["url"], timeout=15)
                txt = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", body)
                body_dates = dates_in_text(re.sub(r"<[^>]+>", " ", txt))
                it["excerpt"] = re.sub(r"\s+", " ",
                                        re.sub(r"<[^>]+>", " ", txt)).strip()[:300]
            except Exception:
                pass
            ok, tag, ds = classify_window(it, body_dates, now)
            it["tag"] = tag
            it["dates"] = [str(d) for d in ds]
            if ok and it["url"] not in seen:
                seen[it["url"]] = {"ts": now.isoformat(), "title": it["title"],
                                    "source": it["source"], "kind": kind}
                kept.append(it)
            elif not ok:
                seen.setdefault("_stale", []).append({"title": it["title"][:40], "tag": tag})
            time.sleep(0.8)
        stats.append((name, kind, len(cands)))
        time.sleep(1.0)

    # 分体裁归档
    for kind, sub in (("news", "news"), ("gonggao", "gonggao")):
        items = [k for k in kept if k["kind"] == kind]
        if not items:
            continue
        f = STORE / sub / f"{now:%Y-%m-%d}.json"
        prev = json.loads(f.read_text(encoding="utf-8")) if f.exists() else []
        merged = {i["url"]: i for i in prev}
        for i in items:
            merged[i["url"]] = i
        f.write_text(json.dumps(list(merged.values()), ensure_ascii=False, indent=2),
                     encoding="utf-8")

    (STORE / "unreachable" / "sources.txt").write_text(
        "\n".join(unreachable), encoding="utf-8")

    state["runs"].append({"ts": now.isoformat(), "kept": len(kept)})
    state["runs"] = state["runs"][-200:]
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")

    print("\n" + "=" * 88)
    print(f"本轮收录 {len(kept)} 条  (新闻 {len([k for k in kept if k['kind']=='news'])} / "
          f"法讯 {len([k for k in kept if k['kind']=='gonggao'])})")
    print("=" * 88)
    for k in kept:
        print(f"\n  [{k['kind']:7}] 【{k['source']}】{k['title'][:52]}")
        print(f"           {k['tag']}   命中: {', '.join(k['matched'][:6])}")
        print(f"           {k['url']}")

    print(f"\n归档: {STORE / 'news'}   |   {STORE / 'gonggao'}")
    if unreachable:
        print(f"需 web_search 兜底: {', '.join(unreachable)}")


if __name__ == "__main__":
    main()
