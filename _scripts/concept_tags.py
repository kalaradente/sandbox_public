#!/usr/bin/env python3
"""Concept tags for every clip and Pinterest pin (vocabulary: _scripts/concept_tags.json).
  python3 _scripts/concept_tags.py                 give every untagged clip / pin a first set of tags from its words; print counts
  python3 _scripts/concept_tags.py --redo <keys>   redo the word-based tags for these (hand tags are replaced too)
  python3 _scripts/concept_tags.py --list          every tag and how many clips/pins carry it
A shortlist is then a filter: shortlist_check.py --tag night,couple --format 9:16 --find "" (or library_view.py --tag …).
First tags come from words, so they're a start: whoever writes or changes a one-liner fixes its tags too (a tag the words
got wrong, one they missed). A clip keeps its tags on later runs; only --redo touches them."""
import contextlib, json, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sandbox_paths import LIB, edit_json  # noqa: E402

VOCAB = {t: ws for g, d in json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "concept_tags.json"))).items()
         if not g.startswith("_") for t, ws in d.items()}
FILES = [("_reference/clip_index.json", "clips"), ("_lab/lab_index.json", "clips"), ("_stills/pinterest/index.json", None)]


def text_of(v):
    parts = [v.get("line"), v.get("description"), v.get("feel"), v.get("colour"), v.get("who"), v.get("title")]
    parts += [h.get("what", "") for h in (v.get("hooks") or []) if isinstance(h, dict)]
    return " " + re.sub(r"[-_/]", " ", " ".join(str(p) for p in parts if p).lower()) + " "   # "golden-hour" reads as "golden hour"


def tags_for(v):
    s = text_of(v); out = []
    for t, ws in VOCAB.items():
        if any(re.search(r"(?<![a-z])" + (re.escape(w[:-1].lower()) if w.endswith("*") else re.escape(w.lower()) + r"(?![a-z])"), s) for w in ws): out.append(t)
    if v.get("source_type") == "film" and "film" not in out: out.append("film")
    if "no-people" in out and {"couple", "friends", "girl", "guy", "crowd", "face"} & set(out): out.remove("no-people")
    return out


def main():
    a = sys.argv[1:]; redo = set(a[a.index("--redo") + 1:]) if "--redo" in a else set(); n = 0; count = {}
    for rel, key in FILES:
        p = os.path.join(LIB, rel)
        if not os.path.exists(p): continue
        # locked from the read to the write (a pull or another session adding clips meanwhile keeps them); --list only reads
        with (contextlib.nullcontext(json.load(open(p))) if "--list" in a else edit_json(p)) as d:
            items = d[key] if key else d.get("pins", d)
            for k, v in items.items():
                if not isinstance(v, dict) or k.startswith("_"): continue
                if v.get("use") not in ("no", "removed") and (not v.get("tags") or k in redo) and (v.get("line") or v.get("description")):
                    v["tags"] = tags_for(v); n += 1
                for t in v.get("tags") or []: count[t] = count.get(t, 0) + 1
    if "--list" in a or n:
        print("  ".join("%s %d" % (t, count.get(t, 0)) for t in VOCAB))
    print("%d tagged this run" % n if "--list" not in a else "")


if __name__ == "__main__": main()
