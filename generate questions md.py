#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generate_questions_md.py  —  Turn the cleaned Markdown source into grounded Dutch MCQs.

This is the MARKDOWN sibling of generate_questions.py (which reads a PDF page-per-page).
It is built for the MStheory "Auto (rijbewijs B)" module, whose source is the file
auto-b-bron-volledig-2026.md — already corrected to 2026 facts, in Dutch.

PIPELINE (per source section, e.g. "1.5 Snelheid"):
  split markdown into sections
    -> [GEN model]  write grounded Dutch MCQs from THIS section only
    -> structural validate
    -> dedup
    -> [CRIT model, ideally a DIFFERENT model]  verify grounding + 2026 currency
    -> route to human-review queue if the topic is flagged by the source
    -> checkpoint.

KEY DIFFERENCES vs the PDF generator:
  * Source is Markdown, not a PDF  -> no PyMuPDF, no page rendering, no vision.
  * Output language is Dutch DIRECTLY. translate_exam.py is NOT needed for this module.
  * Honours the image markers in the source:
      [TEKST] -> fully usable.
      [BEELD] -> only "name/meaning" questions ("Wat betekent bord B6?"), never
                 "which sign do you see" recognition.
      [DROP]  -> ignore the drawing/photo; only use the rule stated in the text.
  * Currency guard: the critic checks every question against the source's
    "current 2026 facts" list (snelweg 100/130, aanhanger 90 + B/code96/BE,
    beginnend bestuurder 5/7 jr + 0,2/0,5, AM i.p.v. F, kentekencard,
    telefoonverbod fietsers, geldigheid 75+, APK 4-2-2-1-1 / diesel-3-jr,
    milieuzonebord C22e/C22f). A question that contradicts these is FLAGGED.
  * Human-review routing: questions touching the still-unverified topics
    (milieuzone C22e/C22f overgang, exacte ladingmaten, remvertraging/aslast/wieldruk)
    are sent to the review queue regardless of the critic's verdict.

WHY TWO MODELS: an LLM checking its own output shares its blind spots. Point CRIT at a
different model for a real second opinion (see env overrides below).

SETUP
  python3 -m venv venv && source venv/bin/activate
  pip install openai
  export DEEPSEEK_API_KEY="sk-..."        # one key, DeepSeek for gen + crit

  # OPTIONAL stronger critic (cross-model), e.g. OpenAI:
  #   export CRIT_BASE_URL="https://api.openai.com/v1"
  #   export CRIT_MODEL="gpt-5.4-mini"
  #   export CRIT_API_KEY="sk-..."

RUN
  python3 generate_questions_md.py --list                       # show sections, no API
  python3 generate_questions_md.py --check                      # tiny API ping, no generation
  python3 generate_questions_md.py --limit 3 --out _b_test.json # TEST: first 3 sections
  python3 generate_questions_md.py                              # full module
  python3 generate_questions_md.py --review                     # print the flagged queue

