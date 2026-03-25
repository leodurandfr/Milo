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

    it('should have zero position and duration', () => {
      expect(store.currentPosition).toBe(0);
      expect(store.currentDuration).toBe(0);
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

  describe('computed properties', () => {
    it('should compute isPlaying from unifiedStore', () => {
      expect(store.isPlaying).toBe(false);

      // Simulate podcast playing
      unifiedStore.systemState.active_source = 'podcast';
      unifiedStore.systemState.metadata = { is_playing: true };

      expect(store.isPlaying).toBe(true);
    });

    it('should compute isBuffering from unifiedStore', () => {
      unifiedStore.systemState.active_source = 'podcast';
      unifiedStore.systemState.metadata = { is_buffering: true };

      expect(store.isBuffering).toBe(true);
    });

    it('should compute progressPercentage correctly', () => {
      store.currentPosition = 30;
      store.currentDuration = 100;

      expect(store.progressPercentage).toBe(30);
    });

    it('should return 0 for progressPercentage when duration is 0', () => {
      store.currentPosition = 30;
      store.currentDuration = 0;

      expect(store.progressPercentage).toBe(0);
    });

    it('should compute hasCurrentEpisode correctly', () => {
      expect(store.hasCurrentEpisode).toBe(false);

      store.currentEpisode = { uuid: 'ep1', name: 'Episode 1' };
      expect(store.hasCurrentEpisode).toBe(true);
    });
  });

  describe('progress cache', () => {
    it('should store episode progress', () => {
      store.setEpisodeProgress('ep1', 120, 600);

      const progress = store.getEpisodeProgress('ep1');
      expect(progress).not.toBeNull();
      expect(progress.position).toBe(120);
      expect(progress.duration).toBe(600);
      expect(progress.last_played).toBeDefined();
    });

    it('should return null for unknown episode', () => {
      const progress = store.getEpisodeProgress('unknown');
      expect(progress).toBeNull();
    });

    it('should update existing progress', () => {
      store.setEpisodeProgress('ep1', 100, 600);
      store.setEpisodeProgress('ep1', 200, 600);

      const progress = store.getEpisodeProgress('ep1');
      expect(progress.position).toBe(200);
    });

    it('should enforce MAX_PROGRESS_ENTRIES limit', () => {
      // Add 250 entries (limit is 200)
      for (let i = 0; i < 250; i++) {
        store.setEpisodeProgress(`ep${i}`, i * 10, 600);
      }

      // Should be capped at 200
      expect(store.progressCache.size).toBeLessThanOrEqual(200);
    });

    it('should preserve current episode in cache during eviction', () => {
      // Set a current episode
      store.currentEpisode = { uuid: 'current-ep' };
      store.setEpisodeProgress('current-ep', 100, 600);

      // Add many entries to trigger eviction
      for (let i = 0; i < 250; i++) {
        store.setEpisodeProgress(`ep${i}`, i * 10, 600);
      }

      // Current episode should still be in cache
      expect(store.getEpisodeProgress('current-ep')).not.toBeNull();
    });

    it('should enrich episodes with progress', () => {
      // Pre-populate cache
      store.setEpisodeProgress('ep1', 120, 600);

      const episodes = [
        { uuid: 'ep1', name: 'Episode 1', playback_progress: { position: 120, duration: 600 } },
        { uuid: 'ep2', name: 'Episode 2' }
      ];

      store.enrichEpisodesWithProgress(episodes);

      expect(store.getEpisodeProgress('ep1')).not.toBeNull();
      expect(store.getEpisodeProgress('ep2')).toBeNull();
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

    describe('seek', () => {
      it('should call seek API with floored position', async () => {
        axios.post.mockResolvedValueOnce({ data: {} });

        await store.seek(125.7);

        expect(axios.post).toHaveBeenCalledWith('/api/podcast/seek', { position: 125 });
        expect(store.currentPosition).toBe(125.7);
      });
    });

    describe('stop', () => {
      it('should call stop API and clear state', async () => {
        axios.post.mockResolvedValueOnce({ data: {} });
        store.currentEpisode = { uuid: 'ep1' };
        store.displayEpisode = { uuid: 'ep1' };
        store.currentPosition = 100;

        await store.stop();

        expect(axios.post).toHaveBeenCalledWith('/api/podcast/stop');
        expect(store.currentEpisode).toBeNull();
        expect(store.displayEpisode).toBeNull();
        expect(store.currentPosition).toBe(0);
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
    describe('handleStateUpdate', () => {
      it('should update current episode from metadata', () => {
        const episode = { uuid: 'ep1', name: 'Test Episode' };

        store.handleStateUpdate({
          current_episode: episode,
          position: 60,
          duration: 300
        });

        expect(store.currentEpisode).toEqual(episode);
        expect(store.displayEpisode).toEqual(episode);
        expect(store.currentPosition).toBe(60);
        expect(store.currentDuration).toBe(300);
      });

      it('should clear pending state when episode confirmed', () => {
        store.pendingEpisodeUuid = 'ep1';

        store.handleStateUpdate({
          current_episode: { uuid: 'ep1', name: 'Episode' }
        });

        expect(store.pendingEpisodeUuid).toBeNull();
      });

      it('should update progress cache from metadata', () => {
        store.handleStateUpdate({
          episode_uuid: 'ep1',
          position: 120,
          duration: 600
        });

        const progress = store.getEpisodeProgress('ep1');
        expect(progress.position).toBe(120);
        expect(progress.duration).toBe(600);
      });

      it('should handle episode_ended event', () => {
        store.currentEpisode = { uuid: 'ep1' };
        store.displayEpisode = { uuid: 'ep1' };
        store.currentPosition = 590;

        store.handleStateUpdate({ episode_ended: true });

        expect(store.currentEpisode).toBeNull();
        expect(store.currentPosition).toBe(0);
        // displayEpisode preserved for animation
        expect(store.displayEpisode).not.toBeNull();
      });
    });

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

      it('should process podcast state_changed events', () => {
        store.handleSourceEvent({
          origin: 'podcast',
          type: 'state_changed',
          data: {
            current_episode: { uuid: 'ep1', name: 'Episode' },
            position: 30
          }
        });

        expect(store.currentEpisode.uuid).toBe('ep1');
        expect(store.currentPosition).toBe(30);
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

  describe('clearState', () => {
    it('should clear playback state but preserve progress cache', () => {
      store.currentEpisode = { uuid: 'ep1' };
      store.displayEpisode = { uuid: 'ep1' };
      store.currentPosition = 100;
      store.setEpisodeProgress('ep1', 100, 600);

      store.clearState();

      expect(store.currentEpisode).toBeNull();
      expect(store.displayEpisode).toBeNull();
      expect(store.currentPosition).toBe(0);
      // Progress cache should be preserved
      expect(store.getEpisodeProgress('ep1')).not.toBeNull();
    });
  });
});
