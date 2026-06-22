#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AgentEdge — Edge Validator
==========================
يقيس إن كان للنظام حافة حقيقية أم مجرد حظ.
يحسب: Profit Factor, Expectancy, Max Drawdown, التوزيع الزمني,
الاتساق عبر الأصول، وأطول سلاسل الربح/الخسارة.

التشغيل:
    python3 edge_validator.py            # كل الصفقات بعد نقطة التعديل
    python3 edge_validator.py all        # كل الصفقات (قديم+جديد)
"""
import sqlite3, sys
from datetime import datetime, timezone

DB = "data/memory.db"
CUT = datetime.fromisoformat("2026-06-01T20:16:00").timestamp()
SCOPE_ALL = len(sys.argv) > 1 and sys.argv[1] == "all"

con = sqlite3.connect(DB)

where = "closed_at IS NOT NULL AND close_reason!='ABANDONED'"
params = []
if not SCOPE_ALL:
    where += " AND closed_at >= ?"
    params = [CUT]

rows = con.execute(
    f"SELECT pnl, win, closed_at, asset, strategy FROM trades WHERE {where} "
    f"ORDER BY closed_at ASC", params
).fetchall()

if not rows:
    print("لا صفقات في النطاق المحدد."); sys.exit()

pnls = [r[0] or 0 for r in rows]
n = len(pnls)
wins = [p for p in pnls if p > 0]
losses = [p for p in pnls if p < 0]

gross_win = sum(wins)
gross_loss = abs(sum(losses))
net = sum(pnls)

print("=" * 58)
print(f"النطاق: {'كل الصفقات' if SCOPE_ALL else 'بعد التعديل فقط'}  |  العدد: {n}")
print("=" * 58)

# 1) Profit Factor
pf = (gross_win / gross_loss) if gross_loss else float("inf")
print(f"\n1) Profit Factor: {pf:.2f}")
print(f"   (إجمالي ربح {gross_win:.2f} ÷ إجمالي خسارة {gross_loss:.2f})")
print(f"   الحكم: ", end="")
if pf >= 1.5: print("ممتاز — حافة قوية")
elif pf >= 1.2: print("جيد — حافة واضحة")
elif pf >= 1.0: print("هامشي — رابح بالكاد")
else: print("خاسر — لا حافة")

# 2) Expectancy
wr = len(wins) / n
avg_win = (gross_win / len(wins)) if wins else 0
avg_loss = (gross_loss / len(losses)) if losses else 0
expectancy = net / n
print(f"\n2) Expectancy لكل صفقة: {expectancy:+.3f}")
print(f"   WR={wr*100:.1f}%  متوسط ربح={avg_win:.2f}  متوسط خسارة={avg_loss:.2f}")
rr = (avg_win / avg_loss) if avg_loss else 0
print(f"   نسبة ربح/خسارة (R:R): {rr:.2f}  "
      f"({'متوازن' if rr>=1 else 'الخاسرة أكبر من الرابحة ⚠️'})")
# الحد الأدنى لـ WR المطلوب عند هذا الـ R:R
if avg_win:
    be_wr = avg_loss / (avg_win + avg_loss) * 100
    print(f"   WR المطلوب للتعادل عند هذا R:R: {be_wr:.1f}%  "
          f"(عندك {wr*100:.1f}%)")

# 3) Max Drawdown (على منحنى رأس المال)
equity = 0; peak = 0; max_dd = 0
for p in pnls:
    equity += p
    peak = max(peak, equity)
    max_dd = min(max_dd, equity - peak)
print(f"\n3) Max Drawdown: {max_dd:.2f}")
print(f"   صافي الربح الكلي: {net:+.2f}")

# 4) أطول سلسلة ربح/خسارة
def longest(seq, sign):
    best = cur = 0
    for p in seq:
        if (p > 0) == (sign > 0) and p != 0:
            cur += 1; best = max(best, cur)
        else:
            cur = 0
    return best
print(f"\n4) أطول سلسلة ربح: {longest(pnls,1)}  |  "
      f"أطول سلسلة خسارة: {longest(pnls,-1)}")

# 5) التوزيع اليومي (هل الربح ثابت أم يوم واحد غطّى؟)
print(f"\n5) الأداء اليومي:")
daily = {}
for p, w, ts, a, s in rows:
    day = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%m-%d")
    d = daily.setdefault(day, [0, 0.0]); d[0] += 1; d[1] += (p or 0)
pos_days = sum(1 for v in daily.values() if v[1] > 0)
for day in sorted(daily):
    nn, pp = daily[day]
    bar = "+" if pp > 0 else "-"
    print(f"   {day}: صفقات={nn:<4} PnL={pp:+8.2f} {bar}")
print(f"   أيام رابحة: {pos_days}/{len(daily)}  "
      f"({'متّسق ✅' if pos_days/len(daily)>=0.5 else 'متقلّب ⚠️'})")

# 6) الاتساق عبر الستراتيجيات
print(f"\n6) الاتساق عبر الستراتيجيات:")
strat = {}
for p, w, ts, a, s in rows:
    d = strat.setdefault(s or "?", [0, 0.0]); d[0] += 1; d[1] += (p or 0)
profitable = sum(1 for v in strat.values() if v[1] > 0)
for s in sorted(strat, key=lambda k: strat[k][1]):
    nn, pp = strat[s]
    print(f"   {str(s):<15} صفقات={nn:<4} PnL={pp:+8.2f}")
print(f"   ستراتيجيات رابحة: {profitable}/{len(strat)}")

# الخلاصة
print("\n" + "=" * 58)
print("الخلاصة:")
verdict = []
verdict.append("PF موجب" if pf >= 1.0 else "PF سالب (خاسر)")
verdict.append("Expectancy موجب" if expectancy > 0 else "Expectancy سالب")
verdict.append(f"R:R {rr:.2f}")
print("  " + "  |  ".join(verdict))
if pf < 1.0:
    print("  ⚠️ النظام لا يزال خاسراً. المشكلة الأساسية: R:R المقلوب.")
    print("     (تربح كثيراً لكن صغيراً، وتخسر قليلاً لكن كبيراً)")
print("=" * 58)
con.close()
