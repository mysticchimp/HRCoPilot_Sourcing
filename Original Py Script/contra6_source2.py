#!/usr/bin/env python3
"""
Contra6 — Talent Sourcing Engine  (v2.0, spec-driven)
=====================================================
Role spec (JSON)  ->  ranked candidate shortlist CSV.

The JD is the human contract Amir signs. It compiles ONCE into a role-spec
JSON (this file's input), and the engine runs off the spec — never off prose.
That keeps the must-have -> retrieval / nice-to-have -> scoring routing
explicit and auditable instead of re-derived (and drifting) on every run.

Two ways in:
    python contra6_source.py --spec roles/hr_assistant.json
    python contra6_source.py --jd  roles/hr_assistant_jd.md   # drafts a spec
                                                               # you confirm,
                                                               # then runs it

Setup (one time):
    pip install anthropic requests
    export APIFY_TOKEN="apify_api_xxxxx"
    export ANTHROPIC_API_KEY="sk-ant-xxxxx"

Spec shape (see roles/hr_assistant.json for a full example):
    {
      "role": "HR Assistant",
      "client": "Prime Focus Group (Prime AC)",
      "retrieval": {                     # must-haves that have a LinkedIn filter
        "functions":         ["Human Resources"],
        "seniority":         [],         # left empty on purpose — years gates it
        "location":          "United Arab Emirates",
        "yearsOfExperience": ["1 to 2 years", "3 to 5 years"],
        "currentJobTitles":  [],
        "searchQuery":       "",
        "industryIds":       []          # deliberately empty -> scoring, not filter
      },
      "scoring": [                        # nice-to-haves + soft signals -> rubric
        {"signal": "...", "tier": "strong"},
        {"signal": "...", "tier": "exclude"}
      ]
    }

Human-readable labels in the spec are mapped to LinkedIn IDs by the maps below,
so the spec stays auditable and the ID translation lives in one testable place.
"""

import os
import sys
import csv
import json
import math
import hashlib
import time
import argparse
import datetime
import threading
import itertools
import requests
from anthropic import Anthropic

# ----------------------------------------------------------------------------
# CONFIG
# ----------------------------------------------------------------------------
QUERY_MODEL   = "claude-haiku-4-5"      # JD -> spec drafting
SCORING_MODEL = "claude-sonnet-4-6"     # per-candidate signal matching
SCRAPER_MODE  = "Full"
VERSION       = "2.0"

DEFAULT_LOCATION = "United Arab Emirates"
DEFAULT_SIZE     = 25
POOL_TARGET      = 60                    # scrape many, score, keep the best N

# ---- Demo pacing (both 0 = natural speed; use 0 in production) --------------
TARGET_MINUTES = 0     # if > 0, spread analysis to ~fill this many minutes
PACE_SECONDS   = 0.0   # fixed extra pause per profile if TARGET_MINUTES == 0

ACTOR = "harvestapi~linkedin-profile-search"
RUN_SYNC_URL = f"https://api.apify.com/v2/acts/{ACTOR}/run-sync-get-dataset-items"

# ---- LinkedIn ID maps (confirmed against the actor input schema) ------------
# seniorityLevelIds / yearsOfExperienceIds are string[] — pass strings.
SENIORITY_MAP = {
    "in training": "100", "entry level": "110", "senior": "120",
    "strategic": "130", "entry level manager": "200",
    "experienced manager": "210", "director": "220",
    "vice president": "300", "cxo": "310", "owner / partner": "320",
    "owner": "320", "partner": "320",
}
YEARS_MAP = {
    "less than 1 year": "1", "1 to 2 years": "2", "3 to 5 years": "3",
    "6 to 10 years": "4", "more than 10 years": "5",
}
# LinkedIn canonical function IDs. The actor schema only publishes 8=Engineering
# as its example; 8 matches LinkedIn's standard taxonomy, so the rest follow.
# VERIFY "human resources" -> "12" once against the Function dropdown in the
# Apify console. If it's wrong, it's a one-line fix right here.
FUNCTION_MAP = {
    "accounting": "1", "administrative": "2", "arts and design": "3",
    "business development": "4", "community and social services": "5",
    "consulting": "6", "education": "7", "engineering": "8",
    "entrepreneurship": "9", "finance": "10", "healthcare services": "11",
    "human resources": "12", "information technology": "13", "legal": "14",
    "marketing": "15", "media and communications": "16",
    "military and protective services": "17", "operations": "18",
    "product management": "19", "program and project management": "20",
    "purchasing": "21", "quality assurance": "22", "real estate": "23",
    "research": "24", "sales": "25", "support": "26",
}

