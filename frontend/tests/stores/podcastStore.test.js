// frontend/tests/stores/podcastStore.test.js
/**
 * podcastStore owns the episode progress cache (including its LRU bound, the
 * ms→s wire conversion and the "listened" mark an end of file leaves), the
 * subscriptions Map, and the optimistic "pending episode" state. Those are the
 * parts a regression can actually break.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { usePodcastStore } from '@/stores/podcastStore';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { apiCall } from '@/services/apiCall';
import { resetApiCallMock, ok, fail } from '../helpers/apiCallMock';
import { makeAudioState, makeSession, publishState } from '../helpers/audioState';

vi.mock('@/services/apiCall', () => import('../helpers/apiCallMock'));

const EPISODE = (uuid, extra = {}) => ({ uuid, title: `Episode ${uuid}`, ...extra });

const T0 = 1_790_270_000; // epoch seconds

/** Podcast selected with `episode` live in a session. */
function podcastSession(episode, { phase = 'playing', id = 'session-1', ms = null, at = T0, durationMs = null, speed = 1.0 } = {}) {
  return {
    source: 'podcast',
    service: 'running',
    session: makeSession({
      id,
      phase,
      title: episode.title,
      duration_ms: durationMs,
      position: ms === null ? null : { ms, at, rate: speed },
    }),
    controls: phase === 'paused' ? ['resume', 'seek', 'set_speed'] : ['pause', 'seek', 'set_speed'],
    details: { kind: 'podcast', episode, speed },
  };
}

/** Podcast selected, no session, `episode` kept to resume at `positionMs`. */
function podcastResume(episode, { positionMs = null, durationMs = null } = {}) {
  return {
    source: 'podcast',
    service: 'running',
    controls: ['resume', 'set_speed'],
    resume: {
      title: episode.title, artist: null, album: null, artwork: null,
      duration_ms: durationMs, position_ms: positionMs,
    },
    details: { kind: 'podcast', episode, speed: 1.0 },
  };
}

/** Podcast selected with nothing loaded and nothing to resume. */
const podcastIdle = () => ({ source: 'podcast', service: 'running', controls: ['set_speed'] });

const publish = (overrides) => publishState(useUnifiedAudioStore(), overrides);

