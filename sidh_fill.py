"""
sidh_fill.py
============
Excel ki har Assessor ID ke liye SIDH API se poori details laata hai
(Personal, Contact/Address, Education, Experience, Associated Agency, Certification)
aur nayi Excel file banata hai. Original file ko touch nahi karta.

API: GET https://adminservices.skillindiadigital.gov.in/api/user/v1/assessor/{ID}
     (wahi call jo "Link Assessor" mein ID + TAB dabane par chalti hai)

Kaam karne ka tareeka:
  1. Output Excel (agar pehle se bani hai) warna input Excel padhta hai.
  2. Same ID ki doosri rows se khaali cells bharta hai (DOB, Mobile, Address...).
  3. Jis ID ki saari rows ke saare cells bhar gaye, ya jo pehle scan ho chuki
     ("Scanned On" bhara hai / cache mein hai) -> dubara scan NAHI karta.
  4. Baaki IDs API se laata hai; har certificate row (QP Code) alag se bharta hai.
  5. Har 50 minute (--save-every) Excel save karta hai, aur end mein bhi.

Token:
  Chrome mein portal login karo -> F12 -> Console -> ye chalao:
      copy(localStorage.access_token)
  Phir script chalate waqt paste karo (session.json mein save ho jata hai).

Usage:
  python sidh_fill.py --limit 2 --output test_filled.xlsx   # demo
  python sidh_fill.py                       # sirf adhoori IDs scan
  python sidh_fill.py --full                # agency details ke liye har ID scan
  python sidh_fill.py --build-only          # sirf cache se Excel dobara banao
"""

import argparse
import json
import os
import random
import sys
import time
from collections import OrderedDict, defaultdict, deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime

import openpyxl
import requests
from openpyxl.cell import WriteOnlyCell
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Font, PatternFill

from sidh_browser import expired, token_expiry

API_BASE = "https://adminservices.skillindiadigital.gov.in/api/user/v1/assessor/"
DEFAULT_INPUT = "pan_assessors_2026-10-15.xlsx"
DEFAULT_OUTPUT = "pan_assessors_filled.xlsx"
CACHE_FILE = "assessor_raw.jsonl"
FAILED_FILE = "failed_ids.txt"
SESSION_FILE = "session.json"

# Person-level columns: same ID ki har row mein same value honi chahiye
PERSON_COLS = ["Assessor Name", "Nationality", "State", "Gender", "DOB", "Mobile Number",
               "Email ID", "Address", "District (Original)", "District", "Pincode"]
# Sheet 1 mein add hone wale columns
EXTRA_COLS = ["Associated Agency Companies", "Agency SPOC / Person", "Current Agency (Active)",
              "Religion", "Category", "Languages Known", "Education", "Registration Status",
              "Scanned On", "Photo File"]
PHOTO_DIR = "photos"


def photo_files():
    """ID -> 'photos/ID.ext' (sidh_photos.py ne jo download kiya)."""
    out = {}
    if os.path.isdir(PHOTO_DIR):
        for f in os.listdir(PHOTO_DIR):
            if not f.startswith("_") and not f.endswith(".part"):
                out[os.path.splitext(f)[0]] = f"{PHOTO_DIR}/{f}"
    return out
ASSOC_HEADER = ["Assessor ID", "Assessor Name", "Agency Company Name", "Agency ID",
                "SPOC / Linked By", "From", "To", "Linking Type", "Current (valid today)"]


# --------------------------------------------------------------------------- helpers
def xl(v):
    """Excel mein allowed na hone wale control characters hatao."""
    return ILLEGAL_CHARACTERS_RE.sub("", v) if isinstance(v, str) else v


def blank(v):
    return v is None or str(v).strip() in ("", "REVIEW")


def d10(s):
    """ISO date -> DD-MM-YYYY (Excel jaisa). Empty/0001 -> ''."""
    if not s or not isinstance(s, str) or s.startswith("0001"):
        return ""
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").strftime("%d-%m-%Y")
    except ValueError:
        return s[:10]


def nm(o, key="name"):
    return (o.get(key) or "") if isinstance(o, dict) else ""


