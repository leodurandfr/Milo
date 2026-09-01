// frontend/tests/stores/settingsStore.test.js
/**
 * The kiosk's ui_scale is applied as a transform on #app, which also sizes
 * itself 100vh/scale so the scaled result covers the panel. Everything under it
 * is therefore laid out in FEWER pixels than a viewport unit counts, and CSS has
 * no way to discover the factor — so applyUiScale publishes it as --ui-scale,
 * and the Artists index rail sizes its band with `100dvh / var(--ui-scale)`.
 *
 * Publish it without applying the transform (or the reverse) and the rail's band
 * is wrong by 15% on the panel and right on every dev machine, where the scale
 * is 1. That is what this pins: the two always move together.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { useSettingsStore } from '@/stores/settingsStore';
import { resetApiCallMock } from '../helpers/apiCallMock';

vi.mock('@/services/apiCall', () => import('../helpers/apiCallMock'));

// isKiosk() is hostname === 'localhost', which is what the test environment
// serves — the same branch the panel takes.
describe('settingsStore — ui_scale published for the CSS under it', () => {
  let store;
  let app;

  const transformScale = () => app.style.transform.match(/scale\(([\d.]+)\)/)?.[1];

  beforeEach(() => {
    resetApiCallMock();
    app = document.createElement('div');
    app.id = 'app';
    document.body.appendChild(app);
    store = useSettingsStore();
  });

  it('publishes the very factor it scales by', () => {
    store.updateScreenUiScale({ ui_scale: 1.15 });

    expect(transformScale()).toBe('1.15');
    expect(app.style.getPropertyValue('--ui-scale')).toBe(transformScale());
    // The height it pairs with, which is what makes a viewport unit overshoot.
    expect(app.style.height).toBe('calc(100vh / 1.15)');
  });

  it('withdraws it with the transform, so nothing divides by a scale that is gone', () => {
    store.updateScreenUiScale({ ui_scale: 1.15 });
    store.updateScreenUiScale({ ui_scale: 1.0 });

    expect(app.style.transform).toBe('');
    expect(app.style.getPropertyValue('--ui-scale')).toBe('');
  });
});
