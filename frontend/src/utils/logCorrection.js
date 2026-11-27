// frontend/src/utils/logCorrection.js
/**
 * The text "Copy for correction" puts on the clipboard, from one event of the
 * logs page and the context the backend read around it
 * (`GET /api/system/logs/context`).
 *
 * It is written for whoever fixes the fault, not for the owner who copies it,
 * so it is English whatever the UI language — like the diagnostic report —
 * and its times are UTC, to the microsecond the journal keeps: the reader
 * compares them with a journal of their own. Every line in it was redacted by
 * the backend before it was sent; nothing here adds any.
 *
 * What it carries, in this order: the release the unit runs (version and
 * commit), the audio source at that moment with how that is known — a state
 * read *now* is said to be the current one, never passed off as the past one —
 * the event (its count and first and last occurrence, its newest text whole),
 * then the lines around it, every service together, the event's own line
 * marked, and last what the backend could not read.
 *
 * The event's text is the one the context read redacted with its neighbors
 * (`context.sample`) when it has one: the page's copy went through another
 * Redactor, whose labels for a value found by its shape (`bt-3`, `track-2`)
 * need not be the context's, and the text is read across.
 */

/** The marker in front of the event's own line among its neighbors. */
export const EVENT_MARK = '>>';

// The ISO strings as the backend sent them (UTC): Date would drop their
// microseconds.
function utc(iso) {
  return iso ?? '?';
}

/** A message whose continuation lines (a traceback) stay under it. */
function indented(message, indent) {
  return message.split('\n').join(`\n${indent}`);
}

function lineText(line) {
  const head = `${utc(line.at)} ${line.level.toUpperCase()} ${line.service}`;
  const logger = line.logger ? ` ${line.logger}` : '';
  return `${head}${logger}: ${line.message}`;
}

function sourceText(state) {
  if (!state) return 'source at that moment: unknown';
  if (state.basis === 'logged_transition') {
    return `source at that moment: ${state.source} (${state.detail}, at ${utc(state.at)})`;
  }
  if (state.basis === 'failed_transition') {
    return `source at that moment: ${state.source}, failed (${state.detail}, at ${utc(state.at)})`;
  }
  if (state.basis === 'backend_started') {
    return `source at that moment: none (${state.detail}, at ${utc(state.at)})`;
  }
  return `source NOW, not known for that moment: ${state.source ?? 'unknown'} (${state.detail})`;
}

/**
 * @param {object} event   a group of `GET /api/system/logs`
 * @param {object} context `GET /api/system/logs/context` for that event
 * @returns {string}
 */
export function correctionText(event, context) {
  const build = context?.build ?? {};
  const out = [
    'Milō log event, for correction',
    `version: ${build.version ?? 'unknown'}`,
    `commit: ${build.commit ?? 'unknown'}`,
    sourceText(context?.source_state),
    '',
    `event: ${event.level.toUpperCase()} ${event.service}${event.logger ? ` ${event.logger}` : ''}`,
    event.count > 1
      ? `seen ${event.count} times, first ${utc(event.first_at)}, last ${utc(event.last_at)}`
      : `seen once, at ${utc(event.last_at)}`,
    `  ${indented(context?.sample ?? event.sample, '  ')}`,
  ];

  const lines = context?.lines ?? [];
  if (lines.length) {
    out.push('', `around it (${lines.length} lines, every service):`);
    lines.forEach((line, index) => {
      const mark = index === context.event_index ? `${EVENT_MARK} ` : '   ';
      out.push(mark + indented(lineText(line), '      '));
    });
  }

  const missing = context?.not_collected ?? [];
  if (missing.length) {
    out.push('', 'not collected:');
    for (const item of missing) out.push(`  ${item.source}: ${item.reason}`);
  }
  return out.join('\n');
}
