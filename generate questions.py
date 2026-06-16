#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generate_questions.py  —  Turn a PDF into grounded multiple-choice questions.

PIPELINE (per page):
  render page -> image  ->  [GEN model] grounded MCQs in the source language
   -> structural validate  ->  [CRIT model, a DIFFERENT model] verify grounding
   -> dedup  ->  checkpoint.
Output is in the SOURCE language with each question carrying its source sentence
(_source) and a review verdict (_review). It chains straight into translate_exam.py,
which adds the Dutch (nl) translation.

WHY TWO MODELS: an LLM checking its own output shares its blind spots. A different
model for the critique pass surfaces the bad questions instead of rubber-stamping them.

WHY VISION: this source is Arabic (RTL) with embedded Dutch terms; raw text
extraction jumbles it. Reading the rendered page image is far more reliable.

SETUP
  pip install openai pymupdf
  export OPENROUTER_API_KEY="sk-or-..."      # one key, two models via OpenRouter
  # (or set GEN_*/CRIT_* separately to mix providers)

RUN
  python3 generate_questions.py --pdf Module1.pdf --pages 11-20    # test a range first
  python3 generate_questions.py --pdf Module1.pdf                  # whole document
  python3 generate_questions.py --review                          # print the flagged queue

Resumable: checkpoints after every page. Re-run to continue; --pages can re-do a range.
Then:  translate to Dutch with the existing script:
  INPUT_FILE=module1_questions.json OUTPUT_FILE=module1_nl.json python3 translate_exam.py
"""

import os, sys, json, time, re, base64, argparse, tempfile, difflib

# ----------------------------------------------------------------------------
# CONFIG
# ----------------------------------------------------------------------------
OUT_FILE    = os.environ.get("OUT_FILE",   "module1_questions.json")
STATE_FILE  = os.environ.get("STATE_FILE", "generate_state.json")
REVIEW_FILE = os.environ.get("REVIEW_FILE","review_queue.json")

SOURCE_LANG = os.environ.get("SOURCE_LANG", "Arabic")
CATEGORY    = {"nl": os.environ.get("CATEGORY_NL", "Vrachtwagen"),
               "ar": os.environ.get("CATEGORY_AR", "شاحنة")}
Q_PER_PAGE  = int(os.environ.get("Q_PER_PAGE", "4"))   # max questions per page
DPI         = int(os.environ.get("DPI", "150"))
TEMPERATURE = 0.2
MAX_RETRIES = 4
DEDUP_RATIO = 0.86   # drop near-duplicate questions above this similarity

# --- Models: GEN must be VISION-capable; CRIT should be a DIFFERENT model. ---
# --- Models. Defaults: DeepSeek for everything (one key). GEN must be vision-capable. ---
_DS = "https://api.deepseek.com"
_KEY = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("LLM_API_KEY", "")
GEN_BASE_URL  = os.environ.get("GEN_BASE_URL",  _DS)
GEN_API_KEY   = os.environ.get("GEN_API_KEY",   _KEY)
GEN_MODEL     = os.environ.get("GEN_MODEL",  "deepseek-chat")   # DeepSeek V4 (multimodal)
CRIT_BASE_URL = os.environ.get("CRIT_BASE_URL", _DS)
CRIT_API_KEY  = os.environ.get("CRIT_API_KEY",  _KEY)
CRIT_MODEL    = os.environ.get("CRIT_MODEL", "deepseek-chat")   # same model to start;
# OPTIONAL UPGRADE: for stronger error-catching, point CRIT at a DIFFERENT model later, e.g.:
#   export CRIT_BASE_URL="https://openrouter.ai/api/v1"
#   export CRIT_MODEL="openai/gpt-4o-mini"   export CRIT_API_KEY="your-openrouter-key"

# INPUT MODE: "text" (default) feeds the page's extracted text — works on DeepSeek (text-only API).
# "vision" sends the page image instead — only use with a vision-capable model (e.g. via OpenRouter).
USE_VISION = os.environ.get("USE_VISION", "0").lower() in ("1", "true", "yes")
TEXT_CAP = 8000

ARABIC_RE = re.compile(r'[\u0600-\u06FF]')

# ----------------------------------------------------------------------------
# PROMPTS
# ----------------------------------------------------------------------------
GEN_SYSTEM = f"""You write exam-quality multiple-choice questions from a single page of study material.
The page is for the Dutch professional truck-driver theory exam (vakbekwaamheid / CBR). Its language is {SOURCE_LANG}.

