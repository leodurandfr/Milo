// frontend/tests/composables/useTimer.test.js
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { defineComponent, h } from 'vue';
import { mount } from '@vue/test-utils';
import { useTimer } from '@/composables/useTimer';

function makeHost(setup) {
  return defineComponent({
    setup,
    render: () => h('div'),
  });
}

describe('useTimer', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('setTimeout fires the callback after the delay', () => {
    const cb = vi.fn();
    let timer;
    const Host = makeHost(() => {
      timer = useTimer();
      timer.setTimeout(cb, 500);
    });
    mount(Host);

    expect(cb).not.toHaveBeenCalled();
    vi.advanceTimersByTime(499);
    expect(cb).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(cb).toHaveBeenCalledTimes(1);
  });

  it('setInterval fires the callback periodically', () => {
    const cb = vi.fn();
    let timer;
    const Host = makeHost(() => {
      timer = useTimer();
      timer.setInterval(cb, 100);
    });
    mount(Host);

    vi.advanceTimersByTime(350);
    expect(cb).toHaveBeenCalledTimes(3);
  });

  it('clear(id) cancels a specific timer', () => {
    const cb = vi.fn();
    let id;
    const Host = makeHost(() => {
      const timer = useTimer();
      id = timer.setTimeout(cb, 500);
      timer.clear(id);
    });
    mount(Host);

    vi.advanceTimersByTime(1000);
    expect(cb).not.toHaveBeenCalled();
  });

  it('clear(id) cancels an interval', () => {
    const cb = vi.fn();
    const Host = makeHost(() => {
      const timer = useTimer();
      const id = timer.setInterval(cb, 100);
      timer.clear(id);
    });
    mount(Host);

    vi.advanceTimersByTime(500);
    expect(cb).not.toHaveBeenCalled();
  });

  it('clearAll() cancels every active timer', () => {
    const cbTimeout = vi.fn();
    const cbInterval = vi.fn();
    const Host = makeHost(() => {
      const timer = useTimer();
      timer.setTimeout(cbTimeout, 500);
      timer.setInterval(cbInterval, 100);
      timer.clearAll();
    });
    mount(Host);

    vi.advanceTimersByTime(1000);
    expect(cbTimeout).not.toHaveBeenCalled();
    expect(cbInterval).not.toHaveBeenCalled();
  });

  it('onUnmounted clears every active timer automatically', () => {
    const cbTimeout = vi.fn();
    const cbInterval = vi.fn();
    const Host = makeHost(() => {
      const timer = useTimer();
      timer.setTimeout(cbTimeout, 500);
      timer.setInterval(cbInterval, 100);
    });
    const wrapper = mount(Host);
    wrapper.unmount();

    vi.advanceTimersByTime(1000);
    expect(cbTimeout).not.toHaveBeenCalled();
    expect(cbInterval).not.toHaveBeenCalled();
  });

  it('setTimeout removes the handle from the registry after firing', () => {
    const cb = vi.fn();
    let timer;
    let id;
    const Host = makeHost(() => {
      timer = useTimer();
      id = timer.setTimeout(cb, 200);
    });
    mount(Host);

    vi.advanceTimersByTime(200);
    expect(cb).toHaveBeenCalledTimes(1);
    timer.clear(id);
    expect(cb).toHaveBeenCalledTimes(1);
  });
});
