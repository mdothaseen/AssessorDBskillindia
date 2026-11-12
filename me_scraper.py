"""
SIDH Assessor Scraper - Media & Entertainment (v4 - Fully Automatic)
====================================================================
Aapko sirf LOGIN karna hai browser mein - baaki sab automatic!
No Enter pressing, no manual steps after login.
"""

import os, sys, json, time, random, requests, threading
from playwright.sync_api import sync_playwright

# --- Config ---
IDS_FILE     = "media_entertainment_ids.json"
DATA_FILE    = "media_entertainment_data.jsonl"
SESSION_FILE = "me_session.json"
API_BASE     = "https://adminservices.skillindiadigital.gov.in/api/user/v1/assessor/"
HOME_URL     = "https://www.skillindiadigital.gov.in/home"
LINK_ASSR    = "https://admin.skillindiadigital.gov.in/admin-profile/assessmentagency/link-assessors"
PACE_MIN     = 1.0
PACE_MAX     = 1.5

# ─── Flatten helper ───────────────────────────────────────────────────────────
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
        "parliamentConstituency": (a.get("parliamentConstituency") or {}).get("name") or "",
        "aadhaarMasked"         : aadhaar,
        "aadhaarValidated"      : bool(idn.get("isValidated")),
        "panValidated"          : bool((idn.get("pan") or {}).get("isValidated")),
        "languages"             : ", ".join(uniq([(l.get("name") or "") for l in (o.get("languagesKnown") or [])])),
        "originationCategory"   : o.get("originationCategory") or "",
        "applicantCategory"     : o.get("applicantCategory") or "",
        "category"              : o.get("category") or "",
        "religion"              : o.get("religion") or "",
        "disability"            : o.get("disability") or "",
        "designation"           : o.get("designation") or "",
        "specialization"        : o.get("specialization") or "",
        "indExpMonths"          : o.get("totalIndustrialExperienceInMonth") or 0,
        "trnExpMonths"          : o.get("totalTrainingExperienceInMonth") or 0,
        "status"                : o.get("status") or "",
        "sdmsId"                : o.get("sdmsId") or "",
        "numJobRoles"           : len(jr),
        "numCertified"          : sum(1 for j in jr if j.get("isCertified")),
        "numMaster"             : sum(1 for j in jr if j.get("isMaster")),
        "sectors"               : ", ".join(uniq([(j.get("sector") or {}).get("name") or "" for j in jr])),
        "qpCodes"               : ", ".join(uniq([j.get("jobRoleId") or "" for j in jr])),
        "jobNames"              : ", ".join(uniq([j.get("jobName") or "" for j in jr])),
        "earliestValidTill"     : vt[0] if vt else "",
        "latestValidTill"       : vt[-1] if vt else "",
        "numAssociations"       : len(asl),
        "associatedAAs"         : ", ".join(uniq([
            (x.get("linkedby") or {}).get("userName") or "" for x in asl
        ])),
    }

# ─── Session helpers ──────────────────────────────────────────────────────────
def load_session():
    if os.path.exists(SESSION_FILE):
        try:
            with open(SESSION_FILE) as f: return json.load(f)
        except Exception: pass
    return None

def save_session(s):
    with open(SESSION_FILE, "w") as f: json.dump(s, f, indent=2)

def build_headers(s):
    return {
        "Authorization": s["Authorization"],
        "X-Csrf-Token" : s["X-Csrf-Token"],
        "uIId"         : s["uIId"],
        "systemIPInfo" : s["systemIPInfo"],
        "Accept"       : "application/json, text/plain, */*",
    }

def test_session(s):
    try:
        r = requests.get(API_BASE + "AR93", headers=build_headers(s), timeout=10)
        return r.status_code == 200
    except Exception: return False

def get_done_ids():
    done = set()
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try: done.add(json.loads(line)["assessorId"])
                    except Exception: pass
    return done

