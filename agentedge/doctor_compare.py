#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AgentEdge — Doctor (نسخة المقارنة الزمنية)
==========================================
يفصل الصفقات إلى: قبل التعديل / بعد التعديل، ويقارن الأداء.
نقطة الفصل الافتراضية = لحظة تطبيق الإصلاحات (2026-06-01 20:16 UTC).

التشغيل:
    python3 doctor_compare.py
    python3 doctor_compare.py 2026-06-01T20:16:00   # نقطة فصل مخصصة
"""
import sqlite3, sys, os
from datetime import datetime

DB = "data/memory.db"

# نقطة الفصل: لحظة تطبيق الإصلاحات (UTC). عدّلها إن لزم.
CUTOFF_STR = sys.argv[1] if len(sys.argv) > 1 else "2026-06-01T20:16:00"
CUTOFF = datetime.fromisoformat(CUTOFF_STR).timestamp()


def pct(a, b): return round(100.0 * a / b, 1) if b else 0.0
def r2(x):
    try: return round(float(x), 2)
    except (TypeError, ValueError): return 0.0


def block(con, label, where_extra, params):
    """يطبع كتلة إحصائية لمجموعة صفقات."""
    base = "FROM trades WHERE closed_at IS NOT NULL"
    n = con.execute(f"SELECT COUNT(*) {base} {where_extra}", params).fetchone()[0]
    if not n:
        print(f"\n### {label}: لا صفقات مغلقة بعد")
        return None
    w = con.execute(f"SELECT COUNT(*) {base} {where_extra} AND win=1", params).fetchone()[0]
    p = con.execute(f"SELECT COALESCE(SUM(pnl),0) {base} {where_extra}", params).fetchone()[0]
    print(f"\n### {label}")
    print(f"  صفقات={n}  WR={pct(w,n)}%  PnL={r2(p)}  متوسط={r2(p/n)}")
    # حسب الستراتيجية
    print("  حسب الستراتيجية:")
    for row in con.execute(
        f"SELECT strategy, COUNT(*), SUM(win=1), COALESCE(SUM(pnl),0) "
        f"{base} {where_extra} GROUP BY strategy ORDER BY 4", params):
        s, nn, ww, pp = row
        print(f"    {str(s):<15}{nn:>4}  WR={pct(ww,nn):>5}%  PnL={r2(pp):>9}")
    return {"trades": n, "win_rate": pct(w, n), "pnl": r2(p)}


def main():
    if not os.path.exists(DB):
        print("❌ ما لقيت", DB); sys.exit(1)
    con = sqlite3.connect(DB)

    # عدد المراكز المفتوحة حالياً (closed_at NULL)
    open_n = con.execute(
        "SELECT COUNT(*) FROM trades WHERE closed_at IS NULL").fetchone()[0]

    print("=" * 56)
    print(f"نقطة الفصل (التعديل): {CUTOFF_STR} UTC")
    print(f"مراكز مفتوحة حالياً (لم تُغلق بعد): {open_n}")
    print("=" * 56)

    before = block(con, "قبل التعديل (القديم)", "AND closed_at < ?", (CUTOFF,))
    after  = block(con, "بعد التعديل (الجديد) ⭐", "AND closed_at >= ?", (CUTOFF,))

    # حكم المقارنة
    print("\n" + "=" * 56)
    if not after:
        print("الحكم: لسا ما في صفقات جديدة مغلقة كفاية.")
        print("       العتبة الأعلى = تداول أبطأ. انتظر 24-48 ساعة وأعد التشغيل.")
    elif before:
        dw = after["win_rate"] - before["win_rate"]
        print("الحكم (الجديد مقابل القديم):")
        print(f"  WR: {before['win_rate']}%  →  {after['win_rate']}%  "
              f"({'+' if dw>=0 else ''}{round(dw,1)} نقطة)")
        print(f"  متوسط PnL/صفقة: راقب الإشارة، العيّنة الجديدة لا تزال صغيرة.")
        if after["trades"] < 20:
            print(f"  ⚠️ العيّنة الجديدة ({after['trades']} صفقة) صغيرة — "
                  f"لا تحكم نهائياً قبل 30+ صفقة.")
    con.close()
    print("=" * 56)


if __name__ == "__main__":
    main()
