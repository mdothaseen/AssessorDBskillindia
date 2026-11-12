"""
merge_scraped_data.py
=====================
Scraped media_entertainment_data.jsonl ko
original List of Assessors (1).xls mein merge karta hai.

Naye columns jo add honge:
  Mobile Number, Email ID, Address, DOB,
  Assessment Agency Name, From Date, To Date

Output: ME_Assessors_Updated.xlsx (same folder)
"""

import json
import os
import pandas as pd
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

XLS_PATH    = r"List of Assessors (1).xls"
JSONL_PATH  = r"media_entertainment_data.jsonl"
OUTPUT_PATH = r"ME_Assessors_Updated.xlsx"
SECTOR      = "Media & Entertainment"


def load_scraped(path):
    """Load scraped JSONL into dict keyed by assessorId."""
    data = {}
    if not os.path.exists(path):
        print(f"[!] {path} not found — no scraped data to merge.")
        return data
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rec = json.loads(line)
                    data[rec["assessorId"]] = rec
                except Exception:
                    pass
    print(f"[*] Loaded {len(data)} scraped records from {path}")
    return data


def build_address(rec):
    parts = []
    for field in ["addressLine1", "landmark", "district", "state", "pincode"]:
        val = rec.get(field, "").strip()
        if val:
            parts.append(val)
    return ", ".join(parts)


def get_cert_dates_for_qp(scraped_raw_path, assessor_id, qp_code):
    """
    For a specific assessorId + QP Code, find the certificateIssueDate
    and certificateValidTillDate from the raw jobRoles.
    Since we only stored the flattened record we'll use qpCodes field
    matching — but From/To dates need the per-QP raw data.
    
    For now, we use earliestValidTill / latestValidTill from the flattened record
    as a best-effort since we don't store per-QP dates in the flat format.
    This can be enhanced later with raw JSON storage.
    """
    return "", ""


def main():
    # ── Load original Excel ──────────────────────────────────────────────────
    print(f"[*] Loading {XLS_PATH}...")
    df = pd.read_excel(XLS_PATH, dtype=str)
    df.columns = [c.strip() for c in df.columns]
    total_rows = len(df)
    print(f"[*] Total rows: {total_rows}")

    # Filter Media & Entertainment only
    me_df = df[df["Sector"] == SECTOR].copy()
    other_df = df[df["Sector"] != SECTOR].copy()
    print(f"[*] Media & Entertainment rows: {len(me_df)}")

    # ── Load scraped data ────────────────────────────────────────────────────
    scraped = load_scraped(JSONL_PATH)

    # ── Add new columns to ME rows ───────────────────────────────────────────
    new_cols = ["Mobile Number", "Email ID", "Address", "DOB",
                "Assessment Agency Name", "From Date", "To Date"]

    for col in new_cols:
        me_df[col] = ""
        other_df[col] = ""

    matched = 0
    for idx, row in me_df.iterrows():
        aid = str(row.get("Assessor ID", "")).strip()
        if aid in scraped:
            rec = scraped[aid]
            matched += 1

            me_df.at[idx, "Mobile Number"]          = rec.get("phone", "")
            me_df.at[idx, "Email ID"]               = rec.get("email", "")
            me_df.at[idx, "Address"]                = build_address(rec)
            me_df.at[idx, "DOB"]                    = rec.get("dob", "")
            me_df.at[idx, "Assessment Agency Name"] = rec.get("associatedAAs", "")

            # From Date / To Date: use the valid till dates available in flat record
            # Best effort: earliestValidTill = From, latestValidTill = To
            me_df.at[idx, "From Date"] = rec.get("earliestValidTill", "")
            me_df.at[idx, "To Date"]   = rec.get("latestValidTill", "")

    print(f"[*] Matched {matched} / {len(me_df)} ME rows with scraped data.")

    # ── Combine back ─────────────────────────────────────────────────────────
    final_df = pd.concat([me_df, other_df], ignore_index=True)
    print(f"[*] Final DataFrame rows: {len(final_df)}")

    # ── Write to Excel ───────────────────────────────────────────────────────
    print(f"[*] Writing to {OUTPUT_PATH}...")
    with pd.ExcelWriter(OUTPUT_PATH, engine="openpyxl") as xw:
        final_df.to_excel(xw, index=False, sheet_name="Assessors")
        ws = xw.sheets["Assessors"]

        # Style header
        hdr_fill = PatternFill("solid", fgColor="1F3864")
        hdr_font = Font(color="FFFFFF", bold=True)
        new_fill = PatternFill("solid", fgColor="2E75B6")  # blue for new cols
        thin     = Side(style="thin", color="D9D9D9")
        border   = Border(left=thin, right=thin, top=thin, bottom=thin)

        # Find column indices of new columns
        header_row = [cell.value for cell in ws[1]]
        new_col_indices = [header_row.index(c) + 1 for c in new_cols if c in header_row]

        for cell in ws[1]:
            if cell.column in new_col_indices:
                cell.fill = new_fill
            else:
                cell.fill = hdr_fill
            cell.font      = hdr_font
            cell.alignment = Alignment(horizontal="center", vertical="center")

        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions

        # Column widths
        for col in ws.columns:
            w = max((len(str(x.value)) if x.value else 0) for x in col)
            ws.column_dimensions[col[0].column_letter].width = min(max(w + 2, 10), 50)

        # Borders
        for row in ws.iter_rows(min_row=1):
            for cell in row:
                cell.border = border

    print(f"\n[✓] Done! Output saved to: {OUTPUT_PATH}")
    print(f"    Rows total: {len(final_df)}")
    print(f"    ME rows with scraped data: {matched}")


if __name__ == "__main__":
    main()