def join(vals, sep=", "):
    out = []
    for v in vals:
        v = str(v).strip() if v is not None else ""
        if v and v not in out:
            out.append(v)
    return sep.join(out)


def val(v, key="name"):
    """API kabhi string deta hai, kabhi {id, name}, kabhi list."""
    if isinstance(v, dict):
        return v.get(key) or ""
    if isinstance(v, list):
        return join(val(x, key) for x in v)
    return str(v) if v not in (None, "") else ""


ORIGINATION = {"domestic": "Indian National", "international": "Foreign National"}


def exp_text(t):
    t = t or {}
    y, m = t.get("years"), t.get("months")
    if y is None or m is None or y < 0 or m < 0:
        return ""
    return f"{y}y {m}m"


def aa_id_of(x):
    lb = x.get("linkedby") or {}
    aa = x.get("assessmentAgencyUserName") or lb.get("userName") or ""
    doc = x.get("supportingDocuments") or ""
    if not aa and "AA_" in doc:  # purane 2021 links: AA ID sirf document path mein
        aa = "AA_" + doc.split("AA_", 1)[1].split("/", 1)[0]
    return aa


def valid_today(v, today):
    v = v or {}
    return (v.get("fromDate") or "9")[:10] <= today <= (v.get("toDate") or "0")[:10]




def ver_key(v):
    """'1', '1.0', 1.0 -> '1.0' taaki match ho."""
    s = str(v).strip() if v is not None else ""
    try:
        return f"{float(s):.1f}"
    except ValueError:
        return s


