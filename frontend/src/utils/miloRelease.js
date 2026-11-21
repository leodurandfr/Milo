/**
 * Milō's own release as the screens draw it: the Updates row and Settings ›
 * Info read it here, so the two print the same string.
 */

// Every other row prints the number upstream states and no "v" — the catalog's
// `version_regex` took it off before the value ever reached here. Milo's rows
// print the git TAG instead, and these tags carry one, so the column only reads
// as one thing if the prefix comes off at the point of display.
//
// The prefix, and nothing else. Parsing "X.Y.Z" out of the tag is the mistake
// this guards: it draws `v0.2.0-rc1` and the `v0.2.0` it is a candidate for as
// the same string. Dropping one leading character keeps those apart, and keeps
// a development build's `-<n>-g<sha>` visible.
export function stripTagPrefix(tag) {
  return typeof tag === 'string' ? tag.replace(/^v(?=\d)/, '') : tag;
}

/**
 * The version Milō runs, from an `installed` record. On a tree outside the
 * channel — a development checkout — there is no tag, and the raw
 * `git describe` is the only honest answer. Never `versions.main`: that is the
 * catalog regex's X.Y.Z, which reads `v0.2.0-rc1` as `0.2.0`.
 *
 * @returns {string | null} null when nothing was read; the caller prints its own fallback.
 */
export function miloVersionLabel(installed) {
  return stripTagPrefix(installed?.release_tag || installed?.raw_version) || null;
}

/**
 * What the Milō row offers, from its `/api/programs` entry. A development build
 * is outside the channel and offered nothing, which is not the same as being
 * up to date — and neither is a check that never reached GitHub (a rate limit,
 * no network): `update_available` is false there too, because nobody asked.
 * Nor is a release the channel published that this unit refuses to install
 * because no key it trusts signed it (`refused: 'signature'`). Up to date is
 * the answer only once the check succeeded and found nothing to refuse.
 *
 * @returns {'update' | 'upToDate' | 'developmentBuild' | 'checkFailed' | 'refusedSignature'}
 */
export function miloRowAction(milo) {
  if (milo?.development_build) return 'developmentBuild';
  if (milo?.refused === 'signature') return 'refusedSignature';
  if (milo?.update_available) return 'update';
  return milo?.check_failed ? 'checkFailed' : 'upToDate';
}
