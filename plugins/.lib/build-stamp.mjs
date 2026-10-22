import { createHash } from "node:crypto";
import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const RUNTIME_FILE = /(?:\.mjs|\.json)$/;
const TEST_FILE = /(?:^test(?:-|\.)|\.test\.mjs$)/;
const GENERATED_DATA = /\.generated\.json$/;
const LOCATION_URL = new URL("./build-stamp-location.json", import.meta.url);
const LOCATION_BYTES = readFileSync(LOCATION_URL);
const LOCATION = JSON.parse(LOCATION_BYTES.toString("utf8"));

function assertConfiguredLocation() {
  const self = fileURLToPath(import.meta.url);
  const pluginRoot = path.dirname(path.dirname(self));
  const configured = path.resolve(pluginRoot, LOCATION.helper);
  if (configured !== self) {
    throw new Error(`build-stamp helper location differs from ${fileURLToPath(LOCATION_URL)}`);
  }
  return self;
}

export function pluginBuildStamp(importMetaUrl) {
  const directory = path.dirname(fileURLToPath(importMetaUrl));
  const helper = assertConfiguredLocation();
  const files = readdirSync(directory, { withFileTypes: true })
    .filter((entry) => entry.isFile() && RUNTIME_FILE.test(entry.name) &&
      !TEST_FILE.test(entry.name) && !GENERATED_DATA.test(entry.name))
    .map((entry) => entry.name)
    .sort();
  const hash = createHash("sha256");
  // The common stamper is runtime code too. A change to it invalidates every
  // plugin stamp even though it lives one directory above the plugin.
  hash.update(`${LOCATION.hash_label}\0`);
  hash.update(readFileSync(helper));
  hash.update(`\0${LOCATION.manifest_label}\0`);
  hash.update(LOCATION_BYTES);
  for (const name of files) {
    hash.update(`\0${name}\0`);
    hash.update(readFileSync(path.join(directory, name)));
  }
  return Object.freeze({ hash: hash.digest("hex"), files: Object.freeze(files) });
}

export function logPluginBuildStamp(api, id, stamp) {
  const log = api?.logger ?? console;
  log.warn?.(`[build-stamp] ${id} ${stamp.hash} files=${stamp.files.join(",")}`);
}