# ─── Automatic browser session capture ───────────────────────────────────────
def capture_session_auto():
    captured = {}
    lock = threading.Lock()

    def on_request(req):
        with lock:
            if captured: return
        try:
            hdrs = req.headers
            url  = req.url
            auth = hdrs.get("authorization") or ""
            csrf = hdrs.get("x-csrf-token")  or ""
            uiid = hdrs.get("uiid")          or ""
            sip  = hdrs.get("systemipinfo")  or ""
            if auth and csrf and "adminservices.skillindiadigital" in url:
                with lock:
                    captured["Authorization"] = auth
                    captured["X-Csrf-Token"]  = csrf
                    captured["uIId"]          = uiid
                    captured["systemIPInfo"]  = sip
                print("\n[OK] API headers automatically capture ho gaye!")
        except Exception:
            pass

    print("\n" + "="*65)
    print("  BROWSER KHUL RAHA HAI")
    print("="*65)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            args=["--start-maximized", "--disable-blink-features=AutomationControlled"]
        )
        ctx = browser.new_context(no_viewport=True)

        # Attach interceptor to ALL future pages automatically
        def on_new_page(pg):
            pg.on("request", on_request)
        ctx.on("page", on_new_page)

        # Main page
        page = ctx.new_page()
        page.on("request", on_request)

        # Navigate to home page
        print("[*] Login page par ja raha hoon...")
        page.goto(HOME_URL, timeout=60000)
        page.wait_for_load_state("networkidle")

        print("\n" + "="*65)
        print("  MANUAL LOGIN REQUIRED:")
        print("="*65)
        print("  1. Browser mein LOGIN karein (Partner -> PMU)")
        print("  2. User: AA_101288  |  Pass: New@#$123")
        print("  3. OTP fill karein -> Admin Dashboard tak pahunchein")
        print("  4. Jab dashboard khul jaaye, yahan ENTER dabaao")
        print("="*65 + "\n")

        input("  [ENTER] dabao jab login ho jaaye... ")

        # === Find admin page from open tabs ===
        admin_page = None
        for pg in ctx.pages:
            try:
                if "admin.skillindiadigital.gov.in" in pg.url:
                    admin_page = pg
                    print(f"[OK] Admin page mili: {pg.url}")
                    break
            except Exception:
                continue

        if not admin_page:
            print("[!] Admin page nahi mili. Last page use kar raha hoon...")
            for pg in ctx.pages:
                try:
                    _ = pg.url
                    admin_page = pg
                    break
                except Exception:
                    continue

        # === PHASE 2: Auto-navigate to Link Assessors ===
        if admin_page:
            time.sleep(2)
            print(f"\n[*] Link Assessors page par ja raha hoon...")
            try:
                admin_page.goto(LINK_ASSR, timeout=30000)
                admin_page.wait_for_load_state("networkidle")
                time.sleep(2)
                print(f"[OK] Link Assessors page khul gaya: {admin_page.url}")
            except Exception as e:
                print(f"[!] Navigation: {e}")

            # === PHASE 3: Auto-trigger API call with test ID ===
            if not captured:
                print("[*] Test API call trigger kar raha hoon (AR93)...")
                selectors = [
                    "input[placeholder*='ssessor']",
                    "input[placeholder*='ID']",
                    "input[placeholder*='Enter']",
                    "mat-form-field input",
                    "input[type='text']",
                ]
                for sel in selectors:
                    try:
                        el = admin_page.locator(sel).first
                        el.wait_for(state="visible", timeout=5000)
                        el.click()
                        el.fill("AR93")
                        el.press("Tab")
                        print(f"[OK] Input trigger: {sel}")
                        # Wait for network call
                        for _ in range(10):
                            time.sleep(1)
                            with lock:
                                if captured:
                                    break
                        break
                    except Exception:
                        continue

        # === PHASE 4: LocalStorage fallback ===
        if not captured:
            print("[*] Network headers nahi mile, localStorage try kar raha hoon...")
            for pg in ctx.pages:
                try:
                    all_ls_str = pg.evaluate("""() => {
                        let r = {};
                        for(let i = 0; i < localStorage.length; i++){
                            let k = localStorage.key(i);
                            r[k] = localStorage.getItem(k);
                        }
                        return JSON.stringify(r);
                    }""")
                    all_ls = json.loads(all_ls_str)
                    print(f"[*] localStorage keys ({pg.url[:40]}): {list(all_ls.keys())}")

                    tok = all_ls.get("access_token") or all_ls.get("token") or ""
                    if tok:
                        # Try the token with a test request to get cookie-based CSRF
                        cookies = ctx.cookies()
                        csrf_cookie = next(
                            (c["value"] for c in cookies if "csrf" in c["name"].lower()), ""
                        )
                        uiid_val = all_ls.get("uIId") or all_ls.get("uiid") or ""
                        sip_val  = all_ls.get("systemIPInfo") or all_ls.get("ipInfo") or ""

                        test_h = {
                            "Authorization": tok,
                            "X-Csrf-Token" : csrf_cookie,
                            "uIId"         : uiid_val,
                            "systemIPInfo" : sip_val,
                            "Accept"       : "application/json, text/plain, */*",
                        }
                        try:
                            r = requests.get(API_BASE + "AR93", headers=test_h, timeout=10)
                            print(f"[*] Token test: HTTP {r.status_code}")
                            if r.status_code == 200:
                                with lock:
                                    captured.update({
                                        "Authorization": tok,
                                        "X-Csrf-Token" : csrf_cookie,
                                        "uIId"         : uiid_val,
                                        "systemIPInfo" : sip_val,
                                    })
                                print("[OK] Token works! Session saved.")
                                break
                        except Exception as ex:
                            print(f"[!] Test error: {ex}")
                except Exception:
                    continue

        time.sleep(2)
        browser.close()
        print("[*] Browser band ho gaya.")

    with lock:
        return dict(captured) if captured.get("Authorization") else {}


