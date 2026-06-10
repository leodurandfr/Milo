// frontend/tests/stores/podcastStore.test.js
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { usePodcastStore } from '@/stores/podcastStore';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import axios from 'axios';

// Mock axios
vi.mock('axios');

describe('podcastStore', () => {
  let store;
  let unifiedStore;

  beforeEach(() => {
    // Fresh stores for each test (Pinia reset in setup.js)
    unifiedStore = useUnifiedAudioStore();
    store = usePodcastStore();
    vi.clearAllMocks();
  });

  describe('initial state', () => {
    it('should have null current episode', () => {
      expect(store.currentEpisode).toBeNull();
      expect(store.displayEpisode).toBeNull();
    });

    it('should have default playback speed of 1.0', () => {
      expect(store.playbackSpeed).toBe(1.0);
    });

    it('should have empty progress cache', () => {
      expect(store.progressCache.size).toBe(0);
    });

    it('should have empty subscriptions', () => {
      expect(store.subscriptions).toEqual([]);
      expect(store.hasSubscriptions).toBe(false);
    });
  });

  describe('progress cache', () => {
    it('should return null for unknown episode', () => {
      const progress = store.getEpisodeProgress('unknown');
      expect(progress).toBeNull();
    });
  });

  describe('playback actions', () => {
    describe('play', () => {
      it('should call API and set pending state', async () => {
        axios.post.mockResolvedValueOnce({ data: { success: true } });

        const playPromise = store.play('ep123');

        // Should be pending immediately
        expect(store.pendingEpisodeUuid).toBe('ep123');

        await playPromise;

        expect(axios.post).toHaveBeenCalledWith('/api/podcast/play', { episode_uuid: 'ep123' });
      });

      it('should clear pending state on error', async () => {
        axios.post.mockRejectedValueOnce(new Error('Network error'));

        await expect(store.play('ep123')).rejects.toThrow();

        expect(store.pendingEpisodeUuid).toBeNull();
      });
    });

    describe('pause', () => {
      it('should call pause API', async () => {
        axios.post.mockResolvedValueOnce({ data: {} });

        await store.pause();

        expect(axios.post).toHaveBeenCalledWith('/api/podcast/pause');
      });
    });

    describe('resume', () => {
      it('should call resume API', async () => {
        axios.post.mockResolvedValueOnce({ data: {} });

        await store.resume();

        expect(axios.post).toHaveBeenCalledWith('/api/podcast/resume');
      });
    });

    describe('setSpeed', () => {
      it('should call speed API and update state', async () => {
        axios.post.mockResolvedValueOnce({ data: { success: true, speed: 1.5 } });

        await store.setSpeed(1.5);

        expect(axios.post).toHaveBeenCalledWith('/api/podcast/speed', { speed: 1.5 });
        expect(store.playbackSpeed).toBe(1.5);
      });
    });
  });

  describe('WebSocket handlers', () => {
    describe('handleSourceEvent', () => {
      it('should ignore non-podcast events', () => {
        const originalEpisode = store.currentEpisode;

        store.handleSourceEvent({
          origin: 'radio',
          type: 'state_changed',
          data: { current_episode: { uuid: 'radio-ep' } }
        });

        expect(store.currentEpisode).toBe(originalEpisode);
      });
    });
  });

  describe('subscriptions', () => {
    describe('addSubscription', () => {
      it('should add new subscription', () => {
        const sub = { uuid: 'pod1', name: 'Podcast 1' };

        store.addSubscription(sub);

        expect(store.subscriptions).toContainEqual(sub);
        expect(store.hasSubscriptions).toBe(true);
      });

      it('should not add duplicate subscription', () => {
        const sub = { uuid: 'pod1', name: 'Podcast 1' };

        store.addSubscription(sub);
        store.addSubscription(sub);

        expect(store.subscriptions.length).toBe(1);
      });
    });

    describe('removeSubscription', () => {
      it('should remove subscription by uuid', () => {
        // Use addSubscription instead of direct assignment (subscriptions is now a computed from Map)
        store.addSubscription({ uuid: 'pod1', name: 'Podcast 1' });
        store.addSubscription({ uuid: 'pod2', name: 'Podcast 2' });

        store.removeSubscription('pod1');

        expect(store.subscriptions).toHaveLength(1);
        expect(store.subscriptions[0].uuid).toBe('pod2');
      });

      it('should also remove episodes from latestSubscriptionEpisodes', () => {
        // Use addSubscription instead of direct assignment
        store.addSubscription({ uuid: 'pod1', name: 'Podcast 1' });
        store.latestSubscriptionEpisodes = [
          { uuid: 'ep1', podcast: { uuid: 'pod1' } },
          { uuid: 'ep2', podcast: { uuid: 'pod2' } }
        ];

        store.removeSubscription('pod1');

        expect(store.latestSubscriptionEpisodes).toHaveLength(1);
        expect(store.latestSubscriptionEpisodes[0].uuid).toBe('ep2');
      });
    });
  });

  describe('search state', () => {
    it('should set search results', () => {
      const podcasts = [{ uuid: 'p1' }];
      const episodes = [{ uuid: 'e1' }];
      const pagination = {
        podcasts: { total: 10, pages: 2 },
        episodes: { total: 5, pages: 1 }
      };

      store.setSearchResults(podcasts, episodes, pagination);

      expect(store.searchResults.podcasts).toEqual(podcasts);
      expect(store.searchResults.episodes).toEqual(episodes);
      expect(store.hasSearched).toBe(true);
    });

    it('should clear search state', () => {
      store.searchTerm = 'test';
      store.searchResults = { podcasts: [{}], episodes: [{}] };
      store.hasSearched = true;

      store.clearSearch();

      expect(store.searchTerm).toBe('');
      expect(store.searchResults.podcasts).toEqual([]);
      expect(store.searchResults.episodes).toEqual([]);
      expect(store.hasSearched).toBe(false);
    });
  });
});
