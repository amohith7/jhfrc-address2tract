# JHFRC Address to Census Tract Converter

A simple tool that converts client or patient addresses into U.S. Census tract identifiers (GEOIDs). Developed by the Journey Health Foundation Research Center (JHFRC) for use by nonprofit partner organizations.

---

## Overview

Census tracts are small, neighborhood-sized geographic areas. Converting your address records to Census tracts lets JHFRC link them to community data — such as income, education, and health indicators — without ever needing names or personal identifiers. This tool reads a file of addresses and returns the Census tract (GEOID) for each one.

---

## Input Format

Prepare an Excel (`.xlsx`) or CSV (`.csv`) file containing **only**:

- **One unique identifier column** — a client ID or case number, consistent with your internal records.
- **Address information** — either one full-address column, or separate columns for street, city, state, and ZIP.

Do not include names, dates of birth, or any other personal information (see [Privacy](#privacy)). Addresses match best when they include the city, state, and ZIP.

**One full-address column:**

| client_id | full_address |
|-----------|--------------|
| 1001 | 123 Main St, Nashville, TN 37201 |
| 1002 | 456 Elm Ave, Memphis, TN 38103 |

**Separate columns:**

| client_id | street | city | state | zip |
|-----------|--------|------|-------|-----|
| 1001 | 123 Main St | Nashville | TN | 37201 |
| 1002 | 456 Elm Ave | Memphis | TN | 38103 |

---

## Privacy

Keep the file you run through this tool limited to the unique identifier and address columns — nothing else. As a safety check, the tool inspects your **column names** (not the data itself) and **stops immediately** if it sees anything suggesting personal data (for example `name`, `dob`, `ssn`, `mrn`, `diagnosis`, `insurance`, `phone`, `email`, or `notes`). If that happens, remove those columns and run again.

---

## Installation (first-time users only)

**Already set up the tool before?** Skip this section and go straight to [Step 1](#running-the-application).

This is a one-time setup, done the first time you use the tool. After completing it once, you only repeat Step 1–4 in [Running the Application](#running-the-application) for each new file.

### Prerequisites

You need **Python 3.10 or later**. To check, open a terminal — on **Windows**, press the Windows key, type `cmd`, and press **Enter**; on **Mac**, press **Command + Space**, type `Terminal`, and press **Enter** — then type:

```
python --version
```

If you see a version like `Python 3.11.2`, you are ready. Otherwise, install it from [python.org](https://www.python.org/downloads/) (on Windows, check **"Add Python to PATH"** during installation).

### Download the project

On the project's GitHub page, click the green **Code** button → **Download ZIP**, then extract it (right-click → **Extract All** on Windows, or double-click on Mac). Note where the folder is saved.

### Open a terminal inside the project folder

- **Windows:** open the project folder in File Explorer, click the address bar, type `cmd`, and press **Enter**.
- **Mac:** open **Terminal**, type `cd ` (with a trailing space), drag the project folder into the window, and press **Enter**.

To confirm you are in the right place, type `dir` (Windows) or `ls` (Mac). You should see `main.py` and `requirements.txt`.

### Create a virtual environment (recommended)

This keeps the tool's software separate from other programs. Type:

```
python -m venv .venv
```

Then activate it:

- **Windows (Command Prompt):** `.venv\Scripts\activate`
- **Windows (PowerShell):** `.venv\Scripts\Activate.ps1`
- **Mac / Linux:** `source .venv/bin/activate`

You will see `(.venv)` at the start of your prompt. Activate it again in each new terminal window.

### Install required packages

```
pip install -r requirements.txt
```

This runs once and takes a minute or two. When it finishes, you are ready.

---

## Running the Application

Repeat these four steps each time you have a file to process.

### Step 1: Create a separate file with only the ID and address columns

Do **not** run this tool on your original records. First, create a **new, empty Excel file** and copy into it **only two things**:

- your **unique identifier** column (for example `client_id`), and
- your **address** column(s) — either one full-address column, or separate street, city, state, and ZIP columns.

Do not include names, dates of birth, phone numbers, or any other personal information. Keeping this file limited to the identifier and address is what prevents any PHI or PII from reaching the tool. (Later, in Step 4, you will match the results back to your own records using the unique identifier.)

Save this new file into the `data/input/` folder inside the project — for example, `data/input/clients.xlsx`.

### Step 2: Run one command

In your terminal (opened inside the project folder), type **one** of the commands below and press **Enter**. Type it all on a single line.

**If your file has ONE column that holds the whole address** (for example a column named `full_address`):

```
python main.py --input data/input/clients.xlsx --output data/output/results.xlsx --id-column client_id --address-column full_address
```

**If your file has SEPARATE columns for street, city, state, and ZIP:**

```
python main.py --input data/input/clients.xlsx --output data/output/results.xlsx --id-column client_id --street-column street --city-column city --state-column state --zip-column zip
```

Change the names in the command to match your file:

- `data/input/clients.xlsx` — the name of **your** input file.
- `data/output/results.xlsx` — the name you want for the **result** file.
- `client_id` — the column that holds your unique ID for each row.
- `full_address` (or `street` / `city` / `state` / `zip`) — your address column(s).

That is all you need to change. The tool automatically tries every method to match as many addresses as possible, including a free OpenStreetMap lookup for hard-to-find addresses.

### Step 3: Get your results and check the match rate

When it finishes, open the result file (for example `data/output/results.xlsx`). It contains your original columns plus the Census tract for each address — see [Output Columns](#understanding-the-output-columns) below.

When the tool finishes, it prints a short **summary** in the terminal, including a line like:

```
Matched total : 995  (99.5%)
Unmatched     : 5    (0.5%)
```

The result file itself does not show this percentage — it shows each address's status in the `match_status` column (unmatched rows say `No_Match`). To see the percentage, read the terminal summary, or filter the `match_status` column in Excel.

**Please report a high No_Match rate.** A few unmatched addresses is normal, but if a large share come back `No_Match` (roughly **more than 10%**), email JHFRC at **mohith-addepalli@utc.edu** with a few example addresses so we can look into it. The tool also prints a reminder when the unmatched rate is above 10%.

**What to expect while it runs:** the **first run** downloads the official Census map file (about 55 MB, once only). After that, a typical file of 1,000 addresses finishes in **about 2 minutes**. You will see progress messages, then the summary.

<details>
<summary>Advanced options (most people do not need these)</summary>

The Census retry and the free OpenStreetMap lookup are **on by default**, so a normal run already gets the highest match rate. These flags only change that:

| Option | Description |
|---|---|
| `--sheet-name` | The Excel sheet to read, if your workbook has more than one sheet. |
| `--no-fallback` | Turn off the slower Census one-at-a-time retry. |
| `--no-external-fallback` | Turn off the OpenStreetMap lookup (faster, but matches fewer). |
| `--chunk-size` | Rows per block for very large files. Set automatically; see [Processing Large Files](#processing-large-files). Use `0` to force a single pass. |
| `--config` | Use a different settings file (default: `config/config.yaml`). |

</details>

### Step 4: Add the Census tract back into your file (VLOOKUP / XLOOKUP)

After you run the tool, its result file lists each **unique identifier** with its **`census_tract_geoid`**. This step uses a lookup on that unique identifier to copy the Census tract back into your own file. Both files must share the **same unique-identifier column** (for example `client_id`) — that is what Excel matches on. In the formulas below, wherever the example says `results.xlsx`, use the actual name of the result file you created.

**Before you begin — preserve leading zeros.** Census tract identifiers are text, not numbers, and some begin with a leading zero (for example Alabama tracts begin with `01`). To stop Excel dropping that zero, format the destination column as **Text** first (**Home → Number Format → Text**).

**How to do it:**

- Open **both** files: your own file and the result file the tool created.
- In your file, click the empty cell in the first data row of the column where you want the Census tract (this example assumes row 2).
- Enter **one** of the formulas below, press **Enter**, and copy it down the column.

**Microsoft 365 or Excel 2021+ (recommended — `XLOOKUP`):** if your unique ID is in `A2`, and the result file has `client_id` in column A and `census_tract_geoid` in column G:

```
=XLOOKUP(A2, [results.xlsx]Sheet1!$A:$A, [results.xlsx]Sheet1!$G:$G, "Not found")
```

**Older Excel (`VLOOKUP`):** counting columns from the identifier (A=1 … G=7):

```
=VLOOKUP(A2, [results.xlsx]Sheet1!$A:$H, 7, FALSE)
```

Replace `7` with the actual position of `census_tract_geoid` counted from your identifier column, and keep `FALSE` so Excel requires an exact match.

**Notes:**

- Open the result file to confirm which column letter holds `census_tract_geoid`; its position shifts with the number of address columns.
- An ID in your file but not in the result file shows `Not found` (XLOOKUP) or `#N/A` (VLOOKUP) — that record simply was not in the file you processed.
- When done, you can **copy the column and paste it back as Values** (Home → Paste → Values) so the tracts stay after the result file is closed.

---

## Processing Large Files

The tool handles files of any size and adjusts automatically — you do not change the command.

| Approximate size | What happens |
|---|---|
| Up to 50,000 records | Processed in a single pass. |
| Over 50,000 records | Switches to **chunked processing** (blocks of 50,000) so memory stays bounded. Blocks are written as numbered part files and combined at the end. |
| Over 100,000 records | The **free OpenStreetMap lookup is turned off** for the run to avoid overloading a free public service (the Census steps still run). A bulk-capable provider such as ArcGIS is not affected. |
| Over 1,048,576 records | Output is written as **CSV** (Excel's row limit). If you asked for `.xlsx`, the tool saves `.csv` and reports the name. |

To control the block size yourself, add `--chunk-size N` (or `--chunk-size 0` to force a single pass).

**Resuming an interrupted run:** if a long run stops partway, run the exact same command again — completed blocks are skipped and never duplicated. Part files are kept in a `<output>_parts` folder; delete it once the result looks correct.

**Time:** speed depends on the Census service — roughly **1,000 addresses every 2 minutes** — so a million records can take one to two days. Plan for an overnight run and rely on resume.

**Free-service caution:** the free OpenStreetMap lookup only runs on the residual the Census could not match, but even a few percent of a huge file is tens of thousands of requests, which OpenStreetMap's policy (one request per second, no bulk use) prohibits. So for files over 100,000 records the free lookup is disabled automatically, and in any run it is capped at 2,000 total requests. For a large residual, use a bulk-capable geocoder (ArcGIS with a token, via `external_provider: arcgis`, or a commercial service such as Geocodio or Smarty) — do not point the free service at bulk work. Contact JHFRC if you are unsure.

---

## Understanding the Output Columns

The output file includes your original columns plus:

| Column | Description |
|---|---|
| `cleaned_address` | The standardized address returned by the geocoder. Blank if not matched. |
| `census_tract_geoid` | The 11-digit Census tract identifier (e.g., `47065001600`). Blank if not matched. |
| `match_status` | How the address was matched, or why it was not (see below). |
| `error_reason` | Plain-language explanation for any address that was not matched. |

Any status beginning with `Matched` means the address **got a Census tract** — nothing further is needed. The ones to review are `No_Match`, `Tie`, and `Rejected`.

| Value | Got a tract? | Meaning |
|---|---|---|
| `Matched` | Yes | Matched directly on the first, fastest pass. |
| `Matched_Fallback` | Yes | Matched after the tool retried the address individually. |
| `Matched_External` | Yes | Not in the Census database; found via the free OpenStreetMap lookup, then placed in a tract. |
| `Matched_CensusAPI_Backup` | Yes | Rare case where the tract came directly from the Census service. |
| `Tie` | No | Ambiguous (more than one possible match). Add detail (e.g. the full ZIP) and run again. |
| `No_Match` | No | Not found by any method. Check for typos, or the address may be too new. |
| `Rejected` | No | The row was missing its ID or address and was skipped. |

---

## Sharing Results with JHFRC

Share only the **output file** (not your original records). Before sending, confirm it contains only the unique identifier, address, and Census tract columns — no names, medical identifiers, or other personal information — and that the IDs match your internal records. If you are unsure whether the file is ready, contact us first.

---

## Troubleshooting

**"The following columns were not found in the input file"**
Check that the column names you passed (`--id-column`, `--address-column`, etc.) exactly match your file. Column names are case-sensitive.

**"The input file contains column(s) that may include sensitive personal information"**
Remove any columns with names, dates of birth, contact information, or other personal data, then run again.

**"Failed to download the Census tract dataset"**
Check your internet connection. If it continues, contact us.

**Many records show "No_Match"**
Usually the addresses are incomplete or unusually formatted. Include the full city, state, and ZIP. A few very new or unusual addresses may still not match.

**The tool runs but produces no output**
Confirm you can write to the `data/output/` folder, and that the `--output` name ends in `.xlsx` or `.csv`.

---

## Technical Support

For questions or issues, contact **mohith-addepalli@utc.edu**. Please include a description of the problem and any error message shown.