STRICT RULES:
- Write questions ONLY about facts that are actually visible on this page. Do NOT use outside knowledge.
- Write in {SOURCE_LANG}. Keep official Dutch terms (e.g. Snelweg, invoegstrook, Spoorvorming, RVV) verbatim.
- Each question: exactly 4 options, ONE correct, three plausible (not absurd) distractors.
- Test understanding, not trivia (no questions about page numbers, headers, or image captions).
- For EACH question include "source": the exact sentence/phrase from the page that proves the answer.
- If the page has no testable content (title page, photo only), return an empty list [].
- Output ONLY a JSON object: {{"questions": [ {{"question": "...", "options": ["..","..","..",".."],
  "correct": <0-3>, "explanation": "...", "source": "<exact text from page>"}} ]}}.
Produce at most {Q_PER_PAGE} questions; fewer is fine if the page is thin."""

CRIT_SYSTEM = f"""You are an independent reviewer checking a multiple-choice question against the page it was made from.
Judge ONLY whether the question is sound and supported by THIS page. Do not invent content.

Check: (1) is the marked-correct answer actually supported by the page? (2) is there exactly ONE correct option?
(3) are the distractors plausible but wrong? (4) is the question answerable from the page alone?
Also note if the page's own statement looks outdated or like an oversimplification (flag it; do not silently pass).

