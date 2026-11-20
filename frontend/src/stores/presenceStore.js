// frontend/src/stores/presenceStore.js
/**
 * Pinia store for the proof of presence: the gesture on the unit itself the
 * backend waits for (HTTP 428) before a request that reaches root — opening
 * SSH, setting the account password, installing a program past the validated
 * set, resetting the setup (backend/core/system/presence.py).
 *
 * `request()` takes apiCall's arguments and answers apiCall's `{ ok, data,
 * error }`: it sends the request and, on a 428, holds the challenge for
 * PresenceDialog until the unit confirms it — `system/presence_changed`, or
 * the resync that follows a power cut — then sends the request again with the
 * token the 428 carried. The token never leaves this store: the gesture made
 * for this tab proves nothing for another one. What waits on the gesture is
 * the request's own (`waiter`), so a second request of this tab — refused 409
 * while the first waits — leaves the first's challenge alone.
 *
 * `unitChallenge` is the challenge the unit waits on whoever asked, as every
 * screen hears it; `othersChallenge` is that challenge when it is not this
 * tab's, which the unit's own screen draws, since its knob is the one a
 * stranger's challenge would take a turn of.
 *
 * The unit's own screen goes down with the unit, so it says which gestures it
 * can wait through (`GESTURES_HEADER`): a request the unit would confirm by a
 * power cut opens nothing from it, and the dialog sends the owner to a phone
 * or a computer, which can then ask at once.
 */
import { defineStore } from 'pinia';
import { computed, ref } from 'vue';
import { apiCall } from '@/services/apiCall';
import { logger } from '@/services/logger';
import { isKiosk } from '@/utils/kiosk';

const CHALLENGE_STATUS = 428;

// backend/core/system/presence.py::PRESENCE_GESTURES_HEADER, and the
// `Gesture` values the unit's own screen sees to the end (every one but the
// power cut). Held to the backend by tests/architecture/presenceKeys.test.js.
export const GESTURES_HEADER = 'X-Milo-Presence-Gestures';
export const ON_THE_UNIT_GESTURES = ['rotary', 'power_button'];

