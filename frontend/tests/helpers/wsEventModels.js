// frontend/tests/helpers/wsEventModels.js
/**
 * Reads the backend's typed WS event layer — backend/core/models/ws_events.py —
 * and exposes, per `(category, type)` pair, the payload fields the backend
 * actually declares.
 *
 * Why parse the source rather than hand-write fixtures: the models ARE the wire
 * payload ("the model's own fields ARE the wire `data` payload"), so a fixture
 * invented on the frontend side would only ever assert what the frontend already
 * believes. Parsing means a backend field added, renamed or dropped shows up
 * here without anyone remembering to update a fixture.
 *
 * The parse is deliberately strict: the file follows one rigid shape (class-level
 * CATEGORY/TYPE, annotated fields, no dynamic construction). If that ever stops
 * holding, `parseWsEventModels()` throws instead of silently returning nothing —
 * a broken extractor must fail loudly, not pass on an empty surface.
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const HERE = dirname(fileURLToPath(import.meta.url));
export const WS_EVENTS_PATH = resolve(HERE, '../../../backend/core/models/ws_events.py');

const CLASS_RE = /^class\s+(\w+)\(([^)]*)\):/;
const CLASSVAR_RE = /^\s{4}([A-Z_]+)\s*=\s*(.+?)\s*(?:#.*)?$/;
const FIELD_RE = /^\s{4}([a-z_][a-z0-9_]*)\s*:\s*([^=#]+?)\s*(?:=\s*([^#]+?)\s*)?(?:#.*)?$/;

/**
 * Split the module into class blocks, tolerating docstrings and methods.
 * @returns {Map<string, {bases: string[], classVars: Record<string,string>,
 *   fields: {name: string, type: string, default: string|null}[]}>}
 */
function parseClasses(source) {
  const classes = new Map();
  let current = null;
  let inDocstring = false;
  let stopFields = false;

  for (const rawLine of source.split('\n')) {
    const classMatch = CLASS_RE.exec(rawLine);
    if (classMatch) {
      current = {
        bases: classMatch[2].split(',').map(b => b.trim()).filter(Boolean),
        classVars: {},
        fields: [],
      };
      classes.set(classMatch[1], current);
      inDocstring = false;
      stopFields = false;
      continue;
    }
    if (!current) continue;

    // Track triple-quoted docstrings so their prose is never read as fields.
    const tripleQuotes = (rawLine.match(/"""/g) || []).length;
    if (inDocstring) {
      if (tripleQuotes % 2 === 1) inDocstring = false;
      continue;
    }
    if (tripleQuotes === 1) {
      inDocstring = true;
      continue;
    }
    if (tripleQuotes >= 2) continue; // single-line docstring

    // Methods / decorators end the field block of a class.
    if (/^\s{4}(def |@)/.test(rawLine)) stopFields = true;
    if (stopFields) continue;

    const classVar = CLASSVAR_RE.exec(rawLine);
    if (classVar) {
      current.classVars[classVar[1]] = classVar[2].replace(/^["']|["']$/g, '');
      continue;
    }

    const field = FIELD_RE.exec(rawLine);
    if (field && !field[2].includes('ClassVar')) {
      current.fields.push({
        name: field[1],
        type: field[2].trim(),
        default: field[3] === undefined ? null : field[3].trim(),
      });
    }
  }

  return classes;
}

/** Walk the inheritance chain, base classes first, so overrides win. */
function resolve_(name, classes, seen = new Set()) {
  const cls = classes.get(name);
  if (!cls || seen.has(name)) return { classVars: {}, fields: [] };
  seen.add(name);

  let classVars = {};
  const fields = new Map();
  for (const base of cls.bases) {
    const inherited = resolve_(base, classes, seen);
    classVars = { ...classVars, ...inherited.classVars };
    for (const field of inherited.fields) fields.set(field.name, field);
  }
  classVars = { ...classVars, ...cls.classVars };
  for (const field of cls.fields) fields.set(field.name, field);

  return { classVars, fields: [...fields.values()] };
}

/**
 * @returns {Map<string, {className: string, fields: {name,type,default}[],
 *   excludeNone: boolean}>} keyed by `${CATEGORY}.${TYPE}`
 */
export function parseWsEventModels(source = readFileSync(WS_EVENTS_PATH, 'utf8')) {
  const classes = parseClasses(source);
  if (!classes.has('WsEvent')) {
    throw new Error(`ws_events.py parse failed: no WsEvent base found in ${WS_EVENTS_PATH}`);
  }

  const byEventKey = new Map();
  for (const name of classes.keys()) {
    const { classVars, fields } = resolve_(name, classes);
    if (!classVars.CATEGORY || !classVars.TYPE) continue; // abstract base
    byEventKey.set(`${classVars.CATEGORY}.${classVars.TYPE}`, {
      className: name,
      fields,
      excludeNone: classVars.EXCLUDE_NONE === 'True',
    });
  }

  if (byEventKey.size === 0) {
    throw new Error('ws_events.py parse produced no concrete events — the extractor is broken');
  }
  return byEventKey;
}

/** Strip Optional[...] / None-union wrappers, reporting nullability. */
function unwrapOptional(type) {
  const optional = /^Optional\[(.+)\]$/.exec(type);
  if (optional) return { inner: optional[1].trim(), nullable: true };
  return { inner: type, nullable: false };
}

const SCALAR_SAMPLES = {
  bool: true,
  int: 1,
  float: 1.5,
  str: 'sample',
};

/**
 * A wire value for a backend annotation.
 *
 * `enumOptions` comes from the Zod schema when the field is a plain `str` on the
 * backend but a closed set on the frontend (e.g. fan `mode`, position_update
 * `source`): the *shape* still comes from the backend, only the value domain is
 * borrowed — inventing 'sample' there would test the fixture, not the contract.
 */
export function sampleForType(type, enumOptions = null) {
  const { inner } = unwrapOptional(type);

  const literal = /^Literal\[(.+)\]$/.exec(inner);
  if (literal) {
    const first = literal[1].split(',')[0].trim();
    return first.replace(/^["']|["']$/g, '');
  }

  const list = /^List\[(.+)\]$/.exec(inner);
  if (list) {
    const itemType = list[1].trim();
    // Only scalars get a populated sample: a Dict[str, Any] item has no
    // declared shape to build from, and guessing one would be fiction.
    return itemType in SCALAR_SAMPLES ? [SCALAR_SAMPLES[itemType]] : [];
  }

  if (inner.startsWith('Dict[') || inner === 'Any') return {};
  if (inner in SCALAR_SAMPLES) {
    if (inner === 'str' && enumOptions?.length) return enumOptions[0];
    return SCALAR_SAMPLES[inner];
  }

  // A referenced model (e.g. NetworkStatus): shape lives in another module.
  return {};
}

/** Enum options declared by a Zod schema field, or null. */
export function zodEnumOptions(schema) {
  const unwrapped = schema?.def?.innerType ?? schema;
  return unwrapped?.def?.type === 'enum' ? unwrapped.options : null;
}

/** Keys a Zod object requires (reject `undefined`). */
export function requiredKeys(objectSchema) {
  return Object.entries(objectSchema.shape)
    .filter(([, field]) => !field.safeParse(undefined).success)
    .map(([key]) => key);
}

/** Build the wire payload the backend model would emit, per its own fields. */
export function samplePayload(model, objectSchema = null) {
  const payload = {};
  for (const field of model.fields) {
    const enumOptions = objectSchema ? zodEnumOptions(objectSchema.shape[field.name]) : null;
    payload[field.name] = sampleForType(field.type, enumOptions);
  }
  return payload;
}
