import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";

const css = fs.readFileSync(new URL("../src/retium/static/styles.css", import.meta.url), "utf8");
test("marker close icons override general left-aligned button styling", () => {
  const rule = css.match(/\.map-marker-palette > header button,\s*\.map-report-composer > header button\s*\{([^}]+)\}/)?.[1];
  assert.ok(rule);
  for (const declaration of ["display: grid", "place-items: center", "flex-shrink: 0", "padding: 0", "text-align: center", "line-height: 1", "letter-spacing: 0"]) {
    assert.ok(rule.includes(declaration), declaration);
  }
  for (const selector of [".map-report-composer > header button", ".map-marker-palette > header button"]) {
    assert.ok(css.includes(`${selector} { width: 44px; height: 44px;`));
  }
});
