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

You will need Python 3.10 or later installed on your computer. You can download Python from [python.org](https://www.python.org/downloads/).

### Step 1: Get the project

Clone or download this repository to your computer.

If you have Git installed:
```bash
git clone https://github.com/your-org/jhfrc-address2tract.git
cd jhfrc-address2tract
```

Or download and unzip the repository from GitHub, then open a terminal in that folder.

### Step 2: Create a virtual environment (recommended)

```bash
python -m venv .venv
```

Activate it:
- **Windows**: `.venv\Scripts\activate`
- **Mac/Linux**: `source .venv/bin/activate`

### Step 3: Install required packages

```bash
pip install -r requirements.txt
```

This installs all necessary libraries. It only needs to be done once.

---

## Running the Application

Place your input file in the `data/input/` folder, then run one of the following commands from the project folder.

### Example 1: Full address column

```bash
python main.py \
  --input data/input/clients.xlsx \
  --output data/output/clients_with_tracts.xlsx \
  --id-column client_id \
  --address-column full_address
```

### Example 2: Separate address fields

```bash
python main.py \
  --input data/input/clients.xlsx \
  --output data/output/clients_with_tracts.xlsx \
  --id-column client_id \
  --street-column street \
  --city-column city \
  --state-column state \
  --zip-column zip
```

### All available options

| Option | Description |
|---|---|
| `--input` | Path to your input file (required) |
| `--output` | Path for the output file (required) |
| `--id-column` | Name of your unique ID column (required) |
| `--address-column` | Name of the full address column |
| `--street-column` | Name of the street address column |
| `--city-column` | Name of the city column |
| `--state-column` | Name of the state column |
| `--zip-column` | Name of the ZIP code column |
| `--sheet-name` | Excel sheet name (if the workbook has multiple sheets) |
| `--use-fallback` | Enable fallback geocoding for unmatched records |
| `--no-fallback` | Disable fallback geocoding |
| `--tract-dataset` | Path to an existing Census tract GeoPackage |
| `--config` | Path to the config file (default: `config/config.yaml`) |

---

## Automatic Census Tract Dataset Download

The first time you run this tool, it will automatically download the official U.S. Census Bureau tract boundary file. This file is used to match coordinates to Census tracts.

What happens:
1. The tool checks whether the file already exists at `data/reference/census_tracts.gpkg`.
2. If the file is missing, it downloads the national Census tract shapefile from the U.S. Census Bureau website.
3. It converts the shapefile to a GeoPackage and saves it locally.
4. On all future runs, the locally saved file is used — **no repeated downloads**.

The download is approximately 50–60 MB and may take a few minutes depending on your internet connection. Progress will be displayed while it downloads.

---

## How the Matching Workflow Works

The tool processes your file in the following steps:

1. **Read the input file** — loads your Excel or CSV file.
2. **Check for sensitive columns** — scans column names for PHI indicators. Stops if any are found.
3. **Combine address fields** — if you provided separate columns, they are joined into a single address string internally.
4. **Validate rows** — records with a missing ID or empty address are flagged and excluded from geocoding.
5. **Primary geocoding** — sends your addresses to the Census Geocoder Batch API in groups. Each address is matched to a latitude and longitude.
6. **Spatial join** — matched coordinates are compared against Census tract boundaries to determine the tract GEOID.
7. **Fallback geocoding** — any records that could not be matched in step 5 are submitted one at a time to the Census Geocoder API (if fallback is enabled).
8. **Assemble output** — all results are combined into a single file, including records that could not be matched.
9. **Print summary** — a brief summary is displayed showing how many records were matched, unmatched, or rejected.

---

## Fallback Geocoding

When fallback is enabled (the default), addresses that were not matched during the batch step are submitted to the Census Geocoder one at a time. This can improve match rates for addresses that are formatted differently or have minor inconsistencies.

Fallback geocoding is slower than batch geocoding. It is automatically rate-limited to avoid overloading the Census API.

Records matched by the fallback method will have `Matched_Fallback` in the `match_status` column so they can be identified.

You can disable fallback with the `--no-fallback` flag, or change the default in `config/config.yaml`.

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
| `Matched` | Address was successfully geocoded and matched to a Census tract. |
| `Matched_Fallback` | Address was matched using the fallback geocoding method. |
| `No_Match` | Address could not be geocoded. |
| `Rejected` | Record was missing a required ID or address and was not processed. |

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