# --------------------------------------------------------------------------- flatten
def flatten(o, aa_names):
    a = o.get("address") or {}
    idn = o.get("identity") or {}
    jr = o.get("jobRoles") or []
    assoc = o.get("associations") or []
    edu = o.get("education") or []
    ind = o.get("industrialExperience") or []
    trn = o.get("trainingExperience") or []
    spoc = o.get("spoc") or {}
    today = datetime.now().strftime("%Y-%m-%d")

    # --- Associated Assessment Agency: company name zaroori, SPOC alag
    agencies, agency_ids, spocs, assoc_lines = [], [], [], []
    for x in assoc:
        lb = x.get("linkedby") or {}
        aa = aa_id_of(x)
        company = x.get("assessmentAgencyName") or aa_names.get(aa, "")
        person = lb.get("name") or ""
        v = x.get("validity") or {}
        agencies.append(company or f"[{aa}]")
        agency_ids.append(aa)
        if person:
            spocs.append(f"{person} ({company or aa})")
        assoc_lines.append(
            f"{company or '[name not shown]'} [{aa}] | SPOC: {person or '-'} | "
            f"{d10(v.get('fromDate'))} to {d10(v.get('toDate'))} | {x.get('linkedType') or '-'}"
        )
    # isActive API mein hamesha false aata hai -> current = aaj ki date validity mein ho
    current_names = join(
        x.get("assessmentAgencyName") or aa_names.get(aa_id_of(x), "") or aa_id_of(x)
        for x in assoc if valid_today(x.get("validity"), today)
    )

    # --- Certification
    cert_lines, valid_till = [], []
    for j in jr:
        c = j.get("certificateDetails") or {}
        raw_vt = c.get("certificateValidTillDate")
        vt = d10(raw_vt)
        if vt:
            valid_till.append(raw_vt[:10])
        cert_lines.append(
            f"{j.get('jobName') or ''} ({j.get('jobRoleId') or ''} v{j.get('version') or ''}) | "
            f"{nm(j.get('sector'))} | {c.get('typeOfCertification') or ''} | "
            f"{'Master' if j.get('isMaster') else ('Certified' if j.get('isCertified') else 'Not certified')} | "
            f"Issue {d10(c.get('certificateIssueDate'))} | Till {vt} | {c.get('certificateStatus') or ''}"
        )
    valid_till.sort()

    def exp_lines(lst):
        out = []
        for e in lst:
            du = e.get("duration") or {}
            end = "Present" if du.get("isCurrentlyWorking") else d10(du.get("endDate"))
            out.append(
                f"{(e.get('sector') or {}).get('sector') or ''} | {e.get('jobTitle') or ''} | "
                f"{e.get('detailsOfIndustries') or ''} | {e.get('employmentType') or ''} | "
                f"{d10(du.get('startDate'))} to {end} | {exp_text(e.get('totalExperience'))}"
            )
        return "\n".join(out)

    pan = idn.get("pan") or {}
    orig = val(o.get("originationCategory"))
    rec = OrderedDict()
    # Personal Information
    rec["Assessor ID"] = o.get("userName") or ""
    rec["Assessor Name"] = o.get("name") or ""
    rec["Registration Status"] = o.get("status") or ""
    rec["Email ID"] = o.get("email") or ""
    rec["Mobile Number"] = str(o.get("phone")) if o.get("phone") else ""
    rec["DOB"] = d10(o.get("dob"))
    rec["Gender"] = o.get("gender") or ""
    rec["Religion"] = val(o.get("religion"))
    rec["Category"] = val(o.get("category"))
    rec["Origination Category"] = ORIGINATION.get(orig.lower(), orig)
    rec["Languages Known"] = join(nm(l) for l in (o.get("languagesKnown") or []))
    rec["Disability"] = val(o.get("disability"), "pwdDesc")
    rec["Applicant Category"] = val(o.get("applicantCategory"))
    rec["Aadhaar (masked)"] = idn.get("value") or (idn.get("aadhaar") or {}).get("aadharNumber") or ""
    rec["Aadhaar Verified"] = "Yes" if idn.get("isValidated") else "No"
    rec["PAN"] = pan.get("value") or ""
    # Contact & Address
    rec["Address"] = join([a.get("addressLine1"), a.get("landmark")])
    rec["State"] = nm(a.get("state"))
    rec["District"] = nm(a.get("district"))
    rec["Pincode"] = a.get("pincode") or ""
    rec["Parliament Constituency"] = nm(a.get("parliamentConstituency"))
    rec["Country"] = nm(a.get("country"))
    # Education
    rec["Education"] = join(
        nm(e.get("education"), "eduQualDesc") + (f" - {e['details']}" if e.get("details") else "") for e in edu
    )
    # Professional Experience
    rec["Designation"] = val(o.get("designation"))
    rec["Specialization"] = val(o.get("specialization"))
    rec["Industry Exp (months)"] = o.get("totalIndustrialExperienceInMonth") or 0
    rec["Training Exp (months)"] = o.get("totalTrainingExperienceInMonth") or 0
    rec["Industry Experience Details"] = exp_lines(ind)
    rec["Training Experience Details"] = exp_lines(trn)
    # Associated Assessment Agency
    rec["Current Agency (Active)"] = current_names
    rec["Associated Agency Companies"] = join(agencies)
    rec["Associated Agency IDs"] = join(agency_ids)
    rec["Agency SPOC / Person"] = join(spocs, "; ")
    rec["No. of Associations"] = len(assoc)
    rec["Association Details"] = "\n".join(assoc_lines)
    rec["Assessor SPOC Email"] = spoc.get("email") or ""
    rec["Assessor SPOC Mobile"] = str(spoc.get("mobileNumber")) if spoc.get("mobileNumber") else ""
    # Certification
    rec["No. of Job Roles"] = len(jr)
    rec["Certified Job Roles"] = sum(1 for j in jr if j.get("isCertified"))
    rec["Master Job Roles"] = sum(1 for j in jr if j.get("isMaster"))
    rec["Sectors (all)"] = join(nm(j.get("sector")) for j in jr)
    rec["QP Codes (all)"] = join(j.get("jobRoleId") for j in jr)
    rec["Earliest Valid Till"] = d10(valid_till[0]) if valid_till else ""
    rec["Latest Valid Till"] = d10(valid_till[-1]) if valid_till else ""
    rec["Certification Details"] = "\n".join(cert_lines)
    rec["SDMS ID"] = o.get("sdmsId") or ""
    rec["Profile Updated On"] = d10(o.get("updatedOn"))
    # Sheet 1 ke original column naam
    rec["Nationality"] = rec["Origination Category"]
    rec["District (Original)"] = rec["District"]
    return rec


