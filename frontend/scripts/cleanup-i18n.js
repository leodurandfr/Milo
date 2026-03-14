#!/usr/bin/env node

/**
 * i18n cleanup script
 *
 * Scans the frontend source code for t() and $t() calls,
 * compares with locale files, and removes unused keys.
 *
 * Usage:
 *   node scripts/cleanup-i18n.js              # Dry run (report only)
 *   node scripts/cleanup-i18n.js --apply      # Actually remove unused keys
 */

import { readFileSync, readdirSync, writeFileSync } from "fs";
import { join } from "path";

const SRC_DIR = new URL("../src", import.meta.url).pathname;
const LOCALES_DIR = join(SRC_DIR, "locales");
const apply = process.argv.includes("--apply");

// Flatten nested object to dot-notation keys
function flattenKeys(obj, prefix = "") {
  const keys = {};
  for (const [key, value] of Object.entries(obj)) {
    const fullKey = prefix ? `${prefix}.${key}` : key;
    if (typeof value === "object" && value !== null && !Array.isArray(value)) {
      Object.assign(keys, flattenKeys(value, fullKey));
    } else {
      keys[fullKey] = value;
    }
  }
  return keys;
}

// Unflatten dot-notation keys back to nested object
function unflattenKeys(flat) {
  const result = {};
  for (const [key, value] of Object.entries(flat)) {
    const parts = key.split(".");
    let current = result;
    for (let i = 0; i < parts.length - 1; i++) {
      if (!(parts[i] in current)) current[parts[i]] = {};
      current = current[parts[i]];
    }
    current[parts[parts.length - 1]] = value;
  }
  return result;
}

// Recursively find all .vue and .js files
function findSourceFiles(dir) {
  const files = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const fullPath = join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === "node_modules" || entry.name === "locales") continue;
      files.push(...findSourceFiles(fullPath));
    } else if (entry.name.endsWith(".vue") || entry.name.endsWith(".js")) {
      files.push(fullPath);
    }
  }
  return files;
}

// Extract static t() keys from source code
function extractStaticKeys(content) {
  const keys = new Set();
  // Match t('key') and $t('key') with single or double quotes
  const regex = /\$?t\(\s*['"]([^'"]+)['"]\s*[,)]/g;
  let match;
  while ((match = regex.exec(content)) !== null) {
    keys.add(match[1]);
  }
  return keys;
}

// Extract dynamic key patterns like t(`prefix.${var}`)
// Also catches indirect patterns: const key = `prefix.${var}`; t(key)
function extractDynamicPrefixes(content) {
  const prefixes = new Set();

  // Direct: t(`prefix.${...}`) or $t(`prefix.${...}`)
  const directRegex = /(?<![.\w])(?:\$t|t)\(\s*`([^`]+)\$\{/g;
  let match;
  while ((match = directRegex.exec(content)) !== null) {
    const prefix = match[1];
    if (prefix.startsWith("/") || prefix.startsWith("http")) continue;
    prefixes.add(prefix);
  }

  // Indirect: const/let someVar = `prefix.${...}`  (where prefix looks like i18n key)
  const indirectRegex = /(?:const|let)\s+\w+\s*=\s*`([a-z][a-zA-Z_]*\.[^`]*)\$\{/g;
  while ((match = indirectRegex.exec(content)) !== null) {
    const prefix = match[1];
    if (prefix.startsWith("/") || prefix.startsWith("http")) continue;
    // Only add if the file also contains a t() call (likely uses this variable)
    if (/(?<![.\w])(?:\$t|t)\(/.test(content)) {
      prefixes.add(prefix);
    }
  }

  return prefixes;
}

// Extract indirect key references (string literals in objects later passed to t())
// e.g. { amplifier: 'setup.audio.amplifiers' } where the value is later used as t(map[key])
function extractIndirectKeys(content) {
  const keys = new Set();
  // Only extract from files that actually use t()
  if (!/(?<![.\w])(?:\$t|t)\(/.test(content)) return keys;
  // Match any string value that looks like a dotted i18n key (2+ segments, lowercase start)
  const regex = /['"]([a-z][a-zA-Z_]+\.[a-zA-Z_]+(?:\.[a-zA-Z_]+)*)['"]/g;
  let match;
  while ((match = regex.exec(content)) !== null) {
    const key = match[1];
    // Skip URL-like, file-like, and CSS-like patterns
    if (key.includes("http") || key.includes("/") || key.endsWith(".vue") ||
        key.endsWith(".js") || key.endsWith(".json") || key.endsWith(".css") ||
        key.startsWith("@") || key.startsWith("v-")) continue;
    keys.add(key);
  }
  return keys;
}

// Scan all source files
const sourceFiles = findSourceFiles(SRC_DIR);
const usedKeys = new Set();
const dynamicPrefixes = new Set();

for (const file of sourceFiles) {
  const content = readFileSync(file, "utf-8");
  for (const key of extractStaticKeys(content)) {
    usedKeys.add(key);
  }
  for (const prefix of extractDynamicPrefixes(content)) {
    dynamicPrefixes.add(prefix);
  }
  for (const key of extractIndirectKeys(content)) {
    usedKeys.add(key);
  }
}

console.log(`\nScanned ${sourceFiles.length} source files`);
console.log(`Found ${usedKeys.size} static keys used in code`);
console.log(`Found ${dynamicPrefixes.size} dynamic prefixes: ${[...dynamicPrefixes].join(", ")}`);

// Check if a key matches any dynamic prefix
function isDynamicKey(key) {
  for (const prefix of dynamicPrefixes) {
    if (key.startsWith(prefix)) return true;
  }
  return false;
}

// Process each locale file
const localeFiles = readdirSync(LOCALES_DIR).filter((f) => f.endsWith(".json"));
let totalRemoved = 0;

for (const file of localeFiles.sort()) {
  const filePath = join(LOCALES_DIR, file);
  const content = JSON.parse(readFileSync(filePath, "utf-8"));
  const flat = flattenKeys(content);
  const allKeys = Object.keys(flat);

  const unusedKeys = allKeys.filter(
    (k) => !usedKeys.has(k) && !isDynamicKey(k)
  );

  if (unusedKeys.length === 0) {
    console.log(`\n  ✓ ${file} — no unused keys (${allKeys.length} keys)`);
    continue;
  }

  console.log(
    `\n  ✗ ${file} — ${unusedKeys.length} unused keys out of ${allKeys.length}`
  );

  for (const key of unusedKeys.sort()) {
    console.log(`    - ${key}`);
  }

  if (apply) {
    for (const key of unusedKeys) {
      delete flat[key];
    }
    const cleaned = unflattenKeys(flat);
    writeFileSync(filePath, JSON.stringify(cleaned, null, 2) + "\n");
    console.log(
      `    → Removed ${unusedKeys.length} keys, ${Object.keys(flat).length} remaining`
    );
  }

  totalRemoved += unusedKeys.length;
}

if (totalRemoved > 0 && !apply) {
  console.log(
    `\n${totalRemoved} unused keys found across all locales. Run with --apply to remove them.\n`
  );
} else if (totalRemoved > 0) {
  console.log(`\n${totalRemoved} unused keys removed across all locales.\n`);
} else {
  console.log("\nAll keys are used.\n");
}
