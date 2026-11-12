# SIDH Assessor Details — API map (scanned 2026-11-05)

## Flow
- Dashboard -> View My Assessors -> `/admin-profile/assessmentagency/link-assessors` (list)
- Row "⋮" menu -> View Details -> `/link-assessors/view-assessor-details/{ASSESSOR_ID}`
  (same menu has **De-Link Assessor** — never click in automation)
- Link Assessor flow (verified): View My Assessors -> orange "LINK ASSESSOR" button -> modal "Link an Assessor"
  -> type Assessor ID -> TAB -> `GET /api/user/v1/assessor/{ID}` fills name + job roles table
  -> "View Assessor" button -> `/view-assessor-details/{ID}` (fetches the same GET again).
  Modal's "GENERATE OTP" = actual linking step — never click in automation.

## Single API = all 6 tabs
`GET https://adminservices.skillindiadigital.gov.in/api/user/v1/assessor/{ASSESSOR_ID}`

Headers: `Authorization`, `X-Csrf-Token`, `uIId`, `systemIPInfo`, `Accept: application/json`.
Response ~40 KB JSON. Other calls on the page: `/get/dropdown/data` (labels), `/api/document/v1/signedURL` (photo).

| UI tab | JSON keys |
|---|---|
| Personal Information | name, email, phone, dob, gender, religion, category, originationCategory, languagesKnown[].name, photo |
| Contact & Address | address{addressLine1, landmark, state.name, district.name, pincode, parliamentConstituency} |
| Education | education[]{education, details, supportingDocument} |
| Professional Experience | industrialExperience[], trainingExperience[] {sector, jobRole, jobTitle, employmentType, detailsOfIndustries, duration, totalExperience}, totals in months |
| Associated Assessment Agency | associations[] (see below) |
| Certification | jobRoles[]{jobRoleId, jobName, sector, isCertified, isMaster, certificateDetails{certificateID, certificateIssueDate, certificateValidTillDate, certificateStatus}} |

## Associated Assessment Agency — important
Per `associations[]` item:
- `assessmentAgencyName` = **company name** (what UI shows) — use this
- `assessmentAgencyUserName` = AA ID (e.g. AA_101288)
- `linkedby.name` = **SPOC / person** who linked (e.g. "Mohd Haseen") — NOT company
- `validity.fromDate/toDate`, `linkedType` (Freelancer), `isActive`

Old scrapers (`scraper.py`, `me_scraper_api.py`) used `linkedby.name` -> gave person names, not company.

Old (2021) associations have empty `linkedby` and no `assessmentAgencyName`; UI shows blank name.
AA ID still recoverable from `supportingDocuments` path: `Assessment Agency/AA_100419/doc/...`.
Company name for those must be mapped from another record that has the same AA ID.

Top-level `spoc` = {mobileNumber, email} — was empty in sample.
