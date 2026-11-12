# AssessorDB — SIDH Assessor Data Tool

Skill India Digital Hub (SIDH) ke **Assessment Agency** login se, aapke apne
"View My Assessors" list ke assessors ki details, photo aur documents nikaalne ke
scripts. Sab data ek hi API se aata hai jo portal khud aapke login ko dikhata hai.

> **Note:** Ye tool sirf us data ke liye hai jo aapke apne authorized login ko
> portal dikhata hai. Scraped data (naam, phone, PAN, photo, documents) kisi aur
> ka private data hai — ise GitHub ya kisi public jagah par mat daalo. `.gitignore`
> is data ko commit hone se rokta hai.

## Setup (naye system par)

1. Python 3.10+ install karo.
2. Repo clone karo:
   ```bash
   git clone https://github.com/mdothaseen/AssessorDBskillindia.git
   cd AssessorDBskillindia
   ```
3. Dependencies:
   ```bash
   pip install -r requirements.txt
   playwright install chromium
   ```
   (Agar system par Google Chrome installed hai to `playwright install` zaroori nahi;
   `sidh_browser.py` pehle Chrome, warna bundled Chromium use karta hai.)
4. Input Excel `pan_assessors_2026-10-15.xlsx` (assessor IDs wali) isi folder mein rakho.
   Ye repo mein nahi hai (PII), ise alag se copy karo.

## Scripts

| Script | Kaam |
|---|---|
| `sidh_fill.py`   | Har ID ki poori details nikaal kar Excel bhar deta hai (`pan_assessors_filled.xlsx`). Resume + har 50 min autosave. |
| `sidh_photos.py` | Har assessor ki photo `photos/ID.ext` mein download. |
| `sidh_docs.py`   | Har assessor ke documents (PAN, education, experience, resume, certificate...) `docs/ID/` mein. |
| `sidh_browser.py`| Chrome khol kar login se token khud le leta hai (token file mein save nahi hota). |

Purani files (`scraper.py`, `me_scraper*.py`, `merge_scraped_data.py`,
`scrape_missing.py`) ab use nahi hoti; `sidh_fill.py` unki jagah leta hai.

## Chalao

```bash
python sidh_fill.py --full          # saari IDs scan (agency/photo columns ke liye)
python sidh_fill.py                 # sirf adhoori IDs
python sidh_photos.py --follow      # photos, scan ke saath
python sidh_docs.py                 # documents
```

Chrome window khulegi — usme SIDH portal par login karo. Token expire ho to
script dobara login ka wait karti hai aur aage chal padti hai. Ctrl+C par jo
ho chuka wo saved rehta hai; dobara chalao to resume hota hai.
