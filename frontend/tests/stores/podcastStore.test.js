// frontend/tests/stores/podcastStore.test.js
/**
 * podcastStore owns the episode progress cache (including its LRU bound and the
 * ms→s wire conversion), the subscriptions Map, and the optimistic "pending
 * episode" state. Those are the parts a regression can actually break.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { usePodcastStore } from '@/stores/podcastStore';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { apiCall } from '@/services/apiCall';
import { resetApiCallMock, ok, fail } from '../helpers/apiCallMock';

vi.mock('@/services/apiCall', () => import('../helpers/apiCallMock'));

const EPISODE = (uuid, extra = {}) => ({ uuid, title: `Episode ${uuid}`, ...extra });

/** A source.state_changed event as App.vue forwards it. */
const sourceEvent = (metadata, origin = 'podcast') => ({
  origin,
  type: 'state_changed',
  data: { metadata },
});

describe('podcastStore', () => {
  let store;

  beforeEach(() => {
    resetApiCallMock();
    store = usePodcastStore();
  });

  describe('metadata ingestion', () => {
    it('adopts the current episode and clears the pending flag for it', () => {
      apiCall.post.mockResolvedValueOnce(ok({ success: true }));
      store.play('ep1');
      expect(store.pendingEpisodeUuid).toBe('ep1');

      store.handleSourceEvent(sourceEvent({ current_episode: EPISODE('ep1') }));

      expect(store.currentEpisode.uuid).toBe('ep1');
      expect(store.displayEpisode.uuid).toBe('ep1');
      expect(store.pendingEpisodeUuid).toBeNull();
    });

    it('keeps the pending flag when a different episode confirms', () => {
      apiCall.post.mockResolvedValueOnce(ok({ success: true }));
      store.play('ep1');

      store.handleSourceEvent(sourceEvent({ current_episode: EPISODE('ep2') }));

      expect(store.pendingEpisodeUuid).toBe('ep1');
    });

    it('ignores events originating from another source', () => {
      store.handleSourceEvent(sourceEvent({ current_episode: EPISODE('ep1') }, 'radio'));

      expect(store.currentEpisode).toBeNull();
    });

    it('converts the millisecond wire position into seconds for the cache', () => {
      // Backend emits ms (shared wire convention); EpisodeCard reads seconds.
      store.handleSourceEvent(sourceEvent({
        episode_uuid: 'ep1',
        position: 65_400,
        duration: 1_800_000,
      }));

      expect(store.getEpisodeProgress('ep1')).toMatchObject({
        position: 65,
        duration: 1800,
      });
    });

    it('drops the current episode when the source goes idle without one', () => {
      // Every stop that is not a natural end — auto-stop after pause, explicit
      // stop, mpv gone — publishes {is_playing, is_buffering} and nothing else.
      // Only episode_ended carries a uuid, so an absent one means "no episode
      // loaded"; without this, useEpisodePlaybackStatus keeps flagging the
      // stopped episode as current for the rest of the session.
      store.handleSourceEvent(sourceEvent({ current_episode: EPISODE('ep1') }));
      expect(store.currentEpisode.uuid).toBe('ep1');

      store.handleSourceEvent(sourceEvent({ is_playing: false, is_buffering: false }));

      expect(store.currentEpisode).toBeNull();
      // displayEpisode still belongs to the player's fade-out, which clears it.
      expect(store.displayEpisode.uuid).toBe('ep1');
    });

    it('applies a playback speed change pushed by the backend', () => {
      store.handleSourceEvent(sourceEvent({ playback_speed: 1.5 }));

      expect(store.playbackSpeed).toBe(1.5);
    });

    it('returns null progress for an episode never played', () => {
      expect(store.getEpisodeProgress('unknown')).toBeNull();
    });
  });

  describe('episode end', () => {
    it('marks the finished episode completed and clears currentEpisode', () => {
      store.handleSourceEvent(sourceEvent({
        episode_uuid: 'ep1',
        position: 10_000,
        duration: 60_000,
      }));

      store.handleSourceEvent(sourceEvent({
        episode_ended: true,
        completed: true,
        episode_uuid: 'ep1',
      }));

      expect(store.currentEpisode).toBeNull();
      const progress = store.getEpisodeProgress('ep1');
      expect(progress.completed).toBe(true);
      // Merged, not replaced: the card still shows a duration.
      expect(progress.duration).toBe(60);
    });

    it('preserves displayEpisode through the fade-out, until explicitly cleared', () => {
      store.handleSourceEvent(sourceEvent({ current_episode: EPISODE('ep1') }));

      store.handleSourceEvent(sourceEvent({ episode_ended: true, episode_uuid: 'ep1' }));
      expect(store.displayEpisode.uuid).toBe('ep1');

      store.clearDisplayEpisode();
      expect(store.displayEpisode).toBeNull();
    });

    it('ignores every other field carried by the episode_ended event', () => {
      // The handler returns early: a trailing position from the ended episode
      // must not land in the cache as fresh progress.
      store.handleSourceEvent(sourceEvent({
        episode_ended: true,
        episode_uuid: 'ep1',
        position: 999_000,
        duration: 1_000_000,
        playback_speed: 2.0,
      }));

      expect(store.getEpisodeProgress('ep1')).toBeNull();
      expect(store.playbackSpeed).toBe(1.0);
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
      store.handleSourceEvent(sourceEvent({ current_episode: EPISODE('ep0') }));
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

  describe('resync heals the now-playing slice', () => {
    // _applyMetadata is the only writer of currentEpisode/displayEpisode/
    // playbackSpeed, and source/state_changed its only trigger. A tab
    // backgrounded across an episode change misses that delta for good, so
    // resync() must re-apply the snapshot App.vue has just healed the mirror
    // with — otherwise the player paints episode A over episode B's progress.
    const healMirror = (metadata, activeSource = 'podcast') => {
      useUnifiedAudioStore().updateState({
        data: {
          full_state: {
            active_source: activeSource,
            source_state: 'active',
            transitioning: false,
            multiroom_enabled: false,
            equalizer_effects_enabled: true,
            metadata,
          },
        },
      });
    };

    beforeEach(() => {
      apiCall.get.mockResolvedValue(ok({ subscriptions: [] }));
    });

    it('adopts the episode the missed delta carried', async () => {
      store.handleSourceEvent(sourceEvent({ current_episode: EPISODE('ep1') }));
      healMirror({ current_episode: EPISODE('ep2'), playback_speed: 1.5 });

      await store.resync();

      expect(store.currentEpisode.uuid).toBe('ep2');
      expect(store.displayEpisode.uuid).toBe('ep2');
      expect(store.playbackSpeed).toBe(1.5);
    });

    it('drops an episode that stopped while the tab was backgrounded', async () => {
      store.handleSourceEvent(sourceEvent({ current_episode: EPISODE('ep1') }));
      healMirror({ is_playing: false });

      await store.resync();

      expect(store.currentEpisode).toBeNull();
      // The player owns displayEpisode until its fade-out ends.
      expect(store.displayEpisode.uuid).toBe('ep1');
    });

    it('leaves the slice alone when podcast is not the active source', async () => {
      store.handleSourceEvent(sourceEvent({ current_episode: EPISODE('ep1') }));
      healMirror({ title: 'Some track' }, 'spotify');

      await store.resync();

      expect(store.currentEpisode.uuid).toBe('ep1');
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

    it('clears the pending flag and throws when the request fails', async () => {
      apiCall.post.mockResolvedValueOnce(fail('Episode unavailable'));

      await expect(store.play('ep1')).rejects.toThrow('Episode unavailable');
      expect(store.pendingEpisodeUuid).toBeNull();
    });

    it('clears the pending flag when the backend answers success: false', async () => {
      apiCall.post.mockResolvedValueOnce(ok({ success: false }));

      await expect(store.play('ep1')).rejects.toThrow();
      expect(store.pendingEpisodeUuid).toBeNull();
    });
  });

  // setSpeed has no test: it delegates to sendCommand and the applied value
  // arrives on the metadata broadcast, already covered by 'applies a playback
  // speed change pushed by the backend' above.

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
});