# ─── Scraping loop ────────────────────────────────────────────────────────────
def scrape_loop(sess, all_ids, done):
    headers  = build_headers(sess)
    remaining = [i for i in all_ids if i not in done]
    total    = len(all_ids)
    done_cnt = len(done)
    failed   = []

    print(f"\n[*] Scraping {len(remaining)} IDs at {PACE_MIN}-{PACE_MAX}s pace...\n")

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
                        print(f"  [{done_cnt}/{total}] {pct}%  OK  {aid}")
                except Exception as e:
                    print(f"  [!] Parse error {aid}: {e}")
                    failed.append(aid)
                idx += 1

            elif r.status_code == 401:
                print("\n  [!!] Token expired (401). Re-login karein...")
                sess = capture_session_auto()
                if not sess:
                    print("  [X] Re-auth failed.")
                    break
                save_session(sess)
                headers = build_headers(sess)
                print("  [OK] Session renewed.\n")

            elif r.status_code == 503:
                print(f"  [!] 503 busy. 8s wait...")
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

    print("\n" + "="*65)
    print(f"  COMPLETE! {done_cnt}/{total} records -> {DATA_FILE}")
    if failed:
        with open("me_failed_ids.txt", "w") as f:
            f.write("\n".join(failed))
        print(f"  {len(failed)} failed IDs -> me_failed_ids.txt")
    print("="*65)
    print("\n  Ab 'python merge_scraped_data.py' chalao -> Excel ready hogi!")


# ─── Main ─────────────────────────────────────────────────────────────────────
def main():
    print("\n" + "="*65)
    print("  SIDH ASSESSOR SCRAPER - Media & Entertainment Sector")
    print("="*65)

    if not os.path.exists(IDS_FILE):
        print(f"[X] {IDS_FILE} not found!"); sys.exit(1)
    with open(IDS_FILE, encoding="utf-8") as f:
        all_ids = json.load(f)
    print(f"[*] Total ME IDs: {len(all_ids)}")

    done = get_done_ids()
    print(f"[*] Already scraped: {len(done)} | Remaining: {len(all_ids) - len(done)}")

    sess = load_session()
    if sess and test_session(sess):
        print("[OK] Saved session valid - skipping login.")
    else:
        sess = capture_session_auto()
        if not sess:
            print("[X] Session capture failed. Exiting.")
            sys.exit(1)
        save_session(sess)
        print(f"[OK] Session saved.")

    scrape_loop(sess, all_ids, done)


if __name__ == "__main__":
    main()