def cert_values(o):
    """(QP Code, version) -> certificate columns, har certificate row ke liye."""
    out = {}
    for j in o.get("jobRoles") or []:
        c = j.get("certificateDetails") or {}
        v = {
            "Sector": nm(j.get("sector")),
            "Domain Job Role": j.get("jobName") or "",
            "Version": j.get("version") or "",
            "Certification Type": c.get("typeOfCertification") or "",
            "TOA Status": "Master Assessor" if j.get("isMaster") else ("Certified Assessor" if j.get("isCertified") else ""),
            "Valid From": d10(c.get("certificateIssueDate")),
            "Valid Till": d10(c.get("certificateValidTillDate")),
            "Validity Extended": "Yes" if c.get("isValidityExtended") else "No",
        }
        qp = (j.get("jobRoleId") or "").strip()
        out[(qp, ver_key(j.get("version")))] = v
        out.setdefault((qp, ""), v)
    return out


# --------------------------------------------------------------------------- IO
def read_sheet(ws):
    rows = ws.iter_rows(values_only=True)
    header = [h for h in next(rows)]
    data = [list(r) for r in rows if r and r[0]]
    return header, data


def load_base(args):
    """Output Excel (pehle ka kaam) agar hai, warna input Excel."""
    prev_details, prev_assoc = {}, defaultdict(list)
    src = args.output if os.path.exists(args.output) else args.input
    print(f"[*] Reading {src} ...")
    wb = openpyxl.load_workbook(src, read_only=True)
    header, data = read_sheet(wb.worksheets[0])
    if src == args.output:
        if "Assessor Details" in wb.sheetnames:
            dh, dd = read_sheet(wb["Assessor Details"])
            prev_details = {"__header__": dh, **{str(r[0]): r for r in dd}}
        if "Agency Associations" in wb.sheetnames:
            _, ad = read_sheet(wb["Agency Associations"])
            for r in ad:
                prev_assoc[str(r[0])].append(r)
    wb.close()
    for c in EXTRA_COLS:  # purani output mein naya column na ho to add
        if c not in header:
            header.append(c)
            for r in data:
                r.append(None)
    for r in data:
        r += [None] * (len(header) - len(r))
        r[0] = str(r[0]).strip()
    return header, data, prev_details, prev_assoc


def orig_cols(header):
    return [h for h in header if h not in EXTRA_COLS]


def sibling_fill(header, data):
    """Same ID ki doosri rows se person-level khaali cells bharo."""
    idx = {h: i for i, h in enumerate(header)}
    cols = [idx[c] for c in PERSON_COLS if c in idx]
    best = defaultdict(dict)
    for r in data:
        for j in cols:
            if not blank(r[j]):
                best[r[0]].setdefault(j, r[j])
    n = 0
    for r in data:
        for j in cols:
            if blank(r[j]) and j in best[r[0]]:
                r[j] = best[r[0]][j]
                n += 1
    return n


def complete_ids(header, data, full):
    """Jis ID ki har row ke saare cells bhare hain."""
    idx = {h: i for i, h in enumerate(header)}
    need = [idx[h] for h in orig_cols(header)]
    scanned = idx["Scanned On"]
    bad = set()
    allids = set()
    for r in data:
        allids.add(r[0])
        if not blank(r[scanned]):
            continue  # pehle scan ho chuka
        if full or any(blank(r[j]) for j in need):
            bad.add(r[0])
    return allids - bad


def load_cache():
    cache = {}
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        obj = json.loads(line)
                        cache[obj["_id"]] = obj
                    except Exception:
                        pass
    return cache


def agency_name_map(cache):
    """AA ID -> company name, sab records se (purane blank links bharne ke liye)."""
    m = {}
    for c in cache.values():
        for x in c["data"].get("associations") or []:
            if x.get("assessmentAgencyName"):
                m.setdefault(aa_id_of(x), x["assessmentAgencyName"])
    return m


