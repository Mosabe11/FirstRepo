
import sqlite3
import json
import os
import sys
from datetime import datetime, timedelta

DB_PATH = os.environ.get("AGENTEDGE_DB", "data/memory.db")
OUT_JSON = "agent_report.json"


def hr(title=""):
    line = "=" * 64
    if title:
        pad = (64 - len(title) - 2) // 2
        print(f"\n{'=' * pad} {title} {'=' * pad}")
    else:
        print(line)


def connect():
    if not os.path.exists(DB_PATH):
        print(f"❌ لم يتم العثور على قاعدة البيانات: {DB_PATH}")
        print("   تأكد أنك داخل مجلد المشروع agentedge_v2/")
        sys.exit(1)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def table_columns(con, table):
    try:
        rows = con.execute(f"PRAGMA table_info({table})").fetchall()
        return [r["name"] for r in rows]
    except sqlite3.Error:
        return []


def pick(cols, *candidates):
    """ارجع أول عمود موجود من قائمة الأسماء المحتملة."""
    for c in candidates:
        if c in cols:
            return c
    return None


def pct(part, whole):
    return round(100.0 * part / whole, 1) if whole else 0.0


def fnum(x):
    try:
        return round(float(x), 2)
    except (TypeError, ValueError):
        return 0.0