Resumable: checkpoints after every section. Re-run to continue.
Before going live: strip the _-prefixed fields (_section/_source/_review/_human_review).
"""

import os, sys, json, time, re, argparse, tempfile, difflib

# ----------------------------------------------------------------------------
# CONFIG
# ----------------------------------------------------------------------------
MD_DEFAULT   = os.environ.get("MD_FILE",     "auto-b-bron-volledig-2026.md")
OUT_FILE     = os.environ.get("OUT_FILE",    "auto_b_questions.json")
STATE_FILE   = os.environ.get("STATE_FILE",  "auto_b_state.json")
REVIEW_FILE  = os.environ.get("REVIEW_FILE", "auto_b_review_queue.json")

LANG          = "nl"                                   # generate Dutch directly
CATEGORY      = {"nl": os.environ.get("CATEGORY_NL", "Auto"),
                 "en": os.environ.get("CATEGORY_EN", "Car")}
Q_PER_SECTION = int(os.environ.get("Q_PER_SECTION", "5"))
TEMPERATURE   = float(os.environ.get("TEMPERATURE", "0.2"))
MAX_RETRIES   = 4
DEDUP_RATIO   = 0.86          # drop near-duplicate questions above this similarity
TEXT_CAP      = 9000          # chars of section text sent to the model

# --- Models. Defaults: DeepSeek for everything (one key). ---
_DS  = "https://api.deepseek.com"
_KEY = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("LLM_API_KEY", "")
GEN_BASE_URL  = os.environ.get("GEN_BASE_URL",  _DS)
GEN_API_KEY   = os.environ.get("GEN_API_KEY",   _KEY)
GEN_MODEL     = os.environ.get("GEN_MODEL",  "deepseek-chat")
CRIT_BASE_URL = os.environ.get("CRIT_BASE_URL", _DS)
CRIT_API_KEY  = os.environ.get("CRIT_API_KEY",  _KEY)
CRIT_MODEL    = os.environ.get("CRIT_MODEL", "deepseek-chat")

# ----------------------------------------------------------------------------
# CURRENT 2026 FACTS  — the critic enforces these; a question that contradicts
# any of them is FLAGGED. Lifted from the ⚠️ corrections in the source.
# ----------------------------------------------------------------------------
CORRECTIONS = """\
- Autosnelweg: overdag (06:00-19:00) 100 km/u op vrijwel alle snelwegen (sinds 16-3-2020);
  wettelijk maximum 130 km/u; 's avonds/'s nachts 120/130 waar het bord dat toestaat.
  Strikvraag: "wettelijke maximumsnelheid" = 130; "maximumsnelheid overdag" = 100.
  (3 trajecten overdag 130 sinds 14-4-2025.)
- Met aanhanger/caravan: max 90 km/u op de snelweg, 80 km/u op autoweg/buitenweg.
- Aanhanger-rijbewijs: B = aanhanger TMM <= 750 kg OF combinatie <= 3.500 kg;
  code 96 (B+) = combinatie tot 4.250 kg (rijvaardigheidstest, GEEN examen);
  BE = combinatie boven 4.250 kg.
- Beginnend bestuurder: max 0,2 promille (88 ug/l), duur 5 jaar (rijbewijs vanaf 18)
  of 7 jaar (rijbewijs voor 18). Ervaren bestuurder: max 0,5 promille (220 ug/l).
  NIET "bromfietser jonger dan 24 jaar".
- Telefoon: het VASTHOUDEN van een mobiel apparaat is verboden voor ALLE bestuurders,
  OOK fietsers (sinds 1-7-2019).
- Rijbewijscategorie AM dekt brom-/snorfiets; "rijbewijs F" / bromfietscertificaat bestaat NIET meer.
- Rijbewijsgeldigheid: standaard 10 jaar; 65-70 -> geldig tot de 75e verjaardag;
  70-75 -> max 5 jaar; vanaf 75 -> verplichte medische keuring, daarna max 5 jaar.
  NIET "5 jaar vanaf 65".
- Kentekenbewijs: sinds 1-1-2014 de kentekencard (creditcardformaat) + tenaamstellingscode;
  het oude papieren deel I/II/overschrijvingsbewijs is vervallen.
- APK personenauto <= 3.500 kg: benzine/elektrisch/hybride 4-2-2-1-1 (1e na 4 jr, dan 2x na 2 jr,
  vanaf 8 jr jaarlijks); diesel of LPG/gas: 1e na 3 jr, daarna jaarlijks; 1e toelating voor 2005 jaarlijks.
  NIET de grove regel "ouder dan 3 jaar -> jaarlijks".
