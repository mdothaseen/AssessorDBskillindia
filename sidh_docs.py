"""
sidh_docs.py
============
assessor_raw.jsonl (sidh_fill.py ka cache) se har assessor ke documents
download karta hai: PAN, education proof, experience proof, resume, disability,
certificate, association doc. Sab ek folder mein:

    docs/AR171908/pan_PAN.pdf
    docs/AR171908/education-1_Msc pas.pdf
    docs/AR171908/resume_CV.pdf

- Sirf wahi documents jo aapke apne login ko portal `signedURL` se deta hai.
- Jo ID ho chuki, wo skip (resume). --follow: sidh_fill.py ke saath saath chale.
- Token: browser se (sidh_browser.py). --no-browser par session.json se.

Flow: key (JSON mein) -> GET /api/document/v1/signedURL?key=<key> -> S3 link -> file

Usage:
  python sidh_docs.py --limit 3          # test
  python sidh_docs.py                    # cache mein jo hai sab
  python sidh_docs.py --follow           # main run ke saath saath
  python sidh_docs.py --no-browser       # token paste karke
"""

import argparse
import json
import os
import re
import time

import requests

from sidh_browser import expired

API = "https://adminservices.skillindiadigital.gov.in/api/"
CACHE_FILE = "assessor_raw.jsonl"
DOC_DIR = "docs"
DONE_FILE = os.path.join(DOC_DIR, "_done.txt")
SESSION_FILE = "session.json"
EXT_OK = {".pdf", ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff", ".doc", ".docx"}

# category label -> getter(data) -> list of storage keys
DOC_FIELDS = [
    ("pan", lambda d: [(d.get("identity") or {}).get("pan", {}).get("supportingDocument")]),
    ("identity", lambda d: [(d.get("identity") or {}).get("supportingDocument")]),
    ("resume", lambda d: [d.get("resume")]),
    ("education", lambda d: [(e or {}).get("supportingDocument") for e in (d.get("education") or [])]),
    ("industry-exp", lambda d: [(e or {}).get("supportingDocument") for e in (d.get("industrialExperience") or [])]),
    ("training-exp", lambda d: [(e or {}).get("supportingDocument") for e in (d.get("trainingExperience") or [])]),
    ("disability", lambda d: [(d.get("disability") or {}).get("supportingDocument")]),
    ("certificate", lambda d: [
        ((j.get("certificateDetails") or {}).get("certificateURLDetails") or {}).get("key")
        for j in (d.get("jobRoles") or [])
    ]),
    ("cert-support", lambda d: [
        ((j.get("certificateDetails") or {}).get("supportingDocumentURLDetails") or {}).get("location")
        for j in (d.get("jobRoles") or [])
    ]),
    ("association", lambda d: [(a or {}).get("supportingDocuments") for a in (d.get("associations") or [])]),
]


def is_file_key(k):
    """Folder path (no file) ko chhod do; sirf asli file keys."""
    if not k or not isinstance(k, str) or k.startswith("http"):
        return False
    tail = k.rsplit("/", 1)[-1]
    ext = "." + tail.rsplit(".", 1)[-1].lower() if "." in tail else ""
    return ext in EXT_OK or ("/doc/" in k and "." in tail)


def safe(name):
    name = name.rsplit("/", 1)[-1]
    name = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", name).strip()
    return name[:120] or "file"


def doc_jobs(data):
    """(category, key) list for one assessor."""
    jobs, seen = [], set()
    for label, getter in DOC_FIELDS:
        n = 0
        for key in getter(data):
            if is_file_key(key) and key not in seen:
                seen.add(key)
                n += 1
                jobs.append((f"{label}{'-' + str(n) if n > 1 else ''}", key))
    return jobs


def done_ids():
    if os.path.exists(DONE_FILE):
        with open(DONE_FILE, encoding="utf-8") as f:
            return {x.split("\t")[0] for x in f.read().split("\n") if x}
    return set()


def read_cache(offset):
    out = []
    if not os.path.exists(CACHE_FILE):
        return out, offset
    with open(CACHE_FILE, "rb") as f:
        f.seek(offset)
        chunk = f.read()
    end = chunk.rfind(b"\n") + 1
    for line in chunk[:end].decode("utf-8", "replace").splitlines():
        try:
            o = json.loads(line)
            out.append((o["_id"], o["data"]))
        except Exception:
            pass
    return out, offset + end


def token_source(args):
    if args.no_browser:
        tok = None
        if os.path.exists(SESSION_FILE) and not args.new_token:
            try:
                tok = json.load(open(SESSION_FILE)).get("token")
            except Exception:
                tok = None
        if not tok:
            print("\nChrome Console: copy(localStorage.access_token)")
            tok = input("Token paste karo: ").strip().strip('"').strip("'")
            json.dump({"token": tok}, open(SESSION_FILE, "w"))

        class F:
            def token(self, bad=None):
                return tok

            def close(self):
                pass
        return F()
    from sidh_browser import PortalBrowser
    print("[*] Chrome window khul rahi hai. Usme SIDH portal par login karo (pehle se ho to kuch mat karo).")
    return PortalBrowser()


def download(sess, key, dest):
    """True=done/skip, False=401, None=retry."""
    for attempt in range(4):
        try:
            r = sess.get(API + "document/v1/signedURL", params={"key": key}, timeout=30)
            if r.status_code == 401:
                return False
            if r.status_code in (429, 502, 503, 504):
                time.sleep(8 * (attempt + 1))
                continue
            if r.status_code != 200:
                return True  # file missing / no access -> skip
            url = r.json()
            f = requests.get(url, timeout=90)
            if f.status_code != 200 or not f.content:
                return True
            with open(dest + ".part", "wb") as fh:
                fh.write(f.content)
            os.replace(dest + ".part", dest)
            return True
        except Exception:
            time.sleep(3 * (attempt + 1))
    return None


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int)
    p.add_argument("--delay", type=float, default=0.5, help="seconds between files (default 0.5)")
    p.add_argument("--follow", action="store_true")
    p.add_argument("--idle-stop", type=float, default=15)
    p.add_argument("--no-browser", action="store_true")
    p.add_argument("--new-token", action="store_true")
    args = p.parse_args()

    os.makedirs(DOC_DIR, exist_ok=True)
    src = token_source(args)
    token = src.token()
    sess = requests.Session()
    sess.headers.update({"Authorization": token, "Accept": "application/json, text/plain, */*"})

    have = done_ids()
    print(f"[*] Pehle se done: {len(have)} IDs", flush=True)
    offset, files, assessors, idle, retry = 0, 0, 0, time.time(), []
    t0 = time.time()
    try:
        while True:
            recs, offset = read_cache(offset)
            todo = [(a, d) for a, d in recs + retry if a not in have]
            retry = []
            for aid, data in todo:
                if args.limit and assessors >= args.limit:
                    break
                jobs = doc_jobs(data)
                if not jobs:
                    have.add(aid)
                    continue
                folder = os.path.join(DOC_DIR, aid)
                os.makedirs(folder, exist_ok=True)
                ok = True
                for cat, key in jobs:
                    dest = os.path.join(folder, f"{cat}_{safe(key)}")
                    if os.path.exists(dest):
                        continue
                    res = download(sess, key, dest)
                    if res is False:
                        print("\n[!] Token expire. Naya le raha hoon (browser mein login rehna)...", flush=True)
                        token = src.token(bad=token)
                        sess.headers["Authorization"] = token
                        res = download(sess, key, dest)
                    if res is None:
                        ok = False
                        continue
                    files += 1
                    time.sleep(args.delay)
                if ok:
                    with open(DONE_FILE, "a", encoding="utf-8") as f:
                        f.write(f"{aid}\t{len(jobs)}\n")
                    have.add(aid)
                    assessors += 1
                    if assessors % 20 == 0 or assessors <= 3:
                        rate = assessors / max(1e-6, time.time() - t0) * 60
                        print(f"  [{assessors}] {aid}  files:{files}  ({rate:.0f} assessors/min)", flush=True)
            if args.limit and assessors >= args.limit:
                break
            if todo:
                idle = time.time()
            if not args.follow:
                break
            if time.time() - idle > args.idle_stop * 60:
                print(f"[*] {args.idle_stop:.0f} min se naya ID nahi -> band.")
                break
            time.sleep(30)
    except KeyboardInterrupt:
        print("\n[!] Ctrl+C - ruk gaya. Jo files aa chuki wo saved hain; dobara chalao to resume.")
    finally:
        src.close()
    print(f"[OK] {assessors} assessors, {files} files -> {DOC_DIR}\\")


if __name__ == "__main__":
    main()
