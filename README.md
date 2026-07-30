# JHFRC Address to Census Tract Converter

Converts a file of addresses into U.S. Census tract IDs (GEOIDs), so JHFRC can link them to community data **without needing any names or personal details**. Built by the Journey Health Foundation Research Center for nonprofit partners.

---

## What you need

An Excel (`.xlsx`) or CSV file containing **only two things**:

- a **unique ID** column (for example `client_id`), and
- **address** columns — either one full-address column, or separate street, city, state, and ZIP columns.

Do **not** include names, birth dates, or any other personal information. As a safety check, the tool reads your **column names** and stops if it sees anything sensitive (`name`, `dob`, `ssn`, `phone`, `email`, etc.).

| client_id | street | city | state | zip |
|-----------|--------|------|-------|-----|
| 1001 | 123 Main St | Nashville | TN | 37201 |

---

## First-time setup (once)

You need **Python 3.10 or later** ([python.org](https://www.python.org/downloads/); on Windows, tick **"Add Python to PATH"**).

1. Download this project: green **Code** button → **Download ZIP**, then unzip it.
2. Open a terminal **inside the project folder**:
   - **Windows:** open the folder in File Explorer, click the address bar, type `cmd`, press Enter.
   - **Mac:** open Terminal, type `cd ` (with a space), drag the folder in, press Enter.
3. Install the requirements:
   ```
   pip install -r requirements.txt
   ```

---

## How to run it

**1. Make a safe input file.** Don't use your original records. Create a new file with **only** the ID and address columns and save it in `data/input/` (for example `data/input/clients.xlsx`).

**2. Run one command** (all on one line). Use the version that matches your file:

Separate street / city / state / ZIP columns:
```
python main.py --input data/input/clients.xlsx --output data/output/results.xlsx --id-column client_id --street-column street --city-column city --state-column state --zip-column zip --approve-egress
```

One full-address column:
```
python main.py --input data/input/clients.xlsx --output data/output/results.xlsx --id-column client_id --address-column full_address --approve-egress
```

Change the file names and column names to match your file. `--approve-egress` confirms it's OK to look up the addresses online — the tool contacts nothing without it.

*First run only:* it downloads the official Census map (~55 MB, once). After that, about 1,000 addresses take roughly 2 minutes.

**3. Check the results.** Open the output file — it has your columns plus the Census tract. The terminal prints a summary like `Matched total : 995 (99.5%)`. A few unmatched rows is normal; if more than ~10% are `No_Match`, email us a few examples.

**4. Merge the tracts back into your own file.** The result file lists each ID with its `census_tract_geoid`. In your own file, look it up by ID:
```
=XLOOKUP(A2, [results.xlsx]Sheet1!$A:$A, [results.xlsx]Sheet1!$G:$G, "Not found")
```
(Adjust the column letters to where your ID and `census_tract_geoid` sit.) Tract IDs are text and some start with `0`, so format the destination column as **Text** first (Home → Number Format → Text) so Excel keeps the leading zero.

---

## Your results file

Your original columns, plus:

| Column | Meaning |
|--------|---------|
| `census_tract_geoid` | The 11-digit Census tract (e.g. `47065001600`). Blank if not matched. |
| `match_status` | Any `Matched…` value means it got a tract. `No_Match`, `Tie`, and `Rejected` are the ones to review. |
| `error_reason` | Plain-language reason a row was not matched. |

For the best match rate, include the full city, state, and ZIP in every address.

---

## Sharing results with JHFRC

Send only the **output file**. Confirm it contains just the ID, address, and Census tract columns — no names or personal data.

## Help

Questions, errors, or a high `No_Match` rate: email **mohith-addepalli@utc.edu** with a few example addresses and any error message shown.

---
---

## Advanced options (technical users)

A normal run already uses Census plus a free OpenStreetMap fallback and gets the highest match rate. The following are for larger or harder jobs.

**Network control.** Nothing is sent until you pass `--approve-egress`; without it the tool prints exactly what it *would* send, then exits. The only destinations are the geocoders (`geocoding.geo.census.gov`, plus `api.geoapify.com` / `geocode.arcgis.com` / `nominatim.openstreetmap.org` if selected) and the one-time Census map download. Tract assignment is fully local; nothing goes anywhere else.

**Stronger geocoders for the residual** (`--external-provider`):
- `geoapify` — free API key (3,000/day); accepts a match only if its ZIP equals the input ZIP.
- `arcgis` — needs a token; best coverage.

Supply keys per run with `--arcgis-token` / `--geoapify-key`, or via the `ARCGIS_TOKEN` / `GEOAPIFY_KEY` environment variables (preferred for temporary ArcGIS tokens), or config. Precedence: flag → env var → config.

**Other flags:**

| Flag | What it does |
|------|--------------|
| `--zip-approx` | Last resort: give any still-unmatched row an **approximate** tract from its ZIP centroid (marked `Matched_ZIP_Approx`). Coarse — a ZIP spans many tracts. |
| `--retry-failed` | Re-process only the `No_Match`/`Tie` rows of a previous result file and merge new matches back in. |
| `--concurrency N` | Requests sent in parallel (default 6). |
| `--no-fallback` / `--no-external-fallback` | Turn off the Census one-at-a-time retry / the external lookup. |
| `--sheet-name` / `--chunk-size` / `--config` | Excel sheet to read / rows per block / alternate settings file. |

**Large files.** Over 50,000 rows the tool auto-chunks and is resumable — rerun the same command to continue. Over 100,000 rows the free OpenStreetMap fallback auto-disables (use `--external-provider arcgis`/`geoapify` for large residuals). Over ~1,048,576 rows the output is written as CSV.

**All `match_status` values:** `Matched`, `Matched_Fallback`, `Matched_External`, `Matched_CensusAPI_Backup`, `Matched_ZIP_Approx` (approximate), `Tie`, `No_Match`, `Rejected`.
