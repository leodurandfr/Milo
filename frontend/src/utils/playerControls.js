// frontend/src/utils/playerControls.js
// Which controls the full-screen player draws, and in which state — read from
// the wire alone: a control exists iff the source lists its command in
// `controls` now, and a toggle's state is the one `details` publishes. No
// source is named here, so a source earns a button by listing a command, never
// by being on a list.
import { pausesOnPress } from '@/utils/transport';
import { nextRepeatMode } from '@/utils/spotifyRepeat';

/** The relative moves a `skip` pair offers, in seconds (the −15 / +30 glyphs). */
export const SKIP_BACK_SECONDS = -15;
export const SKIP_FORWARD_SECONDS = 30;

/**
 * The main button: the pause/resume pair, else a live stream's stop/resume_playback.
 * The pause pair follows the phase rather than which half is listed, as the
 * glyph always has (utils/transport) — a press that would be refused shows
 * disabled instead of sending.
 */
function mainControl(listed, phase) {
  if (listed('pause') || listed('resume')) {
    const pauses = pausesOnPress(phase);
    const command = pauses ? 'pause' : 'resume';
    return { id: 'main', row: 'transport', command, icon: pauses ? 'pause' : 'play', enabled: listed(command) };
  }
  if (listed('stop') || listed('resume_playback')) {
    const stops = listed('stop');
    return {
      id: 'main', row: 'transport',
      command: stops ? 'stop' : 'resume_playback',
      icon: stops ? 'stop' : 'play',
      enabled: true
    };
  }
  return null;
}

/**
 * The two buttons around the main one. Track steps win over the relative skip:
 * a source that has a previous or a next item steps through them (a disc, a
 * queue, the radio favorites), and only one with neither (an episode) offers
 * −15 / +30 in their place. The pair is drawn whole once either step is listed,
 * the missing half disabled — `next` is absent on the last track.
 */
function flankControls(listed) {
  if (listed('prev') || listed('next')) {
    return [
      { id: 'prev', row: 'transport', command: 'prev', icon: 'previous', enabled: listed('prev') },
      { id: 'next', row: 'transport', command: 'next', icon: 'next', enabled: listed('next') }
    ];
  }
  if (listed('skip')) {
    return [
      { id: 'skip-back', row: 'transport', command: 'skip', icon: 'rewind15', seconds: SKIP_BACK_SECONDS, enabled: true },
      { id: 'skip-forward', row: 'transport', command: 'skip', icon: 'forward30', seconds: SKIP_FORWARD_SECONDS, enabled: true }
    ];
  }
  return [];
}

/**
 * The controls the player draws, in drawing order.
 *
 * `row` says where each goes: `progress` (the bar is interactive iff a `seek`
 * control is present), `transport` (the plate: flank, main, flank) and
 * `options` (the toggles and the speed, beside what the source puts in the
 * player's `#actions` slot). Every control carries the command it sends;
 * `params` is the payload a press sends when the control knows it alone.
 *
 * Flanks without a main button are dropped: a transport is a main button first,
 * and a row of arrows around nothing is not one.
 *
 * @param {object} state
 * @param {string[]} state.controls - the commands the source takes now
 * @param {object|null} state.details - the source's details (`kind` and its fields)
 * @param {string|null} state.phase - the session's phase, null with no session
 * @returns {Array<{id: string, row: string, command: string, enabled: boolean}>}
 */
export function playerControls({ controls, details, phase }) {
  const listed = (command) => controls.includes(command);
  const items = [];

  if (listed('seek')) items.push({ id: 'seek', row: 'progress', command: 'seek', enabled: true });

  const main = mainControl(listed, phase);
  if (main) {
    const [before, after] = flankControls(listed);
    items.push(...[before, main, after].filter(Boolean));
  }

  if (listed('set_shuffle')) {
    const active = !!details?.shuffle;
    items.push({
      id: 'shuffle', row: 'options', command: 'set_shuffle', icon: 'shuffle',
      active, params: { shuffle: !active }, enabled: true
    });
  }
  if (listed('set_repeat')) {
    const mode = details?.repeat ?? 'off';
    items.push({
      id: 'repeat', row: 'options', command: 'set_repeat',
      icon: mode === 'track' ? 'repeatOnce' : 'repeat',
      mode, active: mode !== 'off', params: { mode: nextRepeatMode(mode) }, enabled: true
    });
  }
  if (listed('set_speed')) {
    items.push({ id: 'speed', row: 'options', command: 'set_speed', value: details?.speed ?? null, enabled: true });
  }

  return items;
}