export const usePresenceStore = defineStore('presence', () => {
  // This tab's challenge, as the dialog draws it, null while none:
  // { id, action, gesture, status, deadline, cooldownS }, status one of
  // 'pending' | 'confirmed' | 'expired' | 'elsewhere'. 'expired' (nothing was
  // changed) and 'elsewhere' (no challenge opened: ask from another device)
  // stay until dismissed, so the dialog can say so.
  const challenge = ref(null);
  // The challenge the unit waits on, whoever asked (same shape, never
  // 'expired' or 'elsewhere'), null while none.
  const unitChallenge = ref(null);
  // This tab's requests on their way: the unit's word of a challenge one of
  // them opens comes before the 428 that makes it this tab's.
  const asking = ref(0);
  // The request waiting on the gesture: { id, header, token, settle }, where
  // settle resolves it with 'confirmed' | 'ended' | 'cancelled' (null once it has).
  let waiter = null;

  const othersChallenge = computed(() => {
    const theirs = unitChallenge.value;
    if (!theirs || theirs.status !== 'pending' || challenge.value || asking.value > 0) return null;
    return theirs;
  });

  function fromView(view) {
    return {
      id: view.id,
      action: view.action,
      gesture: view.gesture,
      status: view.status,
      deadline: Date.now() + view.remaining_s * 1000,
      cooldownS: view.cooldown_s,
    };
  }

  function settleWith(own, outcome) {
    const resolve = own?.settle;
    if (!resolve) return;
    own.settle = null;
    resolve(outcome);
  }

  function isChallenge(result) {
    return result.error?.status === CHALLENGE_STATUS && typeof result.error.detail?.token === 'string';
  }

  /** A 428 that opened nothing: the gesture is one this client cannot wait through. */
  function isElsewhere(result) {
    const detail = result.error?.status === CHALLENGE_STATUS ? result.error.detail : null;
    return typeof detail?.gesture === 'string' && detail.token === undefined;
  }

  /** Make this request the one waiting on the gesture of the 428 it got. */
  function hold(detail) {
    const own = { id: detail.challenge.id, header: detail.header, token: detail.token, settle: null };
    own.outcome = detail.challenge.status === 'confirmed'
      ? Promise.resolve('confirmed')
      : new Promise((resolve) => { own.settle = resolve; });
    // The unit holds one challenge at a time: one it opened for this request
    // means any other this tab waited on is gone, and its request answers.
    settleWith(waiter, 'ended');
    waiter = own;
    challenge.value = fromView(detail.challenge);
    return own;
  }

  /** The dialog goes with the request that held it, unless it says it expired. */
  function release(own) {
    if (waiter !== own) return;
    waiter = null;
    if (challenge.value?.status !== 'expired') challenge.value = null;
  }

  /** The dialog of a challenge that ended unconfirmed, or was never opened. */
  function dismissible() {
    const status = challenge.value?.status;
    return status === 'expired' || status === 'elsewhere';
  }

  /** What the unit says of the challenge it waits on (null once none). */
  function observe(view) {
    unitChallenge.value = view ? fromView(view) : null;
    const own = waiter;
    if (!own?.settle) return;
    if (view && view.id === own.id) {
      challenge.value = fromView(view);
      if (view.status === 'confirmed') settleWith(own, 'confirmed');
      return;
    }
    // Gone, and not spent by this tab: it ended unconfirmed (or unspent).
    challenge.value = { ...challenge.value, status: 'expired' };
    settleWith(own, 'ended');
  }

  /**
   * apiCall[method](url, body, options), through the proof of presence.
   * A challenge that ends unconfirmed, or that this client cannot wait
   * through, answers `{ ok: false, error: null }`, like a cancelled request:
   * the dialog has already said why.
   */
  async function request(method, url, body = null, options = {}) {
    asking.value += 1;
    try {
      return await guarded(method, url, body, options);
    } finally {
      asking.value -= 1;
    }
  }

  async function guarded(method, url, body, options) {
    const { errorRef = null, logLevel = 'error', ...rest } = options;
    const category = rest.category || 'api';
    const follows = isKiosk() ? { [GESTURES_HEADER]: ON_THE_UNIT_GESTURES.join(', ') } : null;
    const send = (proof) => {
      const headers = follows || proof ? { ...follows, ...proof } : null;
      // The 428 is expected: logged here once the outcome is known, not by apiCall.
      const config = { ...rest, logLevel: 'debug', ...(headers ? { headers } : {}) };
      return method === 'get' || method === 'delete'
        ? apiCall[method](url, config)
        : apiCall[method](url, body, config);
    };

    let own = null;
    let result = await send(null);
    while (isChallenge(result)) {
      if (own) release(own);
      own = hold(result.error.detail);
      const outcome = await own.outcome;
      if (outcome !== 'confirmed') {
        result = { ok: false, data: null, error: null };
        break;
      }
      result = await send({ [own.header]: own.token });
    }
    if (own) release(own);
    if (isElsewhere(result)) {
      const { action, gesture } = result.error.detail;
      challenge.value = { id: null, action, gesture, status: 'elsewhere', deadline: null, cooldownS: null };
      result = { ok: false, data: null, error: null };
    }

    if (!result.ok && result.error) {
      const log = logger[logLevel] || logger.error;
      log.call(logger, category, rest.message || `${method.toUpperCase()} ${url} failed`, result.error);
      if (errorRef) errorRef.value = result.error.detail;
    }
    return result;
  }

  /**
   * The dialog's Cancel: the unit stops waiting, and the request answers
   * nothing. The request lets go of the challenge before the unit is told:
   * the unit announces the end before it answers the DELETE, and that word,
   * reaching a request still waiting, would read as an expiry.
   */
  async function cancel() {
    const own = waiter;
    waiter = null;
    challenge.value = null;
    if (!own) return;
    // Ended here: no screen of this tab should draw it while the unit's word
    // of the end is on its way.
    if (unitChallenge.value?.id === own.id) unitChallenge.value = null;
    const awaited = own.settle !== null;
    settleWith(own, 'cancelled');
    if (!awaited) return;
    await apiCall.delete('/api/system/presence', {
      category: 'system',
      message: 'Failed to cancel the confirmation on the unit',
      headers: { [own.header]: own.token },
      // Answered 404 when it has just ended anyway: nothing to report.
      logLevel: 'debug',
    });
  }

  /** Closes the dialog of a challenge that ended unconfirmed, or never opened. */
  function dismiss() {
    if (dismissible()) challenge.value = null;
  }

  function handleChallengeEvent(event) {
    observe(event?.data?.challenge ?? null);
  }

  /** The challenge as the unit holds it now: how a proof by power cut, made
   *  while this tab's socket was down, reaches the request waiting on it. */
  async function resync() {
    const result = await apiCall.get('/api/system/presence', {
      category: 'system',
      message: 'Failed to read the confirmation the unit waits for',
    });
    if (result.ok) observe(result.data.data.challenge);
    return result.ok;
  }

  return {
    challenge,
    unitChallenge,
    othersChallenge,
    request,
    cancel,
    dismiss,
    handleChallengeEvent,
    resync,
  };
});