# ---- Scoring weights: the ONE tunable layer that is NOT in the JD -----------
# The JD/spec sets the sign and tier of each signal; the magnitude is yours to
# tune off demo results. Keep it here, not in the spec, so specs stay pure
# projections of the JD and the numbers stay reproducible across runs.
TIER_WEIGHTS = {"strong": 3, "normal": 2, "light": 1, "exclude": -5}

# ---- Global scoring: signals applied to EVERY role -------------------------
# These are Contra6 operational preferences (not from any one JD). They're
# merged with each spec's own `scoring`, so cross-role preferences are tuned
# in ONE place instead of copied into all 12 role files.
GLOBAL_SCORING = [
    {"signal": "Based in the Northern Emirates — Sharjah, Ajman, Umm Al Quwain, "
               "Ras Al Khaimah, or Fujairah (commutable to a northern-emirates "
               "site)", "tier": "light"},
]

client = Anthropic()


# ----------------------------------------------------------------------------
# Presentation helpers
# ----------------------------------------------------------------------------
class Spinner:
    """A small working indicator so long stages don't look like a dead pause."""
    def __init__(self, msg):
        self.msg, self.done = msg, False

    def __enter__(self):
        self.t = threading.Thread(target=self._spin, daemon=True)
        self.t.start()
        return self

    def _spin(self):
        for ch in itertools.cycle("|/-\\"):
            if self.done:
                break
            sys.stdout.write(f"\r        {self.msg} {ch}")
            sys.stdout.flush()
            time.sleep(0.1)
        sys.stdout.write("\r" + " " * (len(self.msg) + 24) + "\r")
        sys.stdout.flush()

    def __exit__(self, *a):
        self.done = True
        self.t.join()


def banner():
    print()
    print("  ┌────────────────────────────────────────────┐")
    print("  │   CONTRA6  ·  Talent Sourcing Engine         │")
    print(f"  │   v{VERSION}   ·   GCC Industrial Practice        │")
    print("  └────────────────────────────────────────────┘")
    print()


def ask(label, default=None):
    suffix = f" [{default}]" if default else ""
    val = input(f"  {label}{suffix}: ").strip()
    return val or (default or "")


