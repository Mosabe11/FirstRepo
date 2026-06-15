#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
translate_exam.py  —  Robust, resumable exam-question translator.

Default: Arabic -> Dutch  (Nederlands vakbekwaamheid / CCV truck exam)
Adds an "nl" field next to the existing "ar" field for question / options / explanation.
Keeps the original JSON structure 100% intact.

KEY PROPERTIES
  - Resumable: checkpoints after EVERY question. Kill it / lose connection -> just rerun, it continues.
  - Atomic writes: never corrupts the output file even if killed mid-save.
  - Validated: every translation is structurally checked (option count, no empty fields,
    no leftover Arabic). Bad ones are retried; if still bad they are left untranslated and
    listed, so a later run picks them up. One bad item never halts the run.
  - Model-agnostic: any OpenAI-compatible endpoint (DeepSeek, OpenAI, OpenRouter -> Claude...).
  - Glossary-pinned: official Dutch terms are enforced for consistency.

USAGE
  pip install openai
  export LLM_API_KEY="sk-...your DeepSeek key..."
  python3 translate_exam.py                 # translate (resumes automatically)
  python3 translate_exam.py --verify        # scan finished file, flag anything to review
  python3 translate_exam.py --retry-failed  # re-attempt only the items that failed before

  Run it so it survives a dropped SSH/Termius session:
  nohup python3 translate_exam.py > translate.log 2>&1 &
  tail -f translate.log
"""

import os, sys, json, time, re, argparse, tempfile

# ----------------------------------------------------------------------------
# CONFIG  (edit these constants OR override with env vars)
# ----------------------------------------------------------------------------
INPUT_FILE  = os.environ.get("INPUT_FILE",  "vrachtwagen.json")
OUTPUT_FILE = os.environ.get("OUTPUT_FILE", "vrachtwagen_nl.json")
FAIL_FILE   = os.environ.get("FAIL_FILE",   "translate_failed.json")

SOURCE_LANG = "Arabic"
TARGET_LANG = "Dutch (Nederlands)"

# --- Model / endpoint. Default = DeepSeek. ---
API_KEY  = os.environ.get("LLM_API_KEY") or os.environ.get("DEEPSEEK_API_KEY", "")
BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com")
MODEL    = os.environ.get("LLM_MODEL", "deepseek-chat")   # -> DeepSeek V4-Flash
# To use Claude instead (higher quality, ~25x cost, still cheap):
#   BASE_URL = "https://openrouter.ai/api/v1"
#   MODEL    = "anthropic/claude-sonnet-4.6"
#   LLM_API_KEY = your OpenRouter key
# To use OpenAI:
#   BASE_URL = "https://api.openai.com/v1" ; MODEL = "gpt-5.5"

TEMPERATURE = 0.15      # low = consistent terminology
MAX_RETRIES = 4         # per-item retries on API error or invalid output
SAVE_EVERY  = 1         # checkpoint frequency (1 = after every question; safest)

# ----------------------------------------------------------------------------
# GLOSSARY  —  official Dutch terms that MUST be used verbatim.
# ----------------------------------------------------------------------------
GLOSSARY = """
mobiele werknemer; werknemer; werkgever; eigen rijder; charter; eigen vervoer;
NIWO; kredietwaardigheid; vakbekwaamheid; betrouwbaarheid;
rembours; cross-docking; cabotage; Value Added Logistics; distributievervoer;
FNV; CNV; TLN; VERN; EVO;
maximum massa; massa rijklaar; laadvermogen;
AETR; EG-verordening; ATW; EG 561/2006; rijtijd; arbeidstijd; rusttijd;
dagelijkse rusttijd; weekrust; verkorte weekrust;
bestuurderskaart; tachograaf; retarder; uitlaatrem; promille; beginnende bestuurder;
fictieve aansprakelijkheid; bemamitoe (Bevelen, Maatregelen, Middelen, Toezicht, Controle);
manco; overbevinding; ATP-certificaat; koelwagen; Carnet-TIR; douanezegel; inklaring;
oplegger; bijrijder; jeugdige werknemer; ADR; samengeperste gassen;
AVC 2002; CMR-vrachtbrief; cognossement; Maut; AdBlue; MRN; ILT;
schadeformulier; Wet wegvervoer goederen (WWg)
""".strip()

SYSTEM_PROMPT = f"""You are a professional translator specialising in Dutch road-transport law and the Dutch \
professional truck-driver theory exam (vakbekwaamheid / CCV). Translate exam content from {SOURCE_LANG} into {TARGET_LANG}.