Output ONLY JSON: {{"verdict": "PASS" | "FLAG", "confidence": 0.0-1.0, "reason": "<short>"}}.
PASS only if all checks hold. Otherwise FLAG with a one-line reason."""


# ----------------------------------------------------------------------------
def get_client(base_url, key, what):
    try:
        from openai import OpenAI
    except ImportError:
        sys.exit("Missing dependency. Run:  pip install openai pymupdf")
    if not key:
        sys.exit(f"No API key for {what}. Set OPENROUTER_API_KEY (or {what}_API_KEY).")
    return OpenAI(api_key=key, base_url=base_url)


def render_page(doc, idx, want_image):
    """Return (base64 JPEG or None, extracted text) for a PDF page."""
    import base64 as _b64
    page = doc[idx]
    text = page.get_text("text") or ""
    b64 = None
    if want_image:
        pix = page.get_pixmap(dpi=DPI)
        b64 = _b64.b64encode(pix.tobytes("jpeg")).decode()
    return b64, text


def _chat_json(client, model, system, user_content, retries=MAX_RETRIES):
    """Call an OpenAI-compatible chat endpoint and parse a JSON object from the reply."""
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": user_content}]
    last = None
    for attempt in range(1, retries + 1):
        try:
            resp = client.chat.completions.create(
                model=model, messages=messages, temperature=TEMPERATURE,
                response_format={"type": "json_object"}, max_tokens=3000)
            txt = resp.choices[0].message.content.strip()
            txt = re.sub(r"^```(?:json)?|```$", "", txt, flags=re.MULTILINE).strip()
            return json.loads(txt)
        except Exception as e:
            last = str(e)
            time.sleep(min(2 ** attempt, 30))
    return {"_error": last}


def validate_q(q):
    if not isinstance(q, dict):
        return "not a dict"
    if not str(q.get("question", "")).strip():
        return "empty question"
    opts = q.get("options")
    if not isinstance(opts, list) or len(opts) != 4:
        return "options must be exactly 4"
    if any(not str(o).strip() for o in opts):
        return "empty option"
    if not isinstance(q.get("correct"), int) or not 0 <= q["correct"] <= 3:
        return "correct must be 0-3"
    if not str(q.get("source", "")).strip():
        return "missing source grounding"
    return None


def generate_page(gen, b64, text, page_no):
    instr = (f"This is page {page_no} of the study material. Use ONLY what is on this page.\n"
             f"Page text:\n\"\"\"{text[:TEXT_CAP]}\"\"\"\nGenerate the questions now.")
    if USE_VISION and b64:
        user = [{"type": "text", "text": instr},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}]
    else:
        user = instr
    out = _chat_json(gen, GEN_MODEL, GEN_SYSTEM, user)
    if "_error" in out:
        return [], out["_error"]
    qs = out.get("questions", []) if isinstance(out, dict) else []
    good = []
    for q in qs:
        if validate_q(q) is None:
            good.append(q)
    return good, None


def critique_q(crit, b64, text, q):
    payload = json.dumps({"question": q["question"], "options": q["options"],
                          "correct": q["correct"], "source": q.get("source", "")},
                         ensure_ascii=False)
    if USE_VISION and b64:
        user = [{"type": "text", "text": "Review this question against the page.\n" + payload},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}]
    else:
        user = (f"Review this question against the page below.\nPage text:\n"
                f"\"\"\"{text[:TEXT_CAP]}\"\"\"\n{payload}")
    out = _chat_json(crit, CRIT_MODEL, CRIT_SYSTEM, user, retries=3)
    if "_error" in out:
        return {"verdict": "FLAG", "confidence": 0.0, "reason": "critique call failed: " + out["_error"]}
    v = str(out.get("verdict", "FLAG")).upper()
    return {"verdict": "PASS" if v == "PASS" else "FLAG",
            "confidence": out.get("confidence", 0.0),
            "reason": str(out.get("reason", ""))[:200]}


def _norm(s):
    return re.sub(r"\s+", " ", str(s).strip().lower())


def is_duplicate(q_text, seen_norm):
    qn = _norm(q_text)
    return any(difflib.SequenceMatcher(None, qn, s).ratio() >= DEDUP_RATIO for s in seen_norm)


def atomic_save(obj, path):
    d = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


# ----------------------------------------------------------------------------
def run(pdf_path, page_spec):
    import fitz
    doc = fitz.open(pdf_path)
    n_pages = len(doc)

    # page range
    if page_spec:
        a, _, b = page_spec.partition("-")
        lo = int(a) - 1
        hi = (int(b) if b else int(a))
    else:
        lo, hi = 0, n_pages
    pages = list(range(max(0, lo), min(n_pages, hi)))

    # resume state
    if os.path.exists(OUT_FILE):
        questions = json.load(open(OUT_FILE, encoding="utf-8"))
    else:
        questions = []
    if os.path.exists(STATE_FILE):
        state = json.load(open(STATE_FILE, encoding="utf-8"))
    else:
        state = {"done_pages": [], "next_id": 1}
    done = set(state["done_pages"])

    gen  = get_client(GEN_BASE_URL,  GEN_API_KEY,  "GEN")
    crit = get_client(CRIT_BASE_URL, CRIT_API_KEY, "CRIT")

    lang = SOURCE_LANG[:2].lower()
    seen = [_norm(q["question"].get(lang, "")) for q in questions]

    todo = [p for p in pages if p not in done]
    print(f"PDF: {n_pages} pages. Target {len(pages)} pages, {len(todo)} not yet done.\n")

    t0 = time.time()
    for i, idx in enumerate(todo, 1):
        b64, text = render_page(doc, idx, USE_VISION)
        gen_qs, err = generate_page(gen, b64, text, idx + 1)
        if err:
            print(f"  page {idx+1}: generation error ({err}); will retry on next run")
            continue
        kept = 0
        for q in gen_qs:
            if is_duplicate(q["question"], seen):
                continue
            verdict = critique_q(crit, b64, text, q)
            rec = {
                "id": state["next_id"], "image": None, "category": CATEGORY,
                "question": {lang: q["question"]},
                "options":  {lang: q["options"]},
                "correct":  q["correct"],
                "explanation": {lang: q.get("explanation", "")},
                "_page": idx + 1,
                "_source": q.get("source", ""),
                "_review": f'{verdict["verdict"]} (conf {verdict["confidence"]}) — {verdict["reason"]}',
            }
            questions.append(rec)
            seen.append(_norm(q["question"]))
            state["next_id"] += 1
            kept += 1
        done.add(idx)
        state["done_pages"] = sorted(done)
        atomic_save(questions, OUT_FILE)
        atomic_save(state, STATE_FILE)

        passed = sum(1 for q in questions if q["_review"].startswith("PASS"))
        flagged = len(questions) - passed
        if i % 2 == 0 or i == len(todo):
            rate = (time.time() - t0) / i
            print(f"  page {idx+1}: +{kept} kept | total {len(questions)} "
                  f"({passed} pass / {flagged} flag) | ~{rate:.1f}s/page | "
                  f"ETA {rate*(len(todo)-i)/60:.1f} min")

    # write the review queue (flagged questions only)
    flagged = [q for q in questions if not q["_review"].startswith("PASS")]
    atomic_save(flagged, REVIEW_FILE)
    print(f"\nDone. {len(questions)} questions total -> {OUT_FILE}")
    print(f"{len(flagged)} flagged for human review -> {REVIEW_FILE}")
    print("Next: translate to Dutch with translate_exam.py "
          "(INPUT_FILE/OUTPUT_FILE), then strip _page/_source/_review before going live.")


def run_review():
    if not os.path.exists(REVIEW_FILE):
        sys.exit(f"{REVIEW_FILE} not found. Run generation first.")
    for q in json.load(open(REVIEW_FILE, encoding="utf-8")):
        lang = SOURCE_LANG[:2].lower()
        print(f"--- id {q['id']} (page {q.get('_page')}) ---")
        print("Q:", q["question"].get(lang, ""))
        print("source:", q.get("_source", ""))
        print("review:", q["_review"], "\n")


def run_recritique(pdf_path):
    """Re-run ONLY the critique pass on existing questions, using the CRIT_* model
    (e.g. OpenAI). No regeneration. Re-reads each question's page text for context."""
    import fitz
    if not os.path.exists(OUT_FILE):
        sys.exit(f"{OUT_FILE} not found. Generate first.")
    if not pdf_path:
        sys.exit("--recritique needs --pdf (to re-read the page text for grounding).")
    questions = json.load(open(OUT_FILE, encoding="utf-8"))
    doc = fitz.open(pdf_path)
    crit = get_client(CRIT_BASE_URL, CRIT_API_KEY, "CRIT")
    lang = SOURCE_LANG[:2].lower()
    page_text = {}
    flagged = 0
    print(f"Re-critiquing {len(questions)} questions with {CRIT_MODEL} ...")
    for i, q in enumerate(questions, 1):
        pg = q.get("_page")
        if pg not in page_text:
            page_text[pg] = (doc[pg - 1].get_text("text") if pg else "") or ""
        qd = {"question": q["question"].get(lang, ""),
              "options": q["options"].get(lang, []),
              "correct": q["correct"], "source": q.get("_source", "")}
        v = critique_q(crit, None, page_text[pg], qd)
        q["_review"] = f'{v["verdict"]} (conf {v["confidence"]}) — {v["reason"]}'
        if not q["_review"].startswith("PASS"):
            flagged += 1
        if i % 5 == 0 or i == len(questions):
            atomic_save(questions, OUT_FILE)
            print(f"  {i}/{len(questions)} re-checked | {flagged} flagged so far")
    atomic_save(questions, OUT_FILE)
    atomic_save([q for q in questions if not q["_review"].startswith("PASS")], REVIEW_FILE)
    print(f"\nDone. {flagged}/{len(questions)} now flagged (DeepSeek's self-critique flagged 0).")
    print(f"See them: python3 {os.path.basename(sys.argv[0])} --review")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", help="path to the PDF")
    ap.add_argument("--pages", help="page range, e.g. 11-20 (1-based). Omit = whole PDF")
    ap.add_argument("--review", action="store_true", help="print the flagged-question queue")
    ap.add_argument("--recritique", action="store_true",
                    help="re-run ONLY the critique on existing questions (needs --pdf)")
    args = ap.parse_args()
    if args.review:
        run_review()
    elif args.recritique:
        run_recritique(args.pdf)
    elif args.pdf:
        run(args.pdf, args.pages)
    else:
        ap.print_help()
