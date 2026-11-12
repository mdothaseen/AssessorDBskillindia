import os
import json
import time
import random
import requests
from playwright.sync_api import sync_playwright

# Configuration
ASSESSOR_IDS_FILE = "assessor_ids.json"
DATA_FILE = "data.jsonl"
SESSION_FILE = "session.json"
CHROME_PROFILE_DIR = "./sidh_chrome_profile"
API_BASE = "https://adminservices.skillindiadigital.gov.in/api/user/v1/assessor/"

def clean_date(s):
    if not s or not isinstance(s, str) or s.startswith("0001"):
        return ""
    return s[:10]

def get_unique_non_empty(lst):
    return sorted(list(set(x for x in lst if x)))

def flatten(o):
    a = o.get("address") or {}
    idn = o.get("identity") or {}
    jr = o.get("jobRoles") or []
    as_list = o.get("associations") or []
    
    # Extract certificateValidTillDates
    vt_dates = []
    for j in jr:
        cert = j.get("certificateDetails")
        if cert:
            date_str = cert.get("certificateValidTillDate")
            cleaned = clean_date(date_str)
            if cleaned:
                vt_dates.append(cleaned)
    vt_dates.sort()
    
    # Aadhaar Masked
    aadhaar_masked = idn.get("value") or ""
    if not aadhaar_masked:
        aadhaar_obj = idn.get("aadhaar") or {}
        aadhaar_masked = aadhaar_obj.get("aadharNumber") or ""
        
    pan_obj = idn.get("pan") or {}
    pan_validated = bool(pan_obj.get("isValidated"))
    
    languages_list = o.get("languagesKnown") or []
    languages = ", ".join(get_unique_non_empty([l.get("name") for l in languages_list]))
    
    sectors = ", ".join(get_unique_non_empty([j.get("sector", {}).get("name") for j in jr if j.get("sector")]))
    qp_codes = ", ".join(get_unique_non_empty([j.get("jobRoleId") for j in jr]))
    job_names = ", ".join(get_unique_non_empty([j.get("jobName") for j in jr]))
    
    associated_aas = ", ".join(get_unique_non_empty([x.get("linkedby", {}).get("userName") for x in as_list if x.get("linkedby")]))
    
    return {
        "assessorId": o.get("userName") or "",
        "name": o.get("name") or "",
        "email": o.get("email") or "",
        "phone": str(o.get("phone") or ""),
        "gender": o.get("gender") or "",
        "dob": clean_date(o.get("dob")),
        "state": a.get("state", {}).get("name") if a.get("state") else "",
        "district": a.get("district", {}).get("name") if a.get("district") else "",
        "pincode": a.get("pincode") or "",
        "addressLine1": a.get("addressLine1") or "",
        "landmark": a.get("landmark") or "",
        "parliamentConstituency": a.get("parliamentConstituency", {}).get("name") if a.get("parliamentConstituency") else "",
        "aadhaarMasked": aadhaar_masked,
        "aadhaarValidated": bool(idn.get("isValidated")),
        "panValidated": pan_validated,
        "languages": languages,
        "originationCategory": o.get("originationCategory") or "",
        "applicantCategory": o.get("applicantCategory") or "",
        "category": o.get("category") or "",
        "religion": o.get("religion") or "",
        "disability": o.get("disability") or "",
        "designation": o.get("designation") or "",
        "specialization": o.get("specialization") or "",
        "indExpMonths": o.get("totalIndustrialExperienceInMonth") or 0,
        "trnExpMonths": o.get("totalTrainingExperienceInMonth") or 0,
        "status": o.get("status") or "",
        "sdmsId": o.get("sdmsId") or "",
        "numJobRoles": len(jr),
        "numCertified": sum(1 for j in jr if j.get("isCertified")),
        "numMaster": sum(1 for j in jr if j.get("isMaster")),
        "sectors": sectors,
        "qpCodes": qp_codes,
        "jobNames": job_names,
        "earliestValidTill": vt_dates[0] if vt_dates else "",
        "latestValidTill": vt_dates[-1] if vt_dates else "",
        "numAssociations": len(as_list),
        "associatedAAs": associated_aas
    }

def capture_headers():
    print("\n" + "="*70)
    print("LAUNCHING BROWSER FOR LOGIN & HEADER CAPTURE")
    print("Please do the following:")
    print("1. Log in to the SIDH Portal (if not already logged in).")
    print("2. Go to: AA Profile -> Associations -> Link Assessor")
    print("   (URL: https://admin.skillindiadigital.gov.in/admin-profile/assessmentagency/link-assessors)")
    print("3. Type any Assessor ID (e.g. AR93) and press TAB to trigger the request.")
    print("="*70 + "\n")
    
    captured = {}
    
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            CHROME_PROFILE_DIR, 
            headless=False, 
            args=["--start-maximized"],
            no_viewport=True
        )
        page = context.pages[0] if context.pages else context.new_page()
        
        def on_request(request):
            url = request.url
            if "/api/user/v1/assessor/" in url:
                req_headers = request.headers
                csrf = next((v for k, v in req_headers.items() if k.lower() == "x-csrf-token"), None)
                auth = next((v for k, v in req_headers.items() if k.lower() == "authorization"), None)
                uiid = next((v for k, v in req_headers.items() if k.lower() == "uiid"), None)
                ip = next((v for k, v in req_headers.items() if k.lower() == "systemipinfo"), None)
                
                if csrf and auth:
                    captured["Authorization"] = auth
                    captured["X-Csrf-Token"] = csrf
                    captured["uIId"] = uiid
                    captured["systemIPInfo"] = ip
                    print("[✓] API Headers Captured Successfully!")
                    page.close()
        
        page.on("request", on_request)
        
        # Navigate to home
        try:
            page.goto("https://www.skillindiadigital.gov.in/home")
        except Exception:
            pass # Ignore navigation errors if window is closed or redirected quickly
            
        while not captured:
            page.wait_for_timeout(1000)
            if page.is_closed():
                break
                
        context.close()
        
    return captured