# --------------------------------------------------------------------------- token
class FileToken:
    """Purana tareeka: token paste karo (--no-browser)."""

    def __init__(self, args):
        self.args = args

    def token(self, bad=None):
        tok = None
        if os.path.exists(self.args.session) and not self.args.new_token and bad is None:
            try:
                with open(self.args.session) as f:
                    tok = json.load(f).get("token")
            except Exception:
                tok = None
            exp = token_expiry(tok) if tok else None
            if exp and exp < datetime.now():
                tok = None
        while not tok or tok == bad:
            print("\nChrome mein portal kholo -> F12 -> Console -> chalao:  copy(localStorage.access_token)")
            tok = input("Token yahan paste karo aur Enter dabao: ").strip().strip('"').strip("'")
        with open(self.args.session, "w") as f:
            json.dump({"token": tok}, f)
        exp = token_expiry(tok)
        if exp:
            print(f"[*] Token valid till: {exp:%d-%m-%Y %H:%M}")
        return tok

    def close(self):
        pass


def token_source(args):
    if args.no_browser:
        return FileToken(args)
    from sidh_browser import PortalBrowser
    print("[*] Chrome window khul rahi hai. Usme SIDH portal par login karo (pehle se ho to kuch mat karo).")
    return PortalBrowser()


# --------------------------------------------------------------------------- photos
SIGNED_URL = "https://adminservices.skillindiadigital.gov.in/api/document/v1/signedURL"
NO_PHOTO_FILE = os.path.join(PHOTO_DIR, "_no_photo.txt")
IMG_EXT = {"image/png": ".png", "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/gif": ".gif",
           "image/webp": ".webp", "application/pdf": ".pdf"}


def photo_done_ids():
    have = set(photo_files())
    if os.path.exists(NO_PHOTO_FILE):
        with open(NO_PHOTO_FILE, encoding="utf-8") as f:
            have.update(x.split("\t")[0] for x in f.read().split("\n") if x)
    return have


def photo_key(data):
    p = data.get("photo") or {}
    return (p.get("url") if isinstance(p, dict) else "") or ""


def mark_no_photo(aid, why):
    with open(NO_PHOTO_FILE, "a", encoding="utf-8") as f:
        f.write(f"{aid}\t{why}\n")