def _strip_fences(text):
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```", 2)[1]
        if t.lstrip().startswith("json"):
            t = t.lstrip()[4:]
    return t.strip()


# ----------------------------------------------------------------------------
# Persistence / cache
# ----------------------------------------------------------------------------
# Two independent caches under .cache/ so re-runs don't repay for identical work:
#   scrape cache — keyed by the retrieval filters. Same filters => load profiles
#                  from disk, zero Apify spend. A changed filter (or --refresh)
#                  triggers a fresh pull.
#   score  cache — keyed by the rubric (signals + weights). Same rubric => reuse
#                  a candidate's score. Tweak ONE weight and re-run: the scrape
#                  is reused, only scoring recomputes.
CACHE_DIR = ".cache"


def _hash(obj):
    blob = json.dumps(obj, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha1(blob).hexdigest()[:12]


def _role_slug(spec):
    return (spec.get("role") or "role").replace(" ", "_")


def scrape_cache_path(spec, actor_input):
    return os.path.join(CACHE_DIR, f"scrape_{_role_slug(spec)}_{_hash(actor_input)}.json")


def load_scrape_cache(path):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def save_scrape_cache(path, pool_n, profiles):
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"pool_n": pool_n, "profiles": profiles}, f)


def score_cache_path(scoring):
    return os.path.join(CACHE_DIR, f"scores_{_hash({'r': scoring, 'w': TIER_WEIGHTS})}.json")


def load_score_cache(path):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_score_cache(path, cache):
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False)


def candidate_key(c):
    return c.get("linkedinUrl") or f"{c['firstName']}|{c['lastName']}|{c['current_company']}"


# ----------------------------------------------------------------------------
# Spec loading & JD compilation
# ----------------------------------------------------------------------------
def load_spec(path):
    with open(path, "r", encoding="utf-8") as f:
        spec = json.load(f)
    _validate_spec(spec)
    return spec


def _validate_spec(spec):
    if "retrieval" not in spec or "scoring" not in spec:
        sys.exit("  ERROR: spec must contain 'retrieval' and 'scoring'.")
    for s in spec["scoring"]:
        if s.get("tier") not in TIER_WEIGHTS:
            sys.exit(f"  ERROR: scoring signal has invalid tier {s.get('tier')!r}; "
                     f"use one of {list(TIER_WEIGHTS)}.")


def jd_to_spec(jd_text):
    """Draft a role-spec from JD prose. Output is REVIEWED by a human before it
    runs — prose never drives a scrape directly. Uses the exact label vocab the
    ID maps understand so retrieval compiles cleanly."""
    system = (
        "You convert a job description into a recruiting role-spec as JSON. "
        "Split every requirement by whether it has a LinkedIn search filter:\n"
        "RETRIEVAL (hard AND-filters) — only requirements expressible as one of:\n"
        "  functions (LinkedIn function names), seniority (LinkedIn seniority "
        "names), location (one country/city string), yearsOfExperience "
        "(buckets), currentJobTitles, searchQuery (1-3 domain words max), "
        "industryIds (usually []).\n"
        "SCORING (soft signals) — everything else: nice-to-haves, skills, "
        "credentials, sector preference, tools, languages. Each gets a tier: "
        "'strong' (important nice-to-have or a must-have with NO filter), "
        "'normal', 'light', or 'exclude' (a disqualifier / over-qualification "
        "ceiling).\n"
        "Rules:\n"
        "1. ALWAYS populate currentJobTitles with 4-8 real title variants that "
        "match the LEVEL of this role (e.g. an assistant role -> Assistant, "
        "Coordinator, Administrator, Officer, Executive; a manager role -> "
        "Manager, Lead, Head). Titles are the primary level gate and must never "
        "be left empty.\n"
        "2. functions is the broad CATEGORY net (all HR, all Engineering, etc.) "
        "and is huge on its own — it must be PAIRED with currentJobTitles, never "
        "used to carry the search alone. Function alone over a whole country "
        "returns tens of thousands.\n"
        "3. Leave seniority empty — currentJobTitles + yearsOfExperience already "
        "gate the level; adding the seniority facet is a redundant AND-filter.\n"
        "4. Do NOT put skills/credentials/tools/industry into retrieval; they "
        "over-constrain and belong in scoring. Use industryIds:[] and make "
        "sector a scoring signal.\n"
        "Valid function names: " + ", ".join(sorted({k.title() for k in FUNCTION_MAP})) + ".\n"
        "Valid seniority names: In Training, Entry Level, Senior, Strategic, "
        "Entry Level Manager, Experienced Manager, Director, Vice President, "
        "CXO, Owner / Partner.\n"
        "Valid yearsOfExperience buckets: Less than 1 year, 1 to 2 years, "
        "3 to 5 years, 6 to 10 years, More than 10 years.\n"
        'Return ONLY JSON with keys: role, client, retrieval '
        '{functions, seniority, location, yearsOfExperience, currentJobTitles, '
        'searchQuery, industryIds}, scoring [{signal, tier}].'
    )
    resp = client.messages.create(
        model=QUERY_MODEL, max_tokens=1500, system=system,
        messages=[{"role": "user", "content": jd_text}],
    )
    spec = json.loads(_strip_fences(resp.content[0].text))
    _validate_spec(spec)
    return spec


def _print_spec(spec):
    r = spec.get("retrieval", {})
    print("\n  ── Proposed role-spec " + "─" * 24)
    print(f"     Role      : {spec.get('role','(unnamed)')}")
    print(f"     Client    : {spec.get('client','—')}")
    print("     RETRIEVAL (hard filters):")
    print(f"        functions        : {r.get('functions') or '—'}")
    print(f"        seniority        : {r.get('seniority') or '—'}")
    print(f"        location         : {r.get('location') or DEFAULT_LOCATION}")
    print(f"        yearsOfExperience: {r.get('yearsOfExperience') or '—'}")
    print(f"        currentJobTitles : {r.get('currentJobTitles') or '—'}")
    print(f"        searchQuery      : {r.get('searchQuery') or '(none)'}")
    print(f"        industryIds      : {r.get('industryIds') or '[] (-> scoring)'}")
    print("     SCORING (ranking rubric):")
    for s in spec.get("scoring", []):
        print(f"        [{s['tier']:>6}]  {s['signal']}")
    for s in GLOBAL_SCORING:
        print(f"        [{s['tier']:>6}]  {s['signal']}  (global, all roles)")
    print("  " + "─" * 45)


# ----------------------------------------------------------------------------
# Compile spec -> actor input
# ----------------------------------------------------------------------------
def _map_label(m, label, kind):
    key = str(label).strip().lower()
    if key not in m:
        sys.exit(f"  ERROR: unknown {kind} {label!r}. Valid: "
                 f"{sorted(set(m.keys()))}")
    return m[key]


def compile_retrieval(spec, pool_cap):
    r = spec.get("retrieval", {})
    actor = {
        "profileScraperMode": SCRAPER_MODE,
        "locations": [r.get("location") or DEFAULT_LOCATION],
    }

    titles = r.get("currentJobTitles") or []
    if titles:
        actor["currentJobTitles"] = titles

    # searchQuery is a literal AND-token matcher — keep it 1-3 domain words or ''
    q = " ".join((r.get("searchQuery") or "").split()[:3]).strip()
    if q:
        actor["searchQuery"] = q

    fns = [_map_label(FUNCTION_MAP, x, "function") for x in (r.get("functions") or [])]
    if fns:
        actor["functionIds"] = fns

    sen = [_map_label(SENIORITY_MAP, x, "seniority") for x in (r.get("seniority") or [])]
    if sen:
        actor["seniorityLevelIds"] = sen

    yrs = [_map_label(YEARS_MAP, x, "years of experience")
           for x in (r.get("yearsOfExperience") or [])]
    if yrs:
        actor["yearsOfExperienceIds"] = yrs

    ind = r.get("industryIds") or []
    if ind:
        actor["industryIds"] = [str(x) for x in ind]

    # Pool sizing. takePages MUST be set or a tight query can crawl up to 100
    # pages and burn credits. 25 profiles per page.
    actor["maxItems"] = pool_cap
    actor["takePages"] = max(1, math.ceil(pool_cap / 25))
    return actor


# Order to drop filters when a query returns zero — most-likely culprit first.
RELAX_ORDER = ["searchQuery", "yearsOfExperienceIds", "seniorityLevelIds",
               "functionIds", "industryIds"]


# ----------------------------------------------------------------------------
# Retrieve profiles
# ----------------------------------------------------------------------------
def _token():
    t = os.environ.get("APIFY_TOKEN")
    if not t:
        sys.exit("\n  ERROR: APIFY_TOKEN not set in this terminal.")
    return t


def probe_pool(actor_input):
    """Cheap pre-flight (~$0.10): ONE search page in Short mode. Returns
    (total_count_or_None, [short_profiles]) without the expensive Full scrape,
    so we can see pool size + a preview and catch 0-return queries early."""
    probe = dict(actor_input)
    probe["profileScraperMode"] = "Short"
    probe["takePages"] = 1
    probe.pop("maxItems", None)
    r = requests.post(RUN_SYNC_URL, params={"token": _token()},
                      json=probe, timeout=180)
    r.raise_for_status()
    items = r.json() or []
    count = None
    if items:
        count = (((items[0].get("_meta") or {}).get("pagination") or {})
                 .get("totalElements"))
    return count, items


def probe_with_relax(actor_input):
    """Probe; if 0 hits, drop filters one at a time in RELAX_ORDER and re-probe.
    All still cheap Short-mode — no scrape credits spent yet."""
    pool_n, preview = probe_pool(actor_input)
    for key in RELAX_ORDER:
        if preview:
            break
        if key in actor_input:
            dropped = actor_input.pop(key)
            print(f"        0 hits — relaxing {key}={dropped!r} and re-checking.")
            with Spinner("pre-flight: re-checking pool size"):
                pool_n, preview = probe_pool(actor_input)
    return pool_n, preview


def fetch_profiles(actor_input, poll_every=6, max_wait=1800):
    """Async run: start -> poll -> fetch dataset. The synchronous endpoint caps
    at 300s and returns 408 on longer runs (while the run keeps going and burns
    credits), so a Full pull of many profiles MUST be async. Returns
    (items, status, run_id). Items are fetched even on a non-SUCCEEDED terminal
    status, because whatever was scraped is already paid for."""
    token = _token()
    r = requests.post(f"https://api.apify.com/v2/acts/{ACTOR}/runs",
                      params={"token": token}, json=actor_input, timeout=60)
    r.raise_for_status()
    run = r.json()["data"]
    run_id, dataset_id = run["id"], run["defaultDatasetId"]
    print(f"        run started: {run_id}  (recoverable with --recover-last)")

    waited = 0
    while True:
        info = requests.get(f"https://api.apify.com/v2/actor-runs/{run_id}",
                            params={"token": token}, timeout=60).json()["data"]
        status = info["status"]
        done = status in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT")
        sys.stdout.write(f"\r        run {status.lower()} · {waited}s elapsed"
                         + " " * 12)
        sys.stdout.flush()
        if done or waited >= max_wait:
            break
        time.sleep(poll_every)
        waited += poll_every
    print()

    items = requests.get(f"https://api.apify.com/v2/datasets/{dataset_id}/items",
                         params={"token": token, "clean": "true",
                                 "format": "json"}, timeout=180).json()
    return items, status, run_id


def recover_last_dataset():
    """Pull the most recent SUCCEEDED run's dataset for this actor — used to
    reclaim a run whose sync request timed out (408) but which finished and was
    charged anyway. Returns the profiles list."""
    token = _token()
    r = requests.get(
        f"https://api.apify.com/v2/acts/{ACTOR}/runs/last/dataset/items",
        params={"token": token, "status": "SUCCEEDED", "clean": "true",
                "format": "json"}, timeout=180)
    r.raise_for_status()
    return r.json()


# ----------------------------------------------------------------------------
# Compact a profile for scoring
# ----------------------------------------------------------------------------
def current_role(p):
    for exp in p.get("experience", []) or []:
        if (exp.get("endDate") or {}).get("text") == "Present":
            return exp.get("position", ""), exp.get("companyName", "")
    cp = (p.get("currentPosition") or [{}])[0]
    exp0 = (p.get("experience") or [{}])[0]
    return exp0.get("position", ""), cp.get("companyName", exp0.get("companyName", ""))


def location_str(p):
    parsed = (p.get("location") or {}).get("parsed") or {}
    city, country = parsed.get("city") or "", parsed.get("country") or ""
    return ", ".join([x for x in (city, country) if x]) or \
        (p.get("location") or {}).get("linkedinText", "")


def top_skills(p):
    if p.get("topSkills"):
        return p["topSkills"]
    return " • ".join(s.get("name", "") for s in (p.get("skills") or [])[:6] if s.get("name"))


def compact(p, idx):
    title, company = current_role(p)
    exps = [f"{e.get('position','')} @ {e.get('companyName','')} ({e.get('duration','')})"
            for e in (p.get("experience") or [])[:3]]
    return {
        "idx": idx, "firstName": p.get("firstName", ""), "lastName": p.get("lastName", ""),
        "headline": p.get("headline", ""), "current_title": title,
        "current_company": company, "location": location_str(p),
        "linkedinUrl": p.get("linkedinUrl", ""), "topSkills": top_skills(p),
        "_experience": " | ".join(exps), "_about": (p.get("about") or "")[:400],
    }


# ----------------------------------------------------------------------------
# Score one candidate: LLM matches SIGNALS, Python computes the weighted score.
# Splitting it this way keeps the weights deterministic and the ranking
# reproducible & defensible ("why did X beat Y" has an exact answer).
# ----------------------------------------------------------------------------
def score_one(scoring, c):
    lines = [f"{i+1}. {s['signal']}" for i, s in enumerate(scoring)]
    system = (
        "You are a recruiting analyst. For the candidate below, decide which of "
        "the numbered SIGNALS their profile clearly evidences. Mark a signal met "
        "ONLY on real evidence in the profile — never assume or infer generously. "
        'Return ONLY JSON: {"met": [numbers of signals clearly evidenced]}.'
    )
    user = (
        "SIGNALS:\n" + "\n".join(lines) + "\n\nCANDIDATE:\n"
        f"{c['firstName']} {c['lastName']} — {c['headline']}\n"
        f"now: {c['current_title']} @ {c['current_company']} | {c['location']}\n"
        f"history: {c['_experience']}\n"
        f"skills: {c['topSkills']}\n"
        f"about: {c['_about']}"
    )
    resp = client.messages.create(
        model=SCORING_MODEL, max_tokens=200, system=system,
        messages=[{"role": "user", "content": user}],
    )
    data = json.loads(_strip_fences(resp.content[0].text))
    met_ids = {int(x) for x in data.get("met", []) if str(x).isdigit()}
    met_ids = {i for i in met_ids if 1 <= i <= len(scoring)}
    raw = sum(TIER_WEIGHTS[scoring[i-1]["tier"]] for i in met_ids)
    met_signals = [scoring[i-1]["signal"] for i in sorted(met_ids)]
    return raw, met_signals


def normalize(raw, max_pos):
    if max_pos <= 0:
        return 0
    return round(10 * max(0, min(raw, max_pos)) / max_pos)


# ----------------------------------------------------------------------------
# Output
# ----------------------------------------------------------------------------
def write_csv(ranked, total_found, spec, max_pos):
    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M")
    role = spec.get("role", "role").replace(" ", "_")
    path = f"Contra6_Shortlist_{role}_{stamp}.csv"
    cols = ["rank", "fit_0_10", "points", "matched_signals", "firstName",
            "lastName", "headline", "current_title", "current_company",
            "location", "linkedinUrl", "topSkills"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([f"# Contra6 shortlist  ·  {spec.get('role','')}  ·  {spec.get('client','')}"])
        w.writerow([f"# Location: {spec['retrieval'].get('location', DEFAULT_LOCATION)}"
                    f"  ·  pool size: {total_found}"])
        w.writerow(cols)
        for rank, c in enumerate(ranked, 1):
            w.writerow([rank, normalize(c["raw"], max_pos), c["raw"],
                        " | ".join(c["met"]), c["firstName"], c["lastName"],
                        c["headline"], c["current_title"], c["current_company"],
                        c["location"], c["linkedinUrl"], c["topSkills"]])
    return path


def print_ranking(ranked, total_found, max_pos):
    print(f"\n  Talent pool identified: {total_found} matching profiles")
    print(f"  Shortlisted & ranked  : {len(ranked)}\n")
    print(f"  {'#':>2}  {'Fit':>4}  {'Candidate':24}  {'Current role':40}")
    print("  " + "─" * 78)
    for rank, c in enumerate(ranked, 1):
        name = f"{c['firstName']} {c['lastName']}"[:24]
        tc = f"{c['current_title']} @ {c['current_company']}"[:40]
        print(f"  {rank:>2}  {normalize(c['raw'], max_pos):>3}   {name:24}  {tc:40}")


# ----------------------------------------------------------------------------
def get_spec_from_args(args):
    if args.spec:
        spec = load_spec(args.spec)
        _print_spec(spec)
        if not ask("\n  Run this spec? (y/n)", "y").lower().startswith("y"):
            sys.exit("  Cancelled.")
        return spec

    if args.jd:
        with open(args.jd, "r", encoding="utf-8") as f:
            jd_text = f.read()
        print("\n  Compiling JD -> role-spec...")
        with Spinner("reading the JD"):
            spec = jd_to_spec(jd_text)
        _print_spec(spec)
        print("\n  Review the split above. Retrieval = hard filters, scoring = "
              "ranking.\n  Edit the saved spec file to fine-tune weights/filters.")
        if not ask("\n  Looks right — run it? (y/n)", "y").lower().startswith("y"):
            sys.exit("  Cancelled — adjust the JD and re-run.")
        out = f"roles/{spec.get('role','role').replace(' ', '_').lower()}.json"
        os.makedirs("roles", exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(spec, f, indent=2, ensure_ascii=False)
        print(f"  Spec saved: {out}  (re-run with --spec {out} to skip compile)")
        return spec

    sys.exit("  Usage: contra6_source.py (--spec PATH | --jd PATH) "
             "[--size N] [--pool N]")


def main():
    ap = argparse.ArgumentParser(description="Contra6 spec-driven sourcing")
    ap.add_argument("--spec", help="path to a role-spec JSON")
    ap.add_argument("--jd", help="path to a JD (.md/.txt) to compile into a spec")
    ap.add_argument("--size", type=int, default=DEFAULT_SIZE, help="shortlist size")
    ap.add_argument("--pool", type=int, default=POOL_TARGET, help="max profiles to scrape")
    ap.add_argument("--refresh", action="store_true",
                    help="force a fresh scrape, ignoring the cached pull")
    ap.add_argument("--recover-last", action="store_true",
                    help="pull the last SUCCEEDED Apify run's dataset instead of "
                         "scraping (reclaims a run whose sync request timed out)")
    args = ap.parse_args()

    banner()
    spec = get_spec_from_args(args)
    size = args.size
    pool_cap = max(args.pool, size)

    scoring = GLOBAL_SCORING + spec["scoring"]   # global first, then role-specific
    max_pos = sum(TIER_WEIGHTS[s["tier"]] for s in scoring
                  if TIER_WEIGHTS[s["tier"]] > 0)

    print("\n  [1/4] Compiling retrieval filters")
    actor_input = compile_retrieval(spec, pool_cap)
    for k in ("functionIds", "seniorityLevelIds", "yearsOfExperienceIds",
              "currentJobTitles", "searchQuery", "industryIds"):
        if k in actor_input:
            print(f"        {k:20}: {actor_input[k]}")
    print(f"        {'locations':20}: {actor_input['locations']}")
    print(f"        pool target         : {pool_cap} profiles "
          f"(ranked down to {size})")

    print("\n  [2/4] Querying talent network")
    scrape_path = scrape_cache_path(spec, actor_input)
    cached = None if (args.refresh or args.recover_last) else load_scrape_cache(scrape_path)

    if args.recover_last:
        print("        recovering the last SUCCEEDED Apify run's dataset...")
        with Spinner("pulling last run dataset"):
            profiles = recover_last_dataset()
        if not profiles:
            sys.exit("        No recoverable dataset found. Check the run status "
                     "in the Apify console.")
        pool_n = (((profiles[0].get("_meta") or {}).get("pagination") or {})
                  .get("totalElements"))
        save_scrape_cache(scrape_path, pool_n, profiles)
        print(f"        recovered {len(profiles)} profiles from the last run "
              f"·  cached to disk (no new spend)")
    elif cached:
        profiles = cached["profiles"]
        pool_n = cached.get("pool_n")
        print(f"        cache hit — loaded {len(profiles)} profiles from disk "
              f"(no scrape). Use --refresh to re-pull.")
    else:
        with Spinner("pre-flight: checking pool size"):
            pool_n, preview = probe_with_relax(actor_input)
        if not preview:
            sys.exit("        Still 0 matches after relaxing — broaden the spec's "
                     "filters and re-run. No scrape credits spent.")

        shown = pool_n if pool_n is not None else f"{len(preview)}+"
        print(f"        pool size: {shown} profiles match  ·  previewing first 5:")
        for p in preview[:5]:
            nm = f"{p.get('firstName','')} {p.get('lastName','')}".strip()[:24]
            hl = (p.get("headline") or "")[:58]
            print(f"           · {nm:24}  {hl}")

        if isinstance(pool_n, int) and pool_n > 8000:
            print(f"        ⚠ pool is broad ({pool_n:,}); only the first "
                  f"{actor_input.get('maxItems')} are sampled. If the preview looks")
            print(f"          off-target, abort and tighten the spec before scraping.")
            if not ask("        proceed with the scrape anyway? (y/n)", "y") \
                   .lower().startswith("y"):
                sys.exit("        Aborted before scraping. Adjust and re-run.")

        # Async run: start -> poll -> fetch. Save the moment data is in hand so a
        # later error can never strand a paid-for scrape again.
        try:
            profiles, status, run_id = fetch_profiles(actor_input)
        except Exception as e:
            sys.exit(f"        Fetch error: {e}\n        If the run was charged, "
                     f"reclaim it with:  --recover-last")
        if not profiles:
            sys.exit(f"        Run ended {status} with 0 profiles. If it was "
                     f"charged, try --recover-last.")
        save_scrape_cache(scrape_path, pool_n, profiles)
        note = "" if status == "SUCCEEDED" else f"  (run ended {status} — partial)"
        print(f"        {len(profiles)} profiles retrieved  ·  cached to disk{note}")

    total_found = pool_n if pool_n is not None else "?"

    print("\n  [3/4] Relevance analysis")
    compacts = [compact(p, i) for i, p in enumerate(profiles)]
    n = len(compacts)
    pace = (TARGET_MINUTES * 60.0 / n) if TARGET_MINUTES > 0 else PACE_SECONDS
    phrases = itertools.cycle([
        "matching signals against the rubric",
        "cross-referencing experience",
        "assessing sector & credential signals",
        "scoring relevance",
    ])
    score_path = score_cache_path(scoring)   # keyed by rubric + weights
    score_cache = load_score_cache(score_path)
    reused = 0
    for k, c in enumerate(compacts, 1):
        name = f"{c['firstName']} {c['lastName']}"
        role = f"{c['current_title']} @ {c['current_company']}"[:54]
        ckey = candidate_key(c)
        hit = score_cache.get(ckey)
        print(f"        profile {k:02d}/{n:02d}   {name}")
        print(f"                     {role}")
        if hit is not None:
            c["raw"], c["met"] = hit["raw"], hit["met"]
            reused += 1
            print(f"                     cached  ...  fit "
                  f"{normalize(c['raw'], max_pos)}/10")
            continue
        sys.stdout.write(f"                     {next(phrases)} ")
        sys.stdout.flush()
        try:
            c["raw"], c["met"] = score_one(scoring, c)
        except Exception:
            c["raw"], c["met"] = 0, []
        score_cache[ckey] = {"raw": c["raw"], "met": c["met"]}
        if pace:
            time.sleep(pace)
        print(f"...  fit {normalize(c['raw'], max_pos)}/10")
    save_score_cache(score_path, score_cache)
    ranked = sorted(compacts, key=lambda c: c["raw"], reverse=True)[:size]
    print(f"        analysis complete  ·  {reused}/{n} scores reused from cache")

    print("\n  [4/4] Compiling ranked shortlist")
    with Spinner("formatting deliverable"):
        time.sleep(0.6)
        path = write_csv(ranked, total_found, spec, max_pos)

    print_ranking(ranked, total_found, max_pos)
    print(f"\n  Shortlist exported: {path}\n")


if __name__ == "__main__":
    main()