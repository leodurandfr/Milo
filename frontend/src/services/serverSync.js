// frontend/src/services/serverSync.js
import { logger } from '@/services/logger';

/**
 * Keeps the stores' copy of the server whole: the one place that knows whether
 * the last resync reached every store, and asks again for the ones it did not.
 *
 * Before it, a resync was fire-and-forget: each store swallowed its own failure
 * and nothing retried. The kiosk loads its page before the backend listens
 * whenever a boot runs long, every boot fetch then answers 502, and the app came
 * up in English with no radio favorites until someone reloaded it (measured
 * 2026-09-27).
 *
 * The rules, each one a failure it replaces:
 *  - a domain's resync() resolves `true` once its store reflects the server and
 *    `false` otherwise. Only a failed domain is retried, alone, after a growing
 *    jittered delay — MAX_RETRIES times, then it waits for the next request: a
 *    backend that is up but keeps refusing one route is not polled for ever;
 *  - a request made while a sync runs is neither dropped nor run alongside it:
 *    the running pass finishes, then exactly one full pass follows, however many
 *    requests came meanwhile — the running pass may have read the server before
 *    whatever prompted the request;
 *  - a new request supersedes a pending retry and resets its delay: it is a
 *    fresh sign the server may be back (a socket accepted, a tab shown);
 *  - no domain can hold the others hostage: one that has not settled after
 *    DOMAIN_DEADLINE_MS counts as failed for this pass, so a wedged request
 *    cannot freeze every later sync behind it.
 *
 * Stages run in order, the domains of a stage in parallel: the audio mirror goes
 * first and alone, since every source store's now-playing slice is a view of it
 * (the music library reads its availability there). A stage that failed skips
 * the ones after it, which count as failed too — run against a stale mirror they
 * would report success, and the retry that heals the mirror would never rerun
 * them.
 */

const RETRY_BASE_MS = 1000;
const RETRY_MAX_MS = 15000;
const MAX_RETRIES = 8;  // 1 + 2 + 4 + 8 + 15 × 4 ≈ 75 s of trying
// Past nginx's 60 s proxy_read_timeout on /api: a request raced out here is
// already dead when the retry asks again, so its late answer cannot land after
// the retry's and overwrite it with an older snapshot.
const DOMAIN_DEADLINE_MS = 65000;

export function createServerSync(stages, { isHidden = () => document.hidden, random = Math.random } = {}) {
  let running = null;
  let rerun = false;
  let failed = new Set();
  let attempt = 0;
  let retryTimer = null;
  let disposed = false;

  async function settle(domain) {
    let deadline;
    const expired = new Promise((resolve) => {
      deadline = setTimeout(() => resolve('deadline'), DOMAIN_DEADLINE_MS);
    });
    try {
      const outcome = await Promise.race([domain.resync(), expired]);
      if (outcome === 'deadline') {
        logger.warn('sync', `${domain.name}: no answer within ${DOMAIN_DEADLINE_MS / 1000} s`);
        return false;
      }
      return outcome === true;
    } catch (error) {
      logger.error('sync', `${domain.name}: resync threw`, { error: error?.message });
      return false;
    } finally {
      clearTimeout(deadline);
    }
  }

  async function pass(onlyFailed) {
    const nowFailed = new Set();
    for (const stage of stages) {
      const domains = onlyFailed ? stage.filter(d => failed.has(d.name)) : stage;
      if (nowFailed.size > 0) {
        domains.forEach(d => nowFailed.add(d.name));
        continue;
      }
      const outcomes = await Promise.all(domains.map(settle));
      outcomes.forEach((ok, i) => { if (!ok) nowFailed.add(domains[i].name); });
    }
    return nowFailed;
  }

  function report(previous, current) {
    for (const name of current) {
      if (!previous.has(name)) logger.warn('sync', `${name} could not be refreshed, retrying`);
    }
    for (const name of previous) {
      if (!current.has(name)) logger.info('sync', `${name} refreshed again`);
    }
  }

  function scheduleRetry() {
    if (disposed || failed.size === 0) {
      attempt = 0;
      return;
    }
    if (attempt >= MAX_RETRIES) {
      logger.warn('sync', `Giving up on ${[...failed].join(', ')} until the next resync request`);
      return;
    }
    attempt += 1;
    armRetry(Math.min(RETRY_BASE_MS * 2 ** (attempt - 1), RETRY_MAX_MS) * (0.75 + random() * 0.5));
  }

  function armRetry(delay) {
    retryTimer = setTimeout(() => {
      retryTimer = null;
      // Nobody is looking: hold the requests without spending an attempt. The
      // tab's return requests a full sync anyway.
      if (isHidden()) armRetry(delay);
      else start(true);
    }, delay);
  }

  function clearRetry() {
    if (retryTimer !== null) {
      clearTimeout(retryTimer);
      retryTimer = null;
    }
  }

  async function loop(onlyFailed) {
    let retrying = onlyFailed;
    do {
      rerun = false;
      const previous = failed;
      failed = await pass(retrying);
      report(previous, failed);
      retrying = false;
    } while (rerun && !disposed);
    scheduleRetry();
    return failed.size === 0;
  }

  function start(onlyFailed) {
    running = loop(onlyFailed).finally(() => { running = null; });
    return running;
  }

  /**
   * Resync every domain. Resolves once the pass this request is served by has
   * settled — true when every domain succeeded.
   */
  function request(reason) {
    if (disposed) return Promise.resolve(false);
    logger.debug('sync', `Resync requested (${reason})`);
    clearRetry();
    attempt = 0;
    if (running) {
      rerun = true;
      return running;
    }
    return start(false);
  }

  function dispose() {
    disposed = true;
    clearRetry();
  }

  return { request, dispose };
}
