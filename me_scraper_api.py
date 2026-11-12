"""
me_scraper_api.py
=================
Pure API scraper - no browser needed.
Sirf me_session.json se headers leke 381 IDs scrape karta hai.
Resumable hai - band ho jaye to dobara chalao, wahin se shuru hoga.
"""

import os, sys, json, time, random, requests

IDS_FILE     = "green_jobs_ids.json"
DATA_FILE    = "green_jobs_data.jsonl"
SESSION_FILE = "me_session.json"
API_BASE     = "https://adminservices.skillindiadigital.gov.in/api/user/v1/assessor/"
PACE_MIN     = 1.0
PACE_MAX     = 1.5

def clean_date(s):
    if not s or not isinstance(s, str) or s.startswith("0001"): return ""
    return s[:10]

def uniq(lst):
    return sorted(set(x for x in lst if x))

def flatten(o):
    a   = o.get("address") or {}
    idn = o.get("identity") or {}
    jr  = o.get("jobRoles") or []
    asl = o.get("associations") or []
    vt  = sorted(filter(None, [
        clean_date((j.get("certificateDetails") or {}).get("certificateValidTillDate"))
        for j in jr
    ]))
    aadhaar = idn.get("value") or (idn.get("aadhaar") or {}).get("aadharNumber") or ""
    return {
        "assessorId"            : o.get("userName") or "",
        "name"                  : o.get("name") or "",
        "email"                 : o.get("email") or "",
        "phone"                 : str(o.get("phone") or ""),
        "gender"                : o.get("gender") or "",
        "dob"                   : clean_date(o.get("dob")),
        "state"                 : (a.get("state") or {}).get("name") or "",
        "district"              : (a.get("district") or {}).get("name") or "",
        "pincode"               : a.get("pincode") or "",
        "addressLine1"          : a.get("addressLine1") or "",
        "landmark"              : a.get("landmark") or "",
        "sectors"               : ", ".join(uniq([(j.get("sector") or {}).get("name") or "" for j in jr])),
        "qpCodes"               : ", ".join(uniq([j.get("jobRoleId") or "" for j in jr])),
        "jobNames"              : ", ".join(uniq([j.get("jobName") or "" for j in jr])),
        "earliestValidTill"     : vt[0] if vt else "",
        "latestValidTill"       : vt[-1] if vt else "",
        "numAssociations"       : len(asl),
        "associatedAAs"         : ", ".join(uniq([
            f"{x.get('linkedby', {}).get('name')} ({x.get('linkedby', {}).get('userName')})" if x.get("linkedby", {}).get("name") else x.get("linkedby", {}).get("userName", "") for x in asl if x.get("linkedby")
        ])),
        "status"                : o.get("status") or "",
    }

def get_done_ids():
    done = set()
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try: done.add(json.loads(line)["assessorId"])
                    except: pass
    return done

def main():
    print("\n" + "="*60)
    print("  SIDH API SCRAPER - Media & Entertainment")
    print("="*60)

    if not os.path.exists(SESSION_FILE):
        print(f"[X] {SESSION_FILE} nahi mila!")
        print("    Pehle 'python setup_session.py' chalao.")
        sys.exit(1)

    if not os.path.exists(IDS_FILE):
        print(f"[X] {IDS_FILE} nahi mila!"); sys.exit(1)

    with open(SESSION_FILE) as f:
        sess = json.load(f)
    with open(IDS_FILE, encoding="utf-8") as f:
        all_ids = json.load(f)

    headers = {
        "Authorization": sess["Authorization"],
        "X-Csrf-Token" : sess["X-Csrf-Token"],
        "uIId"         : sess["uIId"],
        "systemIPInfo" : sess["systemIPInfo"],
        "Accept"       : "application/json, text/plain, */*",
    }

    done     = get_done_ids()
    remaining = [i for i in all_ids if i not in done]
    total    = len(all_ids)
    done_cnt = len(done)
    failed   = []

    print(f"[*] Total IDs  : {total}")
    print(f"[*] Already done: {done_cnt}")
    print(f"[*] Remaining  : {len(remaining)}")
    print(f"[*] Output file: {DATA_FILE}")
    print()

    idx = 0
    while idx < len(remaining):
        aid = remaining[idx]
        try:
            r = requests.get(API_BASE + aid, headers=headers, timeout=15)

            if r.status_code == 200:
                try:
                    rec = flatten(r.json())
                    with open(DATA_FILE, "a", encoding="utf-8") as f:
                        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    done_cnt += 1
                    if done_cnt % 10 == 0 or idx == 0:
                        pct = round(done_cnt / total * 100, 1)
                        print(f"  [{done_cnt}/{total}] {pct}%  OK  {aid}  ({rec.get('name','?')})")
                except Exception as e:
                    print(f"  [!] Parse error {aid}: {e}")
                    failed.append(aid)
                idx += 1

            elif r.status_code == 401:
                print("\n  [!!] Token expired (401)!")
                print("  Pehle 'python setup_session.py' dobara chalao,")
                print("  phir is script ko dobara chalao.")
                break

            elif r.status_code == 429:
                print(f"  [!] Rate limited. 30s wait...")
                time.sleep(30)

            elif r.status_code == 503:
                print(f"  [!] Server busy. 8s wait...")
                time.sleep(8)

            else:
                print(f"  [!] HTTP {r.status_code} for {aid}. Skip.")
                failed.append(aid)
                idx += 1

        except Exception as e:
            print(f"  [!] Error {aid}: {e}")
            failed.append(aid)
            idx += 1

        time.sleep(PACE_MIN + random.random() * (PACE_MAX - PACE_MIN))

    print("\n" + "="*60)
    print(f"  DONE! {done_cnt}/{total} records saved -> {DATA_FILE}")
    if failed:
        with open("me_failed_ids.txt", "w") as f:
            f.write("\n".join(failed))
        print(f"  {len(failed)} failed -> me_failed_ids.txt")
    print("="*60)
    print("  Ab 'python merge_scraped_data.py' chalao -> Excel!")

if __name__ == "__main__":
    main()
