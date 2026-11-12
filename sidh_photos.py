"""
sidh_photos.py
==============
assessor_raw.jsonl (sidh_fill.py ka cache) se har assessor ki photo download karta hai:
    photos/AR171908.png, photos/AR120770.jpg ...

- Jo photo pehle se folder mein hai, use skip karta hai (resume).
- --follow: cache badhta rahe (sidh_fill.py chal raha ho) to naye IDs bhi uthata rahe.
- Token: session.json (sidh_fill.py wala). 401 aaye to naya token paste karne ko bolega.

Flow: photo.url (storage key) -> GET /api/document/v1/signedURL?key=<key> -> S3 link -> image

Usage:
  python sidh_photos.py --limit 3     # test
  python sidh_photos.py --follow      # main run ke saath saath
  python sidh_photos.py               # cache mein jo hai sab
"""

import argparse
import json
import os
import sys
import time

import requests

API = "https://adminservices.skillindiadigital.gov.in/api/"
CACHE_FILE = "assessor_raw.jsonl"
PHOTO_DIR = "photos"
SESSION_FILE = "session.json"
NO_PHOTO_FILE = os.path.join(PHOTO_DIR, "_no_photo.txt")
EXT = {"image/png": ".png", "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/gif": ".gif",
       "image/webp": ".webp", "application/pdf": ".pdf"}


def existing():
    have = set()
    if os.path.isdir(PHOTO_DIR):
        for f in os.listdir(PHOTO_DIR):
            if not f.startswith("_") and not f.endswith(".part"):
                have.add(os.path.splitext(f)[0])
    if os.path.exists(NO_PHOTO_FILE):
        with open(NO_PHOTO_FILE, encoding="utf-8") as f:
            have.update(x.split("\t")[0] for x in f.read().split("\n") if x)
    return have


def read_cache(offset):
    """Cache file ko offset se padho; (records, new_offset). Adhoori last line chhod do."""
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
            out.append((o["_id"], ((o.get("data") or {}).get("photo") or {}).get("url") or ""))
        except Exception:
            pass
    return out, offset + end


def load_token(new=False):
    tok = None
    if not new and os.path.exists(SESSION_FILE):
        with open(SESSION_FILE) as f:
            tok = json.load(f).get("token")
    if not tok:
        print("\nChrome Console mein chalao:  copy(localStorage.access_token)")
        tok = input("Token paste karo aur Enter: ").strip().strip('"').strip("'")
        with open(SESSION_FILE, "w") as f:
            json.dump({"token": tok}, f)
    return tok


def mark_none(aid, why):
    with open(NO_PHOTO_FILE, "a", encoding="utf-8") as f:
        f.write(f"{aid}\t{why}\n")


def download(sess, aid, key):
    """True = done/skip permanently, False = token problem, None = retry later."""
    for attempt in range(4):
        try:
            r = sess.get(API + "document/v1/signedURL", params={"key": key}, timeout=30)
            if r.status_code == 401:
                return False
            if r.status_code in (429, 502, 503, 504):
                time.sleep(10 * (attempt + 1))
                continue
            if r.status_code != 200:
                mark_none(aid, f"signedURL HTTP {r.status_code}")
                return True
            url = r.json()
            img = requests.get(url, timeout=60)
            if img.status_code != 200 or not img.content:
                mark_none(aid, f"image HTTP {img.status_code}")
                return True
            ctype = (img.headers.get("Content-Type") or "").split(";")[0].lower()
            ext = EXT.get(ctype) or (os.path.splitext(key)[1].lower() or ".jpg")
            path = os.path.join(PHOTO_DIR, aid + ext)
            with open(path + ".part", "wb") as f:
                f.write(img.content)
            os.replace(path + ".part", path)
            return True
        except Exception:
            time.sleep(3 * (attempt + 1))
    return None


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int)
    p.add_argument("--delay", type=float, default=0.8, help="seconds between photos (default 0.8)")
    p.add_argument("--follow", action="store_true", help="cache mein naye IDs aate rahein to chalta rahe")
    p.add_argument("--idle-stop", type=float, default=15, help="--follow: itne min tak naya na aaye to band")
    p.add_argument("--new-token", action="store_true")
    args = p.parse_args()

    os.makedirs(PHOTO_DIR, exist_ok=True)
    sess = requests.Session()
    sess.headers.update({"Authorization": load_token(args.new_token),
                         "Accept": "application/json, text/plain, */*"})
    have = existing()
    print(f"[*] Pehle se: {len(have)} (photo ya no-photo)", flush=True)
    offset, done, idle_since, retry = 0, 0, time.time(), []
    t0 = time.time()
    while True:
        recs, offset = read_cache(offset)
        todo = [(a, k) for a, k in recs + retry if a not in have]
        retry = []
        for aid, key in todo:
            if args.limit and done >= args.limit:
                break
            if not key:
                mark_none(aid, "no photo on portal")
                have.add(aid)
                continue
            res = download(sess, aid, key)
            if res is False:
                print("\n[!!] Token expire (401). Chrome mein login karke naya token lo, phir:")
                print("     python sidh_photos.py --follow --new-token")
                sys.exit(1)
            if res is None:
                retry.append((aid, key))
                continue
            have.add(aid)
            done += 1
            if done % 50 == 0 or done <= 3:
                print(f"  [{done}] {aid}  ({done / max(1, time.time() - t0) * 60:.0f}/min)", flush=True)
            time.sleep(args.delay)
        if args.limit and done >= args.limit:
            break
        if todo:
            idle_since = time.time()
        if not args.follow:
            break
        if time.time() - idle_since > args.idle_stop * 60:
            print(f"[*] {args.idle_stop:.0f} min se naya ID nahi aaya -> band.")
            break
        time.sleep(30)
    print(f"[OK] {done} photos downloaded -> {PHOTO_DIR}\\  (total folder: {len(existing())})")


if __name__ == "__main__":
    main()