def main():
    con = connect()

    # --- اكتشف الجداول ---
    tables = [r["name"] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    print("الجداول الموجودة:", ", ".join(tables) if tables else "(لا يوجد)")

    if "trades" not in tables:
        print("❌ لا يوجد جدول 'trades'. الجداول المتاحة أعلاه.")
        sys.exit(1)

    cols = table_columns(con, "trades")
    print("أعمدة جدول trades:", ", ".join(cols))

    # --- حدّد الأعمدة المرنة ---
    c_win      = pick(cols, "win", "is_win", "won")
    c_pnl      = pick(cols, "pnl", "profit", "realized_pnl", "pnl_eur")
    c_closed   = pick(cols, "closed_at", "close_time", "closed_ts", "exit_time")
    c_opened   = pick(cols, "opened_at", "open_time", "created_at", "entry_time")
    c_strategy = pick(cols, "strategy", "strategy_name", "source")
    c_class    = pick(cols, "asset_class", "class", "market")
    c_asset    = pick(cols, "asset", "symbol", "pair")
    c_reason   = pick(cols, "close_reason", "reason", "exit_reason")
    c_veto     = pick(cols, "ai_veto", "vetoed", "ai_approved", "ai_decision")

    missing = [n for n, v in {
        "win": c_win, "pnl": c_pnl, "closed_at": c_closed}.items() if not v]
    if missing:
        print(f"⚠️  أعمدة أساسية ناقصة: {missing} — بعض الأقسام قد تكون فارغة.")

    where_closed = f"WHERE {c_closed} IS NOT NULL" if c_closed else ""

    report = {"generated_at": datetime.now().isoformat() + "Z",
              "db_path": DB_PATH}

    # ===== 1. الإحصائيات الإجمالية =====
    hr("OVERALL")
    total = con.execute(
        f"SELECT COUNT(*) n FROM trades {where_closed}").fetchone()["n"]
    wins = con.execute(
        f"SELECT COUNT(*) n FROM trades {where_closed} "
        f"{'AND' if where_closed else 'WHERE'} {c_win}=1").fetchone()["n"] if c_win else 0
    pnl_total = con.execute(
        f"SELECT COALESCE(SUM({c_pnl}),0) s FROM trades {where_closed}"
    ).fetchone()["s"] if c_pnl else 0.0

    wr = pct(wins, total)
    avg = fnum(pnl_total / total) if total else 0.0
    overall = {"total_closed": total, "wins": wins, "win_rate": wr,
               "total_pnl": fnum(pnl_total), "avg_pnl_per_trade": avg}
    report["overall"] = overall
    print(f"إجمالي الصفقات المغلقة : {total}")
    print(f"رابحة                  : {wins}")
    print(f"نسبة الربح (WR)        : {wr}%")
    print(f"صافي PnL               : {fnum(pnl_total)}")
    print(f"متوسط PnL لكل صفقة     : {avg}")

    # دالة مساعدة للتجميع حسب عمود
    def group_by(col, label):
        if not col or not c_pnl:
            return []
        win_expr = f"SUM({c_win}=1)" if c_win else "0"
        rows = con.execute(
            f"SELECT {col} k, COUNT(*) n, {win_expr} w, "
            f"COALESCE(SUM({c_pnl}),0) p "
            f"FROM trades {where_closed} GROUP BY {col} ORDER BY p ASC"
        ).fetchall()
        out = []
        hr(label)
        print(f"{'الفئة':<18}{'صفقات':>7}{'WR%':>7}{'PnL':>11}")
        print("-" * 44)
        for r in rows:
            k = r["k"] if r["k"] is not None else "(none)"
            n, w, p = r["n"], r["w"], fnum(r["p"])
            w_rate = pct(w, n)
            print(f"{str(k):<18}{n:>7}{w_rate:>7}{p:>11}")
            out.append({"key": str(k), "trades": n,
                        "win_rate": w_rate, "pnl": p})
        return out

    report["by_strategy"]   = group_by(c_strategy, "BY STRATEGY")
    report["by_asset_class"] = group_by(c_class, "BY ASSET CLASS")
    report["by_close_reason"] = group_by(c_reason, "BY CLOSE REASON")

    # أفضل/أسوأ أصل
    if c_asset and c_pnl:
        report["by_asset"] = group_by(c_asset, "BY ASSET")

    # ===== AI VETO ANALYSIS =====
    if c_veto:
        hr("AI VETO")
        rows = con.execute(
            f"SELECT {c_veto} k, COUNT(*) n, "
            f"{'SUM('+c_win+'=1)' if c_win else '0'} w, "
            f"COALESCE(SUM({c_pnl}),0) p FROM trades {where_closed} "
            f"GROUP BY {c_veto}"
        ).fetchall()
        veto_out = []
        for r in rows:
            n, w, p = r["n"], r["w"], fnum(r["p"])
            print(f"veto={r['k']}: صفقات={n} WR={pct(w,n)}% PnL={p}")
            veto_out.append({"veto_value": str(r["k"]), "trades": n,
                             "win_rate": pct(w, n), "pnl": p})
        report["ai_veto"] = veto_out
    else:
        print("\n(ملاحظة: لا يوجد عمود يسجّل قرار AI Veto في جدول trades — "
              "تتبّع الرفض يتم عبر Telegram/اللوغ فقط.)")

    # ===== آخر 7 أيام مقابل ما قبلها (لقياس أثر التحديثات) =====
    if c_closed and c_pnl:
        hr("LAST 7 DAYS vs BEFORE")
        cutoff = (datetime.now() - timedelta(days=7)).isoformat()
        for label, op in [("آخر 7 أيام", ">="), ("قبل ذلك", "<")]:
            try:
                r = con.execute(
                    f"SELECT COUNT(*) n, "
                    f"{'SUM('+c_win+'=1)' if c_win else '0'} w, "
                    f"COALESCE(SUM({c_pnl}),0) p FROM trades "
                    f"WHERE {c_closed} IS NOT NULL AND {c_closed} {op} ?",
                    (cutoff,)
                ).fetchone()
                n, w, p = r["n"], r["w"], fnum(r["p"])
                print(f"{label:<12}: صفقات={n:>4} WR={pct(w,n):>5}% PnL={p}")
                report.setdefault("time_window", {})[label] = {
                    "trades": n, "win_rate": pct(w, n), "pnl": p}
            except sqlite3.Error as e:
                print(f"{label}: تعذّر التحليل الزمني ({e}) — "
                      f"قد يكون {c_closed} رقمياً (timestamp) وليس نصاً.")

    # ===== حفظ JSON =====
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    hr()
    print(f"✅ تم حفظ التقرير الكامل في: {OUT_JSON}")
    print("   انسخ محتواه ولصّقه هنا لأحلّله معك بالتفصيل.")

    con.close()



if __name__ == "__main__":
    main()