- Snorfiets: helmplicht sinds 1-1-2023.
- Milieuzone: oude borden C22a-C22d zijn per 1-1-2026 vervangen door hoofdbord C22e (+ onderbord)
  en eindbord C22f. Overgang 1 jan - 1 jul 2026: oude EN nieuwe borden naast elkaar geldig."""

# Topics the source explicitly leaves for human review -> always route to the queue.
HUMAN_REVIEW_PATTERNS = [
    r"milieuzone", r"nul-?emissie", r"emissieklasse", r"\bC22[a-f]\b",
    r"remvertraging", r"\baslast\b", r"wieldruk",
    r"ladingmaten", r"markeringsbord", r"ondeelbare", r"uitsteek|uitsteken|uitsteekt",
]
HUMAN_REVIEW_RE = re.compile("|".join(HUMAN_REVIEW_PATTERNS), re.IGNORECASE)

# ----------------------------------------------------------------------------
# PROMPTS
# ----------------------------------------------------------------------------
GEN_SYSTEM = f"""Je schrijft examenwaardige meerkeuzevragen uit één sectie studiestof voor het
Nederlandse autotheorie-examen (rijbewijs B / CBR). De stof is in het Nederlands en al
gecorrigeerd naar de actuele situatie (2026).

STRIKTE REGELS:
- Schrijf vragen UITSLUITEND over feiten die letterlijk in DEZE sectie staan. Geen kennis van buitenaf.
- Schrijf in het Nederlands. Gebruik officiële termen exact (bv. voorrang, haaientanden, kentekencard, RVV, APK).
- Elke vraag: precies 4 opties, ÉÉN correct, drie plausibele (niet absurde) afleiders.
- Toets begrip, geen trivia (geen vragen over paginanummers, koppen of "de afbeelding/foto").
- BEELDREGELS:
    * Stel NOOIT een vraag van het type "welk bord/symbool zie je" of die naar een afbeelding verwijst.
    * Voor verkeersborden/dashboardsymbolen ([BEELD]-stof) alleen vragen op NAAM/BETEKENIS,
      bv. "Wat betekent bord B6?" -> "Verleen voorrang aan bestuurders op de kruisende weg."
    * Negeer tekeningen/foto's die als illustratie dienen ([DROP]); gebruik alleen de regel uit de tekst.
- Voor ELKE vraag een "source": de exacte zin/zinsnede uit de sectie die het antwoord bewijst.
- Heeft de sectie geen toetsbare tekststof (alleen een checklist-kop of louter beeld), geef dan [].
- Output ALLEEN een JSON-object:
  {{"questions": [ {{"question": "...", "options": ["..","..","..",".."],
    "correct": <0-3>, "explanation": "...", "source": "<exacte tekst uit de sectie>"}} ]}}.
Maximaal {Q_PER_SECTION} vragen; minder mag als de sectie dun is."""

CRIT_SYSTEM = f"""Je bent een onafhankelijke beoordelaar die één meerkeuzevraag toetst tegen de sectie
waaruit hij is gemaakt, voor het Nederlandse autotheorie-examen (rijbewijs B).

Controleer ALLEEN of de vraag deugt en gedekt is door DEZE sectie. Verzin geen inhoud.
(1) Wordt het gemarkeerde juiste antwoord echt door de sectie ondersteund?
(2) Is er precies ÉÉN juist antwoord?
(3) Zijn de afleiders plausibel maar fout?
(4) Is de vraag te beantwoorden met alleen deze sectie?
(5) ACTUALITEIT — strijdt de vraag of het juiste antwoord met een van deze actuele feiten (2026)?
{CORRECTIONS}
Zo ja, FLAG met reden "verouderd: ...".

