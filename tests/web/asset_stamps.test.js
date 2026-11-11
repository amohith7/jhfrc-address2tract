// Fails when a docs/ asset changed without re-running the stamp script, so a
// release can never ship with a cache-busting stamp that points at old code.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { stamp } = require("../../scripts/stamp_web_assets.js");

test("docs/index.html asset stamps match the current files", () => {
  const html = fs.readFileSync(path.join(__dirname, "..", "..", "docs", "index.html"), "utf8");
  assert.equal(stamp(html), html, "Run: node scripts/stamp_web_assets.js");
  assert.match(html, /assets\/app\.js\?v=[0-9a-f]{8}"/);
});
