import assert from "node:assert/strict";
import test from "node:test";
import {filterRegions, regionAction} from "../src/retium/static/routing-catalogue.js";

test("region search is local, accent insensitive, and matches all words", () => {
  const regions = [{id: "nl", name: "Noord-Brabant", country: "Netherlands"}, {id: "es", name: "Málaga", country: "Spain"}];
  assert.deepEqual(filterRegions(regions, "nether brabant").map(r => r.id), ["nl"]);
  assert.deepEqual(filterRegions(regions, "malaga").map(r => r.id), ["es"]);
  assert.equal(filterRegions(regions, "Netherlands Spain").length, 0);
});

test("actions distinguish download, update, installed, cancel, retry and commit", () => {
  const region = {id: "a", state: "available"};
  assert.equal(regionAction(region).label, "DOWNLOAD");
  assert.equal(regionAction({...region, state: "unpublished"}).label, "PENDING RELEASE");
  assert.equal(regionAction({...region, state: "unpublished"}).disabled, true);
  assert.equal(regionAction({...region, state: "installed"}).disabled, true);
  assert.equal(regionAction({...region, state: "update_available"}).label, "UPDATE");
  assert.equal(regionAction(region, {id: "a", status: "downloading"}).cancel, true);
  assert.equal(regionAction(region, {id: "a", status: "error"}).label, "RETRY");
  assert.equal(regionAction(region, {id: "a", status: "installing"}).disabled, true);
  assert.equal(regionAction(region, {id: "b", status: "downloading"}).disabled, true);
});