# --------------------------------------------------------------------------- scrape
def scrape(jobs, args, autosave):
    """jobs: deque of ("data", aid) / ("photo", aid, key). 401 par login ka wait, phir aage."""
    if not jobs:
        return
    os.makedirs(PHOTO_DIR, exist_ok=True)
    src = token_source(args)
    token = src.token()
    sess = requests.Session()
    sess.headers.update({"Authorization": token, "Accept": "application/json, text/plain, */*"})
    need = [False]
    failed = []
    n_data = sum(1 for j in jobs if j[0] == "data")
    cnt = {"data": 0, "photo": 0}
    t0 = last_save = time.time()

    def work(job):
        err = ""
        for attempt in range(5):
            if need[0]:
                return job, "401", None
            try:
                if job[0] == "data":
                    r = sess.get(API_BASE + job[1], timeout=30)
                else:
                    r = sess.get(SIGNED_URL, params={"key": job[2]}, timeout=30)
            except Exception as e:
                err = str(e)
                time.sleep(3 * (attempt + 1))
                continue
            if r.status_code == 401:
                need[0] = True
                return job, "401", None
            if r.status_code in (429, 502, 503, 504):
                err = f"HTTP {r.status_code}"
                time.sleep(10 * (attempt + 1))
                continue
            if r.status_code != 200:
                return job, f"HTTP {r.status_code}", None
            if job[0] == "data":
                return job, None, r.json()
            try:
                img = requests.get(r.json(), timeout=60)
            except Exception as e:
                err = str(e)
                time.sleep(3 * (attempt + 1))
                continue
            if img.status_code != 200 or not img.content:
                return job, f"image HTTP {img.status_code}", None
            return job, None, ((img.headers.get("Content-Type") or "").split(";")[0].lower(), img.content)
        return job, err or "failed", None

    def handle(job, err, res, cf):
        kind, aid = job[0], job[1]
        if err == "401":
            jobs.appendleft(job)  # naye token ke baad dobara
            return
        if kind == "data":
            if res is None:
                failed.append(f"{aid}\t{err}")
                return
            rec = {"_id": aid, "scanned": datetime.now().strftime("%d-%m-%Y %H:%M"), "data": res}
            cf.write(json.dumps(rec, ensure_ascii=False) + "\n")
            cf.flush()
            cnt["data"] += 1
            if args.photos:
                key = photo_key(res)
                if key:
                    jobs.appendleft(("photo", aid, key))
                else:
                    mark_no_photo(aid, "no photo on portal")
            if cnt["data"] % 25 == 0 or cnt["data"] <= 2:
                rate = cnt["data"] / max(1e-6, time.time() - t0)
                left = (n_data - cnt["data"]) / rate / 60 if rate else 0
                print(f"  [{cnt['data']}/{n_data}] {aid} {res.get('name') or ''}  "
                      f"| photos {cnt['photo']}  (~{left:.0f} min left)", flush=True)
        else:
            if res is None:
                mark_no_photo(aid, err)
                return
            ctype, content = res
            ext = IMG_EXT.get(ctype) or (os.path.splitext(job[2])[1].lower() or ".jpg")
            path = os.path.join(PHOTO_DIR, aid + ext)
            with open(path + ".part", "wb") as f:
                f.write(content)
            os.replace(path + ".part", path)
            cnt["photo"] += 1

    try:
        with open(CACHE_FILE, "a", encoding="utf-8") as cf, ThreadPoolExecutor(args.workers) as ex:
            pending = set()

            def collect(block=False):
                if not pending:
                    return
                done_set, _ = wait(pending, timeout=None if block else 0.5, return_when=FIRST_COMPLETED)
                for fu in done_set:
                    pending.discard(fu)
                    handle(*fu.result(), cf)

            while jobs or pending:
                if need[0] or (jobs and expired(token)):
                    while pending:
                        collect(block=True)
                    print("\n[!] Token expire ho gaya. Naya token le raha hoon ...", flush=True)
                    token = src.token(bad=token)
                    sess.headers["Authorization"] = token
                    need[0] = False
                    continue
                if args.save_every and time.time() - last_save >= args.save_every * 60:
                    while pending:
                        collect(block=True)
                    print(f"\n[*] {args.save_every:g} min ho gaye -> progress Excel mein save ...", flush=True)
                    autosave()
                    last_save = time.time()
                if jobs and len(pending) < args.workers:
                    pending.add(ex.submit(work, jobs.popleft()))
                    time.sleep(args.delay * (0.7 + random.random() * 0.6))
                    collect()
                else:
                    collect(block=not jobs)
    except KeyboardInterrupt:
        need[0] = True
        print("\n[!] Ctrl+C - ruk gaya. Ab tak ka data Excel mein save ho raha hai ...")
    finally:
        src.close()
    print(f"[*] Is run mein: {cnt['data']} assessors, {cnt['photo']} photos")
    if failed:
        with open(FAILED_FILE, "a", encoding="utf-8") as f:
            f.write("\n".join(failed) + "\n")
        print(f"[!] {len(failed)} failed -> {FAILED_FILE}")


