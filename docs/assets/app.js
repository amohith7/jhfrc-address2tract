/*
 * address2tract web: page wiring. Four steps (upload, columns, process,
 * results), the browser network transport, and the results workspace
 * (filter, search, sort, fix and retry). Geocoding rules live in
 * geocoder.js and core.js.
 */
(function () {
  "use strict";

  const MAX_ROWS = 5000;
  // Set in index.html so the footer and the downloads quote the same version.
  const APP_VERSION = (document.querySelector('meta[name="a2t-version"]') || {}).content || "";
  // ID column names that suggest a medical record, account or member number.
  const SENSITIVE_ID = /mrn|medical|patient|chart|account|acct|member|insur|ssn|social|encounter|visit/i;
  const PAGE_SIZE = 50;
  const FEED_SIZE = 8;
  const NONE = "";
  const $ = (id) => document.getElementById(id);

  const STATUS_LABELS = {
    Matched: "Census",
    Matched_Fallback: "Census (2020 / tidied)",
    Matched_External: "ArcGIS",
    No_Match: "Unmatched",
    Rejected: "Rejected",
  };
  const STATUS_COLORS = {
    Matched: "var(--s-matched)",
    Matched_Fallback: "var(--s-fallback)",
    Matched_External: "var(--s-external)",
    No_Match: "var(--s-nomatch)",
    Rejected: "var(--s-rejected)",
  };
  const STATUS_ORDER = ["Matched", "Matched_Fallback", "Matched_External", "No_Match", "Rejected"];

  const state = {
    workbook: null,
    fileName: "",
    columns: [],
    rows: [],
    cols: null,
    records: null,
    controller: null,
    edits: new Map(), // record -> edited address text
    view: { filter: "all", q: "", sortKey: "", sortDir: 1, page: 0, tract: "", tractName: "" },
  };

  // ------------------------------------------------------------ small helpers

  function el(tag, attrs, ...children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") node.className = v;
      else if (k === "text") node.textContent = v;
      else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v === true ? "" : v);
    }
    for (const c of children) if (c !== null && c !== undefined) node.append(c);
    return node;
  }

  function icon(name) {
    const ns = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("class", "ic");
    svg.setAttribute("aria-hidden", "true");
    const use = document.createElementNS(ns, "use");
    use.setAttribute("href", "#i-" + name);
    svg.append(use);
    return svg;
  }

  function statusChip(status) {
    return el("span", { class: "status " + status, text: STATUS_LABELS[status] || status });
  }

  const fmt = (n) => n.toLocaleString();
  const pct = (n, d) => (d ? ((n / d) * 100).toFixed(1) + "%" : "0%");

  function toast(message, kind = "info", ms = 4000) {
    const t = el("div", { class: "toast " + kind, role: kind === "error" ? "alert" : "status" },
      icon(kind === "ok" ? "check" : kind === "error" ? "alert" : "sparkle"), el("span", { text: message }));
    $("toasts").append(t);
    setTimeout(() => {
      t.classList.add("out");
      setTimeout(() => t.remove(), 300);
    }, ms);
  }

  function formatDuration(seconds) {
    seconds = Math.max(0, Math.round(seconds));
    if (seconds < 60) return seconds + "s";
    const m = Math.floor(seconds / 60);
    const s = seconds % 60;
    return m + "m " + String(s).padStart(2, "0") + "s";
  }

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
      script.src = url + "?" + new URLSearchParams({ ...params, callback: name }).toString();
      document.head.appendChild(script);
    });
  }

  /**
   * ArcGIS over a form POST: a "simple" CORS request (no preflight), and the
   * token travels in the request body rather than the URL, so it stays out of
   * logs that record addresses of requests.
   */
  async function postForm(url, params) {
    const res = await fetch(url, {
      method: "POST",
      body: new URLSearchParams(params),
      credentials: "omit",
      referrerPolicy: "origin",
    });
    if (!res.ok) throw new Error("HTTP " + res.status);
    return res.json();
  }

  const transport = { jsonp, postForm, sleep: (ms) => new Promise((r) => setTimeout(r, ms)) };

  // ------------------------------------------------------------ ArcGIS mode

  // The ArcGIS box sits collapsed under "Advanced" on the Columns step. Older
  // links ending in #arcgis open it automatically. The token lives only in
  // this page's memory: never saved in the browser, put in a URL or written
  // to a results file.
  const ARCGIS_LINK = /(^|[#&])arcgis\b/i.test(location.hash);

  function arcgisToken() {
    return $("arcgis-token").value.trim();
  }

  /** Describe the optional ArcGIS step for this run. */
  function setExternalProvider(useArcgis) {
    $("stage-external-note").textContent = useArcgis
      ? "Addresses the Census could not find, with your token"
      : "Off: no ArcGIS token was added";
  }

  function initArcgis() {
    if (ARCGIS_LINK) $("arcgis-box").open = true;
    const input = $("arcgis-token");
    $("arcgis-show").addEventListener("change", (e) => (input.type = e.target.checked ? "text" : "password"));
    const status = $("arcgis-status");
    const setStatus = (kind, text) => {
      status.className = "token-status " + kind;
      status.textContent = text;
      status.hidden = !text;
    };
    input.addEventListener("input", () => setStatus("", ""));
    $("arcgis-check").addEventListener("click", async () => {
      const btn = $("arcgis-check");
      btn.classList.add("busy");
      btn.disabled = true;
      // One public landmark address (the White House) is sent, nothing from the file.
      setStatus("", "Checking with ArcGIS using a public test address...");
      const res = await A2TGeocoder.checkArcgisToken(transport, input.value);
      btn.classList.remove("busy");
      btn.disabled = false;
      if (res.ok) setStatus("ok", "Token accepted by ArcGIS.");
      else setStatus("bad", res.message + ".");
    });
    $("arcgis-clear").addEventListener("click", () => {
      setStatus("", "");
      input.value = "";
      input.type = "password";
      $("arcgis-show").checked = false;
      toast("Token cleared from this page.", "info");
    });
  }

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

  // ------------------------------------------------------------------- steps

  let currentStep = 1;

  function goTo(step) {
    currentStep = step;
    document.querySelectorAll("[data-panel]").forEach((p) => (p.hidden = Number(p.dataset.panel) !== step));
    document.querySelectorAll(".stepper li").forEach((li) => {
      const n = Number(li.dataset.step);
      li.classList.toggle("active", n === step);
      li.classList.toggle("done", n < step);
      if (n === step) li.setAttribute("aria-current", "step");
      else li.removeAttribute("aria-current");
    });
    syncActionBar();
    const app = $("app");
    if (app.getBoundingClientRect().top < 0 || app.getBoundingClientRect().top > window.innerHeight * 0.6) {
      app.scrollIntoView({ behavior: "smooth", block: "start" });
    }
    const h = $("h" + step);
    if (h) h.focus({ preventScroll: true });
  }

  // ----------------------------------------------------------- step 1: upload

  function resetUpload() {
    state.workbook = null;
    state.fileName = "";
    state.columns = [];
    state.rows = [];
    state.records = null;
    state.edits.clear();
    ["file-card", "sheet-field", "privacy-ok", "file-error", "too-big"].forEach((id) => ($(id).hidden = true));
    $("to-columns").disabled = true;
  }

  async function openFile(file) {
    resetUpload();
    const ext = (file.name.split(".").pop() || "").toLowerCase();
    if (!["xlsx", "xls", "csv"].includes(ext)) {
      showFileCard(file.name, "Not a supported file type", true);
      showError("Please choose an Excel (.xlsx, .xls) or CSV file.");
      return;
    }
    let data;
    try {
      data = await file.arrayBuffer();
      // CSV: keep every value as text so ZIPs and IDs keep their leading zeros.
      // Excel: keep number formats (cellNF) so a "00000" ZIP format is seen.
      state.workbook = XLSX.read(data, ext === "csv" ? { type: "array", raw: true } : { type: "array", cellNF: true });
    } catch (err) {
      showFileCard(file.name, "Could not be read", true);
      showError("This file could not be read. Is it a valid Excel or CSV file?");
      return;
    }
    const sheets = state.workbook.SheetNames;
    const sel = $("sheet");
    sel.innerHTML = "";
    sheets.forEach((s) => sel.add(new Option(s, s)));
    $("sheet-field").hidden = sheets.length < 2;
    const { columns, rows } = readSheet(state.workbook.Sheets[sheets[0]]);
    acceptRows(file.name, columns, rows);
  }

  /** Run every upload check; only a clean, right-sized sheet unlocks step 2. */
  function acceptRows(fileName, columns, rows) {
    state.fileName = fileName;
    state.columns = columns;
    state.rows = [];
    ["privacy-ok", "file-error", "too-big"].forEach((id) => ($(id).hidden = true));
    $("to-columns").disabled = true;

    // Privacy first: refuse before anything else is shown or offered.
    const phi = A2T.checkForPhi(columns);
    if (phi.length) {
      showFileCard(fileName, "Refused: sensitive columns", true);
      showError(phiMessage(phi));
      return;
    }
    const cells = A2T.scanCellsForPhi(rows, columns);
    if (cells.length) {
      showFileCard(fileName, "Refused: personal details found", true);
      showError(cellPhiMessage(cells));
      return;
    }
    if (!rows.length) {
      showFileCard(fileName, "No data rows", true);
      showError("This sheet has no data rows.");
      return;
    }

    state.rows = rows;
    showFileCard(fileName, fmt(rows.length) + " rows · " + columns.length + " columns", false);
    $("privacy-ok-text").textContent =
      "No sensitive column names, and no emails, phone numbers, SSNs, dates or names found in " +
      fmt(rows.length * columns.length) + " cells.";
    $("privacy-ok").hidden = false;

    if (rows.length > MAX_ROWS) {
      const parts = Math.ceil(rows.length / MAX_ROWS);
      $("too-big-title").textContent = "This file has " + fmt(rows.length) + " rows.";
      $("split-label").textContent = "Split into " + parts + " files of up to " + fmt(MAX_ROWS) + " rows";
      $("too-big").hidden = false;
      return;
    }
    $("to-columns").disabled = false;
  }

  function showFileCard(name, stats, bad) {
    $("file-name").textContent = name;
    $("file-stats").textContent = stats;
    $("file-card").classList.toggle("bad", !!bad);
    $("file-card").hidden = false;
  }

  function showError(text) {
    $("file-error-text").textContent = text;
    $("file-error").hidden = false;
  }

  function phiMessage(phi) {
    return (
      "This file contains column(s) that may include sensitive personal information:\n" +
      phi.map((c) => '  - "' + c + '"').join("\n") +
      "\n\nTo protect client privacy, nothing has been sent anywhere. Please remove these columns " +
      "from your file, save it, and choose it again. Keep only a unique ID and the address column(s)."
    );
  }

  /** Where personal-looking values were found, by row number, never the values. */
  function cellPhiMessage(cells) {
    const rowList = (rs) => rs.slice(0, 8).join(", ") + (rs.length > 8 ? " and " + (rs.length - 8) + " more" : "");
    return (
      "This file appears to contain personal information:\n" +
      cells.map((c) => '  - column "' + c.column + '" has ' + c.kind + " in row(s) " + rowList(c.rows)).join("\n") +
      "\n\nTo protect client privacy, nothing has been sent anywhere. Please remove this " +
      "information from your file (keep only a unique ID and the address), save it, and choose it again."
    );
  }

  function loadSample() {
    resetUpload();
    $("sheet-field").hidden = true;
    const s = window.A2TSample;
    acceptRows(s.fileName, s.columns.slice(), s.rows.map((r) => ({ ...r })));
    goTo(1);
    toast("Sample data loaded: " + window.A2TSample.rows.length + " public places around Chattanooga.", "ok");
  }

  /** Download the (clean) file as parts of at most MAX_ROWS rows each. */
  function splitFile() {
    const base = state.fileName.replace(/\.[^.]+$/, "");
    const parts = Math.ceil(state.rows.length / MAX_ROWS);
    for (let p = 0; p < parts; p++) {
      const chunk = state.rows.slice(p * MAX_ROWS, (p + 1) * MAX_ROWS);
      writeXlsx(chunk, state.columns, state.columns, base + "_part" + (p + 1) + ".xlsx");
    }
    toast("Downloaded " + parts + " files. Run them one at a time.", "ok", 6000);
  }

  // ---------------------------------------------------------- step 2: columns

  const PICKERS = { id: "col-id", address: "col-address", street: "col-street", city: "col-city", state: "col-state", zip: "col-zip" };
  const ROLE_LABELS = { id: "ID", address: "Address", street: "Street", city: "City", state: "State", zip: "ZIP" };

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

  const mode = () => document.querySelector('input[name="mode"]:checked').value;

  function syncMode() {
    $("split-fields").hidden = mode() !== "split";
    $("single-fields").hidden = mode() !== "single";
    renderMapping();
  }

  function chosenColumns() {
    const v = (key) => $(PICKERS[key]).value;
    if (mode() === "single") return { id: v("id"), address: v("address") };
    return { id: v("id"), street: v("street"), city: v("city"), state: v("state"), zip: v("zip") };
  }

  /** Preview table with the chosen columns highlighted, plus a sample address. */
  function renderMapping() {
    const cols = chosenColumns();
    const roleOf = {};
    for (const [role, col] of Object.entries(cols)) if (col) roleOf[col] = ROLE_LABELS[role];
    const table = $("preview");
    table.innerHTML = "";
    const head = el("tr");
    for (const c of state.columns) {
      const th = el("th", { class: roleOf[c] ? "role" : null }, roleOf[c] ? el("span", { class: "role-tag", text: roleOf[c] }) : null, c);
      head.append(th);
    }
    table.append(el("thead", null, head));
    const body = el("tbody");
    for (const row of state.rows.slice(0, 5)) {
      const tr = el("tr");
      for (const c of state.columns) tr.append(el("td", { class: roleOf[c] ? "role" : null, text: row[c] }));
      body.append(tr);
    }
    table.append(body);

    const first = state.rows.find((r) => !A2T.isBlank(cols.address ? r[cols.address] : r[cols.street]));
    const ex = $("example");
    ex.innerHTML = "";
    if (first && (cols.address || cols.street)) {
      const addr = cols.address ? A2T.cleanCell(first[cols.address]) : A2T.combineAddress(first, cols);
      ex.append(el("span", { class: "muted", text: "Example lookup: " }), el("strong", { text: addr }));
    } else {
      ex.append(el("span", { class: "muted", text: "Choose the address column(s) to see an example lookup." }));
    }

    // Warn when the ID column looks like a medical record or account number.
    $("id-warning").hidden = !(cols.id && SENSITIVE_ID.test(cols.id));
    renderEstimate(cols);
  }

  /**
   * Rough run time: the Census answers about 5 to 10 addresses a second with
   * six lookups in flight; misses take longer because both address files and
   * a tidied street are tried.
   */
  function renderEstimate(cols) {
    const out = $("estimate");
    if (!cols.id || !(cols.address || cols.street)) {
      out.textContent = "";
      return;
    }
    const n = state.rows.filter((r) => {
      const addr = cols.address ? A2T.cleanCell(r[cols.address]) : A2T.combineAddress(r, cols);
      return !A2T.isBlank(addr) && !A2T.isBlank(A2T.cleanCell(r[cols.id]));
    }).length;
    const lo = Math.max(5, n / 10);
    const hi = Math.max(10, n / 5);
    out.textContent =
      "Estimated time for " + fmt(n) + (n === 1 ? " address: " : " addresses: ") +
      (hi < 60 ? "under a minute" : "about " + formatDuration(lo) + " to " + formatDuration(hi)) +
      ". Addresses the Census cannot find on the first try, and ArcGIS lookups, can make it take longer. Keep the tab open while it runs.";
  }

  /** Same checks the Python tool runs before geocoding. Returns an error or "". */
  function validate(cols) {
    if (!cols.id) return "Choose the column that holds each record's unique ID.";
    if (mode() === "single" && !cols.address) return "Choose the column that holds the full address.";
    if (mode() === "split" && !cols.street) return "Choose at least the street column.";
    const picked = Object.values(cols).filter(Boolean);
    if (new Set(picked).size !== picked.length) return "Each column can only be used once. Please check your choices.";
    // Backstop only: acceptRows already refuses files with these.
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
    if (!$("ack").checked) return "Please read the confirmation above the Start button and tick the box.";
    return "";
  }

  // ---------------------------------------------------------- step 3: process

  const COUNTERS = [
    ["Matched", "Census"],
    ["Matched_Fallback", "Census 2020 / tidied"],
    ["Matched_External", "ArcGIS"],
    ["pending", "No match yet"],
  ];

  function warnOnLeave(e) {
    e.preventDefault();
    e.returnValue = "";
  }

  function setStage(stage, status) {
    const li = document.querySelector('#stages [data-stage="' + stage + '"]');
    li.classList.remove("active", "done", "skipped");
    if (status) li.classList.add(status);
  }

  async function start() {
    const cols = chosenColumns();
    const error = validate(cols);
    if (error) {
      $("column-error-text").textContent = error;
      $("column-error").hidden = false;
      return;
    }
    $("column-error").hidden = true;
    state.cols = cols;
    state.processedOn = localDate();
    state.records = A2TGeocoder.buildRecords(state.rows, cols);
    state.edits.clear();
    state.view = { filter: "all", q: "", sortKey: "", sortDir: 1, page: 0, tract: "", tractName: "" };
    if (window.A2TMap) A2TMap.clearSelection();
    $("search").value = "";
    state.controller = new AbortController();
    const token = arcgisToken();
    setExternalProvider(Boolean(token));
    $("provider-error").hidden = true;

    ["census", "retry", "external"].forEach((s) => setStage(s, null));
    setStage("census", "active");
    $("feed").innerHTML = "";
    $("bar-fill").style.width = "0%";
    $("run-detail").textContent = "Starting...";
    goTo(3);
    window.addEventListener("beforeunload", warnOnLeave);

    const startedAt = Date.now();
    const timer = setInterval(() => ($("elapsed").textContent = "Elapsed " + formatDuration((Date.now() - startedAt) / 1000) + "."), 1000);
    const processed = new Set();
    const feed = [];
    const seen = new Set(["census"]);
    let stageStart = startedAt;
    let lastStage = "census";
    let renderQueued = false;

    const renderLive = () => {
      renderQueued = false;
      const t = A2T.tally(state.records);
      const matched = t.census + t.censusAlt + t.external;
      const values = { Matched: t.census, Matched_Fallback: t.censusAlt, Matched_External: t.external, pending: Math.max(0, processed.size - matched) };
      $("counters").replaceChildren(
        ...COUNTERS.map(([key, label]) =>
          el("div", { class: "counter", style: "--c:" + (STATUS_COLORS[key] || "var(--s-nomatch)") }, el("b", { text: fmt(values[key]) }), el("span", { text: label })))
      );
      $("feed").replaceChildren(
        ...feed.map((r) =>
          el("li", null, el("strong", { text: r.id }), statusChip(r.match_status),
            el("span", { class: "mono", text: r.census_tract_geoid ? "Tract " + r.census_tract_geoid : r.match_status === "No_Match" ? "No match yet" : "No tract" })))
      );
    };
    const queueRender = () => {
      if (!renderQueued) {
        renderQueued = true;
        setTimeout(renderLive, 150);
      }
    };
    renderLive();

    try {
      await A2TGeocoder.run(state.records, {
        transport,
        signal: state.controller.signal,
        options: { arcgisToken: token },
        onProviderError: showProviderError,
        onRecord: (rec) => {
          processed.add(rec);
          feed.unshift({ id: rec.id, match_status: rec.match_status, census_tract_geoid: rec.census_tract_geoid });
          feed.length = Math.min(feed.length, FEED_SIZE);
          queueRender();
        },
        onProgress: ({ stage, done, total }) => {
          if (stage !== lastStage) {
            setStage(lastStage, "done");
            setStage(stage, "active");
            seen.add(stage);
            lastStage = stage;
            stageStart = Date.now();
          }
          const p = total ? Math.round((done / total) * 100) : 100;
          $("bar-fill").style.width = p + "%";
          $("bar").setAttribute("aria-valuenow", p);
          const elapsed = (Date.now() - stageStart) / 1000;
          const left = done ? (elapsed / done) * (total - done) : 0;
          $("run-detail").textContent =
            fmt(done) + " of " + fmt(total) + " addresses" + (done < total && left > 5 ? " · about " + formatDuration(left) + " left in this step" : "");
        },
      });
      ["census", "retry", "external"].forEach((s) => setStage(s, seen.has(s) ? "done" : "skipped"));
      renderLive();
      showResults();
      goTo(4);
      const t = A2T.tally(state.records);
      toast("Done in " + formatDuration((Date.now() - startedAt) / 1000) + ": " + fmt(t.matched) + " of " + fmt(t.total) + " matched.", "ok", 6000);
    } catch (err) {
      if (err instanceof A2TGeocoder.Cancelled) {
        goTo(2);
        toast("Run cancelled. Nothing was saved.", "info");
      } else {
        $("run-detail").textContent = "Something went wrong: " + err.message + ". Please try again.";
        toast("The run stopped because of an error.", "error");
      }
    } finally {
      clearInterval(timer);
      window.removeEventListener("beforeunload", warnOnLeave);
    }
  }

  /** The ArcGIS step stopped because the token was refused. */
  function showProviderError(message) {
    $("provider-error-text").textContent =
      message + ". The rows it could not look up are marked unmatched. " +
      "Check the token with JHFRC, then use Start over, or fix and retry rows below.";
    $("provider-error").hidden = false;
    toast(message + ".", "error", 8000);
  }

  // ---------------------------------------------------------- step 4: results

  function showResults() {
    const t = A2T.tally(state.records);
    $("results-sub").textContent = state.fileName + " · " + fmt(t.total) + " records";
    const review = t.unmatched + t.rejected;
    const rate = t.total ? t.matched / t.total : 0;
    const matchedCard = kpi(t.matched, pct(t.matched, t.total), "Matched to a tract");
    const meter = el("span", { class: "kpi-meter" }, el("i"));
    matchedCard.append(meter);
    requestAnimationFrame(() => (meter.firstChild.style.width = (rate * 100).toFixed(1) + "%"));
    const reviewCard = kpi(review, review ? null : "all clear", "Need review (unmatched or rejected)");
    reviewCard.classList.add("kpi-review");
    if (review) {
      reviewCard.classList.add("has-review");
      reviewCard.setAttribute("role", "button");
      reviewCard.tabIndex = 0;
      reviewCard.title = "Show these rows in the table";
      const go = () => {
        state.view.filter = t.unmatched ? "No_Match" : "Rejected";
        state.view.page = 0;
        showResults();
        $("results").scrollIntoView({ behavior: "smooth", block: "start" });
      };
      reviewCard.addEventListener("click", go);
      reviewCard.addEventListener("keydown", (e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), go()));
    }
    $("kpis").replaceChildren(
      kpi(t.total, null, "Total records"),
      matchedCard,
      kpi(t.censusAlt + t.external, null, "Found by a backup lookup"),
      reviewCard
    );

    const counts = { Matched: t.census, Matched_Fallback: t.censusAlt, Matched_External: t.external, No_Match: t.unmatched, Rejected: t.rejected };
    const stack = $("stack");
    stack.replaceChildren(...STATUS_ORDER.map((s) => el("i", { style: "background:" + STATUS_COLORS[s], title: STATUS_LABELS[s] + ": " + fmt(counts[s]) })));
    stack.setAttribute("aria-label", STATUS_ORDER.map((s) => STATUS_LABELS[s] + " " + counts[s]).join(", "));
    requestAnimationFrame(() =>
      [...stack.children].forEach((bar, i) => (bar.style.width = (t.total ? (counts[STATUS_ORDER[i]] / t.total) * 100 : 0) + "%"))
    );
    $("legend").replaceChildren(
      ...STATUS_ORDER.filter((s) => counts[s]).map((s) =>
        el("li", null, el("i", { style: "background:" + STATUS_COLORS[s] }), STATUS_LABELS[s] + " ", el("b", { text: fmt(counts[s]) })))
    );

    $("fix-hint").hidden = !t.unmatched;
    $("fix-hint-title").textContent = fmt(t.unmatched) + (t.unmatched === 1 ? " address was" : " addresses were") + " not matched.";
    renderChips(counts, t.total);
    renderTable();
    // After the panel is shown, so the map can measure its size.
    requestAnimationFrame(() => {
      if (window.A2TMap && window.L) A2TMap.update(state.records);
    });
  }

  const REDUCE_MOTION = !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);

  /** Animate a number from 0 up to its value (instant with reduced motion). */
  function countUp(node, value) {
    if (REDUCE_MOTION || value < 4) {
      node.textContent = fmt(value);
      return;
    }
    const start = performance.now();
    const step = (now) => {
      const k = Math.min(1, (now - start) / 650);
      node.textContent = fmt(Math.round(value * (1 - Math.pow(1 - k, 3))));
      if (k < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  function kpi(value, sub, label) {
    const num = el("span", { class: "kpi-num" });
    countUp(num, value);
    return el("div", { class: "kpi" }, el("b", null, num, sub ? el("small", { text: sub }) : null), el("span", { class: "kpi-label", text: label }));
  }

  function renderChips(counts, total) {
    const chips = [["all", "All", total]].concat(
      STATUS_ORDER.filter((s) => counts[s]).map((s) => [s, STATUS_LABELS[s], counts[s]])
    );
    const tractChip = state.view.tract
      ? [el("button", {
          class: "chip tract-chip", type: "button", "aria-pressed": "true", title: "Show all tracts",
          onclick: () => clearTractFilter(),
        }, "Tract " + state.view.tractName.replace(/^Census Tract /, ""), icon("x"))]
      : [];
    $("chips").replaceChildren(
      ...tractChip,
      ...chips.map(([key, label, n]) =>
        el("button", {
          class: "chip", type: "button", "aria-pressed": String(state.view.filter === key),
          onclick: () => {
            state.view.filter = key;
            state.view.page = 0;
            renderChips(counts, total);
            renderTable();
          },
        }, label, el("span", { class: "n", text: fmt(n) })))
    );
  }

  const COLUMNS = [
    ["id", "ID"],
    ["address", "Address"],
    ["status", "Status"],
    ["tract", "Census tract"],
    ["reason", "Note"],
  ];

  function sortValue(rec, key) {
    if (key === "id") return rec.id;
    if (key === "address") return state.edits.get(rec) ?? rec.address;
    if (key === "status") return STATUS_ORDER.indexOf(rec.match_status);
    if (key === "tract") return rec.census_tract_geoid;
    return rec.error_reason;
  }

  function visibleRecords() {
    const { filter, q, sortKey, sortDir } = state.view;
    const needle = q.trim().toLowerCase();
    let list = state.records.filter((r) => filter === "all" || r.match_status === filter);
    if (state.view.tract) list = list.filter((r) => r.census_tract_geoid === state.view.tract);
    if (needle) {
      list = list.filter((r) => [r.id, state.edits.get(r) ?? r.address, r.census_tract_geoid].join(" ").toLowerCase().includes(needle));
    }
    if (sortKey) {
      list = list.slice().sort((a, b) => {
        const x = sortValue(a, sortKey);
        const y = sortValue(b, sortKey);
        return (typeof x === "number" ? x - y : String(x).localeCompare(String(y), undefined, { numeric: true })) * sortDir;
      });
    }
    return list;
  }

  function renderTable() {
    const list = visibleRecords();
    const pages = Math.max(1, Math.ceil(list.length / PAGE_SIZE));
    state.view.page = Math.min(state.view.page, pages - 1);
    const start = state.view.page * PAGE_SIZE;
    const pageRows = list.slice(start, start + PAGE_SIZE);

    const head = el("tr");
    for (const [key, label] of COLUMNS) {
      const sorted = state.view.sortKey === key;
      const th = el("th", {
        class: "sortable", tabindex: "0", scope: "col",
        "aria-sort": sorted ? (state.view.sortDir > 0 ? "ascending" : "descending") : null,
        onclick: () => sortBy(key),
        onkeydown: (e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), sortBy(key)),
      }, label, icon("sort"));
      head.append(th);
    }

    const body = el("tbody");
    if (!pageRows.length) {
      body.append(el("tr", null, el("td", { class: "empty", colspan: String(COLUMNS.length), text: "No records match this filter." })));
    }
    for (const rec of pageRows) {
      const tr = el("tr", { class: [state.edits.has(rec) ? "edited" : "", rec.match_status === "No_Match" ? "needs-review" : ""].join(" ").trim() || null });
      tr.dataset.id = rec.id;
      tr.append(el("td", { class: "mono", text: rec.id }));
      const addrCell = el("td");
      if (rec.match_status === "No_Match") {
        const input = el("input", {
          class: "cell-input", type: "text", value: state.edits.get(rec) ?? rec.address,
          "aria-label": "Address for ID " + rec.id, spellcheck: "false",
        });
        input.addEventListener("input", () => {
          const text = input.value;
          if (text.trim() === rec.address.trim()) state.edits.delete(rec);
          else state.edits.set(rec, text);
          input.classList.remove("invalid");
          tr.classList.toggle("edited", state.edits.has(rec));
          syncActionBar();
        });
        input.addEventListener("keydown", (e) => e.key === "Enter" && state.edits.size && retryEdits());
        addrCell.append(input);
      } else {
        addrCell.append(rec.address);
        if (rec.corrected_address) addrCell.append(" ", el("span", { class: "status Matched_External", text: "edited" }));
      }
      tr.append(addrCell, el("td", null, statusChip(rec.match_status)), el("td", { class: "mono", text: rec.census_tract_geoid || "" }),
        el("td", { class: "reason", text: rec.error_reason || "" }));
      body.append(tr);
    }
    $("results").replaceChildren(el("thead", null, head), body);

    $("pager").replaceChildren(
      el("span", { text: list.length ? "Showing " + fmt(start + 1) + " to " + fmt(start + pageRows.length) + " of " + fmt(list.length) : "" }),
      el("span", { class: "btns" },
        el("button", { class: "btn ghost sm", type: "button", disabled: state.view.page === 0, onclick: () => setPage(-1) }, icon("left"), "Previous"),
        el("button", { class: "btn ghost sm", type: "button", disabled: state.view.page >= pages - 1, onclick: () => setPage(1) }, "Next", icon("right")))
    );
  }

  /** The map picked a tract: show only its rows. */
  function filterToTract(geoid, name) {
    state.view.tract = geoid;
    state.view.tractName = name || geoid;
    state.view.filter = "all";
    state.view.page = 0;
    showResults();
  }

  function clearTractFilter() {
    state.view.tract = "";
    state.view.tractName = "";
    state.view.page = 0;
    if (window.A2TMap) A2TMap.clearSelection();
    showResults();
  }

  function sortBy(key) {
    if (state.view.sortKey === key) state.view.sortDir *= -1;
    else {
      state.view.sortKey = key;
      state.view.sortDir = 1;
    }
    renderTable();
  }

  function setPage(delta) {
    state.view.page += delta;
    renderTable();
    $("results").scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  function syncActionBar() {
    const n = state.edits.size;
    $("action-bar").hidden = !(n && currentStep === 4);
    $("action-text").textContent = n + (n === 1 ? " address edited" : " addresses edited");
    $("retry-label").textContent = "Retry " + n + (n === 1 ? " row" : " rows");
  }

  /** Re-run only the rows whose address the user corrected. */
  async function retryEdits() {
    const edits = [...state.edits];
    // Every edit goes through the same privacy screen as the file itself.
    for (const [rec, text] of edits) {
      const clean = A2T.cleanCell(text);
      const found = A2T.scanCellsForPhi([{ a: clean }], ["a"]);
      const problem = A2T.isBlank(clean) ? "is empty" : found.length ? "looks like it contains " + found[0].kind : "";
      if (problem) {
        const input = document.querySelector('tr[data-id="' + CSS.escape(rec.id) + '"] .cell-input');
        if (input) {
          input.classList.add("invalid");
          input.focus();
        }
        toast("The address for ID " + rec.id + " " + problem + ". Nothing was sent.", "error", 6000);
        return;
      }
    }
    const subset = edits.map(([rec, text]) => {
      rec.address = rec.corrected_address = A2T.cleanCell(text);
      rec.match_status = "No_Match";
      rec.error_reason = "Address could not be geocoded";
      rec.cleaned_address = "";
      rec.census_tract_geoid = "";
      return rec;
    });
    const btn = $("retry-edits");
    btn.classList.add("busy");
    try {
      await A2TGeocoder.run(subset, {
        transport,
        options: { arcgisToken: arcgisToken() },
        onProviderError: showProviderError,
        onProgress: ({ done, total }) => ($("retry-label").textContent = "Retrying " + done + " of " + total),
      });
    } catch (err) {
      toast("Retry stopped: " + err.message, "error");
    } finally {
      btn.classList.remove("busy");
    }
    state.edits.clear();
    const fixed = subset.filter((r) => r.match_status.startsWith("Matched")).length;
    showResults();
    syncActionBar();
    subset.forEach((r) => document.querySelector('tr[data-id="' + CSS.escape(r.id) + '"]')?.classList.add("flash"));
    toast(fixed + " of " + subset.length + (subset.length === 1 ? " corrected address" : " corrected addresses") + " now matched.", fixed ? "ok" : "info", 6000);
  }

  // ---------------------------------------------------------------- downloads

  /** Today's date in the user's time zone as YYYY-MM-DD. */
  function localDate() {
    const d = new Date();
    return [d.getFullYear(), String(d.getMonth() + 1).padStart(2, "0"), String(d.getDate()).padStart(2, "0")].join("-");
  }

  /** Write rows to .xlsx with the given columns stored as Excel text. */
  function writeXlsx(rows, header, textCols, fileName) {
    const ws = XLSX.utils.json_to_sheet(rows, { header });
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
    const widest = (h) => rows.reduce((w, r) => Math.max(w, String(r[h] ?? "").length), String(h).length);
    ws["!cols"] = header.map((h) => ({ wch: Math.max(12, Math.min(60, widest(h) + 2)) }));
    const wb = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(wb, ws, "Sheet1");
    XLSX.writeFile(wb, fileName);
  }

  function download(rows, fileName, header) {
    writeXlsx(rows, header || Object.keys(rows[0] || {}), [state.cols && state.cols.id, "census_tract_geoid"].filter(Boolean), fileName);
    toast("Downloaded " + fileName, "ok");
  }

  function downloadSummary() {
    const rows = A2TGeocoder.processingSummary(state.records, {
      processedOn: state.processedOn,
      arcgis: Boolean(arcgisToken()),
      appVersion: APP_VERSION,
    });
    download(rows, "processing_summary.xlsx", ["item", "value"]);
  }

  function downloadTracts() {
    const min = Number($("small-cells").value) || 0;
    const rows = A2TGeocoder.tractSummary(state.records, min);
    download(rows, "tract_summary.xlsx", ["census_tract_geoid", "records", "geography_vintage"]);
  }

  /** Blank input file with the column names the page guesses automatically. */
  const TEMPLATE_HEADER = ["client_id", "street", "city", "state", "zip"];
  function downloadTemplate(kind) {
    if (kind === "csv") {
      const blob = new Blob([TEMPLATE_HEADER.join(",") + "\r\n"], { type: "text/csv" });
      const url = URL.createObjectURL(blob);
      el("a", { href: url, download: "address2tract_template.csv" }).click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } else {
      writeXlsx([], TEMPLATE_HEADER, TEMPLATE_HEADER, "address2tract_template.xlsx");
    }
  }

  // ------------------------------------------------------------------- wiring

  document.querySelectorAll("[data-max-rows]").forEach((n) => (n.textContent = fmt(MAX_ROWS)));

  $("file").addEventListener("change", (e) => {
    const f = e.target.files[0];
    // Clear the input so choosing the same (now fixed) file again still fires.
    e.target.value = "";
    if (f) openFile(f);
  });
  $("sheet").addEventListener("change", (e) => {
    const { columns, rows } = readSheet(state.workbook.Sheets[e.target.value]);
    acceptRows(state.fileName, columns, rows);
  });
  $("file-remove").addEventListener("click", () => {
    resetUpload();
    toast("File removed.", "info", 2500);
  });
  $("split-btn").addEventListener("click", splitFile);
  document.querySelectorAll('[data-action="sample"]').forEach((b) => b.addEventListener("click", loadSample));
  $("to-columns").addEventListener("click", () => {
    fillColumnPickers();
    goTo(2);
  });
  document.querySelectorAll("[data-back]").forEach((b) => b.addEventListener("click", () => goTo(Number(b.dataset.back))));
  document.querySelectorAll('input[name="mode"]').forEach((r) => r.addEventListener("change", syncMode));
  Object.values(PICKERS).forEach((id) => $(id).addEventListener("change", renderMapping));
  $("start").addEventListener("click", start);
  initArcgis();
  $("cancel").addEventListener("click", () => state.controller && state.controller.abort());
  $("dl-full").addEventListener("click", () => download(A2TGeocoder.resultRows(state.records, state.cols, state.processedOn), "results.xlsx"));
  $("dl-summary").addEventListener("click", downloadSummary);
  $("dl-tracts").addEventListener("click", downloadTracts);
  document.querySelectorAll("[data-template]").forEach((b) => b.addEventListener("click", () => downloadTemplate(b.dataset.template)));
  $("ack").addEventListener("change", () => {
    if ($("ack").checked) $("column-error").hidden = true;
  });
  $("map-refit").addEventListener("click", () => window.A2TMap && A2TMap.refit());
  if (window.A2TMap) A2TMap.onSelect(filterToTract);
  $("map-streets").addEventListener("change", (e) => window.A2TMap && A2TMap.setStreetMap(e.target.checked));
  $("show-unmatched").addEventListener("click", () => {
    state.view.filter = "No_Match";
    state.view.page = 0;
    showResults();
    document.querySelector("#results .cell-input")?.focus();
  });
  let searchTimer;
  $("search").addEventListener("input", (e) => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      state.view.q = e.target.value;
      state.view.page = 0;
      renderTable();
    }, 120);
  });
  $("retry-edits").addEventListener("click", retryEdits);
  $("discard-edits").addEventListener("click", () => {
    state.edits.clear();
    renderTable();
    syncActionBar();
  });
  $("again").addEventListener("click", () => {
    resetUpload();
    syncActionBar();
    goTo(1);
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
    const f = e.dataTransfer.files[0];
    if (f) openFile(f);
  });
})();
