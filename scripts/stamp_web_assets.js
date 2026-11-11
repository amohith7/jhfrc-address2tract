#!/usr/bin/env node
/*
 * Stamp each local script/stylesheet link in docs/index.html with a short hash
 * of the file's contents, e.g. assets/app.js?v=1a2b3c4d.
 *
 * GitHub Pages lets browsers reuse a file for 10 minutes, so without this a
 * partner can keep running the previous release after an update. A changed
 * file gets a new hash, which the browser treats as a new URL.
 *
 *   node scripts/stamp_web_assets.js           rewrite docs/index.html
 *   node scripts/stamp_web_assets.js --check   exit 1 if any stamp is stale
 */
"use strict";

const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");

const DOCS = path.join(__dirname, "..", "docs");
const INDEX = path.join(DOCS, "index.html");
// Local assets/... references in src= or href=, with or without an old ?v=.
const ASSET_REF = /((?:src|href)=")(assets\/[^"?]+\.(?:js|css))(?:\?v=[0-9a-f]*)?(")/g;

function hashOf(rel) {
  const data = fs.readFileSync(path.join(DOCS, rel));
  return crypto.createHash("sha256").update(data).digest("hex").slice(0, 8);
}

function stamp(html) {
  return html.replace(ASSET_REF, (_, pre, rel, post) => `${pre}${rel}?v=${hashOf(rel)}${post}`);
}

function main() {
  const html = fs.readFileSync(INDEX, "utf8");
  const stamped = stamp(html);
  if (process.argv.includes("--check")) {
    if (stamped !== html) {
      console.error("docs/index.html asset stamps are stale. Run: node scripts/stamp_web_assets.js");
      process.exit(1);
    }
    console.log("docs/index.html asset stamps are current.");
    return;
  }
  fs.writeFileSync(INDEX, stamped);
  console.log("Stamped docs/index.html.");
}

if (require.main === module) main();
module.exports = { stamp };
