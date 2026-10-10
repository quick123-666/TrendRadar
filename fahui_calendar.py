#!/usr/bin/env python
"""藏传佛教法会日历 —— 固定农历日期 → 公历日期, 附带倒计时

法会是按**农历固定日期**举行的周期性宗教活动, 不需要抓新闻, 直接算即可。
本模块:
  1. 内置各寺院的固定法会日程 (农历 月/日)
  2. 逐年换算成公历日期 (自动处理闰月、大小月)
  3. 输出 JSON + HTML 日历, 标出"距今 N 天"
  4. --within N 可以只看未来 N 天内要办的 (给监控脚本当预警用)

用法:
  python fahui_calendar.py                    # 2026-2027 全量
  python fahui_calendar.py --years 2025-2030  # 指定年份区间
  python fahui_calendar.py --within 90       # 只看未来 90 天
  python fahui_calendar.py --json            # 输出 JSON 到 stdout
"""
import sys
import json
import argparse
import pathlib
from datetime import date, timedelta, datetime, timezone

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

try:
    from lunar_python import Solar, Lunar
except ImportError:
    print("需要 lunar-python: pip install lunar-python", file=sys.stderr)
    raise SystemExit(1)

ROOT = pathlib.Path(__file__).parent
OUT = ROOT / "data" / "fahui_monitor"
OUT.mkdir(parents=True, exist_ok=True)
TZ = timezone(timedelta(hours=8))

# ────────────────────────────────────────────────────────────
# 固定农历日程
#   (寺院, 法会名, 农历月, 农历日, 重要度, 说明)
#   重要度 3 = 大型/必看   2 = 常规   1 = 附属活动
# ────────────────────────────────────────────────────────────
SCHEDULE = [
    # ===== 拉卜楞寺 (甘肃甘南夏河) 七次大法会 =====
    ("拉卜楞寺", "毛兰姆法会(祈愿法会) 开幕", 1, 3, 3, "正月初三起, 历时15天, 甘南最隆重"),
    ("拉卜楞寺", "毛兰姆法会·放生节", 1, 8, 3, "嘉木样大师率僧官展出寺宝, 放生马牛羊"),
    ("拉卜楞寺", "毛兰姆法会·瞻佛(晒大佛)", 1, 13, 3, "近百米唐卡巨佛, 数万信众, 全年最热"),
    ("拉卜楞寺", "毛兰姆法会·法舞(跳神)", 1, 14, 3, "大经堂广场, 30余僧人面具舞, 5小时"),
    ("拉卜楞寺", "毛兰姆法会·酥油花供灯会", 1, 15, 3, "元宵节, 大经堂内外, 灯节核心"),
    ("拉卜楞寺", "毛兰姆法会·转香巴(转弥勒)", 1, 16, 2, "抬弥勒佛绕寺一周, 游客可参与"),
    ("拉卜楞寺", "二月祭会(丛确节)·亮宝", 2, 8, 2, "数百僧人持寺宝绕寺一周"),
    ("拉卜楞寺", "四月娘乃节(浴佛节)", 4, 15, 2, "释迦牟尼诞生/成道/圆寂, 闭斋念六字真言"),
    ("拉卜楞寺", "敦白日扎法会(七月法会) 开幕", 6, 29, 3, "农历六月廿九至七月十五, 共17天"),
    ("拉卜楞寺", "敦白日扎法会·辩经序幕", 6, 27, 2, "大法台与各学院法台示范性辩论"),
    ("拉卜楞寺", "敦白日扎法会·米拉日巴劝善会", 7, 8, 3, "晒佛 + 七场法舞, 规模仅次于正月法会"),
    ("拉卜楞寺", "禳灾法会", 9, 29, 2, "喜金刚学院跳禳灾舞"),
    ("拉卜楞寺", "宗喀巴圆寂纪念·燃灯节", 10, 25, 3, "寺院开放, 屋顶点灯, 燃灯节"),

    # ===== 塔尔寺 (青海西宁湟中) 四大法会 =====
    ("塔尔寺", "正月祈愿法会(孟兰钦莫)", 1, 1, 3, "四大法会之首, 含晒佛/跳神/酥油花"),
    ("塔尔寺", "正月法会·酥油花展", 1, 15, 3, "塔尔寺灯节核心, 国家级非遗"),
    ("塔尔寺", "四月祈愿法会(德钦松宗·三重节)", 4, 15, 3, "释迦牟尼诞生/成佛/涅槃, 晒大佛"),
    ("塔尔寺", "六月祈愿法会(曲科德钦·法轮节)", 6, 7, 3, "晒大佛+转金佛+马首金刚护法舞"),
    ("塔尔寺", "九月祈愿法会(拉浣德钦·降凡节)", 9, 22, 3, "天降节, 拜大佛, 纪念释迦返人间"),

    # ===== 郎木寺 (甘肃碌曲/四川若尔盖) =====
    ("郎木寺", "正月晒佛", 1, 13, 3, "与拉卜楞寺同日, 规模较小"),
    ("郎木寺", "亮宝节", 1, 14, 2, "部落组织巡游"),

    # ===== 扎什伦布寺 (西藏日喀则) =====
    ("扎什伦布寺", "色莫钦姆羌姆(跳神)", 1, 13, 3, "2026-09-15 曾被中国西藏网报道"),
    ("扎什伦布寺", "玛尼法会", 1, 8, 2, "念诵六字真言"),
]

