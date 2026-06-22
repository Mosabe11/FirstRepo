#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AgentEdge — Agent Doctor
========================
يحلل سجل الصفقات بعمق، يكتشف الأنماط الخاسرة، ويطلّع
توصيات تحسين قابلة للتطبيق (بدون ما يغيّر أي شيء بنفسه).

التشغيل (داخل مجلد المشروع، بعد تفعيل venv):
    python3 agent_doctor.py

يحفظ النتائج في doctor_report.json
"""

import sqlite3, os, json, sys
from datetime import datetime

DB = os.environ.get("AGENTEDGE_DB", "data/memory.db")

# عتبات القرار (قابلة للتعديل)
MIN_SAMPLE      = 10      # أقل عدد صفقات حتى نحكم على ستراتيجية/أصل
KILL_WR         = 30.0    # تحت هالنسبة + خسارة = أوقفها
KILL_PNL        = -10.0   # خسارة تراكمية تستدعي الإيقاف
WARN_WR         = 40.0    # تحتها = مراقبة/تشديد
GOOD_WR         = 55.0    # فوقها = أبقها/عزّزها


def line(c="="): print(c * 60)
def head(t): print(f"\n{'='*4} {t} {'='*4}")
def r2(x):
    try: return round(float(x), 2)
    except (TypeError, ValueError): return 0.0
def pct(a, b): return round(100.0 * a / b, 1) if b else 0.0


def cols_of(con, table):
    return [r[1] for r in con.execute(f"PRAGMA table_info({table})")]


def col(cols, *cands):
    for c in cands:
        if c in cols: return c
    return None


def grouped(con, gcol, W, P, wc):
    """ارجع تجميع (key, trades, wins, pnl, avg) مرتب بالـ PnL."""
    win_expr = f"SUM({W}=1)" if W else "0"
    rows = con.execute(
        f"SELECT {gcol} k, COUNT(*) n, {win_expr} w, COALESCE(SUM({P}),0) p "
        f"FROM trades {wc} GROUP BY {gcol}"
    ).fetchall()
    out = []
    for k, n, w, p in rows:
        out.append({
            "key": str(k) if k is not None else "(none)",
            "trades": n, "wins": w or 0, "win_rate": pct(w or 0, n),
            "pnl": r2(p), "avg_pnl": r2((p or 0) / n) if n else 0.0
        })
    return sorted(out, key=lambda x: x["pnl"])


def verdict(rec):
    """قرار آلي لكل ستراتيجية/أصل."""
    n, wr, pnl = rec["trades"], rec["win_rate"], rec["pnl"]
    if n < MIN_SAMPLE:
        return "HOLD", f"عيّنة صغيرة ({n}) — راقب"
    if wr < KILL_WR and pnl <= KILL_PNL:
        return "KILL", f"WR {wr}% وخسارة {pnl} — أوقفها"
    if pnl <= KILL_PNL:
        return "KILL", f"خسارة تراكمية {pnl} — أوقفها"
    if wr < WARN_WR:
        return "TIGHTEN", f"WR {wr}% ضعيف — شدّد العتبة أو قلّل الحجم"
    if wr >= GOOD_WR and pnl > 0:
        return "BOOST", f"WR {wr}% وربح {pnl} — عزّزها (زد الوزن)"
    return "KEEP", f"WR {wr}% مقبول"


def main():
    if not os.path.exists(DB):
        print("❌ ما لقيت قاعدة البيانات:", DB); sys.exit(1)
    con = sqlite3.connect(DB)
    cols = cols_of(con, "trades")

    W = col(cols, "win", "is_win")
    P = col(cols, "pnl", "profit")
    C = col(cols, "closed_at", "close_time")
    S = col(cols, "strategy", "strategy_name")
    A = col(cols, "asset", "symbol")
    R = col(cols, "close_reason", "reason")
    D = col(cols, "direction", "side")
    EDGE = col(cols, "signal_edge", "edge")
    CONF = col(cols, "council_confidence", "confidence")
    REG = col(cols, "regime")
    wc = f"WHERE {C} IS NOT NULL" if C else ""

    report = {"generated_at": datetime.now().isoformat(), "actions": []}

    # ===== الإجمالي =====
    head("الإجمالي")
    total = con.execute(f"SELECT COUNT(*) FROM trades {wc}").fetchone()[0]
    wins = con.execute(
        f"SELECT COUNT(*) FROM trades {wc} {'AND' if wc else 'WHERE'} {W}=1"
    ).fetchone()[0] if W else 0
    pnl = con.execute(f"SELECT COALESCE(SUM({P}),0) FROM trades {wc}").fetchone()[0]
    print(f"صفقات={total}  WR={pct(wins,total)}%  PnL={r2(pnl)}  "
          f"متوسط={r2(pnl/total) if total else 0}")
    report["overall"] = {"trades": total, "win_rate": pct(wins, total),
                         "pnl": r2(pnl)}

    # ===== الستراتيجيات + قرارات =====
    head("الستراتيجيات (مع القرار الآلي)")
    strat = grouped(con, S, W, P, wc) if S else []
    print(f"{'ستراتيجية':<16}{'صفقات':>6}{'WR%':>6}{'PnL':>10}  القرار")
    line("-")
    for rec in strat:
        v, why = verdict(rec)
        print(f"{rec['key']:<16}{rec['trades']:>6}{rec['win_rate']:>6}"
              f"{rec['pnl']:>10}  {v}")
        rec["verdict"], rec["reason"] = v, why
        if v in ("KILL", "TIGHTEN", "BOOST"):
            report["actions"].append(
                {"target": "strategy", "name": rec["key"],
                 "action": v, "why": why})
    report["by_strategy"] = strat

    # ===== الأصول الأسوأ =====
    head("أسوأ 8 أصول")
    assets = grouped(con, A, W, P, wc) if A else []
    for rec in assets[:8]:
        v, why = verdict(rec)
        print(f"{rec['key']:<12}{rec['trades']:>5} صفقة  WR={rec['win_rate']:>5}%"
              f"  PnL={rec['pnl']:>9}  {v}")
        if v == "KILL":
            report["actions"].append(
                {"target": "asset", "name": rec["key"],
                 "action": "REMOVE_FROM_WATCHLIST", "why": why})
    report["worst_assets"] = assets[:8]

    # ===== أسباب الإغلاق =====
    if R:
        head("أسباب الإغلاق")
        for rec in grouped(con, R, W, P, wc):
            print(f"{rec['key']:<10}{rec['trades']:>5}  WR={rec['win_rate']:>5}%"
                  f"  PnL={rec['pnl']:>9}")
        report["by_close_reason"] = grouped(con, R, W, P, wc)

    # ===== تحليل الاتجاه LONG/SHORT =====
    if D:
        head("LONG مقابل SHORT")
        for rec in grouped(con, D, W, P, wc):
            print(f"{rec['key']:<8}{rec['trades']:>5}  WR={rec['win_rate']:>5}%"
                  f"  PnL={rec['pnl']:>9}")
        report["by_direction"] = grouped(con, D, W, P, wc)

    # ===== هل الإيدج العالي فعلاً أفضل؟ =====
    if EDGE and W and P:
        head("هل الإيدج العالي = أداء أفضل؟")
        for lo, hi in [(0, 55), (55, 65), (65, 75), (75, 200)]:
            r = con.execute(
                f"SELECT COUNT(*) n, SUM({W}=1) w, COALESCE(SUM({P}),0) p "
                f"FROM trades {wc} AND {EDGE}>=? AND {EDGE}<?", (lo, hi)
            ).fetchone()
            n, w, p = r[0], r[1] or 0, r[2] or 0
            if n:
                print(f"إيدج {lo}-{hi}: صفقات={n:>4} WR={pct(w,n):>5}% PnL={r2(p):>9}")
                report.setdefault("by_edge_band", []).append(
                    {"band": f"{lo}-{hi}", "trades": n,
                     "win_rate": pct(w, n), "pnl": r2(p)})

    # ===== هل ثقة المجلس فعلاً مفيدة؟ =====
    if CONF and W and P:
        head("هل ثقة المجلس العالية = أداء أفضل؟")
        for lo, hi in [(0, 60), (60, 75), (75, 90), (90, 101)]:
            r = con.execute(
                f"SELECT COUNT(*) n, SUM({W}=1) w, COALESCE(SUM({P}),0) p "
                f"FROM trades {wc} AND {CONF}>=? AND {CONF}<?", (lo, hi)
            ).fetchone()
            n, w, p = r[0], r[1] or 0, r[2] or 0
            if n:
                print(f"ثقة {lo}-{hi}: صفقات={n:>4} WR={pct(w,n):>5}% PnL={r2(p):>9}")

    # ===== ملخص التوصيات =====
    head("ملخص التوصيات الآلية")
    if not report["actions"]:
        print("لا توجد إجراءات حرجة — الأداء ضمن الحدود.")
    for a in report["actions"]:
        print(f"  • [{a['action']}] {a['target']}={a['name']} — {a['why']}")

    with open("doctor_report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    line()
    print("✅ التقرير الكامل: doctor_report.json")
    print("   انسخ محتواه هنا لنقرر سوا شو نطبّق على الكود.")
    con.close()

if __name__ == "__main__":
    main()
