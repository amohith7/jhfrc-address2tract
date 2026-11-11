# Vendored libraries

| File | Library | Version | License | Source |
|------|---------|---------|---------|--------|
| `xlsx.full.min.js` | SheetJS Community Edition | 0.20.3 | Apache License 2.0 | https://cdn.sheetjs.com/xlsx-0.20.3/package/dist/xlsx.full.min.js |

SheetJS is served from this repository rather than a CDN so the site keeps working
if the CDN changes, and so the exact code that reads a partner's file is the code
reviewed here. The npm-published `xlsx` package stops at 0.18.5, which has known
security issues (CVE-2023-30533, CVE-2024-22363); 0.20.3 fixes both.

To upgrade, download the new `xlsx.full.min.js` from https://cdn.sheetjs.com/,
replace this file, and update the version above.