# 需要重点盯的灌顶(无法按农历推算, 需人工/搜索补充)
WANGCHEN_NOTE = (
    "灌顶法会(如时轮金刚灌顶)由具体上师定档, 无固定农历, 需靠中国西藏网"
    "「金瓶宝鉴」专题或 web_search 捕捉 —— 参考: 2025-10-09~12 班禅在扎什伦布寺"
    "主持时轮金刚灌顶, 距上次隔九年。"
)


def lunar_to_solar(ly, lm, ld, year_span=range(2025, 2031)):
    """把 (农历月, 日) 在给定公历年份区间内换算成公历日期.

    做法: 逐年逐日遍历, 公历→农历比对. 这样闰月、大小月都自动正确,
    不会出现"正月三十"这种不存在的日期被硬算出来.
    """
    out = []
    for y in year_span:
        d = date(y, 1, 1)
        end = date(y + 1, 1, 1)
        while d < end:
            try:
                lu = Solar.fromYmd(d.year, d.month, d.day).getLunar()
            except Exception:
                d += timedelta(days=1)
                continue
            # 闰月时 getMonth() 返回负数, 取绝对值后与目标月比较
            # ⚠️ 必须用 lu.getMonth(), 不是 lm —— 写成 lm 会让条件恒真,
            #    导致每年 13 个月份的同一天全被收进来 (2026-10-10 踩过)
            lm_i = lu.getMonth() if lu.getMonth() > 0 else -lu.getMonth()
            if lm_i == lm and lu.getDay() == ld:
                out.append({
                    "solar": d.isoformat(),
                    "lunar": f"{ly}年{lu.getMonthInChinese()}月{lu.getDayInChinese()}"
                              f"{'(闰)' if lu.getMonth() < 0 else ''}",
                    "is_leap": lu.getMonth() < 0,
                })
            d += timedelta(days=1)
    return out


def build(years, within=None, today=None):
    today = today or datetime.now(TZ).date()
    span = range(years[0], years[1] + 1)
    events = []
    for temple, name, lm, ld, weight, note in SCHEDULE:
        for r in lunar_to_solar(temple, lm, ld, span):
            sd = date.fromisoformat(r["solar"])
            days = (sd - today).days
            if within is not None and not (0 <= days <= within):
                continue
            events.append({
                "temple": temple, "name": name, "weight": weight, "note": note,
                "solar": r["solar"], "lunar": r["lunar"], "is_leap": r["is_leap"],
                "days_until": days, "weekday": "一二三四五六日"[sd.weekday()],
            })
    events.sort(key=lambda e: e["solar"])
    return events


