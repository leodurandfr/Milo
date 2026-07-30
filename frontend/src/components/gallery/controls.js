// frontend/src/components/gallery/controls.js
/**
 * Turns a component into a list of control descriptors for the gallery panel.
 *
 * Nothing here is hand-written per component, and that is the point: Vue keeps
 * `Component.props` and `Component.emits` as real data on the compiled SFC, so
 * type, default, required-ness and the event list are read rather than restated.
 * A new prop shows up in the panel on its own.
 *
 * The one thing Vue does not hand over is the *allowed values* of an enum prop:
 * they live inside a `validator` function. We read them out of the function's
 * own source. That survives the production build — esbuild minifies
 * `(value) => ['a', 'b'].includes(value)` down to `e=>["a","b"].includes(e)` and
 * the array literal is still there, so the panel behaves the same in `npm run
 * dev` and in the bundle served from dist/.
 *
 * Two shapes defeat the parse, and both are handled by an explicit override in
 * registry.js rather than by a silent fallback:
 *   - a validator that closes over an identifier (`APP_ICON_NAMES.includes(v)`),
 *   - a prop with no validator at all whose useful values are still a short list
 *     (an icon name, a pixel size).
 * tests/architecture/gallery.test.js fails when a String prop carrying a
 * validator is neither parsed nor overridden — without that, a validator
 * rewritten in another shape would quietly downgrade to a free-text box.
 */

/** `[…].includes(` — the shape every enum validator in components/ui uses. */
const ENUM_FROM_VALIDATOR = /\[([^\]]*)\]\s*\.includes\(/;

/**
 * The allowed values of an enum prop, read from its validator's source.
 * Returns null when the validator is not a literal-array membership test.
 */
export function enumFromValidator(validator) {
  if (typeof validator !== 'function') return null;

  const match = String(validator).match(ENUM_FROM_VALIDATOR);
  if (!match) return null;

  const values = match[1]
    .split(',')
    .map(part => part.trim().replace(/^['"]|['"]$/g, ''))
    .filter(Boolean);

  if (!values.length) return null;

  // ToggleSection accepts both '2' and 2, so the literal carries duplicates
  // once the quotes are stripped.
  return [...new Set(values)];
}

/** Constructors on a prop definition, always as an array. */
function typesOf(definition) {
  const type = definition?.type;
  if (!type) return [];
  return Array.isArray(type) ? type : [type];
}

function hasType(definition, ctor) {
  return typesOf(definition).includes(ctor);
}

/**
 * How the panel should render one prop.
 *
 * `fixed` means "no widget": a value the panel shows read-only because a text
 * field or a select cannot express it (an options array, a callback). The
 * playground descriptor supplies those.
 */
function kindOf(name, definition, override) {
  if (override?.kind) return override.kind;
  if (override?.options) return 'enum';

  // A callback prop is behaviour, not a value — the canvas substitutes a stub
  // that reports to the event log, so there is nothing to edit here.
  if (hasType(definition, Function)) return 'fixed';
  if (hasType(definition, Object) || hasType(definition, Array)) return 'fixed';

  // Boolean only when it is the *whole* type. `[String, Number, Boolean]` is a
  // value that merely happens to accept a boolean (ButtonGroup's modelValue), and
  // a checkbox would make its other two thirds unreachable.
  if (typesOf(definition).length === 1 && hasType(definition, Boolean)) return 'boolean';

  const enumOptions = enumFromValidator(definition.validator);
  if (enumOptions) return 'enum';

  // `[String, Number]` with no validator (a size) reads as text and coerces.
  if (hasType(definition, Number) && !hasType(definition, String)) return 'number';
  if (hasType(definition, String)) return 'text';

  return 'fixed';
}

/** The default Vue would apply, resolved through a factory when there is one. */
function defaultOf(definition) {
  const value = definition?.default;
  return typeof value === 'function' && !hasType(definition, Function) ? value() : value;
}

/**
 * Control descriptors for one component, in declaration order.
 *
 * @param {object} component  a compiled SFC (its `props` carry the metadata)
 * @param {object} overrides  per-prop `{ kind, options }`, from registry.js
 */
export function describeProps(component, overrides = {}) {
  return Object.entries(component?.props ?? {}).map(([name, definition]) => {
    const override = overrides[name];
    const kind = kindOf(name, definition, override);

    return {
      name,
      kind,
      options: override?.options ?? (kind === 'enum' ? enumFromValidator(definition.validator) : null),
      default: defaultOf(definition),
      required: !!definition.required,
      types: typesOf(definition).map(ctor => ctor.name).join(' | ') || 'any',
      // Kept so the guardrail can tell "no enum needed" from "enum we failed to read".
      hasValidator: typeof definition.validator === 'function',
    };
  });
}

/** Event names a component declares, or an empty list when it declares none. */
export function describeEvents(component) {
  const emits = component?.emits;
  if (!emits) return [];
  return Array.isArray(emits) ? [...emits] : Object.keys(emits);
}

/** Names of the callback props the canvas should stub with a logger. */
export function callbackProps(component) {
  return Object.entries(component?.props ?? {})
    .filter(([, definition]) => hasType(definition, Function))
    .map(([name]) => name);
}
