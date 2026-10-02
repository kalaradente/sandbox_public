#!/usr/bin/env python3
"""The engine's instructions, stored packed in _docs/*.sbx (compressed, so they aren't plain text on GitHub; not secret).
  python3 _scripts/docs.py              the main instructions: read these first, every session
  python3 _scripts/docs.py <name>       one doc (welcome, guide, craft, effects, example-study, check, lab, post-sync, fyp-study,
                                        song-rollout-template; with no name: instructions)
  python3 _scripts/docs.py --list       what's there"""
import base64, os, sys, zlib
if {"-h", "--help"} & set(sys.argv[1:]): print(__doc__.strip()); sys.exit(0)   # the usage and nothing else, as every script

DOCS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "_docs")


def read(name):
    p = os.path.join(DOCS, name + ".sbx")
    if not os.path.exists(p): sys.exit("no doc called %r (try --list)" % name)
    body = open(p).read().split("\n", 1)[1]
    return zlib.decompress(base64.b64decode("".join(body.split()))).decode("utf-8")


def names():
    return sorted(f[:-4] for f in os.listdir(DOCS) if f.endswith(".sbx"))


if __name__ == "__main__":
    a = sys.argv[1:]
    if a and a[0] == "--list": print("\n".join(names()))
    else: print(read(a[0] if a else "instructions"))