def to_html(events, today, years_str=""):
    MARK = {3: "🔥", 2: "★", 1: "·"}
    by_date = {}
    for e in events:
        by_date.setdefault(e["solar"], []).append(e)

    today_s = today.isoformat()
    rows = []
    for d in sorted(by_date):
        cls = " class='today'" if d == today_s else (
            " class='past'" if d < today_s else "")
        items = "".join(
            f"<div class='e w{e['weight']}'><b>{MARK[e['weight']]} {e['temple']}</b> "
            f"{e['name']}<br><span class='meta'>农历 {e['lunar']} · 周{e['weekday']}"
            f"{' · ' + e['note'] if e['note'] else ''}</span></div>"
            for e in by_date[d])
        du = by_date[d][0]["days_until"]
        du_txt = ("今天" if du == 0 else
                  (f"{du} 天后" if du > 0 else f"{-du} 天前"))
        rows.append(f"<tr{cls}><td class='d'>{d}<br><span class='meta'>周{by_date[d][0]['weekday']}"
                    f" · {du_txt}</span></td><td>{items}</td></tr>")

    html = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>藏传佛教法会日历</title><style>
:root{{--bg:#0d1117;--card:#161b22;--bd:#30363d;--fg:#c9d1d9;--dim:#8b949e;
--ac:#58a6ff;--hot:#f85149;--w2:#d29922}}
*{{box-sizing:border-box}}
body{{margin:0;padding:24px;background:var(--bg);color:var(--fg);
font:14px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}}
h1{{font-size:21px;margin:0 0 4px}}
.sub{{color:var(--dim);font-size:12px;margin-bottom:20px}}
table{{width:100%;border-collapse:collapse;background:var(--card);
border:1px solid var(--bd);border-radius:8px;overflow:hidden}}
td{{padding:10px 14px;border-bottom:1px solid var(--bd);vertical-align:top}}
tr:last-child td{{border-bottom:none}}
tr:hover td{{background:#1c2128}}
tr.today td{{background:#1d2b1d}}
tr.past{{opacity:.42}}
.d{{white-space:nowrap;font-weight:600;color:var(--ac);width:150px}}
.meta{{color:var(--dim);font-size:11px;font-weight:400}}
.e{{padding:3px 0}}
.e b{{color:var(--fg)}}
.w3 b{{color:var(--hot)}}.w2 b{{color:var(--w2)}}
.note{{background:#1c2128;border:1px solid var(--bd);border-radius:8px;
padding:12px 16px;margin-top:18px;font-size:12px;color:var(--dim)}}
code{{background:#21262d;padding:1px 5px;border-radius:4px}}
</style></head><body>
<h1>藏传佛教法会日历</h1>
<div class="sub">生成于 {datetime.now(TZ):%Y-%m-%d %H:%M} (UTC+8) · 共 {len(events)} 场 · 覆盖 {years_str} 年<br>
日历日期为<b>农历固定日期换算的公历</b>, 🔥=大型必看 ★=常规</div>
<table>{''.join(rows) if rows else '<tr><td>无数据</td></tr>'}</table>
<div class="note"><b>说明</b><br>
1. 法会按农历固定日期举行, 此表由农历直接换算, 不依赖抓取, 不会漏。<br>
2. 闰月会单独标注(实际法会通常顺延或按寺规另行通知)。<br>
3. {WANGCHEN_NOTE}<br>
4. 藏历与汉地农历存在换算差异, 出行前建议再核对寺院公告。</div>
</body></html>"""
    return html


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", default="2026-2027", help="如 2026-2027")
    ap.add_argument("--within", type=int, default=None, help="只看未来 N 天")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    a, b = args.years.split("-")
    years = (int(a), int(b))
    today = datetime.now(TZ).date()
    events = build(years, args.within, today)

    if args.json:
        print(json.dumps(events, ensure_ascii=False, indent=2))
        return

    (OUT / "fahui_calendar.json").write_text(
        json.dumps(events, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "fahui_calendar.html").write_text(
        to_html(events, today, f"{years[0]}-{years[1]}"), encoding="utf-8")

    print("=" * 84)
    print(f"藏传佛教法会日历  {years[0]}-{years[1]}   共 {len(events)} 场   今天 {today}")
    print("=" * 84)
    for e in events:
        du = e["days_until"]
        mark = {3: "🔥", 2: "★", 1: "·"}[e["weight"]]
        cnt = (f"{du} 天后" if du > 0 else ("今天" if du == 0 else f"{-du} 天前"))
        print(f"  {mark} {e['solar']} 周{e['weekday']}  [{cnt:>7}]  {e['temple']:8} {e['name']}")
    print(f"\n已生成 {OUT / 'fahui_calendar.html'}")


if __name__ == "__main__":
    main()
