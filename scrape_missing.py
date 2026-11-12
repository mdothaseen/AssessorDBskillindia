"""
scrape_missing.py
=================
pan_assessors_missing_ids.json wali IDs scrape karta hai (jo kisi .jsonl mein nahi thi).
me_scraper_api.py ka hi logic use karta hai - resumable hai.
Output: pan_missing_data.jsonl
"""

import me_scraper_api as m

m.IDS_FILE  = "pan_assessors_missing_ids.json"
m.DATA_FILE = "pan_missing_data.jsonl"

if __name__ == "__main__":
    m.main()
