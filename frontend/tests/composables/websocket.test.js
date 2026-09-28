// frontend/tests/composables/websocket.test.js
/**
 * The grace delay that gates the "connection lost" banner.
 *
 * iOS suspends the app and kills the socket on every backgrounding, so a wake
 * is always a close followed by a reopen ~100 ms later. What breaks if this
 * stops holding: the banner blinks on every return to the app (iOS, Safari and
 * the Mac app alike) — or, the other way round, a genuine outage stays silent.
 * The consumer is App.vue's `showConnectionLost`.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { defineComponent } from 'vue';
import { mount } from '@vue/test-utils';
import useWebSocket from '@/services/websocket';

class FakeSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSED = 3;
  static instances = [];

  constructor(url) {
    this.url = url;
    this.readyState = FakeSocket.CONNECTING;
    FakeSocket.instances.push(this);
  }

  send() {}

  // The browser dispatches the close event after close() returns, and the
  // ordering matters here: a teardown's cleanup runs before it lands
  close() {
    if (this.readyState === FakeSocket.CLOSED) return;
    this.readyState = FakeSocket.CLOSED;
    setTimeout(() => this.onclose?.(), 0);
  }

  // What the server end does, which no FakeSocket call would otherwise trigger
  accept() {
    this.readyState = FakeSocket.OPEN;
    this.onopen?.();
  }

  static get last() {
    return FakeSocket.instances[FakeSocket.instances.length - 1];
  }
}

function setHidden(hidden) {
  Object.defineProperty(document, 'hidden', { configurable: true, get: () => hidden });
  document.dispatchEvent(new Event('visibilitychange'));
}

const Host = defineComponent({
  setup: () => useWebSocket(),
  render: () => null,
});

let wrapper;
let ws;

// close() only marks the socket; this is the event landing on the handler
function deliverClose() {
  vi.advanceTimersByTime(0);
}

beforeEach(() => {
  vi.useFakeTimers();
  FakeSocket.instances = [];
  global.WebSocket = FakeSocket;
  setHidden(false);
  wrapper = mount(Host);
  ws = wrapper.vm;
  FakeSocket.last.accept();
});

afterEach(() => {
  if (!wrapper.vm.$.isUnmounted) wrapper.unmount(); // zero subscribers: closes the socket, resets the banner
  vi.useRealTimers();
});

describe('showDisconnectedBanner', () => {
  it('stays down through a background/foreground cycle that reconnects', () => {
    setHidden(true);
    FakeSocket.last.close(); // iOS kills the socket while suspended
    deliverClose();

    setHidden(false);
    expect(ws.showDisconnectedBanner).toBe(false);

    vi.advanceTimersByTime(100);
    FakeSocket.last.accept(); // the visibility handler's reconnect lands

    vi.advanceTimersByTime(10000);
    expect(ws.showDisconnectedBanner).toBe(false);
  });

  it('comes up when the socket stays down past the grace delay', () => {
    FakeSocket.last.close();
    deliverClose();

    vi.advanceTimersByTime(2000);
    expect(ws.showDisconnectedBanner).toBe(false);

    vi.advanceTimersByTime(3000);
    expect(ws.showDisconnectedBanner).toBe(true);
  });

  it('goes back down as soon as a connection is accepted', () => {
    FakeSocket.last.close();
    deliverClose();
    vi.advanceTimersByTime(5000);
    expect(ws.showDisconnectedBanner).toBe(true);

    FakeSocket.last.accept();
    expect(ws.showDisconnectedBanner).toBe(false);
  });

  it('does not arm while the document is hidden', () => {
    setHidden(true);
    FakeSocket.last.close();
    deliverClose();

    vi.advanceTimersByTime(30000);
    expect(ws.showDisconnectedBanner).toBe(false);

    // and the return re-arms it, since the socket is still down
    setHidden(false);
    vi.advanceTimersByTime(30000);
    expect(ws.showDisconnectedBanner).toBe(true);
  });

  it('stays down when the close lands after the last subscriber is gone', () => {
    wrapper.unmount(); // teardown closes the socket and resets the banner...

    vi.advanceTimersByTime(30000); // ...and the close event lands afterwards

    expect(ws.showDisconnectedBanner).toBe(false);
  });
});

/**
 * Whether the first connection of a page load triggers App.vue's resyncStores.
 *
 * The kiosk's browser loads the page before the backend listens after a reboot:
 * the boot fetch of every store answers 502 and the socket's first attempt fails.
 * What breaks if this stops holding: the kiosk comes up in English with no radio
 * favorites, podcast subscriptions or settings, until someone reloads the page.
 * Each case imports a fresh module, since the connection history is a singleton.
 */
describe('onReconnect on the first connection of a page load', () => {
  async function freshHost() {
    vi.resetModules();
    // The fresh module graph holds a fresh logger too, back at its DEV level:
    // silenced here as tests/setup.js silences the first one.
    const { logger } = await import('@/services/logger');
    logger.setLevel('none');
    const { default: freshUseWebSocket } = await import('@/services/websocket');
    const onReconnect = vi.fn();
    const host = mount(defineComponent({
      setup() {
        freshUseWebSocket().onReconnect(onReconnect);
      },
      render: () => null,
    }));
    return { host, onReconnect };
  }

  it('fires when the backend answered only after the page loaded', async () => {
    const { host, onReconnect } = await freshHost();

    FakeSocket.last.close(); // nginx answers 502: the backend is not up yet
    deliverClose();
    vi.advanceTimersByTime(1250); // the first backoff step (1 s, ±25 %) opens a new socket
    FakeSocket.last.accept();

    expect(onReconnect).toHaveBeenCalledTimes(1);
    host.unmount();
  });

  it('does not fire when the first attempt connects', async () => {
    const { host, onReconnect } = await freshHost();

    FakeSocket.last.accept();

    expect(onReconnect).not.toHaveBeenCalled();
    host.unmount();
  });

  it('fires once for each later reconnection', async () => {
    const { host, onReconnect } = await freshHost();
    FakeSocket.last.accept();

    FakeSocket.last.close(); // a backend restart
    deliverClose();
    vi.advanceTimersByTime(1250);
    FakeSocket.last.accept();

    expect(onReconnect).toHaveBeenCalledTimes(1);
    host.unmount();
  });

  it('reconnects when the tab returns after a first attempt failed while hidden', async () => {
    // An iOS app launched then backgrounded at once, or a tab opened behind
    // another: a close while hidden schedules no retry, so the return to the
    // tab is the only thing left to reconnect — and it must resync too.
    const { host, onReconnect } = await freshHost();
    const failed = FakeSocket.last;
    setHidden(true);
    failed.close();
    deliverClose();

    vi.advanceTimersByTime(60000);
    expect(FakeSocket.last).toBe(failed);

    setHidden(false);
    expect(FakeSocket.last).not.toBe(failed);
    FakeSocket.last.accept();

    expect(onReconnect).toHaveBeenCalledTimes(1);
    host.unmount();
  });
});