Output ALLEEN JSON: {{"verdict": "PASS" | "FLAG", "confidence": 0.0-1.0, "reason": "<kort>"}}.
PASS alleen als alle controles kloppen. Anders FLAG met een reden van één regel."""

# ----------------------------------------------------------------------------
# MARKDOWN PARSING
# ----------------------------------------------------------------------------
def split_sections(md_text):
    """Split the source into generatable sections.

    Rule: only content under level-1 headings that start with 'HOOFDSTUK'.
      * If a chapter has '## ' subsections, each subsection is one section.
      * If it has none (chapter 5 is one big [BEELD] checklist), the whole chapter is one section.
    Front matter, the legend, 'Beeldinventaris', 'Generator-richtlijnen' and
    'Te verifiëren' are meta and are skipped.
    """
    lines = md_text.splitlines()
    chapter = None
    in_chapter = False
    sections = []          # list of dicts: {chapter, title, body_lines, has_sub}
    cur = None

    def flush():
        nonlocal cur
        if cur and any(l.strip() for l in cur["body_lines"]):
            sections.append(cur)
        cur = None

    for line in lines:
        m1 = re.match(r"^#\s+(.*)$", line)
        m2 = re.match(r"^##\s+(.*)$", line)
        if m1:
            flush()
            title = m1.group(1).strip()
            in_chapter = title.upper().startswith("HOOFDSTUK")
            chapter = title if in_chapter else None
            if in_chapter:
                # open a chapter-level section; it stays open unless a '## ' replaces it
                cur = {"chapter": chapter, "title": title, "body_lines": [], "has_sub": False}
            continue
        if not in_chapter:
            continue
        if m2:
            flush()
            cur = {"chapter": chapter, "title": m2.group(1).strip(),
                   "body_lines": [], "has_sub": True}
            continue
        if cur is not None and line.strip() != "---":
            cur["body_lines"].append(line)
    flush()

    # finalise: join body, detect markers, drop empty checklists
    out = []
    for s in sections:
        body = "\n".join(s["body_lines"]).strip()
        if not body:
            continue
        out.append({
            "chapter": s["chapter"],
            "title": s["title"],
            "text": body,
            "markers": sorted(set(re.findall(r"\[(TEKST|BEELD|DROP)\]", body))),
        })
    return out


# ----------------------------------------------------------------------------
# MODEL PLUMBING
# ----------------------------------------------------------------------------
def get_client(base_url, key, what):
    try:
        from openai import OpenAI
    except ImportError:
        sys.exit("Missing dependency. Run:  pip install openai")
    if not key:
        sys.exit(f"No API key for {what}. Set DEEPSEEK_API_KEY (or {what}_API_KEY).")
    return OpenAI(api_key=key, base_url=base_url)


_TOKEN_PARAM = "max_tokens"   # auto-switches to max_completion_tokens for GPT-5/o-series
_USE_TEMP = True              # auto-drops temperature if a model only allows the default


def _chat_json(client, model, system, user_content, retries=MAX_RETRIES):
    """Call an OpenAI-compatible chat endpoint and parse a JSON object from the reply.
    Adapts to provider quirks (max_tokens vs max_completion_tokens, temperature)."""
    global _TOKEN_PARAM, _USE_TEMP
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": user_content}]
    last = None
    attempt = 0
    while attempt < retries:
        attempt += 1
        try:
            kwargs = dict(model=model, messages=messages,
                          response_format={"type": "json_object"})
            kwargs[_TOKEN_PARAM] = 3000
            if _USE_TEMP:
                kwargs["temperature"] = TEMPERATURE
            resp = client.chat.completions.create(**kwargs)
            txt = resp.choices[0].message.content.strip()
            txt = re.sub(r"^```(?:json)?|```$", "", txt, flags=re.MULTILINE).strip()
            return json.loads(txt)
        except Exception as e:
            msg = str(e)
            if "max_completion_tokens" in msg and _TOKEN_PARAM == "max_tokens":
                _TOKEN_PARAM = "max_completion_tokens"; attempt -= 1; continue
            if "temperature" in msg and _USE_TEMP:
                _USE_TEMP = False; attempt -= 1; continue
            last = msg
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


def generate_section(gen, sec):
    marker_hint = ""
    if "BEELD" in sec["markers"]:
        marker_hint = ("\nLet op: deze sectie bevat [BEELD]-stof (borden/symbolen). "
                       "Stel daarover alleen NAAM/BETEKENIS-vragen, geen herkenningsvragen.")
    if "DROP" in sec["markers"]:
        marker_hint += ("\nNegeer [DROP]-tekeningen/foto's; gebruik alleen de regels uit de tekst.")
    instr = (f"Hoofdstuk: {sec['chapter']}\nSectie: {sec['title']}{marker_hint}\n\n"
             f"Sectietekst:\n\"\"\"{sec['text'][:TEXT_CAP]}\"\"\"\n\nGenereer nu de vragen.")
    out = _chat_json(gen, GEN_MODEL, GEN_SYSTEM, instr)
    if "_error" in out:
        return [], out["_error"]
    qs = out.get("questions", []) if isinstance(out, dict) else []
    return [q for q in qs if validate_q(q) is None], None


def critique_q(crit, sec_text, q):
    payload = json.dumps({"question": q["question"], "options": q["options"],
                          "correct": q["correct"], "source": q.get("source", "")},
                         ensure_ascii=False)
    user = (f"Beoordeel deze vraag tegen de sectie hieronder.\nSectietekst:\n"
            f"\"\"\"{sec_text[:TEXT_CAP]}\"\"\"\n{payload}")
    out = _chat_json(crit, CRIT_MODEL, CRIT_SYSTEM, user, retries=3)
    if "_error" in out:
        return {"verdict": "FLAG", "confidence": 0.0,
                "reason": "critique call failed: " + out["_error"]}
    v = str(out.get("verdict", "FLAG")).upper()
    return {"verdict": "PASS" if v == "PASS" else "FLAG",
            "confidence": out.get("confidence", 0.0),
            "reason": str(out.get("reason", ""))[:200]}


def needs_human_review(q, sec):
    hay = " ".join([q.get("question", ""), " ".join(q.get("options", [])),
                    q.get("explanation", ""), q.get("source", ""), sec["title"]])
    return bool(HUMAN_REVIEW_RE.search(hay))


# ----------------------------------------------------------------------------
# UTILITIES
# ----------------------------------------------------------------------------
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


def load_md(path):
    if not os.path.exists(path):
        sys.exit(f"Source markdown not found: {path}  (set --md or MD_FILE)")
    with open(path, encoding="utf-8") as f:
        return f.read()


# ----------------------------------------------------------------------------
# COMMANDS
# ----------------------------------------------------------------------------
def run(md_path, limit):
    sections = split_sections(load_md(md_path))
    if limit:
        sections = sections[:limit]

    questions = json.load(open(OUT_FILE, encoding="utf-8")) if os.path.exists(OUT_FILE) else []
    state = json.load(open(STATE_FILE, encoding="utf-8")) if os.path.exists(STATE_FILE) \
            else {"done_keys": [], "next_id": 1}
    done = set(state["done_keys"])

    gen  = get_client(GEN_BASE_URL,  GEN_API_KEY,  "GEN")
    crit = get_client(CRIT_BASE_URL, CRIT_API_KEY, "CRIT")
    same = (GEN_MODEL == CRIT_MODEL and GEN_BASE_URL == CRIT_BASE_URL)

    seen = [_norm(q["question"].get(LANG, "")) for q in questions]
    todo = [s for s in sections if f'{s["chapter"]}::{s["title"]}' not in done]

    print(f"Source: {len(sections)} sections, {len(todo)} not yet done.")
    print(f"GEN={GEN_MODEL}  CRIT={CRIT_MODEL}"
          f"{'  (same model — self-critique; consider a cross-model CRIT)' if same else ''}\n")

    t0 = time.time()
    for i, sec in enumerate(todo, 1):
        key = f'{sec["chapter"]}::{sec["title"]}'
        gen_qs, err = generate_section(gen, sec)
        if err:
            print(f"  {sec['title']}: generation error ({err}); will retry next run")
            continue
        kept = 0
        for q in gen_qs:
            if is_duplicate(q["question"], seen):
                continue
            verdict = critique_q(crit, sec["text"], q)
            hr = needs_human_review(q, sec)
            review = f'{verdict["verdict"]} (conf {verdict["confidence"]}) — {verdict["reason"]}'
            if hr:
                review = "HUMAN-REVIEW (source-flagged topic) | " + review
            rec = {
                "id": state["next_id"], "image": None, "category": CATEGORY,
                "question":    {LANG: q["question"]},
                "options":     {LANG: q["options"]},
                "correct":     q["correct"],
                "explanation": {LANG: q.get("explanation", "")},
                "_chapter": sec["chapter"],
                "_section": sec["title"],
                "_source":  q.get("source", ""),
                "_review":  review,
                "_human_review": hr,
            }
            questions.append(rec)
            seen.append(_norm(q["question"]))
            state["next_id"] += 1
            kept += 1
        done.add(key)
        state["done_keys"] = sorted(done)
        atomic_save(questions, OUT_FILE)
        atomic_save(state, STATE_FILE)

        passed = sum(1 for q in questions if q["_review"].startswith("PASS"))
        rate = (time.time() - t0) / i
        print(f"  [{i}/{len(todo)}] {sec['title'][:40]:40s} +{kept} | total {len(questions)} "
              f"({passed} pass) | ~{rate:.1f}s/sec | ETA {rate*(len(todo)-i)/60:.1f} min")

    flagged = [q for q in questions if not q["_review"].startswith("PASS")]
    atomic_save(flagged, REVIEW_FILE)
    print(f"\nDone. {len(questions)} questions -> {OUT_FILE}")
    print(f"{len(flagged)} need attention (critic-FLAG or human-review) -> {REVIEW_FILE}")
    print("This module is Dutch-direct: do NOT run translate_exam.py. "
          "Strip _-prefixed fields before going live.")


def run_list(md_path):
    sections = split_sections(load_md(md_path))
    print(f"{len(sections)} sections:\n")
    cur_ch = None
    for s in sections:
        if s["chapter"] != cur_ch:
            cur_ch = s["chapter"]
            print(f"\n# {cur_ch}")
        mk = " ".join(s["markers"]) or "TEKST"
        print(f"   - {s['title']:45s} [{mk}]")


def run_check():
    """Cheapest possible pre-flight: one tiny call to each endpoint."""
    gen = get_client(GEN_BASE_URL, GEN_API_KEY, "GEN")
    print(f"Pinging GEN ({GEN_MODEL}) ...")
    r = _chat_json(gen, GEN_MODEL, "Reply with JSON only.",
                   'Return {"ok": true} and nothing else.', retries=2)
    print("  GEN:", "OK" if r.get("ok") is True else f"unexpected -> {r}")
    if not (GEN_MODEL == CRIT_MODEL and GEN_BASE_URL == CRIT_BASE_URL):
        crit = get_client(CRIT_BASE_URL, CRIT_API_KEY, "CRIT")
        print(f"Pinging CRIT ({CRIT_MODEL}) ...")
        r = _chat_json(crit, CRIT_MODEL, "Reply with JSON only.",
                       'Return {"ok": true} and nothing else.', retries=2)
        print("  CRIT:", "OK" if r.get("ok") is True else f"unexpected -> {r}")
    print("Pre-flight done.")


def run_review():
    if not os.path.exists(REVIEW_FILE):
        sys.exit(f"{REVIEW_FILE} not found. Run generation first.")
    for q in json.load(open(REVIEW_FILE, encoding="utf-8")):
        print(f"--- id {q['id']}  [{q.get('_section')}] ---")
        print("Q:", q["question"].get(LANG, ""))
        print("source:", q.get("_source", ""))
        print("review:", q["_review"], "\n")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Markdown -> grounded Dutch MCQs (rijbewijs B).")
    ap.add_argument("--md", default=MD_DEFAULT, help=f"source markdown (default {MD_DEFAULT})")
    ap.add_argument("--out", help="override output JSON path")
    ap.add_argument("--limit", type=int, help="only process the first N sections (TEST mode)")
    ap.add_argument("--list", action="store_true", help="list sections + markers, no API")
    ap.add_argument("--check", action="store_true", help="tiny API ping, no generation")
    ap.add_argument("--review", action="store_true", help="print the flagged-question queue")
    args = ap.parse_args()

    if args.out:
        OUT_FILE = args.out
        STATE_FILE = args.out.replace(".json", "_state.json")
        REVIEW_FILE = args.out.replace(".json", "_review.json")

    if args.list:
        run_list(args.md)
    elif args.check:
        run_check()
    elif args.review:
        run_review()
    else:
        run(args.md, args.limit)