# --------------------------------------------------------------------------- excel
def build_excel(header, data, prev_details, prev_assoc, sel, args):
    t0 = time.time()
    cache = load_cache()
    aa_names = agency_name_map(cache)
    keep = set(sel) if sel is not None else None
    flat, certs = {}, {}
    for k, c in cache.items():
        if keep is None or k in keep:
            flat[k] = flatten(c["data"], aa_names)
            flat[k]["Scanned On"] = c.get("scanned") or "yes"
            certs[k] = cert_values(c["data"])

    idx = {h: i for i, h in enumerate(header)}
    cert_cols = ["Sector", "Domain Job Role", "Version", "Certification Type", "TOA Status",
                 "Valid From", "Valid Till", "Validity Extended"]
    fill_cols = [c for c in PERSON_COLS + EXTRA_COLS if c in idx]

    wb = openpyxl.Workbook(write_only=True)
    bold = Font(bold=True)
    new_col = PatternFill("solid", fgColor="FFF2CC")
    green = PatternFill("solid", fgColor="E2EFDA")
    link_font = Font(color="0563C1", underline="single")
    photos = photo_files()

    def head(ws, cols, mark_from=None):
        cells = []
        for i, h in enumerate(cols):
            c = WriteOnlyCell(ws, value=xl(h))
            c.font = bold
            if mark_from is not None and i >= mark_from:
                c.fill = new_col
            cells.append(c)
        ws.append(cells)

    # Sheet 1: original rows; khaali cells API se bhare (green)
    ws = wb.create_sheet("PAN Assessors")
    ws.freeze_panes = "B2"
    head(ws, header, mark_from=len(orig_cols(header)))
    n_rows = n_cells = 0
    for r in data:
        aid = r[0]
        rec = flat.get(aid)
        if keep is not None and not rec:
            continue
        row, changed = list(r), set()
        if rec:
            for col in fill_cols:
                j = idx[col]
                if blank(row[j]) and rec.get(col) not in (None, ""):
                    row[j] = rec[col]
                    changed.add(j)
            j_qp, j_v = idx.get("QP Code"), idx.get("Version")
            if j_qp is not None:
                cv = certs[aid].get((str(row[j_qp] or "").strip(), ver_key(row[j_v]) if j_v is not None else "")) \
                    or certs[aid].get((str(row[j_qp] or "").strip(), ""))
                for col in cert_cols:
                    j = idx.get(col)
                    if cv and j is not None and blank(row[j]) and cv.get(col):
                        row[j] = cv[col]
                        changed.add(j)
            # Scanned/agency columns hamesha latest API se
            for col in EXTRA_COLS:
                if col != "Photo File":
                    row[idx[col]] = rec.get(col, "")
        j_photo = idx["Photo File"]
        if aid in photos:
            row[j_photo] = photos[aid]
        cells = []
        for j, v in enumerate(row):
            c = WriteOnlyCell(ws, value=xl(v))
            if j in changed:
                c.fill = green
            if j == j_photo and v:
                c.hyperlink = v  # relative path: Excel file ke saath photos\ folder
                c.font = link_font
            cells.append(c)
        ws.append(cells)
        n_rows += 1
        n_cells += len(changed)

    # Sheet 2: ek assessor = ek row, saari details (pehle ki output ki rows bhi rakho)
    ws2 = wb.create_sheet("Assessor Details")
    ws2.freeze_panes = "B2"
    cols = list(next(iter(flat.values())).keys()) if flat else prev_details.get("__header__", [])
    cols = [c for c in cols if c not in ("Nationality", "District (Original)")]
    head(ws2, cols)
    seen = set()
    for r in data:
        aid = r[0]
        if aid in seen or (keep is not None and aid not in keep):
            continue
        seen.add(aid)
        if aid in flat:
            ws2.append([xl(flat[aid].get(c, "")) for c in cols])
        elif aid in prev_details:
            old_h = prev_details["__header__"]
            old = dict(zip(old_h, prev_details[aid]))
            ws2.append([xl(old.get(c, "")) for c in cols])

    # Sheet 3: ek association = ek row
    ws3 = wb.create_sheet("Agency Associations")
    ws3.freeze_panes = "B2"
    head(ws3, ASSOC_HEADER)
    today = datetime.now().strftime("%Y-%m-%d")
    for aid in seen:
        c = cache.get(aid)
        if not c:
            for old in prev_assoc.get(aid, []):
                ws3.append([xl(v) for v in old])
            continue
        o = c["data"]
        for x in o.get("associations") or []:
            lb = x.get("linkedby") or {}
            aa = aa_id_of(x)
            v = x.get("validity") or {}
            row3 = [aid, o.get("name") or "", x.get("assessmentAgencyName") or aa_names.get(aa, ""), aa,
                    lb.get("name") or "", d10(v.get("fromDate")), d10(v.get("toDate")),
                    x.get("linkedType") or "", "Yes" if valid_today(v, today) else "No"]
            ws3.append([xl(z) for z in row3])

    tmp = args.output + ".tmp.xlsx"
    wb.save(tmp)
    try:
        os.replace(tmp, args.output)
        out = args.output
    except PermissionError:
        out = args.output.replace(".xlsx", "_autosave.xlsx")
        os.replace(tmp, out)
        print(f"[!] {args.output} Excel mein khuli hai, isliye {out} mein save kiya.")
    print(f"[OK] Excel saved -> {out}  ({n_rows} rows, {len(flat)} scanned IDs, "
          f"{n_cells} cells API se bhare, {time.time() - t0:.0f}s)")


