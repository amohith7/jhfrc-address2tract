# JHFRC Address to Census Tract Converter

A simple tool that converts client or patient addresses into U.S. Census tract identifiers. Developed by the Journey Health Foundation Research Center (JHFRC) for use by nonprofit partner organizations.

---

## Overview

This tool reads an address file from your organization, matches each address to a geographic location, and returns the corresponding U.S. Census tract identifier (called a GEOID) for each record.

The resulting file can then be shared with JHFRC for community-level research and analysis.

---

## Purpose

Many public health and social service datasets are organized at the Census tract level — a small geographic unit similar in size to a neighborhood. By converting your organization's address records to Census tracts, JHFRC can link them to broader community data — such as income levels, education rates, and health indicators — without ever needing names or personal identifiers.

---

## Required Input Format

Your input file must be an Excel (`.xlsx`) or CSV (`.csv`) file. It should contain:

- **One unique identifier column** — a client ID, case number, or other internal identifier. This should be consistent with your organization's records.
- **Address information** in one of two formats:
  - A single column containing the full address (e.g., `123 Main St, Nashville, TN 37201`)
  - Separate columns for street, city, state, and ZIP code

Your file should contain **only** these columns. Please do not include names, dates of birth, phone numbers, or other personal information. See the [Privacy Guidance](#privacy-guidance) section for more details.

### Example: Single address column

| client_id | full_address                          |
|-----------|---------------------------------------|
| 1001      | 123 Main St, Nashville, TN 37201      |
| 1002      | 456 Elm Ave, Memphis, TN 38103        |

### Example: Separate address columns

| client_id | street       | city        | state | zip   |
|-----------|--------------|-------------|-------|-------|
| 1001      | 123 Main St  | Nashville   | TN    | 37201 |
| 1002      | 456 Elm Ave  | Memphis     | TN    | 38103 |

Sample files are available in the `examples/` folder.

---

## Example Address Formats

The tool works best with complete, standard U.S. addresses. Some examples:

```
123 Main St, Nashville, TN 37201
456 Elm Ave Apt 2, Memphis, TN 38103
789 Oak Dr, Chattanooga, TN 37402
321 Maple Blvd, Knoxville, TN 37902
```

Addresses without a city, state, or ZIP code are less likely to match successfully.

---

## Privacy Guidance

Before sharing your file with this tool:

- **Include only what is necessary**: a unique ID and address fields.
- **Remove all other columns**, including names, dates of birth, phone numbers, email addresses, diagnosis codes, and insurance information.
- The tool will **automatically stop** if it detects column names that suggest sensitive personal data. This is a safety check to protect your clients' privacy.

---

## How PHI Detection Works

Before processing begins, the tool scans your file's column names for keywords that suggest protected health information (PHI), such as:

- `name`, `first_name`, `last_name`, `patient_name`
- `date_of_birth`, `dob`
- `ssn`, `social_security`, `mrn`, `medical_record_number`
- `diagnosis`, `insurance`
- `phone`, `email`
- `notes`, `clinical_notes`

If any such column names are found, the tool will stop immediately and display a message explaining what was found. No data will be processed until the file is corrected.

This check is based on column **names only** — it does not read or examine the actual contents of your file.

---

## Installation

### Prerequisites

You will need **Python 3.10 or later** installed on your computer.

To check if Python is already installed, open a terminal (see below) and type:
```
python --version
```
If you see a version number like `Python 3.11.2`, you are ready. If you see an error or a version older than 3.10, download and install Python from [python.org](https://www.python.org/downloads/). During installation on Windows, check the box that says **"Add Python to PATH"** before clicking Install.

---

### How to Open a Terminal

A terminal (also called a command prompt or shell) is a text window where you type commands. Here is how to open one on each operating system.

#### Windows

**Option A — Command Prompt**
1. Press the **Windows key** on your keyboard (or click the Start button).
2. Type `cmd` in the search box.
3. Click **Command Prompt** in the results.

**Option B — PowerShell**
1. Press the **Windows key**.
2. Type `powershell` in the search box.
3. Click **Windows PowerShell** in the results.

**Option C — From a folder (easiest)**
1. Open **File Explorer** and navigate to the project folder.
2. Click on the address bar at the top of the window (it shows the folder path).
3. Type `cmd` and press **Enter**. A Command Prompt will open already inside that folder.

#### Mac

1. Press **Command + Space** to open Spotlight Search.
2. Type `Terminal` and press **Enter**.

#### Linux

1. Press **Ctrl + Alt + T** on most distributions.
2. Or search for "Terminal" in your application menu.

---

### Step 1: Download the project

1. Go to the project page on GitHub.
2. Click the green **Code** button near the top right.
3. Click **Download ZIP**.
4. Once downloaded, right-click the ZIP file and select **Extract All** (Windows) or double-click it (Mac).
5. Note where the extracted folder is saved — for example, `C:\Users\YourName\Downloads\jhfrc-address2tract`.

---

### Step 2: Open a terminal inside the project folder

**Windows:**
1. Open **File Explorer** and navigate into the `jhfrc-address2tract` folder.
2. Click on the address bar at the top and type `cmd`, then press **Enter**.
3. A Command Prompt window will open already inside the project folder.

**Mac:**
1. Open **Terminal**.
2. Type `cd ` (with a space after it), then drag the `jhfrc-address2tract` folder from Finder into the Terminal window. Press **Enter**.

You can confirm you are in the right folder by typing:
```
dir
```
(Windows) or:
```
ls
```
(Mac/Linux). You should see files like `main.py` and `requirements.txt` listed.

---

### Step 3: Create a virtual environment (recommended)

A virtual environment keeps this tool's software separate from other programs on your computer. This is optional but recommended.

In your terminal, type:
```
python -m venv .venv
```

Then activate it:

**Windows (Command Prompt):**
```
.venv\Scripts\activate
```

**Windows (PowerShell):**
```
.venv\Scripts\Activate.ps1
```

**Mac / Linux:**
```
source .venv/bin/activate
```

When the virtual environment is active, you will see `(.venv)` at the beginning of your terminal prompt. You will need to activate it again each time you open a new terminal window.

---

### Step 4: Install required packages

In your terminal (with the virtual environment active), type:
```
pip install -r requirements.txt
```

This downloads and installs all required software libraries. It only needs to be done once. The process may take a minute or two.

When it finishes, you are ready to run the tool.

---

## Running the Application

Follow these three steps.

### Step 1: Put your file in the input folder

Copy your Excel or CSV file into the `data/input/` folder inside the project.
For example, `data/input/clients.xlsx`.

### Step 2: Run one command

In your terminal (opened inside the project folder), type **one** of the
commands below and press **Enter**. Type it all on a single line.

**If your file has ONE column that holds the whole address** (for example a
column named `full_address`):

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
- `client_id` — the name of the column that holds your unique ID for each row.
- `full_address` (or `street` / `city` / `state` / `zip`) — the names of your
  address column(s).

That is all you need to change. The tool automatically tries every method to
match as many addresses as possible, including a free OpenStreetMap lookup for
hard-to-find addresses — you do not need to add anything to the command.

### Step 3: Get your results and check the match rate

When it finishes, open the result file (for example `data/output/results.xlsx`).
It contains your original columns plus the Census tract for each address. See
[Understanding the Output Columns](#understanding-the-output-columns) below.

**Check how many addresses were matched.** When the tool finishes, it prints a
short **summary** in the terminal that includes a line like:

```
Matched total : 995  (99.5%)
Unmatched     : 5    (0.5%)
```

The result file itself does **not** show this percentage — it shows the status
of each address one row at a time in the `match_status` column (rows that could
not be matched say `No_Match`). To see the percentage, read the summary in the
terminal, or in Excel turn on a filter and count how many rows say `No_Match`.

**Please report a high No_Match rate.** A small number of unmatched addresses is
normal. But if a large share of your addresses come back `No_Match` (for
example more than about **10%**), please email JHFRC at **mohith-addepalli@utc.edu**
with a few example addresses so we can look into it. The tool will also print a
reminder in the terminal when the unmatched rate is above 10%.

### What to expect while it runs

- The **first run** downloads the official Census map file (about 55 MB). This
  happens only once and may take a minute or two.
- After that, a typical file of 1,000 addresses finishes in **about 2 minutes**.
- You will see progress messages in the terminal, then a short summary showing
  how many addresses were matched.

<details>
<summary>Advanced options (most people do not need these)</summary>

Both the Census retry and the free OpenStreetMap lookup are **on by default**,
so a normal run already gets the highest match rate. The flags below are only if
you want to change that.

| Option | Description |
|---|---|
| `--sheet-name` | The Excel sheet to read, if your workbook has more than one sheet. |
| `--no-fallback` | Turn off the slower Census one-at-a-time retry. |
| `--no-external-fallback` | Turn off the OpenStreetMap lookup (makes runs faster, but matches fewer). |
| `--chunk-size` | Number of rows to process per block for very large files. Set automatically; see [Processing Large Files](#processing-large-files). Use `0` to force a single pass. |
| `--config` | Use a different settings file (default: `config/config.yaml`). |

</details>

### Step 4: Add the Census tract to a separate master file (optional — most people can skip this)

**You probably do not need this step.** The result file the tool creates already
contains **all of your original columns plus the Census tract**, matched to the
correct row. If that result file is what you will use going forward, you are done
after Step 3 — just open and use it. Nothing needs to be renamed or looked up.

This step is only for one situation: you keep a **separate master spreadsheet**
(for example, one that also holds columns you did not submit to the tool, such as
names or dates of birth) and you want the Census tract copied back into that
separate file. Because it is a different file, Excel lines up the rows using the
shared unique identifier. Both files must contain the **same
unique-identifier column** (for example `client_id`).

You do **not** rename anything. In the formulas below, wherever the example says
`results.xlsx`, use the actual name of the result file you already created — that
is, whatever you typed after `--output` when you ran the tool.

**Before you begin — preserve leading zeros.** Census tract identifiers are text,
not numbers, and some begin with a leading zero (for example Alabama tracts begin
with `01`). To prevent Excel from dropping that zero, format the destination
column as **Text** first: select the column, then choose **Home → Number Format →
Text** before entering the formula below.

**Step-by-step:**

1. Open **both** files in Excel: your master file and the result file the tool
   created.
2. In your master file, click the empty cell in the first data row of the column
   where you want the Census tract to appear (this example assumes row 2).
3. Enter **one** of the formulas below, then press **Enter** and copy the formula
   down the whole column.

**If you have Microsoft 365 or Excel 2021 or newer (recommended — use `XLOOKUP`):**

```
=XLOOKUP([unique ID cell], [result-file ID column], [result-file tract column], "Not found")
```

For example, if your unique ID is in cell `A2`, and the result file you created
(here called `results.xlsx`) has its `client_id` in column A and its
`census_tract_geoid` in column G:

```
=XLOOKUP(A2, [results.xlsx]Sheet1!$A:$A, [results.xlsx]Sheet1!$G:$G, "Not found")
```

**If you have an older version of Excel (use `VLOOKUP`):**

`VLOOKUP` requires the identifier column to be to the **left** of the tract
column in the result file (it is, by default), and it counts columns starting
from the identifier column. If the identifier is in column A and the
`census_tract_geoid` is the 7th column across (A=1, B=2, … G=7), use:

```
=VLOOKUP(A2, [results.xlsx]Sheet1!$A:$H, 7, FALSE)
```

Replace `7` with the actual position of the `census_tract_geoid` column counted
from your identifier column, and always keep `FALSE` at the end so Excel requires
an exact identifier match.

**Notes:**

- Open the result file to confirm which column letter holds `census_tract_geoid`
  and adjust the formula accordingly. The column position shifts depending on how
  many address columns your file has.
- Any identifier that appears in your master file but not in the result file will
  show `Not found` (XLOOKUP) or `#N/A` (VLOOKUP). This simply means that record
  was not in the file you processed.
- Once the formula has filled the column, you may **copy the column and paste it
  back as "Values"** (Home → Paste → Values) so the tracts remain even after the
  result file is closed.

---

## First Run Note

The first time you run this tool, it will automatically download the official U.S. Census Bureau tract boundary file (~55 MB). This only happens once — all future runs use the locally saved file. Progress will be shown while it downloads.

---

## Processing Large Files

This tool is designed to process files of any size, from a few dozen records to
several million. For larger files, it adjusts its behavior automatically so that
you do not need to change the command. The relevant thresholds are documented
below so that you can plan accordingly.

### Automatic behavior by file size

| Approximate file size | What the tool does | Action required |
|---|---|---|
| Up to 50,000 records | Processes the entire file in a single pass, held in memory. | None. |
| More than 50,000 records | Automatically switches to **chunked processing**: the file is read and processed in blocks of 50,000 records at a time, so memory use stays bounded regardless of total size. Each block is written to a numbered part file, and the parts are combined into the final output at the end. | None. |
| More than 100,000 records | In addition to chunking, the **free OpenStreetMap lookup is automatically disabled for the entire run** to avoid overloading a free public service (see the caution below). The Census matching steps still run in full. A bulk-capable provider such as ArcGIS is **not** disabled. | None, unless you require the external lookup at this scale — configure ArcGIS (see below). |
| More than 1,048,576 records (output) | Because Microsoft Excel cannot open a file with more than 1,048,576 rows, chunked runs always write the combined output in **CSV** format. If you request an `.xlsx` output name, the tool writes a `.csv` file instead and reports the corrected name. | Open the result in a CSV-capable program (Excel can still open CSV files, subject to its row limit). |

You do not have to enable any of this. If you prefer to control the block size
manually, add `--chunk-size N` to the command (for example `--chunk-size 25000`).
Use `--chunk-size 0` to force single-pass processing regardless of file size.

### Resuming an interrupted run

Large files can take a long time to process. If a run is interrupted (for
example, the computer restarts or the network drops), simply run the **exact same
command again**. The tool detects the part files that were already completed,
skips them, and resumes from the first unfinished block. Completed work is never
repeated and never duplicated in the final output.

The numbered part files are kept in a folder next to your output file (named
`<output>_parts`) until you confirm the final result looks correct. You may
delete that folder afterward.

### Time expectations

Processing time is dominated by the U.S. Census geocoding service, not by your
computer. As a rough guide, when the Census service is responding normally the
tool processes on the order of **1,000 addresses every two minutes**. On that
basis, a file of one million records may take on the order of **one to two full
days** of continuous processing. Plan for an overnight or multi-day run, and rely
on the resume capability rather than attempting to complete a very large file in
one sitting.

### Important caution regarding free services

The optional external lookup uses **OpenStreetMap's Nominatim service**, which is
provided free of charge for light, occasional use. Its usage policy permits at
most one request per second and **expressly prohibits bulk or high-volume
geocoding**. Submitting a large volume of requests may cause your access to be
blocked and places an unfair burden on a shared public resource.

Importantly, the free lookup only ever runs on the **residual** — the small
share of addresses the Census service could not resolve — never on the whole
file. Even so, a small percentage of a very large file is a large number of
requests in absolute terms: at roughly 5% unresolved, a one-million-record file
would send on the order of **50,000 requests** to a free server capped at one per
second (about 14 hours of continuous traffic), which is exactly the bulk use the
service prohibits. The limit is therefore about the absolute request count, not
the percentage.

To keep every run within acceptable use, the tool applies two automatic limits
**only to the free OpenStreetMap provider**:

- The free lookup is **disabled for the whole run** when the input exceeds
  **100,000 records** (`external_max_rows` in the configuration file).
- Across the whole run, the free lookup sends at most **2,000 requests** total
  (`external_max_residual` in the configuration file); any remaining unresolved
  addresses are left as `No_Match`.

**These caps do not apply to bulk-capable providers.** If you configure the Esri
ArcGIS World Geocoding service (`external_provider: arcgis`) with a developer
token, the external step keeps running on the entire residual at any scale,
because ArcGIS is designed for volume geocoding. For a large residual, that (or a
commercial geocoder such as Geocodio or Smarty) is the correct tool — please do
**not** raise the caps against the free OpenStreetMap service. Contact JHFRC for
guidance if you are unsure.

---

## Understanding the Output Columns

The output file includes your original columns plus the following:

| Column | Description |
|---|---|
| `cleaned_address` | The standardized address returned by the geocoder. Blank if the address could not be matched. |
| `census_tract_geoid` | The 11-digit Census tract identifier (e.g., `47065001600`). Blank if the address could not be matched. |
| `match_status` | How the address was matched (or why it was not). See the table below for every possible value. |
| `error_reason` | Plain-language explanation for any address that was not matched. |

### Match status values

Any status beginning with `Matched` means the address **got a Census tract** —
you do not need to do anything with those. The ones to look at are `No_Match`,
`Tie`, and `Rejected`.

| Value | Got a tract? | Meaning |
|---|---|---|
| `Matched` | Yes | Matched directly on the first, fastest pass. |
| `Matched_Fallback` | Yes | Not found on the first pass; matched after the tool retried the address individually. |
| `Matched_External` | Yes | Not in the Census address database; found with the free OpenStreetMap lookup, then placed in a Census tract. |
| `Matched_CensusAPI_Backup` | Yes | A rare case where the tract was taken directly from the Census service. |
| `Tie` | No | The address was **ambiguous** (more than one possible match). Add more detail (e.g. the full ZIP) and run again. |
| `No_Match` | No | The address could not be found by any method. Check for typos, or the address may be too new to be in the databases. |
| `Rejected` | No | The row was missing its ID or address and was skipped. Fill in the missing information. |

---

## How addresses are matched

You do not need to do anything for this to work — it is automatic. In short, the
tool tries several methods so that hard-to-find addresses are not simply dropped:

1. It first looks up all addresses against the official U.S. Census address
   system. This finds the large majority.
2. For any it cannot find, it retries each one individually and applies a few
   safe spelling clean-ups (for example expanding "Pk" to "Pike").
3. For anything still not found, it automatically makes one more attempt using a
   free OpenStreetMap lookup, which can find addresses that are simply not in the
   Census system yet (such as brand-new streets).

In a test of 1,000 real addresses, this matched about **99.5%** to a Census
tract, compared with about 95% using the Census lookup alone. A small number of
addresses (poorly formatted or very new) may still not match; those are marked
`No_Match` in the results so you can review them.

---

## Sharing Results with JHFRC

After running this tool, you may share the resulting output file with the Journey Health Foundation Research Center (JHFRC) for collaborative research and analysis.

**Before sharing, please:**

1. **Verify your unique identifier column.** Make sure the IDs in the output file are correct and consistent with your internal records. This is important so that records can be interpreted accurately during analysis.

2. **Confirm that no sensitive information is included.** The output file should contain only the unique identifier, address fields, and the Census tract columns. It should not include names, medical identifiers, or any other personal information.

3. **Share only the output file**, not the original input file.

If you are unsure whether your file is ready to share, please reach out to us before sending.

---

## Troubleshooting

**"The following columns were not found in the input file"**
Check that the column names you provided with `--id-column`, `--address-column`, etc. exactly match the column names in your file. Column names are case-sensitive.

**"The input file contains column(s) that may include sensitive personal information"**
Remove any columns that contain names, dates of birth, contact information, or other personal data, then run the tool again.

**"Failed to download the Census tract dataset"**
Check your internet connection. If the problem continues, contact us for assistance.

**Many records show "No_Match"**
This usually means addresses are incomplete or formatted in an unusual way. Try including the full city, state, and ZIP code. You can also try enabling the fallback option with `--use-fallback` if it is not already on.

**The tool runs but produces no output**
Check that you have write permission to the `data/output/` folder. Also check that the `--output` path does not use a file format other than `.xlsx` or `.csv`.

---

## Technical Support

For questions or issues related to this tool, please contact:

**mohith-addepalli@utc.edu**

Please include a description of the issue and, if possible, the error message that was displayed.
