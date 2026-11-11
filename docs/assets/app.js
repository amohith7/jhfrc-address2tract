/*
 * address2tract web: page wiring (file reading, column picking, progress,
 * downloads) and the browser network transport. Geocoding rules live in
 * geocoder.js and core.js.
 */
(function () {
  "use strict";

  const MAX_ROWS = 5000;
  const NONE = "";
  const $ = (id) => document.getElementById(id);

  const state = { workbook: null, fileName: "", columns: [], rows: [], records: null, cols: null, controller: null };

  // ---------------------------------------------------------------- transport

  let jsonpCounter = 0;

  /** Census geocoder over JSONP (it sends no CORS headers, so fetch cannot read it). */
  function jsonp(url, params, timeoutMs = 30000) {
    return new Promise((resolve, reject) => {
      const name = "__a2t_cb_" + ++jsonpCounter;
      const script = document.createElement("script");
      let timer;
      const cleanup = () => {
        clearTimeout(timer);
        script.remove();
        // A late response must still find a function, so keep a no-op briefly.
        window[name] = () => {};
        setTimeout(() => delete window[name], 60000);
      };
      window[name] = (data) => {
        cleanup();
        resolve(data);
      };
      script.onerror = () => {
        cleanup();
        reject(new Error("Census geocoder request failed"));
      };
      timer = setTimeout(() => {
        cleanup();
        reject(new Error("Census geocoder request timed out"));
      }, timeoutMs);
      const qs = new URLSearchParams({ ...params, callback: name });
      script.src = url + "?" + qs.toString();
      document.head.appendChild(script);
    });
  }

  async function fetchJson(url, params) {
    const res = await fetch(url + "?" + new URLSearchParams(params).toString(), {
      headers: { Accept: "application/json" },
    });
    if (!res.ok) throw new Error("HTTP " + res.status);
    return res.json();
  }

  const transport = {
    jsonp,
    fetchJson,
    sleep: (ms) => new Promise((r) => setTimeout(r, ms)),
  };

  // ------------------------------------------------------------- file reading

  /**
   * Text of one cell as Excel shows it, without the usual traps: a ZIP stored
   * as a number with a "00000" format keeps its leading zero, and a long
   * numeric ID is written out in full rather than in scientific notation.
   */
  function cellText(cell) {
    if (!cell || cell.v === undefined || cell.v === null) return "";
    if (cell.t === "n") {
      if (cell.z && /^0+$/.test(String(cell.z)) && cell.w) return cell.w;
      // A date is stored as a serial number; use the date Excel shows.
      if (cell.z && cell.w && XLSX.SSF.is_date(cell.z)) return cell.w;
      if (Number.isInteger(cell.v)) return BigInt(cell.v).toString();
      return String(cell.v);
    }
    if (cell.t === "d" || cell.t === "e") return cell.w || String(cell.v);
    return String(cell.v);
  }

  function readSheet(ws) {
    if (!ws || !ws["!ref"]) return { columns: [], rows: [] };
    const range = XLSX.utils.decode_range(ws["!ref"]);
    const columns = [];
    const used = new Set();
    for (let c = range.s.c; c <= range.e.c; c++) {
      let name = cellText(ws[XLSX.utils.encode_cell({ r: range.s.r, c })]).trim();
      if (!name) name = "Column " + (c - range.s.c + 1);
      let unique = name;
      for (let k = 2; used.has(unique); k++) unique = name + " (" + k + ")";
      used.add(unique);
      columns.push(unique);
    }
    const rows = [];
    for (let r = range.s.r + 1; r <= range.e.r; r++) {
      const row = {};
      let any = false;
      for (let c = range.s.c; c <= range.e.c; c++) {
        const text = cellText(ws[XLSX.utils.encode_cell({ r, c })]);
        row[columns[c - range.s.c]] = text;
        if (text.trim()) any = true;
      }
      if (any) rows.push(row);
    }
    return { columns, rows };
  }

  async function openFile(file) {
    resetFrom("file");
    const ext = (file.name.split(".").pop() || "").toLowerCase();
    if (!["xlsx", "xls", "csv"].includes(ext)) {
      showFileInfo("Please choose an Excel (.xlsx, .xls) or CSV file.", true);
      return;
    }
    const data = await file.arrayBuffer();
    try {
      // CSV: keep every value as text so ZIPs and IDs keep their leading zeros.
      // Excel: keep number formats (cellNF) so a "00000" ZIP format is seen.
      state.workbook = XLSX.read(data, ext === "csv" ? { type: "array", raw: true } : { type: "array", cellNF: true });
    } catch (err) {
      showFileInfo("This file could not be read. Is it a valid Excel or CSV file?", true);
      return;
    }
    state.fileName = file.name;
    const sheets = state.workbook.SheetNames;
    const sel = $("sheet");
    sel.innerHTML = "";
    sheets.forEach((s) => sel.add(new Option(s, s)));
    $("sheet-field").hidden = sheets.length < 2;
    loadSheet(sheets[0]);
  }

  function loadSheet(name) {
    const { columns, rows } = readSheet(state.workbook.Sheets[name]);
    state.columns = columns;
    state.rows = rows;
    $("step-columns").hidden = true;
    // Refuse the file as soon as it is opened, before any column is offered.
    const phi = A2T.checkForPhi(columns);
    if (phi.length) {
      state.rows = [];
      showFileInfo(state.fileName + ": " + phiMessage(phi), true);
      return;
    }
    const cells = A2T.scanCellsForPhi(rows, columns);
    if (cells.length) {
      state.rows = [];
      showFileInfo(state.fileName + ": " + cellPhiMessage(cells), true);
      return;
    }
    if (!rows.length) {
      showFileInfo(state.fileName + ": this sheet has no data rows.", true);
      return;
    }
    if (rows.length > MAX_ROWS) {
      showFileInfo(
        state.fileName + " has " + rows.length.toLocaleString() + " rows. The limit is " +
          MAX_ROWS.toLocaleString() + ". Please split it into smaller files.",
        true
      );
      return;
    }
    showFileInfo(state.fileName + ": " + rows.length.toLocaleString() + " rows, " + columns.length + " columns", false);
    fillColumnPickers();
    $("step-columns").hidden = false;
  }

  /** Where personal-looking values were found, by row number, never the values. */
  function cellPhiMessage(cells) {
    const rowList = (rs) =>
      rs.slice(0, 8).join(", ") + (rs.length > 8 ? " and " + (rs.length - 8) + " more" : "");
    return (
      "This file appears to contain personal information:\n" +
      cells.map((c) => '  - column "' + c.column + '" has ' + c.kind + " in row(s) " + rowList(c.rows)).join("\n") +
      "\n\nTo protect client privacy, nothing has been sent anywhere. Please remove this " +
      "information from your file (keep only a unique ID and the address), save it, and choose it again."
    );
  }

  function phiMessage(phi) {
    return (
      "This file contains column(s) that may include sensitive personal information:\n" +
      phi.map((c) => '  - "' + c + '"').join("\n") +
      "\n\nTo protect client privacy, this tool only accepts the minimum necessary information. " +
      "Please remove these columns from your file, save it, and choose it again. " +
      "Keep only a unique ID and the address column(s)."
    );
  }

  function showFileInfo(text, isError) {
    const el = $("file-info");
    el.textContent = text;
    el.className = isError ? "alert" : "file-info";
    el.hidden = false;
  }

  // ---------------------------------------------------------- column picking

  const PICKERS = { id: "col-id", address: "col-address", street: "col-street", city: "col-city", state: "col-state", zip: "col-zip" };

  function fillColumnPickers() {
    const guess = A2T.guessColumns(state.columns);
    for (const [key, id] of Object.entries(PICKERS)) {
      const sel = $(id);
      sel.innerHTML = "";
      sel.add(new Option(key === "id" ? "Choose a column" : "(none)", NONE));
      state.columns.forEach((c) => sel.add(new Option(c, c)));
      sel.value = guess[key] || NONE;
    }
    const single = !guess.street && !!guess.address;
    document.querySelector('input[name="mode"][value="' + (single ? "single" : "split") + '"]').checked = true;
    syncMode();
    $("column-error").hidden = true;
  }

  function mode() {
    return document.querySelector('input[name="mode"]:checked').value;
  }

  function syncMode() {
    $("split-fields").hidden = mode() !== "split";
    $("single-fields").hidden = mode() !== "single";
  }

  function chosenColumns() {
    const v = (key) => $(PICKERS[key]).value;
    if (mode() === "single") return { id: v("id"), address: v("address") };
    return { id: v("id"), street: v("street"), city: v("city"), state: v("state"), zip: v("zip") };
  }

  /** Same checks the Python tool runs before geocoding. Returns an error or "". */
  function validate(cols) {
    if (!cols.id) return "Choose the column that holds each record's unique ID.";
    if (mode() === "single" && !cols.address) return "Choose the column that holds the full address.";
    if (mode() === "split" && !cols.street) return "Choose at least the street column.";
    const picked = Object.values(cols).filter(Boolean);
    if (new Set(picked).size !== picked.length) return "Each column can only be used once. Please check your choices.";

    // Backstop only: loadSheet already refuses a sheet with these columns.
    const phi = A2T.checkForPhi(state.columns);
    if (phi.length) return phiMessage(phi);

    const addressCols = picked.filter((c) => c !== cols.id);
    const dups = A2T.findDuplicateIds(state.rows, cols.id, addressCols);
    if (dups.length) {
      return (
        "Each ID must be unique, but " + dups.length + " ID(s) appear more than once, for example: " +
        dups.slice(0, 10).join(", ") + ". Please fix the duplicates and choose the file again."
      );
    }
    return "";
  }

  // ------------------------------------------------------------------ running

  function warnOnLeave(e) {
    e.preventDefault();
    e.returnValue = "";
  }

  const STAGES = {
    census: "Looking up addresses with the U.S. Census geocoder",
    retry: "Retrying addresses that hit a network error",
    external: "Checking remaining addresses with OpenStreetMap (about 1 per second)",
  };

  async function start() {
    const cols = chosenColumns();
    const error = validate(cols);
    const box = $("column-error");
    if (error) {
      box.textContent = error;
      box.hidden = false;
      return;
    }
    box.hidden = true;
    state.cols = cols;
    state.records = A2TGeocoder.buildRecords(state.rows, cols);
    state.controller = new AbortController();

    setLocked(true);
    $("step-results").hidden = true;
    $("step-run").hidden = false;
    $("step-run").scrollIntoView({ behavior: "smooth", block: "start" });
    window.addEventListener("beforeunload", warnOnLeave);

    const startedAt = Date.now();
    let stageStart = startedAt;
    let lastStage = "";
    try {
      await A2TGeocoder.run(state.records, {
        transport,
        signal: state.controller.signal,
        onProgress: ({ stage, done, total }) => {
          if (stage !== lastStage) {
            lastStage = stage;
            stageStart = Date.now();
          }
          $("run-stage").textContent = STAGES[stage] + "...";
          const pct = total ? Math.round((done / total) * 100) : 100;
          $("bar-fill").style.width = pct + "%";
          $("bar").setAttribute("aria-valuenow", pct);
          const elapsed = (Date.now() - stageStart) / 1000;
          const left = done ? Math.round((elapsed / done) * (total - done)) : 0;
          $("run-detail").textContent =
            done.toLocaleString() + " of " + total.toLocaleString() + " done" +
            (done < total && left > 5 ? ", about " + formatDuration(left) + " left in this step" : "");
        },
      });
      showResults();
    } catch (err) {
      if (err instanceof A2TGeocoder.Cancelled) {
        $("step-run").hidden = true;
      } else {
        $("run-stage").textContent = "Something went wrong: " + err.message + ". Please try again.";
      }
    } finally {
      window.removeEventListener("beforeunload", warnOnLeave);
      setLocked(false);
    }
  }

  function formatDuration(seconds) {
    if (seconds < 90) return seconds + " seconds";
    return Math.round(seconds / 60) + " minutes";
  }

  function setLocked(locked) {
    $("start").disabled = locked;
    $("file").disabled = locked;
    $("sheet").disabled = locked;
    Object.values(PICKERS).forEach((id) => ($(id).disabled = locked));
    document.querySelectorAll('input[name="mode"]').forEach((r) => (r.disabled = locked));
  }

  // ------------------------------------------------------------------ results

  function showResults() {
    const t = A2T.tally(state.records);
    const pct = (n) => (t.total ? ((n / t.total) * 100).toFixed(1) + "%" : "0%");
    const tiles = [
      ["Total records", t.total.toLocaleString()],
      ["Matched", t.matched.toLocaleString() + " (" + pct(t.matched) + ")"],
      ["Unmatched", t.unmatched.toLocaleString() + " (" + pct(t.unmatched) + ")"],
      ["Rejected (missing data)", t.rejected.toLocaleString()],
    ];
    $("tiles").innerHTML = "";
    for (const [label, value] of tiles) {
      const div = document.createElement("div");
      div.className = "tile";
      div.innerHTML = '<div class="value"></div><div class="label"></div>';
      div.querySelector(".value").textContent = value;
      div.querySelector(".label").textContent = label;
      $("tiles").appendChild(div);
    }
    const methods = [
      ["Census geocoder, current address file (Matched)", t.census],
      ["Census geocoder, 2020 file or tidied street (Matched_Fallback)", t.censusAlt],
      ["OpenStreetMap Nominatim (Matched_External)", t.external],
    ];
    $("methods").innerHTML = "";
    for (const [label, n] of methods) {
      const tr = $("methods").insertRow();
      tr.insertCell().textContent = label;
      tr.insertCell().textContent = n.toLocaleString();
    }
    $("step-run").hidden = true;
    $("step-results").hidden = false;
    $("step-results").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  /** Write rows to .xlsx with the ID and tract columns stored as text. */
  function download(rows, fileName) {
    const header = Object.keys(rows[0] || {});
    const ws = XLSX.utils.json_to_sheet(rows, { header });
    const textCols = [state.cols.id, "census_tract_geoid"];
    textCols.forEach((name) => {
      const c = header.indexOf(name);
      if (c < 0) return;
      for (let r = 1; r <= rows.length; r++) {
        const ref = XLSX.utils.encode_cell({ r, c });
        if (!ws[ref]) ws[ref] = { t: "s", v: "" };
        ws[ref].t = "s";
        ws[ref].v = String(ws[ref].v);
        ws[ref].z = "@";
      }
    });
    ws["!cols"] = header.map((h) => ({ wch: Math.max(12, Math.min(48, h.length + 4)) }));
    const wb = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(wb, ws, "Sheet1");
    XLSX.writeFile(wb, fileName);
  }

  function resetFrom(step) {
    if (step === "file") {
      state.workbook = null;
      state.records = null;
      $("file-info").hidden = true;
      $("sheet-field").hidden = true;
      $("step-columns").hidden = true;
    }
    $("step-run").hidden = true;
    $("step-results").hidden = true;
  }

  // ------------------------------------------------------------------- wiring

  $("max-rows").textContent = MAX_ROWS.toLocaleString();
  $("file").addEventListener("change", (e) => {
    const f = e.target.files[0];
    // Clear the input so choosing the same (now fixed) file again still fires.
    e.target.value = "";
    if (f) openFile(f);
  });
  $("sheet").addEventListener("change", (e) => {
    resetFrom("sheet");
    loadSheet(e.target.value);
  });
  document.querySelectorAll('input[name="mode"]').forEach((r) => r.addEventListener("change", syncMode));
  $("start").addEventListener("click", start);
  $("cancel").addEventListener("click", () => state.controller && state.controller.abort());
  $("dl-share").addEventListener("click", () => download(A2TGeocoder.shareRows(state.records, state.cols), "results_to_share.xlsx"));
  $("dl-full").addEventListener("click", () => download(A2TGeocoder.resultRows(state.records, state.cols), "results.xlsx"));
  $("again").addEventListener("click", () => {
    $("file").value = "";
    resetFrom("file");
    window.scrollTo({ top: 0, behavior: "smooth" });
  });

  const drop = $("drop");
  ["dragenter", "dragover"].forEach((ev) =>
    drop.addEventListener(ev, (e) => {
      e.preventDefault();
      drop.classList.add("over");
    })
  );
  ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, () => drop.classList.remove("over")));
  drop.addEventListener("drop", (e) => {
    e.preventDefault();
    if ($("file").disabled) return;
    const f = e.dataTransfer.files[0];
    if (f) openFile(f);
  });
})();
