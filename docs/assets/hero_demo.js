/*
 * address2tract web: the hero demo. Example addresses (public places in
 * Hamilton County, Tennessee, the same ones as the sample file) type out one at
 * a time, a pin drops into the tract the Census geocoder returned for each, and
 * the tracts fill in like a small heatmap. Purely illustrative: nothing is
 * looked up, and no network request is made.
 *
 * Plays only while the hero is on screen and the tab is visible. With
 * "reduce motion" set, it shows the finished map instead of animating.
 */
(function () {
  "use strict";

  const data = window.A2THero;
  const svg = document.getElementById("demo-map");
  if (!data || !svg) return;

  // [address, tract GEOID, tract name] from the sample file's Census results.
  const DEMO = [
    ["615 McCallie Ave, Chattanooga, TN 37403", "47065012400", "Census Tract 124"],
    ["1 Broad St, Chattanooga, TN 37402", "47065003100", "Census Tract 31"],
    ["301 N Holtzclaw Ave, Chattanooga, TN 37404", "47065001100", "Census Tract 11"],
    ["1001 Broad St, Chattanooga, TN 37402", "47065003100", "Census Tract 31"],
    ["2100 Hamilton Place Blvd, Chattanooga, TN 37421", "47065011446", "Census Tract 114.46"],
    ["1111 Ridgeway Ave, Signal Mountain, TN 37377", "47065011100", "Census Tract 111"],
    ["625 Georgia Ave, Chattanooga, TN 37402", "47065003100", "Census Tract 31"],
    ["3117 Dayton Blvd, Red Bank, TN 37415", "47065010800", "Census Tract 108"],
    ["4910 Swinyar Dr, Collegedale, TN 37315", "47065011311", "Census Tract 113.11"],
    ["9835 Dayton Pike, Soddy Daisy, TN 37379", "47065010309", "Census Tract 103.09"],
    ["1517 Tombras Ave, East Ridge, TN 37412", "47065011800", "Census Tract 118"],
    ["975 E 3rd St, Chattanooga, TN 37403", "47065000400", "Census Tract 4"],
  ];

  const NS = "http://www.w3.org/2000/svg";
  const reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const addrEl = document.getElementById("demo-addr");
  const tractEl = document.getElementById("demo-tract");
  const nameEl = document.getElementById("demo-name");
  const resultEl = document.getElementById("demo-result");

  svg.setAttribute("viewBox", "0 0 " + data.w + " " + data.h);
  const tracts = new Map();
  const base = document.createElementNS(NS, "g");
  for (const t of data.tracts) {
    const p = document.createElementNS(NS, "path");
    p.setAttribute("d", t.d);
    p.setAttribute("class", "dt");
    base.appendChild(p);
    tracts.set(t.g, { path: p, x: t.x, y: t.y });
  }
  svg.appendChild(base);

  const pin = document.createElementNS(NS, "g");
  pin.setAttribute("class", "demo-pin");
  const pinInner = document.createElementNS(NS, "g");
  pinInner.setAttribute("class", "demo-pin-body");
  const halo = document.createElementNS(NS, "circle");
  halo.setAttribute("r", "13");
  halo.setAttribute("class", "demo-halo");
  const drop = document.createElementNS(NS, "path");
  drop.setAttribute("d", "M0 0c-1.2-3.5-7-8.1-7-13a7 7 0 0 1 14 0c0 4.9-5.8 9.5-7 13z");
  const dot = document.createElementNS(NS, "circle");
  dot.setAttribute("cy", "-13");
  dot.setAttribute("r", "2.6");
  dot.setAttribute("class", "demo-dot");
  pinInner.append(drop, dot);
  pin.append(halo, pinInner);
  svg.appendChild(pin);

  const counts = new Map();
  function heat(g) {
    const n = (counts.get(g) || 0) + 1;
    counts.set(g, n);
    const t = tracts.get(g);
    t.path.setAttribute("class", "dt h" + Math.min(n, 3));
  }

  function placePin(t) {
    pin.style.transform = "translate(" + t.x + "px," + t.y + "px)";
    pin.classList.remove("show");
    void pin.getBoundingClientRect(); // restart the drop animation
    pin.classList.add("show");
  }

  function showResult(item) {
    tractEl.textContent = item[1];
    nameEl.textContent = item[2] + ", Hamilton County";
    resultEl.classList.remove("in");
    void resultEl.offsetWidth;
    resultEl.classList.add("in");
  }

  if (reduce) {
    DEMO.forEach((item) => heat(item[1]));
    const last = DEMO[0];
    addrEl.textContent = last[0];
    placePin(tracts.get(last[1]));
    showResult(last);
    return;
  }

  // Play only while visible.
  let visible = true;
  let wake = null;
  const resume = () => {
    if (visible && !document.hidden && wake) {
      const w = wake;
      wake = null;
      w();
    }
  };
  if ("IntersectionObserver" in window) {
    new IntersectionObserver((entries) => {
      visible = entries[0].isIntersecting;
      resume();
    }).observe(svg);
  }
  document.addEventListener("visibilitychange", resume);

  function sleep(ms) {
    return new Promise((resolve) => {
      setTimeout(function tick() {
        if (visible && !document.hidden) resolve();
        else wake = resolve;
      }, ms);
    });
  }

  async function type(text) {
    addrEl.textContent = "";
    for (let i = 1; i <= text.length; i++) {
      addrEl.textContent = text.slice(0, i);
      await sleep(i < 4 ? 70 : 28);
    }
  }

  async function loop() {
    for (;;) {
      for (const item of DEMO) {
        resultEl.classList.remove("in");
        await type(item[0]);
        await sleep(260);
        const t = tracts.get(item[1]);
        placePin(t);
        await sleep(220);
        heat(item[1]);
        showResult(item);
        await sleep(1500);
      }
      await sleep(2200);
      counts.clear();
      tracts.forEach((t) => t.path.setAttribute("class", "dt"));
      pin.classList.remove("show");
      await sleep(600);
    }
  }

  loop();
})();
