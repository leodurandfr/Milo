#!/usr/bin/env node

/**
 * i18n completeness and usage checker
 *
 * Compares all locale files against a reference locale and optionally
 * scans source code to detect unused translation keys.
 *
 * Usage:
 *   node scripts/check-i18n.js                          # Check inter-locale consistency
 *   node scripts/check-i18n.js --fix                    # Show missing keys with reference values
 *   node scripts/check-i18n.js --reference english.json # Use a different reference
 *   node scripts/check-i18n.js --unused                 # Also detect unused keys in code
 */

import { readFileSync, readdirSync } from "fs";
import { join, basename } from "path";

const SRC_DIR = new URL("../src", import.meta.url).pathname;
const LOCALES_DIR = join(SRC_DIR, "locales");

// Parse arguments
const args = process.argv.slice(2);
const showFix = args.includes("--fix");
const checkUnused = args.includes("--unused");
const refIndex = args.indexOf("--reference");
const referenceFile = refIndex !== -1 ? args[refIndex + 1] : "french.json";

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

// Recursively find source files
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

// Scan source code for used i18n keys
function scanUsedKeys() {
  const usedKeys = new Set();
  const dynamicPrefixes = new Set();
  const sourceFiles = findSourceFiles(SRC_DIR);

  for (const file of sourceFiles) {
    const content = readFileSync(file, "utf-8");

    // Static t('key') and $t('key')
    const staticRegex = /\$?t\(\s*['"]([^'"]+)['"]\s*[,)]/g;
    let match;
    while ((match = staticRegex.exec(content)) !== null) {
      usedKeys.add(match[1]);
    }

    // Direct dynamic: t(`prefix.${var}`)
    const directDynRegex = /(?<![.\w])(?:\$t|t)\(\s*`([^`]+)\$\{/g;
    while ((match = directDynRegex.exec(content)) !== null) {
      const prefix = match[1];
      if (!prefix.startsWith("/") && !prefix.startsWith("http")) {
        dynamicPrefixes.add(prefix);
      }
    }

    // Indirect dynamic: const key = `prefix.${var}` in files that use t()
    if (/(?<![.\w])(?:\$t|t)\(/.test(content)) {
      const indirectRegex = /(?:const|let)\s+\w+\s*=\s*`([a-z][a-zA-Z_]*\.[^`]*)\$\{/g;
      while ((match = indirectRegex.exec(content)) !== null) {
        const prefix = match[1];
        if (!prefix.startsWith("/") && !prefix.startsWith("http")) {
          dynamicPrefixes.add(prefix);
        }
      }

      // Indirect string literals that look like i18n keys
      const indirectStrRegex = /['"]([a-z][a-zA-Z_]+\.[a-zA-Z_]+(?:\.[a-zA-Z_]+)*)['"]/g;
      while ((match = indirectStrRegex.exec(content)) !== null) {
        const key = match[1];
        if (!key.includes("http") && !key.includes("/") &&
            !key.endsWith(".vue") && !key.endsWith(".js") &&
            !key.endsWith(".json") && !key.endsWith(".css") &&
            !key.startsWith("@") && !key.startsWith("v-")) {
          usedKeys.add(key);
        }
      }
    }
  }

  return { usedKeys, dynamicPrefixes, fileCount: sourceFiles.length };
}

// Load and flatten all locale files
const files = readdirSync(LOCALES_DIR).filter((f) => f.endsWith(".json"));
const locales = {};

for (const file of files) {
  const content = JSON.parse(readFileSync(join(LOCALES_DIR, file), "utf-8"));
  locales[file] = flattenKeys(content);
}

// Reference locale
const reference = locales[referenceFile];
if (!reference) {
  console.error(`Reference file "${referenceFile}" not found in ${LOCALES_DIR}`);
  process.exit(1);
}

const referenceKeys = new Set(Object.keys(reference));
let hasErrors = false;

// === Part 1: Inter-locale consistency ===
console.log(`\n=== Locale consistency (reference: ${referenceFile}, ${referenceKeys.size} keys) ===\n`);

for (const file of files.sort()) {
  if (file === referenceFile) continue;

  const locale = locales[file];
  const localeKeys = new Set(Object.keys(locale));
  const name = basename(file, ".json");

  const missing = [...referenceKeys].filter((k) => !localeKeys.has(k));
  const extra = [...localeKeys].filter((k) => !referenceKeys.has(k));

  if (missing.length === 0 && extra.length === 0) {
    console.log(`  ✓ ${name} — complete (${localeKeys.size} keys)`);
    continue;
  }

  hasErrors = true;
  console.log(`  ✗ ${name} — ${localeKeys.size} keys`);

  if (missing.length > 0) {
    console.log(`    Missing ${missing.length} keys:`);
    for (const key of missing.sort()) {
      if (showFix) {
        console.log(`      - ${key}: ${JSON.stringify(reference[key])}`);
      } else {
        console.log(`      - ${key}`);
      }
    }
  }

  if (extra.length > 0) {
    console.log(`    Extra ${extra.length} keys (not in reference):`);
    for (const key of extra.sort()) {
      console.log(`      + ${key}`);
    }
  }

  console.log();
}

// === Part 2: Unused key detection ===
if (checkUnused) {
  const { usedKeys, dynamicPrefixes, fileCount } = scanUsedKeys();

  function isDynamicKey(key) {
    for (const prefix of dynamicPrefixes) {
      if (key.startsWith(prefix)) return true;
    }
    return false;
  }

  const unusedKeys = [...referenceKeys].filter(
    (k) => !usedKeys.has(k) && !isDynamicKey(k)
  );

  console.log(`\n=== Unused keys (scanned ${fileCount} source files) ===\n`);
  console.log(`  Dynamic prefixes: ${[...dynamicPrefixes].join(", ") || "none"}`);

  if (unusedKeys.length === 0) {
    console.log("  ✓ All keys are used in code\n");
  } else {
    hasErrors = true;
    console.log(`  ✗ ${unusedKeys.length} unused keys in ${referenceFile}:\n`);
    for (const key of unusedKeys.sort()) {
      console.log(`    - ${key}`);
    }
    console.log(`\n  Run 'npm run i18n:cleanup' to remove them.\n`);
  }
}

if (hasErrors) {
  process.exit(1);
} else {
  console.log("\nAll checks passed.\n");
}
