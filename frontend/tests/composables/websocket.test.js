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