# --------------------------------------------------------------------------- main
def main():
    p = argparse.ArgumentParser(description="SIDH assessor details -> Excel")
    p.add_argument("--input", default=DEFAULT_INPUT)
    p.add_argument("--output", help=f"default {DEFAULT_OUTPUT} (--limit/--ids par test_filled.xlsx)")
    p.add_argument("--session", default=SESSION_FILE)
    p.add_argument("--limit", type=int, help="sirf pehli N adhoori IDs (demo)")
    p.add_argument("--ids", help="comma-separated IDs (complete ho tab bhi scan)")
    p.add_argument("--full", action="store_true",
                   help="har ID scan karo (agency/religion etc. ke liye), sirf pehle scan hui skip")
    p.add_argument("--workers", type=int, default=2, help="parallel requests (default 2)")
    p.add_argument("--delay", type=float, default=0.6, help="seconds between requests (default 0.6)")
    p.add_argument("--save-every", type=float, default=50, help="har N minute Excel save (default 50)")
    p.add_argument("--no-browser", action="store_true", help="browser mat kholo, token paste karo")
    p.add_argument("--new-token", action="store_true", help="--no-browser ke saath: naya token paste karo")
    p.add_argument("--no-photos", dest="photos", action="store_false", help="photos download mat karo")
    p.add_argument("--build-only", action="store_true", help="fetch mat karo, sirf Excel banao")
    args = p.parse_args()
    if not args.output:  # test run kabhi main output overwrite na kare
        args.output = "test_filled.xlsx" if (args.limit or args.ids) else DEFAULT_OUTPUT

    if os.path.abspath(args.output) == os.path.abspath(args.input):
        sys.exit("[X] Output file input se alag honi chahiye.")

    resumed = os.path.exists(args.output)  # pehle ki output se aage badh rahe hain
    header, data, prev_details, prev_assoc = load_base(args)
    n = sibling_fill(header, data)
    cache = load_cache()
    all_ids = list(dict.fromkeys(r[0] for r in data))
    done_ids = complete_ids(header, data, args.full)
    print(f"[*] Rows: {len(data)} | IDs: {len(all_ids)} | same-ID rows se {n} cells bhare")
    print(f"[*] Complete / pehle scan: {len(done_ids)} | cache mein: {len(cache)}")

    if args.ids:
        sel = [s.strip() for s in args.ids.split(",") if s.strip()]
        todo = [i for i in sel if i not in cache]
    else:
        pending = [i for i in all_ids if i not in done_ids and i not in cache]
        sel = None
        if args.limit:
            pending = pending[: args.limit]
            # test output pehle se hai -> uski IDs bhi rakho (warna file khaali ho jayegi)
            sel = list(dict.fromkeys(all_ids + pending)) if resumed else pending
        todo = pending
    jobs = deque(("data", i) for i in todo)
    n_photo = 0
    if args.photos:  # pehle scan hue IDs jinki photo abhi nahi aayi
        have = photo_done_ids()
        for i, c in cache.items():
            if (sel is None or i in sel) and i not in have and photo_key(c["data"]):
                jobs.append(("photo", i, photo_key(c["data"])))
                n_photo += 1
    print(f"[*] Scan karni hain: {len(todo)} IDs | pending photos (purane): {n_photo}"
          + (f"  (~{(len(todo) * 1.6 + n_photo) * args.delay / 60 * 1.1:.0f} min)" if jobs else ""))

    def autosave():
        build_excel(header, data, prev_details, prev_assoc, sel, args)

    if jobs and not args.build_only:
        scrape(jobs, args, autosave)
    autosave()


if __name__ == "__main__":
    main()
