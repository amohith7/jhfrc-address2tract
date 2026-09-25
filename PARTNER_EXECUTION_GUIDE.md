# JHFRC Address-to-Tract Converter: In-Person Partner Execution Guide

**Prepared by:** Mohith Addepalli  
**Organization:** Journey Health Foundation Research Center, University of Tennessee at Chattanooga  
**Updated:** September 25, 2026  
**Repository:** https://github.com/amohith7/jhfrc-address2tract

> **Current distribution status:** This repository contains source code. A prebuilt Windows ZIP has not yet been published in GitHub Releases. Complete Part A on a UTC-managed Windows laptop and test the bundle before the partner visit. The nonprofit does not need Python.

## Objective and data boundary

All real partner data and outputs remain on the nonprofit's own computer. JHFRC provides software and in-person technical assistance, but does not copy the partner's original file, full converter output, or temporary files to a UTC device. Confirm the approved workflow with both organizations' IT/privacy contacts. Local execution does **not** mean fully offline execution: with explicit `--approve-egress`, addresses are sent to the U.S. Census geocoder. In the commands below, optional third-party fallback is disabled.

## Part A — Prepare on the UTC-managed Windows computer (once)

**A1. Clone the project** (PowerShell; if Git is unavailable, use the repository's Code > Download ZIP and extract it, then change to that folder):

```powershell
New-Item -ItemType Directory -Force "C:\JHFRC" | Out-Null
Set-Location "C:\JHFRC"
git clone https://github.com/amohith7/jhfrc-address2tract.git
Set-Location "C:\JHFRC\jhfrc-address2tract"
```

**A2. Create an environment and install build tools** (Python 3.11 is needed *only* on the UTC build computer):

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install pyinstaller
```

**A3. Build the Windows one-folder executable** (copy this entire command):

```powershell
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --onedir --console --name JHFRC_Address2Tract --paths src --collect-all pyogrio --collect-all geopandas --collect-all shapely --collect-all pyproj main.py
```

**A4. Copy the external configuration and create the working folders:**

```powershell
$bundle = "dist\JHFRC_Address2Tract"
Copy-Item -Path "config" -Destination $bundle -Recurse -Force
New-Item -ItemType Directory -Force -Path "$bundle\data\input", "$bundle\data\output", "$bundle\data\reference" | Out-Null
Copy-Item -Path "examples\example_input.csv" -Destination "$bundle\data\input\sample.csv" -Force
```

Expected structure: `JHFRC_Address2Tract.exe`, `_internal\`, `config\config.yaml`, and `data\input\`, `data\output\`, `data\reference\`. Distribute the **whole folder**, not the EXE alone.

**A5. Smoke-test on synthetic sample data** (the sample addresses are fictitious, so unmatched results are not necessarily a failure):

```powershell
Push-Location "dist\JHFRC_Address2Tract"
.\JHFRC_Address2Tract.exe --help
.\JHFRC_Address2Tract.exe --input "data\input\sample.csv" --output "data\output\sample_results.csv" --id-column client_id --street-column street --city-column city --state-column state --zip-column zip --approve-egress --no-external-fallback
Test-Path "data\output\sample_results.csv"
Pop-Location
```

Verify that the final command returns `True`, open the output, and check the required headers. Resolve any build/geospatial-library issues before the visit. These packaging commands are a build recipe, not a claim that a Windows bundle has already been tested.

**A6. Create the transfer ZIP and verify its integrity:**

```powershell
Compress-Archive -Path "dist\JHFRC_Address2Tract" -DestinationPath "dist\JHFRC_Address2Tract_Windows.zip" -Force
Get-FileHash "dist\JHFRC_Address2Tract_Windows.zip" -Algorithm SHA256
```

Transfer only the tested program ZIP using a method approved by the nonprofit's IT team. Do not include real client data, credentials, token files, or test outputs containing sensitive data.

## Part B — Run on the nonprofit's Windows computer (in person)

**B1. Obtain approval.** Verify the nonprofit permits the executable and permits addresses to be sent to the U.S. Census geocoder. If it does not allow external address transmission, **do not run this online converter**. IT may need to allow `geocoding.geo.census.gov` and `www2.census.gov`. Do not bypass Windows security warnings or organizational controls.

**B2. Extract the supplied ZIP** to `C:\JHFRC_Address2Tract` (or another approved location). The partner does not need Python, Git, or VS Code.

**B3. The partner prepares its own minimal working Excel/CSV file** using a unique ID and either a single full-address column or separate `street`, `city`, `state`, `zip` columns. Exclude names, DOB, diagnoses, medical record numbers, and unrelated fields. Review values manually: the tool screens *column names*, not every cell. Example for separate columns:

```text
record_id,street,city,state,zip
P001,123 Main St,Chattanooga,TN,37402
```

Save the partner's working copy as `C:\JHFRC_Address2Tract\data\input\partner_addresses.xlsx`. The original master dataset stays under the partner's control.

**B4. Open Command Prompt inside the extracted program folder:** in File Explorer, open `C:\JHFRC_Address2Tract`, click the address bar, type `cmd`, and press Enter.

**B5. Preview the network destinations without transmitting data** (same command as the actual run, without `--approve-egress`):

```bat
JHFRC_Address2Tract.exe --input "data\input\partner_addresses.xlsx" --output "data\output\partner_results.xlsx" --id-column record_id --street-column street --city-column city --state-column state --zip-column zip --no-external-fallback
```

**B6. Execute only after the partner approves external Census geocoding:**

```bat
JHFRC_Address2Tract.exe --input "data\input\partner_addresses.xlsx" --output "data\output\partner_results.xlsx" --id-column record_id --street-column street --city-column city --state-column state --zip-column zip --approve-egress --no-external-fallback
```

If the spreadsheet has one full-address column named `full_address`, use:

```bat
JHFRC_Address2Tract.exe --input "data\input\partner_addresses.xlsx" --output "data\output\partner_results.xlsx" --id-column record_id --address-column full_address --approve-egress --no-external-fallback
```

Replace `record_id` and the address-column names with the exact names in the partner's file. If the input has more than 50,000 records, this program automatically uses resumable chunks and writes a CSV output; check the terminal for the actual output path.

**B7. Inspect and retain the results locally.** The program writes `census_tract_geoid`, `match_status`, `error_reason`, and a `cleaned_address` field along with input address columns. Review `No_Match`, `Tie`, and `Rejected`. Do not treat `Matched_ZIP_Approx` as a verified address-level match. Store 11-digit GEOIDs as **text** to retain leading zeros.

**B8. Prepare the approved exchange.** For the partner's internal join, create an ID-to-GEOID extract (plus match status, if needed). Do **not** email the full converter output: it contains street addresses. Any extract provided to JHFRC must use the approved secure channel and minimum necessary fields. Counts and match rates may be reported without sharing records. The presence of a random ID does not itself make an address file anonymous.

**B9. Close the visit.** Let the partner retain source, results, and approved working files on its system. Include `data\output` intermediate chunk folders in its retention/deletion process; do not copy them to UTC. Document processing date, tool version/commit, record counts, match status counts, and technical issues without recording client addresses.

## Troubleshooting

- **Python not installed on partner computer:** expected. Run `JHFRC_Address2Tract.exe`, not `python main.py`.
- **The EXE is absent or `_internal` is missing:** extract the complete ZIP again. Do not copy only the EXE.
- **Windows security blocks execution:** contact partner IT for an approved/signed distribution; do not bypass controls.
- **Missing column:** check header spelling and update the matching `--id-column` / address-column arguments.
- **Network/firewall error:** have partner IT review the approved Census destinations. Never use `--approve-egress` without authorization.
- **Many unmatched records:** preserve the counts and contact JHFRC; do not enable third-party geocoders or ZIP approximation without a documented decision.
- **No output / library loading error:** reproduce using synthetic data and revise the Windows build on the UTC computer; do not troubleshoot by exporting the real partner file.

**Project repository:** https://github.com/amohith7/jhfrc-address2tract  
**Help:** mohith-addepalli@utc.edu

**Author: Mohith Addepalli**