describe('podcastStore', () => {
  let store;

  beforeEach(() => {
    resetApiCallMock();
    store = usePodcastStore();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  describe('state ingestion', () => {
    it('adopts the current episode and clears the pending flag for it', () => {
      apiCall.post.mockResolvedValueOnce(ok({ status: 'success' }));
      store.play('ep1');
      expect(store.pendingEpisodeUuid).toBe('ep1');

      publish(podcastSession(EPISODE('ep1'), { phase: 'loading' }));

      expect(store.currentEpisode.uuid).toBe('ep1');
      expect(store.pendingEpisodeUuid).toBeNull();
    });

    it('keeps the pending flag when a different episode confirms', () => {
      apiCall.post.mockResolvedValueOnce(ok({ status: 'success' }));
      store.play('ep1');

      publish(podcastSession(EPISODE('ep2')));

      expect(store.pendingEpisodeUuid).toBe('ep1');
    });

    it('clears the pending flag when the episode asked for was already the one kept', () => {
      // Replaying the resume episode changes no episode, only the session: the
      // spinner must still end with the state that answers the press.
      publish(podcastResume(EPISODE('ep1'), { positionMs: 5_000, durationMs: 60_000 }));
      apiCall.post.mockResolvedValueOnce(ok({ status: 'success' }));
      store.play('ep1');

      publish(podcastSession(EPISODE('ep1'), { phase: 'loading' }));

      expect(store.pendingEpisodeUuid).toBeNull();
    });

    it('ignores podcast details while another source is selected', () => {
      publish({ ...podcastSession(EPISODE('ep1')), source: 'radio' });

      expect(useUnifiedAudioStore().systemState.source).toBe('radio');
      expect(store.currentEpisode).toBeNull();
    });

    it('converts the millisecond wire position into seconds for the cache', () => {
      // The wire is ms (shared convention); EpisodeCard reads seconds.
      publish(podcastSession(EPISODE('ep1'), { phase: 'paused', ms: 65_400, durationMs: 1_800_000 }));

      expect(store.getEpisodeProgress('ep1')).toMatchObject({
        position: 65,
        duration: 1800,
      });
    });

    it('records the episode being left where it stood when it was left', () => {
      // The anchor is republished only on a discontinuity, so the cache entry
      // written with it is minutes old by the time another episode starts. The
      // episode being left is recorded as of that moment instead.
      vi.useFakeTimers();
      vi.setSystemTime(T0 * 1000);
      publish(podcastSession(EPISODE('ep1'), { ms: 60_000, durationMs: 1_800_000 }));

      vi.setSystemTime((T0 + 30) * 1000);
      publish(podcastSession(EPISODE('ep2'), { id: 'session-2', phase: 'loading' }));

      expect(store.getEpisodeProgress('ep1')).toMatchObject({ position: 90, duration: 1800 });
    });

    it('reads the resume point for an episode that stopped', () => {
      publish(podcastResume(EPISODE('ep1'), { positionMs: 42_000, durationMs: 600_000 }));

      expect(store.currentEpisode.uuid).toBe('ep1');
      expect(store.currentEpisodeProgress).toEqual({ positionMs: 42_000, durationMs: 600_000 });
    });

    it('drops the current episode when the source has nothing to show', () => {
      publish(podcastSession(EPISODE('ep1')));

      publish(podcastIdle());

      expect(store.currentEpisode).toBeNull();
      expect(store.currentEpisodeProgress).toBeNull();
    });

    it('applies the speed the state publishes', () => {
      publish(podcastSession(EPISODE('ep1'), { speed: 1.5 }));

      expect(store.playbackSpeed).toBe(1.5);
    });

    it('falls back to the saved speed while no episode carries one', async () => {
      apiCall.get.mockResolvedValueOnce(ok({ settings: { playback_speed: 1.25 } }));
      await store.loadSettings();

      publish(podcastIdle());

      expect(store.playbackSpeed).toBe(1.25);
    });

    it('returns null progress for an episode never played', () => {
      expect(store.getEpisodeProgress('unknown')).toBeNull();
    });
  });

  describe('session end', () => {
    it('marks an episode played to its end as listened', () => {
      publish(podcastSession(EPISODE('ep1'), { phase: 'paused', ms: 10_000, durationMs: 60_000 }));

      store.handleSessionEnded({ source: 'podcast', session_id: 'session-1', reason: 'eof' });
      // The state that follows the end: nothing left to resume.
      publish(podcastIdle());

      expect(store.currentEpisode).toBeNull();
      const progress = store.getEpisodeProgress('ep1');
      // Not overwritten by the ended session's last playhead.
      expect(progress.completed).toBe(true);
      // Merged, not replaced: the card still shows a duration.
      expect(progress.duration).toBe(60);
    });

    it('does not mark an episode that was only stopped', () => {
      publish(podcastSession(EPISODE('ep1'), { phase: 'paused', ms: 10_000, durationMs: 60_000 }));

      store.handleSessionEnded({ source: 'podcast', session_id: 'session-1', reason: 'user_stop' });

      expect(store.getEpisodeProgress('ep1').completed).toBeUndefined();
    });

    it('ignores the end of another source or of a session it does not show', () => {
      publish(podcastSession(EPISODE('ep1'), { phase: 'paused', ms: 10_000, durationMs: 60_000 }));

      store.handleSessionEnded({ source: 'music_library', session_id: 'session-1', reason: 'eof' });
      store.handleSessionEnded({ source: 'podcast', session_id: 'session-0', reason: 'eof' });

      expect(store.getEpisodeProgress('ep1').completed).toBeUndefined();
    });
  });

  describe('progress cache bound', () => {
    it('evicts the least recently played entry past the 200-entry limit', () => {
      const episodes = Array.from({ length: 201 }, (_, i) => EPISODE(`ep${i}`, {
        playback_progress: { position: 10, duration: 100, last_played: 1000 + i },
      }));

      store.enrichEpisodesWithProgress(episodes);

      expect(store.progressCache.size).toBe(200);
      expect(store.getEpisodeProgress('ep0')).toBeNull();
      expect(store.getEpisodeProgress('ep200')).not.toBeNull();
    });

    it('never evicts the episode currently playing', () => {
      publish(podcastSession(EPISODE('ep0')));
      const episodes = Array.from({ length: 201 }, (_, i) => EPISODE(`ep${i}`, {
        // ep0 is the oldest, so it would be the first victim.
        playback_progress: { position: 10, duration: 100, last_played: 1000 + i },
      }));

      store.enrichEpisodesWithProgress(episodes);

      expect(store.progressCache.size).toBe(200);
      expect(store.getEpisodeProgress('ep0')).not.toBeNull();
      expect(store.getEpisodeProgress('ep1')).toBeNull();
    });

    it('enrichEpisodesWithProgress records the completed flag and returns its input', () => {
      const episodes = [EPISODE('ep1', {
        playback_progress: { position: 30, duration: 60, completed: true },
      })];

      const returned = store.enrichEpisodesWithProgress(episodes);

      expect(returned).toBe(episodes);
      expect(store.getEpisodeProgress('ep1').completed).toBe(true);
    });

    it('enrichEpisodesWithProgress tolerates a non-array payload', () => {
      expect(store.enrichEpisodesWithProgress(undefined)).toBeUndefined();
      expect(store.progressCache.size).toBe(0);
    });
  });

  describe('subscriptions', () => {
    it('exposes subscriptions sorted by name', () => {
      store.addSubscription({ uuid: 'p2', name: 'Zeta' });
      store.addSubscription({ uuid: 'p1', name: 'Alpha' });

      expect(store.subscriptions.map(s => s.name)).toEqual(['Alpha', 'Zeta']);
      expect(store.hasSubscriptions).toBe(true);
    });

    it('upserts on re-subscribe instead of duplicating', () => {
      store.addSubscription({ uuid: 'p1', name: 'Podcast 1' });
      store.addSubscription({ uuid: 'p1', name: 'Podcast 1 renamed' });

      expect(store.subscriptions).toHaveLength(1);
      expect(store.subscriptions[0].name).toBe('Podcast 1 renamed');
    });

    it('invalidates the latest-episodes cache when a subscription is added', async () => {
      apiCall.get.mockResolvedValue(ok({ subscriptions: [], results: [] }));
      await store.loadSubscriptions();
      expect(store.subscriptionsLoaded).toBe(true);

      store.addSubscription({ uuid: 'p1', name: 'Podcast 1' });

      expect(store.subscriptionsLoaded).toBe(false);
    });

    it('removing a subscription also drops its episodes from the latest list', () => {
      store.addSubscription({ uuid: 'p1', name: 'Podcast 1' });
      store.latestSubscriptionEpisodes = [
        { uuid: 'ep1', podcast: { uuid: 'p1' } },
        { uuid: 'ep2', podcast: { uuid: 'p2' } },
      ];

      store.removeSubscription('p1');

      expect(store.subscriptions).toHaveLength(0);
      expect(store.latestSubscriptionEpisodes.map(e => e.uuid)).toEqual(['ep2']);
    });
  });

  describe('loadSubscriptions', () => {
    it('hides already-listened episodes from the latest list', async () => {
      apiCall.get.mockImplementation(async (url) => {
        if (url === '/api/podcast/subscriptions') {
          return ok({ subscriptions: [{ uuid: 'p1', name: 'Podcast 1' }] });
        }
        return ok({
          results: [
            { uuid: 'ep1', podcast: { uuid: 'p1' }, playback_progress: { completed: true, position: 60, duration: 60 } },
            { uuid: 'ep2', podcast: { uuid: 'p1' }, playback_progress: { completed: false, position: 5, duration: 60 } },
            { uuid: 'ep3', podcast: { uuid: 'p1' } },
          ],
        });
      });

      await store.loadSubscriptions();

      expect(store.latestSubscriptionEpisodes.map(e => e.uuid)).toEqual(['ep2', 'ep3']);
    });

    it('skips the discovery call entirely when there is no subscription', async () => {
      apiCall.get.mockResolvedValue(ok({ subscriptions: [] }));

      await store.loadSubscriptions();

      expect(apiCall.get).toHaveBeenCalledTimes(1);
      expect(store.latestSubscriptionEpisodes).toEqual([]);
    });

    it('serves the cache on a second call and refetches the episodes when forced', async () => {
      apiCall.get.mockResolvedValue(ok({
        subscriptions: [{ uuid: 'p1', name: 'Podcast 1' }],
        results: [],
      }));
      await store.loadSubscriptions();
      const callsAfterFirstLoad = apiCall.get.mock.calls.length;

      await store.loadSubscriptions();
      expect(apiCall.get).toHaveBeenCalledTimes(callsAfterFirstLoad);

      // forceRefresh re-runs the discovery call only — the subscriptions list
      // itself stays loaded (invalidating it is preloadSubscriptionsList's job).
      await store.loadSubscriptions(true);
      expect(apiCall.get).toHaveBeenLastCalledWith(
        '/api/podcast/subscriptions/latest-episodes',
        expect.objectContaining({ params: { limit: 20 } }),
      );
      expect(apiCall.get).toHaveBeenCalledTimes(callsAfterFirstLoad + 1);
    });

    it('reuses a list already preloaded instead of refetching it', async () => {
      apiCall.get.mockResolvedValue(ok({ subscriptions: [], results: [] }));
      await store.preloadSubscriptionsList();

      await store.loadSubscriptions();

      // Only the preload call: the list was already loaded, and with zero
      // subscriptions there is no discovery call either.
      expect(apiCall.get).toHaveBeenCalledTimes(1);
    });

    it('resync forces a refetch and invalidates the latest episodes', async () => {
      apiCall.get.mockResolvedValue(ok({ subscriptions: [], results: [] }));
      await store.loadSubscriptions();

      await store.resync();

      expect(apiCall.get).toHaveBeenCalledTimes(2);
      expect(store.subscriptionsLoaded).toBe(false);
    });
  });

  describe('resync', () => {
    it('follows the healed mirror, with no copy of its own to re-apply', async () => {
      // A tab backgrounded across an episode change misses that state for
      // good; App.vue heals unifiedStore first, and the episode is read from it.
      publish(podcastSession(EPISODE('ep1')));
      apiCall.get.mockImplementation(async (url) => {
        if (url === '/api/audio/state') return ok(makeAudioState(podcastSession(EPISODE('ep2'), { speed: 1.5 })));
        return ok({ subscriptions: [] });
      });

      await useUnifiedAudioStore().resync();
      await store.resync();

      expect(store.currentEpisode.uuid).toBe('ep2');
      expect(store.playbackSpeed).toBe(1.5);
    });
  });

  describe('play', () => {
    it('flags the episode pending before the request resolves', async () => {
      apiCall.post.mockResolvedValueOnce(ok({ success: true }));

      const promise = store.play('ep1');
      expect(store.pendingEpisodeUuid).toBe('ep1');

      await promise;
      // Cleared by the WS confirmation, not here.
      expect(store.pendingEpisodeUuid).toBe('ep1');
    });

    it('clears the pending flag and throws when the command fails', async () => {
      // The generic control route answers a refused play_episode with a 400.
      apiCall.post.mockResolvedValueOnce(fail('Failed to load stream', 400));

      await expect(store.play('ep1')).rejects.toThrow();
      expect(store.pendingEpisodeUuid).toBeNull();
    });
  });

  // setSpeed has no test: it delegates to sendCommand and the applied value
  // arrives in the state, already covered by 'applies the speed the state
  // publishes' above.

  describe('search state', () => {
    it('records results, pagination and the term that produced them', () => {
      store.searchTerm = 'design';

      store.setSearchResults([{ uuid: 'p1' }], { podcasts: { total: 10, pages: 2 } });

      expect(store.searchResults.podcasts).toHaveLength(1);
      expect(store.searchPagination.podcasts.total).toBe(10);
      expect(store.lastSearchTerm).toBe('design');
      expect(store.hasSearched).toBe(true);
    });

    it('appends the next page and advances the page counter', () => {
      store.setSearchResults([{ uuid: 'p1' }], { podcasts: { total: 10, pages: 2 } });

      store.appendSearchResults([{ uuid: 'p2' }]);

      expect(store.searchResults.podcasts.map(p => p.uuid)).toEqual(['p1', 'p2']);
      expect(store.searchCurrentPage.podcasts).toBe(2);
    });

    it('clearSearch resets every search field', () => {
      store.searchTerm = 'design';
      store.setSearchResults([{ uuid: 'p1' }], { podcasts: { total: 10, pages: 2 } });

      store.clearSearch();

      expect(store.searchTerm).toBe('');
      expect(store.lastSearchTerm).toBe('');
      expect(store.searchResults.podcasts).toEqual([]);
      expect(store.searchCurrentPage.podcasts).toBe(1);
      expect(store.hasSearched).toBe(false);
    });

    it('clearSearch clears the catalogue-unavailable flag', () => {
      // Left set, the "catalogue unavailable" panel greets the next visit to
      // search before a single keystroke, and only a later success clears it.
      store.apiError = true;

      store.clearSearch();

      expect(store.apiError).toBe(false);
    });
  });

  // Two uncached iTunes queries can invert, so the response for a term the user
  // has already replaced must not land. The store cancels the previous request
  // and identifies the one it gets back; an in-flight search losing that
  // identity is the whole contract.
  describe('search supersession', () => {
    /** A response the test lands by hand, to drive two searches out of order. */
    const deferred = () => {
      let settle;
      const promise = new Promise((resolve) => { settle = resolve; });
      return { promise, settle };
    };

    const page = (uuid) => ok({
      podcasts: [{ uuid }],
      pagination: { podcasts: { total: 1, pages: 1 } },
    });

    it('aborts the request the previous term left in flight', async () => {
      const first = deferred();
      const second = deferred();
      apiCall.get.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);

      store.searchTerm = 'beat';
      const stale = store.search();
      store.searchTerm = 'beatles';
      const fresh = store.search();

      expect(apiCall.get.mock.calls[0][1].signal.aborted).toBe(true);
      expect(apiCall.get.mock.calls[1][1].signal.aborted).toBe(false);

      second.settle(page('beatles-1'));
      await fresh;
      first.settle(page('beat-1'));
      await stale;
    });

    it('keeps the newer results when the older response lands last', async () => {
      const first = deferred();
      const second = deferred();
      apiCall.get.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);

      store.searchTerm = 'beat';
      const stale = store.search();
      store.searchTerm = 'beatles';
      const fresh = store.search();

      second.settle(page('beatles-1'));
      await fresh;
      first.settle(page('beat-1'));
      await stale;

      expect(store.searchResults.podcasts.map(p => p.uuid)).toEqual(['beatles-1']);
      expect(store.lastSearchTerm).toBe('beatles');
      expect(store.searchLoading).toBe(false);
    });

    it('ignores a superseded api_error instead of raising the unavailable panel', async () => {
      // The part that actually hurts: the stale response is a *failed* one, so
      // it replaces a fresh result list with "catalogue unavailable".
      const first = deferred();
      const second = deferred();
      apiCall.get.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);

      store.searchTerm = 'beat';
      const stale = store.search();
      store.searchTerm = 'beatles';
      const fresh = store.search();

      second.settle(page('beatles-1'));
      await fresh;
      first.settle(ok({ api_error: true }));
      await stale;

      expect(store.apiError).toBe(false);
      expect(store.searchResults.podcasts.map(p => p.uuid)).toEqual(['beatles-1']);
    });

    it('drops a response that lands after clearSearch', async () => {
      const inFlight = deferred();
      apiCall.get.mockReturnValueOnce(inFlight.promise);

      store.searchTerm = 'beat';
      const stale = store.search();
      store.clearSearch();

      inFlight.settle(page('beat-1'));
      await stale;

      expect(store.searchResults.podcasts).toEqual([]);
      expect(store.hasSearched).toBe(false);
      expect(store.searchLoading).toBe(false);
    });
  });
});