RULES (follow exactly):
- Translate MEANING into natural, correct, formal Dutch — never word-for-word.
- Use the OFFICIAL Dutch terminology below verbatim. Do not invent or literally translate these terms:
{GLOSSARY}
- The {SOURCE_LANG} explanations refer to the source document as "الملف". Always render this as "Het lesmateriaal".
- Keep every number, unit, percentage, article reference and abbreviation exactly as in the source.
- Translate ALL answer options. Keep the SAME number of options and the SAME order as the input.
- Output NO {SOURCE_LANG} text. No transliteration. No commentary.
- Return ONLY a strict JSON object, nothing else, with exactly these keys:
  {{"question": "<string>", "options": ["<string>", ...], "explanation": "<string>"}}
"""

# Few-shot anchors (define the expected style/quality).
FEWSHOT = [
    {
        "role": "user",
        "content": json.dumps({
            "question": "في أي من الحالات التالية يعتبر السائق \"عامل متنقل\" (Mobiele werknemer) وفقًا للقانون الهولندي؟",
            "options": [
                "سائق شاحنة يقوم بالتوصيل داخل مدينة أمستردام فقط",
                "سائق شاحنة يقود مركبة وزنها الإجمالي 600 كجم لنقل الأثاث الخاص",
                "سائق شاحنة يعمل كموظف لدى شركة نقل، وجزء أساسي من عمله هو قيادة المركبات في النقل البري",
                "سائق شاحنة يعمل لحسابه الخاص (Eigen rijder)"
            ],
            "explanation": "الملف يعرّف «العامل المتنقل» بأنه الموظف الذي يتكوّن عمله بشكل رئيسي من قيادة المركبات في النقل البري."
        }, ensure_ascii=False)
    },
    {
        "role": "assistant",
        "content": json.dumps({
            "question": "In welke van de volgende gevallen wordt de chauffeur volgens de Nederlandse wet beschouwd als 'mobiele werknemer'?",
            "options": [
                "Een vrachtwagenchauffeur die alleen binnen de stad Amsterdam bezorgt",
                "Een vrachtwagenchauffeur die een voertuig met een totaalgewicht van 600 kg bestuurt voor het vervoer van eigen meubels",
                "Een vrachtwagenchauffeur die in dienst is bij een transportbedrijf en wiens werk voor een wezenlijk deel bestaat uit het besturen van voertuigen in het wegvervoer",
                "Een vrachtwagenchauffeur die als zelfstandige werkt (eigen rijder)"
            ],
            "explanation": "Het lesmateriaal definieert de 'mobiele werknemer' als de werknemer wiens werk hoofdzakelijk bestaat uit het besturen van voertuigen in het wegvervoer."
        }, ensure_ascii=False)
    },
    {
        "role": "user",
        "content": json.dumps({
            "question": "شاحنة كتلتها القصوى المسموح بها (Maximum Massa) هي 40 طنًا، ووزنها فارغة (Massa rijklaar) هو 15 طنًا. ما هي الحمولة القصوى (laadvermogen)؟",
            "options": ["55 طن", "40 طن", "25 طن", "15 طن"],
            "explanation": "F2 - G = الحمولة القصوى. إذن 40 - 15 = 25 طن."
        }, ensure_ascii=False)
    },
    {
        "role": "assistant",
        "content": json.dumps({
            "question": "Een vrachtwagen heeft een maximum massa van 40 ton en een massa rijklaar van 15 ton. Wat is het maximale laadvermogen?",
            "options": ["55 ton", "40 ton", "25 ton", "15 ton"],
            "explanation": "F2 - G = het maximale laadvermogen. Dus 40 - 15 = 25 ton."
        }, ensure_ascii=False)
    },
]

ARABIC_RE = re.compile(r'[\u0600-\u06FF]')

# ----------------------------------------------------------------------------
def get_client():
    try:
        from openai import OpenAI
    except ImportError:
        sys.exit("Missing dependency. Run:  pip install openai")
    if not API_KEY:
        sys.exit("No API key. Run:  export LLM_API_KEY='your-deepseek-key'")
    return OpenAI(api_key=API_KEY, base_url=BASE_URL)


def atomic_save(data, path):
    d = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def needs_translation(item):
    q = item.get("question", {})
    o = item.get("options", {})
    e = item.get("explanation", {})
    if "nl" not in q or "nl" not in o or "nl" not in e:
        return True
    if not q.get("nl") or not e.get("nl"):
        return True
    if len(o.get("nl", [])) != len(o.get("ar", [])):
        return True
    return False


def validate(out, n_options):
    if not isinstance(out, dict):
        return "not a dict"
    if not isinstance(out.get("question"), str) or not out["question"].strip():
        return "missing/empty question"
    if not isinstance(out.get("explanation"), str) or not out["explanation"].strip():
        return "missing/empty explanation"
    opts = out.get("options")
    if not isinstance(opts, list) or len(opts) != n_options:
        return f"option count mismatch (got {len(opts) if isinstance(opts,list) else 'n/a'}, need {n_options})"
    for o in opts:
        if not isinstance(o, str) or not o.strip():
            return "empty option"
    blob = out["question"] + " " + out["explanation"] + " " + " ".join(opts)
    if ARABIC_RE.search(blob):
        return "leftover Arabic text"
    return None


def translate_item(client, item):
    payload = {
        "question": item["question"].get("ar", ""),
        "options":  item["options"].get("ar", []),
        "explanation": item["explanation"].get("ar", ""),
    }
    n_options = len(payload["options"])
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + FEWSHOT + [
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}
    ]
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = client.chat.completions.create(
                model=MODEL,
                messages=messages,
                temperature=TEMPERATURE,
                response_format={"type": "json_object"},
                max_tokens=2000,
            )
            text = resp.choices[0].message.content.strip()
            text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
            out = json.loads(text)
            err = validate(out, n_options)
            if err is None:
                return out, None
            last_err = err
            messages.append({"role": "assistant", "content": text})
            messages.append({"role": "user",
                "content": f"That output was invalid: {err}. Re-output ONLY corrected JSON "
                           f"with exactly {n_options} options, no Arabic."})
        except Exception as e:
            last_err = str(e)
            time.sleep(min(2 ** attempt, 30))  # backoff on rate-limit / network
    return None, last_err


# ----------------------------------------------------------------------------
def run_translate(only_failed=False):
    src = json.load(open(INPUT_FILE, encoding="utf-8"))
    if os.path.exists(OUTPUT_FILE):
        data = json.load(open(OUTPUT_FILE, encoding="utf-8"))
        print(f"Resuming from {OUTPUT_FILE}")
    else:
        data = json.loads(json.dumps(src))  # deep copy
        print(f"Starting fresh from {INPUT_FILE}")

    by_id = {d["id"]: d for d in data}
    client = get_client()

    todo = [d for d in data if needs_translation(d)]
    total = len(data)
    print(f"{total - len(todo)}/{total} already done. {len(todo)} to translate.\n")

    failed, done_now, t0 = [], 0, time.time()
    for i, item in enumerate(todo, 1):
        out, err = translate_item(client, item)
        tgt = by_id[item["id"]]
        if out:
            tgt["question"]["nl"]    = out["question"]
            tgt["options"]["nl"]     = out["options"]
            tgt["explanation"]["nl"] = out["explanation"]
            done_now += 1
        else:
            failed.append({"id": item["id"], "error": err})
            print(f"  ! id {item['id']} FAILED after {MAX_RETRIES} tries: {err}")

        if i % SAVE_EVERY == 0 or i == len(todo):
            atomic_save(data, OUTPUT_FILE)

        if i % 10 == 0 or i == len(todo):
            rate = (time.time() - t0) / i
            eta = rate * (len(todo) - i)
            total_done = sum(1 for d in data if not needs_translation(d))
            print(f"  {total_done}/{total} done | this run {i}/{len(todo)} | "
                  f"~{rate:.1f}s/item | ETA {eta/60:.1f} min")

    atomic_save(data, OUTPUT_FILE)
    if failed:
        json.dump(failed, open(FAIL_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"\nFinished with {len(failed)} failures -> {FAIL_FILE}. "
              f"Rerun the script to retry them.")
    else:
        print(f"\nAll {total} questions translated cleanly -> {OUTPUT_FILE}")


def run_verify():
    """Scan the finished file and flag anything a human should eyeball."""
    if not os.path.exists(OUTPUT_FILE):
        sys.exit(f"{OUTPUT_FILE} not found. Run the translation first.")
    data = json.load(open(OUTPUT_FILE, encoding="utf-8"))
    issues = []
    for d in data:
        i = d.get("id")
        q, o, e = d.get("question", {}), d.get("options", {}), d.get("explanation", {})
        nl_opts = o.get("nl", [])

        # --- HANDOFF hard requirements (app validator rejects these) ---
        if not isinstance(i, int):
            issues.append((i, "id missing or not an integer"))
        if not d.get("category", {}).get("nl"):
            issues.append((i, "missing category.nl (required)"))
        if not str(q.get("nl", "")).strip():
            issues.append((i, "missing Dutch question (required)"))
        if not isinstance(nl_opts, list) or len(nl_opts) != 4:
            issues.append((i, f"options.nl must be EXACTLY 4 (got {len(nl_opts) if isinstance(nl_opts,list) else 'n/a'})"))
        elif any(not str(x).strip() for x in nl_opts):
            issues.append((i, "an option.nl is empty (no blanks allowed)"))
        c = d.get("correct")
        if not isinstance(c, int) or c < 0 or c > 3:
            issues.append((i, f"correct must be 0-based index 0-3 (got {c!r})"))

        # --- translation quality flags ---
        if needs_translation(d):
            issues.append((i, "not translated / incomplete")); continue
        blob = (q.get("nl", "") + " " + e.get("nl", "") + " " + " ".join(nl_opts))
        if ARABIC_RE.search(blob):
            issues.append((i, "leftover Arabic in Dutch fields"))
        if not str(e.get("nl", "")).strip():
            issues.append((i, "missing Dutch explanation (recommended)"))
        elif len(e.get("ar", "")) > 60 and len(e.get("nl", "")) < 25:
            issues.append((i, "explanation suspiciously short — check for dropped content"))
    total = len(data)
    ok = total - len({i for i, _ in issues})
    print(f"Verified {total} questions. Clean: {ok}. Flagged: {len(issues)}.")
    for i, msg in issues:
        print(f"  - id {i}: {msg}")
    if not issues:
        print("No issues found. File is clean.")


# ----------------------------------------------------------------------------
if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", action="store_true", help="scan finished file for issues")
    ap.add_argument("--retry-failed", action="store_true", help="retry previously failed items")
    args = ap.parse_args()
    if args.verify:
        run_verify()
    else:
        run_translate(only_failed=args.retry_failed)
