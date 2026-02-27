// frontend/tests/components/MultiroomItem.test.js
// Tests for MultiroomItem volume slider rendering (Story 3.5 - Task 2)
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { mount } from '@vue/test-utils';
import { setActivePinia, createPinia } from 'pinia';
import MultiroomItem from '@/components/multiroom/MultiroomItem.vue';

// Mock the stores
vi.mock('@/stores/settingsStore', () => ({
  useSettingsStore: vi.fn(() => ({
    volumeLimits: {
      min_db: -80,
      max_db: -21
    }
  }))
}));

vi.mock('@/stores/equalizerStore', () => ({
  useEqualizerStore: vi.fn(() => ({
    getClientSpeakerType: vi.fn(() => 'bookshelf')
  }))
}));

// Mock the composable
vi.mock('@/composables/useVolumeThrottle', () => ({
  useVolumeThrottle: vi.fn(() => ({
    throttledFn: vi.fn(),
    flush: vi.fn()
  })),
  useVolumeThrottleMap: vi.fn(() => ({
    getThrottledFn: vi.fn(() => vi.fn())
  }))
}));

describe('MultiroomItem - Volume Slider Rendering', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
  });

  describe('Task 2.1: Client volume slider display', () => {
    it('should display volume slider with current volume_db', async () => {
      const wrapper = mount(MultiroomItem, {
        props: {
          client: {
            id: 'client-1',
            mac_id: 'dc:a6:32:7e:d3:43',
            name: 'Test Client',
            equalizerVolume: -25,
            equalizerMuted: false
          },
          isLoading: false,
          isZone: false
        },
        global: {
          stubs: {
            RangeSlider: {
              template: '<div class="range-slider-stub" :data-model-value="modelValue" :data-min="min" :data-max="max"></div>',
              props: ['modelValue', 'min', 'max', 'step', 'disabled', 'muted', 'showValue', 'valueUnit']
            },
            Toggle: true,
            SvgIcon: true
          }
        }
      });

      const slider = wrapper.find('.range-slider-stub');
      expect(slider.exists()).toBe(true);
      expect(slider.attributes('data-model-value')).toBe('-25');
    });

    it('should clamp volume to min/max limits', async () => {
      const wrapper = mount(MultiroomItem, {
        props: {
          client: {
            id: 'client-1',
            mac_id: 'dc:a6:32:7e:d3:43',
            name: 'Test Client',
            equalizerVolume: -100, // Below min
            equalizerMuted: false
          },
          isLoading: false,
          isZone: false
        },
        global: {
          stubs: {
            RangeSlider: {
              template: '<div class="range-slider-stub" :data-model-value="modelValue"></div>',
              props: ['modelValue', 'min', 'max', 'step', 'disabled', 'muted', 'showValue', 'valueUnit']
            },
            Toggle: true,
            SvgIcon: true
          }
        }
      });

      const slider = wrapper.find('.range-slider-stub');
      // Should be clamped to min_db (-80)
      expect(parseInt(slider.attributes('data-model-value'))).toBe(-80);
    });
  });

  describe('Task 2.3: Slider min/max from settingsStore.volumeLimits', () => {
    it('should use volume limits from settings store', async () => {
      const wrapper = mount(MultiroomItem, {
        props: {
          client: {
            id: 'client-1',
            mac_id: 'dc:a6:32:7e:d3:43',
            name: 'Test Client',
            equalizerVolume: -30,
            equalizerMuted: false
          },
          isLoading: false,
          isZone: false
        },
        global: {
          stubs: {
            RangeSlider: {
              template: '<div class="range-slider-stub" :data-min="min" :data-max="max"></div>',
              props: ['modelValue', 'min', 'max', 'step', 'disabled', 'muted', 'showValue', 'valueUnit']
            },
            Toggle: true,
            SvgIcon: true
          }
        }
      });

      const slider = wrapper.find('.range-slider-stub');
      expect(slider.attributes('data-min')).toBe('-80');
      expect(slider.attributes('data-max')).toBe('-21');
    });
  });

  describe('Task 2.4: Offline client display', () => {
    it('should display "Hors ligne" for offline clients in expanded zone', async () => {
      const wrapper = mount(MultiroomItem, {
        props: {
          client: {
            id: 'zone-1',
            name: 'Test Zone',
            equalizerVolume: -30,
            equalizerMuted: false
          },
          isLoading: false,
          isZone: true,
          zoneClientDetails: [
            { mac_id: 'client-1', name: 'Online Client', equalizerVolume: -25, equalizerMuted: false, speakerType: 'bookshelf', online: true },
            { mac_id: 'client-2', name: 'Offline Client', equalizerVolume: -30, equalizerMuted: false, speakerType: 'bookshelf', online: false }
          ]
        },
        global: {
          stubs: {
            RangeSlider: {
              template: '<div class="range-slider-stub"></div>',
              props: ['modelValue', 'min', 'max', 'step', 'disabled', 'muted', 'showValue', 'valueUnit']
            },
            Toggle: true,
            SvgIcon: true
          }
        }
      });

      // Expand the zone first
      const expandButton = wrapper.find('.expand-button');
      await expandButton.trigger('click');

      // Check for offline indicator
      const offlineIndicator = wrapper.find('.client-offline');
      expect(offlineIndicator.exists()).toBe(true);
      expect(offlineIndicator.text()).toBe('Hors ligne');
    });
  });

  describe('Task 3: Mute toggle functionality', () => {
    describe('Task 3.1: Toggle shows inverted state', () => {
      it('should show mute toggle with inverted state (muted=true → enabled=false)', async () => {
        const wrapper = mount(MultiroomItem, {
          props: {
            client: {
              id: 'client-1',
              mac_id: 'dc:a6:32:7e:d3:43',
              name: 'Test Client',
              equalizerVolume: -25,
              equalizerMuted: true // Muted
            },
            isLoading: false,
            isZone: false
          },
          global: {
            stubs: {
              RangeSlider: true,
              Toggle: {
                template: '<div class="toggle-stub" :data-model-value="modelValue"></div>',
                props: ['modelValue', 'variant', 'disabled']
              },
              SvgIcon: true
            }
          }
        });

        const toggle = wrapper.find('.toggle-stub');
        expect(toggle.exists()).toBe(true);
        // Toggle shows "enabled" state, which is inverse of "muted"
        // equalizerMuted=true → modelValue should be false
        expect(toggle.attributes('data-model-value')).toBe('false');
      });

      it('should show enabled=true when not muted', async () => {
        const wrapper = mount(MultiroomItem, {
          props: {
            client: {
              id: 'client-1',
              mac_id: 'dc:a6:32:7e:d3:43',
              name: 'Test Client',
              equalizerVolume: -25,
              equalizerMuted: false // Not muted
            },
            isLoading: false,
            isZone: false
          },
          global: {
            stubs: {
              RangeSlider: true,
              Toggle: {
                template: '<div class="toggle-stub" :data-model-value="modelValue"></div>',
                props: ['modelValue', 'variant', 'disabled']
              },
              SvgIcon: true
            }
          }
        });

        const toggle = wrapper.find('.toggle-stub');
        // equalizerMuted=false → modelValue should be true (enabled)
        expect(toggle.attributes('data-model-value')).toBe('true');
      });
    });

    describe('Task 3.3: Standalone client mute emits event', () => {
      it('should emit mute-toggle event with correct parameters', async () => {
        const wrapper = mount(MultiroomItem, {
          props: {
            client: {
              id: 'client-1',
              mac_id: 'dc:a6:32:7e:d3:43',
              name: 'Test Client',
              equalizerVolume: -25,
              equalizerMuted: false
            },
            isLoading: false,
            isZone: false
          },
          global: {
            stubs: {
              RangeSlider: true,
              Toggle: {
                template: '<button class="toggle-stub" @click="$emit(\'change\', false)"></button>',
                props: ['modelValue', 'variant', 'disabled'],
                emits: ['change']
              },
              SvgIcon: true
            }
          }
        });

        const toggle = wrapper.find('.toggle-stub');
        await toggle.trigger('click');

        // Should emit mute-toggle with client id and new muted state
        expect(wrapper.emitted('mute-toggle')).toBeTruthy();
        expect(wrapper.emitted('mute-toggle')[0]).toEqual(['client-1', true]); // enabled=false → muted=true
      });
    });

    describe('Task 3.4: Visual feedback for muted state', () => {
      it('should apply muted class to client name when muted', async () => {
        const wrapper = mount(MultiroomItem, {
          props: {
            client: {
              id: 'client-1',
              mac_id: 'dc:a6:32:7e:d3:43',
              name: 'Test Client',
              equalizerVolume: -25,
              equalizerMuted: true
            },
            isLoading: false,
            isZone: false
          },
          global: {
            stubs: {
              RangeSlider: true,
              Toggle: true,
              SvgIcon: true
            }
          }
        });

        const clientName = wrapper.find('.client-name');
        expect(clientName.classes()).toContain('muted');
      });

      it('should apply muted class to volume control when muted', async () => {
        const wrapper = mount(MultiroomItem, {
          props: {
            client: {
              id: 'client-1',
              mac_id: 'dc:a6:32:7e:d3:43',
              name: 'Test Client',
              equalizerVolume: -25,
              equalizerMuted: true
            },
            isLoading: false,
            isZone: false
          },
          global: {
            stubs: {
              RangeSlider: true,
              Toggle: true,
              SvgIcon: true
            }
          }
        });

        const volumeControl = wrapper.find('.volume-control');
        expect(volumeControl.classes()).toContain('muted');
      });
    });
  });
});
