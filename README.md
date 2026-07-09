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

That is all you need. You do not have to set anything else.

### Step 3: Get your results

When it finishes, open the result file (for example `data/output/results.xlsx`).
It contains your original columns plus the Census tract for each address. See
[Understanding the Output Columns](#understanding-the-output-columns) below.

### What to expect while it runs

- The **first run** downloads the official Census map file (about 55 MB). This
  happens only once and may take a minute or two.
- After that, a typical file of 1,000 addresses finishes in **about 2 minutes**.
- You will see progress messages in the terminal, then a short summary showing
  how many addresses were matched.

<details>
<summary>Advanced options (most people do not need these)</summary>

| Option | Description |
|---|---|
| `--sheet-name` | The Excel sheet to read, if your workbook has more than one sheet. |
| `--no-fallback` | Turn off the slower one-at-a-time retry for hard addresses. |
| `--config` | Use a different settings file (default: `config/config.yaml`). |

To try to match the small number of addresses the Census cannot find (using a
free OpenStreetMap lookup), open `config/config.yaml` and change
`use_external_fallback` to `true`. This is optional and makes runs slower.

</details>

---

## First Run Note

The first time you run this tool, it will automatically download the official U.S. Census Bureau tract boundary file (~55 MB). This only happens once — all future runs use the locally saved file. Progress will be shown while it downloads.

---

## Understanding the Output Columns

The output file includes your original columns plus the following:

| Column | Description |
|---|---|
| `cleaned_address` | The standardized address returned by the geocoder. Blank if the address could not be matched. |
| `census_tract_geoid` | The 11-digit Census tract identifier (e.g., `47065001600`). Blank if the address could not be matched. |
| `match_status` | One of: `Matched`, `Matched_Fallback`, `No_Match`, or `Rejected`. |
| `error_reason` | Explanation of why a record was not matched, if applicable. |

### Match status values

| Value | Meaning |
|---|---|
| `Matched` | Address was geocoded and matched to a Census tract using the local boundary file. |
| `Matched_CensusAPI_Backup` | Tract was assigned using the Census API directly, as a backup when the local boundary file returned no result. |
| `Matched_Fallback` | Address required one-at-a-time Census geocoding (sweeping the Current and 2020 benchmarks, with a conservative street-normalization retry), then matched to a tract. |
| `Matched_External` | Address was not in the Census address database and was resolved by the optional free external geocoder (OpenStreetMap/Nominatim or ArcGIS); its coordinates were then matched to a Census tract locally. |
| `No_Match` | Address could not be geocoded after all methods were tried. |
| `Rejected` | Record was missing a required ID or address and was not processed. |

---

## How addresses are matched

You do not need to do anything for this to work — it is automatic. In short, the
tool tries several methods so that hard-to-find addresses are not simply dropped:

1. It first looks up all addresses against the official U.S. Census address
   system. This finds the large majority.
2. For any it cannot find, it retries each one individually and applies a few
   safe spelling clean-ups (for example expanding "Pk" to "Pike").
3. If you turn on the optional free lookup (see the Advanced options above), it
   makes one more attempt using OpenStreetMap for addresses that are simply not
   in the Census system yet (such as brand-new streets).

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