def load_session():
    if os.path.exists(SESSION_FILE):
        try:
            with open(SESSION_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return None

def save_session(session_data):
    with open(SESSION_FILE, "w") as f:
        json.dump(session_data, f, indent=4)

def test_session(headers):
    # Test session validity by querying a dummy ID
    test_id = "AR93"
    url = f"{API_BASE}{test_id}"
    req_headers = {
        "Authorization": headers.get("Authorization"),
        "X-Csrf-Token": headers.get("X-Csrf-Token"),
        "uIId": headers.get("uIId"),
        "systemIPInfo": headers.get("systemIPInfo"),
        "Accept": "application/json, text/plain, */*"
    }
    try:
        r = requests.get(url, headers=req_headers, timeout=10)
        return r.status_code == 200
    except Exception:
        return False

def get_completed_ids():
    completed = set()
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    try:
                        record = json.loads(line)
                        completed.add(record["assessorId"])
                    except Exception:
                        pass
    return completed

def main():
    # Load assessor IDs
    if not os.path.exists(ASSESSOR_IDS_FILE):
        print(f"Error: {ASSESSOR_IDS_FILE} not found. Please place it in the same directory.")
        return
        
    with open(ASSESSOR_IDS_FILE, "r", encoding="utf-8") as f:
        all_ids = json.load(f)
        
    print(f"Loaded {len(all_ids)} assessor IDs from {ASSESSOR_IDS_FILE}")
    
    # Get completed records for resuming
    completed = get_completed_ids()
    print(f"Found {len(completed)} already completed records. Resuming...")
    
    # Get active session
    session = load_session()
    if not session or not test_session(session):
        print("No active or valid session found. Let's log in and capture headers.")
        session = capture_headers()
        if not session:
            print("Failed to capture headers. Browser was closed before capture. Exiting.")
            return
        save_session(session)
        
    print("Session active. Starting loop...")
    
    headers = {
        "Authorization": session["Authorization"],
        "X-Csrf-Token": session["X-Csrf-Token"],
        "uIId": session["uIId"],
        "systemIPInfo": session["systemIPInfo"],
        "Accept": "application/json, text/plain, */*"
    }
    
    count = len(completed)
    failed_ids = []
    
    # Filter remaining IDs
    remaining_ids = [i for i in all_ids if i not in completed]
    
    idx = 0
    while idx < len(remaining_ids):
        assessor_id = remaining_ids[idx]
        url = f"{API_BASE}{assessor_id}"
        
        try:
            r = requests.get(url, headers=headers, timeout=15)
            
            if r.status_code == 200:
                try:
                    data = r.json()
                    flat_record = flatten(data)
                    
                    # Write immediately to file
                    with open(DATA_FILE, "a", encoding="utf-8") as f:
                        f.write(json.dumps(flat_record, ensure_ascii=False) + "\n")
                        
                    count += 1
                    if count % 10 == 0 or idx == 0:
                        print(f"Progress: {count}/{len(all_ids)} completed successfully.")
                        
                except Exception as e:
                    print(f"Error parsing record {assessor_id}: {e}")
                    failed_ids.append(assessor_id)
                
                idx += 1
                
            elif r.status_code == 401:
                print("\n[!] Unauthorized (401). Token probably expired. Re-capturing session headers...")
                # Re-capture headers
                session = capture_headers()
                if not session:
                    print("Failed to re-capture session. Exiting.")
                    return
                save_session(session)
                # Update headers dict
                headers["Authorization"] = session["Authorization"]
                headers["X-Csrf-Token"] = session["X-Csrf-Token"]
                headers["uIId"] = session["uIId"]
                headers["systemIPInfo"] = session["systemIPInfo"]
                print("Session renewed. Continuing...\n")
                # Do NOT increment index, retry this ID
                
            elif r.status_code == 503:
                print(f"[!] Server Busy (503) on {assessor_id}. Waiting 6 seconds before retry...")
                time.sleep(6)
                # Do NOT increment index, retry this ID
                
            else:
                print(f"[!] Request for {assessor_id} failed with code {r.status_code}. Skipping.")
                failed_ids.append(assessor_id)
                idx += 1
                
        except Exception as e:
            print(f"[!] Error fetching {assessor_id}: {e}. Skipping.")
            failed_ids.append(assessor_id)
            idx += 1
            
        # Paced delay (1.0 to 1.5 seconds)
        time.sleep(1.0 + random.random() * 0.5)
        
    print("\n" + "="*50)
    print("Scraping completed!")
    print(f"Total processed: {count}/{len(all_ids)}")
    if failed_ids:
        print(f"Failed IDs: {len(failed_ids)} (Saved to failed_ids.txt)")
        with open("failed_ids.txt", "w") as f:
            for fid in failed_ids:
                f.write(fid + "\n")
    print("="*50 + "\n")

if __name__ == "__main__":
    main()
