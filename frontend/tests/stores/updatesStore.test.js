// frontend/tests/stores/updatesStore.test.js
/**
 * updatesStore mirrors program/satellite update state, fed by `programs.*` WS
 * deltas and reconciled from GET /api/programs.
 *
 * Pinned here: the resync gate — App.vue resyncs every delta store on
 * reconnect and on every tab return; where a Milō update stands is read every
 * time, through a read that probes nothing, while the program list (a version
 * probe per program) and the satellites only once a view has opened them. What happens to a satellite
 * between the moment it reports an update finished and the moment its own API
 * answers again — the window that left a row showing a skeleton for a fetch
 * nobody would ever make. The pieces of update state the rows read back: how
 * long "completed" lasts, and which release an in-flight install is putting
 * on. And where an update of Milō itself stands, which the whole app draws.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { setActivePinia, createPinia } from 'pinia';
import { useUpdatesStore } from '@/stores/updatesStore';
import { useMultiroomStore } from '@/stores/multiroomStore';
import { apiCall } from '@/services/apiCall';
import { resetApiCallMock, ok, fail } from '../helpers/apiCallMock';

vi.mock('@/services/apiCall', () => import('../helpers/apiCallMock'));

describe('updatesStore recheck', () => {
  let store;

  beforeEach(() => {
    resetApiCallMock();
    store = useUpdatesStore();
  });

  it('a Retry after a failed check asks the backend to ask GitHub again, and keeps the rows drawn', async () => {
    // Without the recheck the backend answers the failure it remembers for five
    // minutes, and the Retry repeats what it was pressed to get past; flipping
    // the loading flag would put the whole list back to skeletons for it.
    apiCall.get.mockResolvedValue(ok({ programs: { milo: { check_failed: true } }, active_updates: [] }));
    apiCall.post.mockResolvedValue(ok({ programs: {}, active_updates: [] }));
    await store.loadLocalPrograms();
    apiCall.get.mockClear();

    const load = store.loadLocalPrograms({ recheck: true });
    expect(store.localProgramsLoading).toBe(false);
    await load;

    expect(apiCall.post.mock.calls.map(([url]) => url)).toEqual(['/api/programs/recheck']);
    expect(apiCall.get).not.toHaveBeenCalled();
  });

  it('a refused Retry keeps the rows drawn: only a read that never got a list replaces the screen', async () => {
    // A recheck that failed used to flip the read error, which swaps the whole
    // list for the "could not load" card and throws the server's detail away;
    // the row's own refusal says it instead.
    apiCall.get.mockResolvedValue(ok({ programs: { milo: { check_failed: true } }, active_updates: [] }));
    await store.loadLocalPrograms();
    apiCall.post.mockResolvedValue(fail('GitHub unreachable', 502));

    expect(await store.loadLocalPrograms({ recheck: true })).toBe(false);

    expect(store.localProgramsError).toBe(false);
    expect(Object.keys(store.localPrograms)).toEqual(['milo']);

    apiCall.get.mockResolvedValue(fail('backend down', 500));
    await store.loadLocalPrograms();
    expect(store.localProgramsError).toBe(true);
  });

  it('a Retry reads a satellite list it holds again, after the recheck dropped the failures', async () => {
    // The recheck forgets the failed checks the satellites' read answers from
    // too; without a second read their snapclient and CamillaDSP rows kept
    // "check impossible" — or "up to date" — until the next resync.
    const satellite = { mac_id: 'aa', online: true, check_failed: true, camilladsp_check_failed: true };
    apiCall.get.mockImplementation(async (url) => (url === '/api/programs/satellites'
      ? ok({ satellites: [satellite] })
      : ok({ programs: {}, active_updates: [] })));
    await store.loadSatellites();
    const order = [];
    apiCall.post.mockImplementation(async (url) => {
      order.push(`POST ${url}`);
      return ok({ programs: {}, active_updates: [] });
    });
    apiCall.get.mockImplementation(async (url) => {
      order.push(`GET ${url}`);
      return ok({ satellites: [{ ...satellite, check_failed: false, camilladsp_check_failed: false }] });
    });

    await store.recheckPrograms();

    expect(order).toEqual(['POST /api/programs/recheck', 'GET /api/programs/satellites']);
  });

  it('a Retry with no satellite list held reads none', async () => {
    apiCall.post.mockResolvedValue(ok({ programs: {}, active_updates: [] }));

    await store.recheckPrograms();

    expect(apiCall.post.mock.calls.map(([url]) => url)).toEqual(['/api/programs/recheck']);
    expect(apiCall.get).not.toHaveBeenCalled();
  });

  it('an ordinary read does not ask GitHub again', async () => {
    apiCall.get.mockResolvedValue(ok({ programs: {}, active_updates: [] }));
    await store.loadLocalPrograms();

    expect(apiCall.post).not.toHaveBeenCalled();
  });
});

describe('updatesStore resync gate', () => {
  let store;

  beforeEach(() => {
    resetApiCallMock();
    store = useUpdatesStore();
  });

  it('reads where a Milō update stands on every resync, the boot\'s included', async () => {
    // Gated on Settings having been opened, a kiosk back from the update's
    // reboot, or a phone opened halfway through one, would draw the app over
    // an update.
    apiCall.get.mockResolvedValue(ok({ milo_update: null, last_update: null }));

    await store.resync();
    await store.resync();

    const reads = apiCall.get.mock.calls.filter(([url]) => url === '/api/programs/milo/update');
    expect(reads).toHaveLength(2);
    expect(store.miloUpdateKnown).toBe(true);
  });

  it('leaves the program list alone until a view has loaded it', async () => {
    // Every program's installed version is probed per read: on every phone
    // unlock and every reconnect of every client, on a box whose multiroom
    // stutters when it runs short of CPU.
    apiCall.get.mockResolvedValue(ok({ milo_update: null, last_update: null }));

    await store.resync();

    expect(apiCall.get).not.toHaveBeenCalledWith('/api/programs', expect.anything());
  });

  it('refetches once the store has been loaded', async () => {
    apiCall.get.mockResolvedValue(ok({ programs: { milo: { status: 'installed' } }, active_updates: [] }));
    await store.loadLocalPrograms();
    apiCall.get.mockClear();

    await store.resync();

    expect(apiCall.get).toHaveBeenCalledWith('/api/programs', expect.anything());
  });

  it('leaves the satellites alone while they have never been loaded', async () => {
    // Satellites only exist while multiroom is on, and discovering them probes
    // every unit over HTTP — the price of resyncing a list nobody asked for.
    apiCall.get.mockResolvedValue(ok({ programs: {}, active_updates: [] }));
    await store.loadLocalPrograms();
    apiCall.get.mockClear();

    await store.resync();

    expect(apiCall.get).not.toHaveBeenCalledWith('/api/programs/satellites', expect.anything());
  });

  it('refetches the satellites once they have been loaded', async () => {
    // Their in-flight flags are delta-only too: a progress or completion event
    // missed while the tab was hidden leaves a button stuck on "updating".
    apiCall.get.mockResolvedValue(ok({ programs: {}, active_updates: [], satellites: [] }));
    await store.loadLocalPrograms();
    await store.loadSatellites();
    apiCall.get.mockClear();

    await store.resync();

    expect(apiCall.get).toHaveBeenCalledWith('/api/programs/satellites', expect.anything());
  });
});

describe('updatesStore satellite return', () => {
  const MAC = 'dc:a6:32:7e:d3:43';
  let store;

  const withSatellite = () => ok({ satellites: [{ mac_id: MAC, app_version: 'v0.2.0' }] });
  const withoutSatellite = () => ok({ satellites: [] });

  beforeEach(() => {
    resetApiCallMock();
    store = useUpdatesStore();
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('keeps fetching until the satellite that just restarted answers', async () => {
    // The completion is emitted while the unit is restarting its own services,
    // so the inventory it triggers answers without it. Measured on the fleet:
    // one satellite of two, 8 s after the press.
    apiCall.get
      .mockResolvedValueOnce(withoutSatellite())
      .mockResolvedValueOnce(withoutSatellite())
      .mockResolvedValue(withSatellite());

    useMultiroomStore().clients.set(MAC, { mac_id: MAC, online: true });

    store.handleSatelliteAppUpdateComplete({ mac_id: MAC, success: true });
    await vi.advanceTimersByTimeAsync(30000);

    expect(store.satelliteByMacId[MAC]).toBeTruthy();
    expect(store.isSatelliteAwaitingReturn(MAC)).toBe(false);
  });

  it('keeps waiting while its API answers but Snapcast still has it offline', async () => {
    // The snapclient restarts with the app and is admitted again ~9 s after
    // the completion (measured on both units). Ending the wait on the API
    // alone left an offline satellite nobody was waiting for, and its whole
    // settings section vanished for those seconds.
    const multiroom = useMultiroomStore();
    multiroom.clients.set(MAC, { mac_id: MAC, online: false });
    apiCall.get.mockResolvedValue(withSatellite());

    store.handleSatelliteAppUpdateComplete({ mac_id: MAC, success: true });
    await vi.advanceTimersByTimeAsync(5000);
    expect(store.satelliteByMacId[MAC]).toBeTruthy();
    expect(store.isSatelliteAwaitingReturn(MAC)).toBe(true);

    multiroom.clients.set(MAC, { mac_id: MAC, online: true });
    await vi.advanceTimersByTimeAsync(4000);
    expect(store.isSatelliteAwaitingReturn(MAC)).toBe(false);
  });

  it('draws the skeleton while it waits, not once it has given up', async () => {
    // Both halves matter: without the first the row reads "offline" during a
    // restart that is going fine, without the second it waits forever.
    apiCall.get.mockResolvedValue(withoutSatellite());

    store.handleSatelliteAppUpdateComplete({ mac_id: MAC, success: true });
    await vi.advanceTimersByTimeAsync(0);
    expect(store.isSatelliteAwaitingReturn(MAC)).toBe(true);

    await vi.advanceTimersByTimeAsync(60000);
    expect(store.isSatelliteAwaitingReturn(MAC)).toBe(false);

    const polls = apiCall.get.mock.calls.length;
    await vi.advanceTimersByTimeAsync(60000);
    expect(apiCall.get.mock.calls.length).toBe(polls);
  });

  it.each([
    ['snapclient', 'handleSatelliteUpdateComplete', 'isSatelliteUpdateCompleted'],
    ['app', 'handleSatelliteAppUpdateComplete', 'isSatelliteAppUpdateCompleted'],
    ['CamillaDSP', 'handleSatelliteCamillaUpdateComplete', 'isSatelliteCamillaUpdateCompleted'],
  ])('forgets a finished %s update once the satellite answers the fleet read', async (_, complete, completed) => {
    // Kept past that read, the mark hides the row's real state for the rest of
    // the page's life: the next release offered to that satellite has no
    // button, and its row reads "up to date".
    useMultiroomStore().clients.set(MAC, { mac_id: MAC, online: true });
    apiCall.get.mockResolvedValue(withSatellite());

    store[complete]({ mac_id: MAC, success: true });
    await vi.advanceTimersByTimeAsync(0);

    expect(store[completed](MAC)).toBe(false);
  });

  it('keeps a finished update marked while the fleet read answers without that satellite', async () => {
    // The first read after a completion is often taken while the satellite is
    // restarting: an answer that does not list it says nothing about it.
    useMultiroomStore().clients.set(MAC, { mac_id: MAC, online: true });
    apiCall.get.mockResolvedValueOnce(withoutSatellite()).mockResolvedValue(withSatellite());

    store.handleSatelliteAppUpdateComplete({ mac_id: MAC, success: true });
    await vi.advanceTimersByTimeAsync(0);
    expect(store.isSatelliteAppUpdateCompleted(MAC)).toBe(true);

    await vi.advanceTimersByTimeAsync(4000);
    expect(store.isSatelliteAppUpdateCompleted(MAC)).toBe(false);
  });

  it.each([
    ['snapclient', 'handleSatelliteUpdateComplete', 'startSatelliteUpdate', 'isSatelliteUpdateCompleted'],
    ['app', 'handleSatelliteAppUpdateComplete', 'startSatelliteAppUpdate', 'isSatelliteAppUpdateCompleted'],
    ['CamillaDSP', 'handleSatelliteCamillaUpdateComplete', 'startSatelliteCamillaUpdate',
      'isSatelliteCamillaUpdateCompleted'],
  ])('keeps a finished %s mark through a fleet read sent before that update moved again',
    async (_, complete, start, completed) => {
      // The answer was read before the satellite's update moved, so it says
      // nothing about that move; dropping the mark on it, or on another
      // update's move, would decide from a list older than the event.
      useMultiroomStore().clients.set(MAC, { mac_id: MAC, online: true });
      let answer;
      apiCall.get.mockReturnValueOnce(new Promise(resolve => { answer = resolve; }))
        .mockResolvedValue(withSatellite());
      apiCall.post.mockResolvedValue(ok({ status: 'success' }));

      store[complete]({ mac_id: MAC, success: true });
      await store[start](MAC);
      answer(withSatellite());
      await vi.advanceTimersByTimeAsync(0);

      expect(store[completed](MAC)).toBe(true);
    });

  it('holds the fleet it already has while it refetches', async () => {
    // The skeleton is drawn from `satellites === null`, so re-entering that on
    // a refresh flashed every satellite section back to skeletons for the
    // length of a fleet-wide probe.
    apiCall.get.mockResolvedValue(withSatellite());
    await store.loadSatellites();

    let release;
    apiCall.get.mockReturnValue(new Promise((resolve) => { release = resolve; }));
    const inFlight = store.loadSatellites();

    expect(store.satelliteByMacId[MAC]).toBeTruthy();

    release(withSatellite());
    await inFlight;
  });
});


describe('updatesStore in-flight update state', () => {
  let store;

  beforeEach(() => {
    resetApiCallMock();
    store = useUpdatesStore();
  });

  it('forgets a completed update as soon as the list is refetched', async () => {
    // "Completed" suppresses exactly one stale frame — the window between the
    // completion event and the refetch it triggers. Kept past that refetch it
    // stops describing anything: a program returned to the manifest reads as
    // up to date while upstream is offering a release again, for the rest of
    // the session, with the button that would install it hidden.
    apiCall.get.mockResolvedValue(ok({ programs: {}, active_updates: [] }));
    await store.loadLocalPrograms();

    await store.handleProgramUpdateComplete({ program: 'shairport-sync', success: true });

    expect(store.isLocalUpdateCompleted('shairport-sync')).toBe(false);
  });

  it('remembers which release an update is installing', async () => {
    // The button that was pressed says what it is doing rather than being
    // joined by a second one: a return and an update are the same flow, and
    // only the target separates them.
    apiCall.get.mockResolvedValue(ok({ programs: { 'shairport-sync': {} }, active_updates: [] }));
    await store.loadLocalPrograms();
    apiCall.post.mockResolvedValue(ok({ message: 'started' }));

    await store.startLocalUpdate('shairport-sync', 'validated');

    expect(apiCall.post).toHaveBeenCalledWith(
      '/api/programs/shairport-sync/update',
      { target: 'validated' },
      expect.anything()
    );
    expect(store.localUpdateTarget('shairport-sync')).toBe('validated');
  });

  it('sends the chosen release with a trial and remembers it', async () => {
    // Dropped from the body, the backend installs upstream's latest instead of
    // the release picked in the menu.
    apiCall.get.mockResolvedValue(ok({ programs: { 'go-librespot': {} }, active_updates: [] }));
    await store.loadLocalPrograms();
    apiCall.post.mockResolvedValue(ok({ message: 'started' }));

    await store.startLocalUpdate('go-librespot', 'upstream', '0.8.1');

    expect(apiCall.post).toHaveBeenCalledWith(
      '/api/programs/go-librespot/update',
      { target: 'upstream', version: '0.8.1' },
      expect.anything()
    );
    expect(store.localUpdateVersion('go-librespot')).toBe('0.8.1');
  });

  it('lets a satellite update run beside anything but the app update', async () => {
    // A satellite is a separate machine: blocking the whole screen while one
    // updates makes a two-speaker house a queue. The app update is the one
    // exception — it reconciles the set, pushes the client app and reboots.
    apiCall.get.mockResolvedValue(ok({ programs: { milo: {} }, active_updates: [] }));
    await store.loadLocalPrograms();
    apiCall.post.mockResolvedValue(ok({ message: 'started' }));

    await store.startSatelliteUpdate('aa:bb:cc:dd:ee:ff');

    expect(store.isSatelliteBusy('aa:bb:cc:dd:ee:ff')).toBe(true);
    expect(store.isSatelliteBusy('11:22:33:44:55:66')).toBe(false);
    expect(store.isLocalUpdateBusy()).toBe(false);
    expect(store.isMiloUpdating()).toBe(false);
  });

  it('holds every app push while one runs, and nothing else', async () => {
    // The server refuses a second app push whatever the satellite (one room
    // cut at a time); an enabled button there spins, is refused and drops
    // back with nothing on screen to say why.
    apiCall.get.mockResolvedValue(ok({ programs: { milo: {} }, active_updates: [] }));
    await store.loadLocalPrograms();
    apiCall.post.mockResolvedValue(ok({ message: 'started' }));

    await store.startSatelliteAppUpdate('aa:bb:cc:dd:ee:ff');

    expect(store.isSatelliteAppBlocked('11:22:33:44:55:66')).toBe(true);
    expect(store.isSatelliteBusy('11:22:33:44:55:66')).toBe(false);
  });

  /** Park every inventory request until the test releases it, oldest first. */
  function deferInventory() {
    const pending = [];
    apiCall.get.mockImplementation(() => new Promise((resolve) => { pending.push(resolve); }));
    return pending;
  }

  it('does not resurrect an update that completed while the list was in flight', async () => {
    // A failed completion triggers no refetch, so nothing would ever clear an
    // entry the stale answer brought back: the buttons stay greyed.
    apiCall.get.mockResolvedValue(ok({ programs: { snapcast: {} }, active_updates: ['snapcast'] }));
    await store.loadLocalPrograms();
    const pending = deferInventory();
    const load = store.loadLocalPrograms();

    store.handleProgramUpdateComplete({ program: 'snapcast', success: false });
    pending[0](ok({ programs: { snapcast: {} }, active_updates: ['snapcast'] }));
    await load;

    expect(store.isLocalUpdating('snapcast')).toBe(false);
  });

  it('an update started while the inventory request is in flight is not cleared by its answer', async () => {
    // Cleared, the button offers a second press the server refuses.
    apiCall.get.mockResolvedValue(ok({ programs: { 'shairport-sync': {} }, active_updates: [] }));
    await store.loadLocalPrograms();
    const pending = deferInventory();
    const load = store.loadLocalPrograms();

    apiCall.post.mockResolvedValue(ok({ message: 'started' }));
    await store.startLocalUpdate('shairport-sync', 'validated');
    pending[0](ok({ programs: { 'shairport-sync': {} }, active_updates: [] }));
    await load;

    expect(store.isLocalUpdating('shairport-sync')).toBe(true);
  });

  it('a delta for one program leaves the in-flight answer in force for the others', async () => {
    // Another device started snapcast while the list was read: its one
    // progress broadcast was ignored here, so the list is the only word of it.
    apiCall.get.mockResolvedValue(ok({ programs: { snapcast: {}, 'shairport-sync': {} }, active_updates: [] }));
    await store.loadLocalPrograms();
    const pending = deferInventory();
    const load = store.loadLocalPrograms();

    apiCall.post.mockResolvedValue(ok({ message: 'started' }));
    await store.startLocalUpdate('shairport-sync', 'validated');
    pending[0](ok({ programs: { snapcast: {}, 'shairport-sync': {} }, active_updates: ['snapcast'] }));
    await load;

    expect(store.isLocalUpdating('snapcast')).toBe(true);
  });

  const MAC = 'aa:bb:cc:dd:ee:ff';
  const satellite = (flags = {}) => ok({ satellites: [{ mac_id: MAC, ...flags }] });

  it('a satellite update pressed while the fleet is read is not cleared by its answer', async () => {
    // Cleared, the button offers a second press the satellite refuses (409).
    apiCall.get.mockResolvedValue(satellite());
    await store.loadSatellites();
    const pending = deferInventory();
    const load = store.loadSatellites();

    apiCall.post.mockResolvedValue(ok({ message: 'started' }));
    await store.startSatelliteAppUpdate(MAC);
    pending[0](satellite({ app_updating: false }));
    await load;

    expect(store.isSatelliteAppUpdating(MAC)).toBe(true);
  });

  it('a satellite update that failed while the fleet was read stays failed', async () => {
    // A failed completion triggers no refetch: brought back by the stale
    // answer, the row's three buttons stay greyed for the rest of the session.
    apiCall.get.mockResolvedValue(satellite({ camilladsp_updating: true }));
    await store.loadSatellites();
    const pending = deferInventory();
    const load = store.loadSatellites();

    store.handleSatelliteCamillaUpdateComplete({ mac_id: MAC, success: false });
    pending[0](satellite({ camilladsp_updating: true }));
    await load;

    expect(store.isSatelliteCamillaUpdating(MAC)).toBe(false);
  });

  it('a press on one satellite update leaves the answer in force for its other two', async () => {
    // Another device started the satellite's dependency update meanwhile; the
    // fleet read is the only word of it this client gets.
    apiCall.get.mockResolvedValue(satellite());
    await store.loadSatellites();
    const pending = deferInventory();
    const load = store.loadSatellites();

    apiCall.post.mockResolvedValue(ok({ message: 'started' }));
    await store.startSatelliteAppUpdate(MAC);
    pending[0](satellite({ app_updating: true, updating: true }));
    await load;

    expect(store.isSatelliteUpdating(MAC)).toBe(true);
  });

  it('applies only the last fleet request sent', async () => {
    const pending = deferInventory();
    const first = store.loadSatellites();
    const second = store.loadSatellites();

    pending[1](satellite());
    await second;
    pending[0](satellite({ updating: true }));
    await first;

    expect(store.isSatelliteUpdating(MAC)).toBe(false);
  });

  it('applies only the last inventory request sent, and stays loading until it answers', async () => {
    const pending = deferInventory();
    const first = store.loadLocalPrograms();
    const second = store.loadLocalPrograms();

    pending[1](ok({ programs: { milo: {} }, active_updates: [] }));
    await second;
    pending[0](ok({ programs: { milo: {}, snapcast: {} }, active_updates: ['snapcast'] }));
    await first;

    expect(store.canUpdateLocal('snapcast')).toBe(false);
    expect(store.isLocalUpdating('snapcast')).toBe(false);

    const third = store.loadLocalPrograms();
    const fourth = store.loadLocalPrograms();
    pending[2](ok({ programs: {}, active_updates: [] }));
    await third;

    expect(store.localProgramsLoading).toBe(true);
    pending[3](ok({ programs: {}, active_updates: [] }));
    await fourth;
    expect(store.localProgramsLoading).toBe(false);
  });
});

describe('updatesStore satellite app push failure', () => {
  const MAC = 'd8:3a:dd:68:e7:e4';
  const PAYLOAD = '3f1c9a0e';
  let store;

  const failure = (verdict, detail) => ({ payload: PAYLOAD, verdict, detail, at: 1_763_200_000 });
  const fleet = (row = {}) => ok({ satellites: [{ mac_id: MAC, online: true, app_update_failure: null, ...row }] });

  beforeEach(() => {
    resetApiCallMock();
    store = useUpdatesStore();
    useMultiroomStore().clients.set(MAC, { mac_id: MAC, online: true });
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('keeps a failed push on the row and reads the fleet again', async () => {
    // The satellite that went back to its previous version restarted as well:
    // without a read its row keeps the state it had before the push, and the
    // cause is gone with the spinner.
    const detail = "ModuleNotFoundError: No module named 'zeroconf'";
    apiCall.get.mockResolvedValue(fleet({ app_update_failure: failure('rolled_back', detail) }));

    store.handleSatelliteAppUpdateComplete({ mac_id: MAC, success: false, verdict: 'rolled_back', detail });
    // Before any read answers: probing a satellite that did not return takes
    // the server its whole timeout, and the row has the cause meanwhile.
    expect(store.satelliteAppFailure(MAC)).toEqual({ verdict: 'rolled_back', detail });
    await vi.advanceTimersByTimeAsync(0);

    expect(apiCall.get).toHaveBeenCalledWith('/api/programs/satellites', expect.anything());
    expect(store.satelliteAppFailure(MAC)).toEqual({ verdict: 'rolled_back', detail });
    expect(store.isSatelliteAppUpdating(MAC)).toBe(false);
  });

  it('clears the failure once a later push of that satellite succeeds', async () => {
    apiCall.get.mockResolvedValue(fleet({ app_update_failure: failure('did_not_return', null) }));
    await store.loadSatellites();
    expect(store.satelliteAppFailure(MAC)).not.toBeNull();

    apiCall.get.mockResolvedValue(fleet());
    store.handleSatelliteAppUpdateComplete({ mac_id: MAC, success: true, verdict: 'committed', detail: null });

    expect(store.satelliteAppFailure(MAC)).toBeNull();
    await vi.advanceTimersByTimeAsync(0);
    expect(store.satelliteAppFailure(MAC)).toBeNull();
  });

  it('shows after a reload the failure the server remembers, for a satellite that does not answer', async () => {
    // A unit that never came back is listed by the server only because of its
    // failure: the page opened afterwards has no WS event to learn it from.
    const detail = 'TimeoutError: ';
    apiCall.get.mockResolvedValue(fleet({ online: false, app_update_failure: failure('did_not_return', detail) }));

    await store.loadSatellites();

    expect(store.satelliteAppFailure(MAC)).toEqual({ verdict: 'did_not_return', detail });
  });

  it('forgets a failure the server no longer lists', async () => {
    // Cleared on the server by a success another device pushed: the row would
    // otherwise go on showing a cause that is over.
    apiCall.get.mockResolvedValue(fleet({ app_update_failure: failure('rejected', '409') }));
    await store.loadSatellites();

    apiCall.get.mockResolvedValue(ok({ satellites: [] }));
    await store.loadSatellites();

    expect(store.satelliteAppFailure(MAC)).toBeNull();
  });

  it('keeps a failure that lands while the fleet read is in flight', async () => {
    // That answer was read before the push ended; erasing the failure on it
    // would drop the one word of it this client gets until the next read.
    apiCall.get.mockResolvedValue(fleet());
    await store.loadSatellites();
    let answer;
    apiCall.get.mockReturnValueOnce(new Promise(resolve => { answer = resolve; }))
      .mockResolvedValue(fleet({ app_update_failure: failure('rolled_back', null) }));
    const load = store.loadSatellites();

    store.handleSatelliteAppUpdateComplete({ mac_id: MAC, success: false, verdict: 'rolled_back', detail: null });
    answer(fleet());
    await load;

    expect(store.satelliteAppFailure(MAC)).toEqual({ verdict: 'rolled_back', detail: null });
  });
});


describe('updatesStore satellite program install failure', () => {
  // A snapclient or CamillaDSP install that left the old version reached the
  // row as nothing at all: the spinner went, the button came back, and the
  // satellite's reason was never shown. The server records app pushes alone,
  // so the completion is the only word of it.
  const MAC = 'd8:3a:dd:68:e7:e4';
  let store;

  const fleet = (row = {}) => ok({ satellites: [{
    mac_id: MAC, online: true, app_update_failure: null,
    update_available: true, camilladsp_update_available: true, ...row,
  }] });

  beforeEach(() => {
    resetApiCallMock();
    store = useUpdatesStore();
    useMultiroomStore().clients.set(MAC, { mac_id: MAC, online: true });
  });

  it.each([
    ['snapclient', 'handleSatelliteUpdateComplete', 'satelliteUpdateFailure', 'update_available'],
    ['camilladsp', 'handleSatelliteCamillaUpdateComplete', 'satelliteCamillaFailure', 'camilladsp_update_available'],
  ])('%s: the failure stays on the row until a success or a read with nothing left to install',
    async (_kind, complete, failureOf, offered) => {
      const detail = 'Installation failed: dpkg: dependency problems';
      store[complete]({ mac_id: MAC, success: false, verdict: 'rolled_back', detail });
      expect(store[failureOf](MAC)).toEqual({ verdict: 'rolled_back', detail });

      // Still offered: the version did not move, the failure is still the row's.
      apiCall.get.mockResolvedValue(fleet());
      await store.loadSatellites();
      expect(store[failureOf](MAC)).toEqual({ verdict: 'rolled_back', detail });

      // Installed since, from another screen: the row is up to date.
      apiCall.get.mockResolvedValue(fleet({ [offered]: false }));
      await store.loadSatellites();
      expect(store[failureOf](MAC)).toBeNull();

      store[complete]({ mac_id: MAC, success: false, verdict: 'did_not_return', detail: null });
      apiCall.get.mockResolvedValue(fleet());
      store[complete]({ mac_id: MAC, success: true, verdict: 'committed', detail: null });
      expect(store[failureOf](MAC)).toBeNull();
    });

  it('a failure of one program says nothing about the other', () => {
    store.handleSatelliteUpdateComplete({ mac_id: MAC, success: false, verdict: 'rejected', detail: 'HTTP 409' });

    expect(store.satelliteCamillaFailure(MAC)).toBeNull();
    expect(store.satelliteAppFailure(MAC)).toBeNull();
  });
});


describe('updatesStore local program install failure', () => {
  // A program update that failed (rolled back, a service that did not restart)
  // reached the row as nothing at all: the spinner went, the button came back
  // as "Update", and the reason was in the server's log alone. The completion
  // is the only word of it here, as for the satellites' programs.
  let store;

  const inventory = (row = {}) => ok({ programs: { navidrome: { update_available: true, ...row } }, active_updates: [] });

  beforeEach(async () => {
    resetApiCallMock();
    store = useUpdatesStore();
    apiCall.get.mockResolvedValue(inventory());
    await store.loadLocalPrograms();
  });

  it('keeps the failure, raw, with what the press asked for, until a success or a read with nothing to install', async () => {
    apiCall.post.mockResolvedValue(ok({ status: 'success' }));
    await store.startLocalUpdate('navidrome', 'upstream', '0.60.0');
    const error = 'Failed to start Navidrome after update. Rolled back to previous version.';
    store.handleProgramUpdateComplete({ program: 'navidrome', success: false, error });

    expect(store.localUpdateFailure('navidrome')).toEqual({ error, target: 'upstream', version: '0.60.0' });

    // Still offered: the version did not move, the failure is still the row's.
    await store.loadLocalPrograms();
    expect(store.localUpdateFailure('navidrome')?.error).toBe(error);

    // Installed since, from another screen: the row is up to date.
    apiCall.get.mockResolvedValue(inventory({ update_available: false }));
    await store.loadLocalPrograms();
    expect(store.localUpdateFailure('navidrome')).toBeNull();

    store.handleProgramUpdateComplete({ program: 'navidrome', success: false, error });
    store.handleProgramUpdateComplete({ program: 'navidrome', success: true });
    expect(store.localUpdateFailure('navidrome')).toBeNull();
  });

  it('a read that left before the failure does not forget it', async () => {
    let answer;
    apiCall.get.mockImplementation(() => new Promise((resolve) => { answer = resolve; }));
    const load = store.loadLocalPrograms();
    store.handleProgramUpdateComplete({ program: 'navidrome', success: false, error: 'pip install failed' });
    answer(inventory({ update_available: false }));
    await load;

    expect(store.localUpdateFailure('navidrome')?.error).toBe('pip install failed');
  });

  it('a failed Milō update is the last-update record\'s, never a program row\'s', () => {
    store.handleProgramUpdateComplete({ program: 'milo', success: false });

    expect(store.localUpdateFailure('milo')).toBeNull();
  });
});


describe('updatesStore Milō update', () => {
  let store;

  const update = (phase, extra = {}) => ({
    phase, from_version: 'v0.1.4', to_version: 'v0.1.5', item: null, index: null, total: null, ...extra,
  });
  const progress = (phase, extra = {}) => ({ program: 'milo', status: 'updating', ...update(phase), ...extra });
  const programs = (fields = {}) => ok({ programs: {}, active_updates: [], milo_update: null, last_update: null, ...fields });
  const record = (outcome, extra = {}) => ({
    from_version: 'v0.1.4', to_version: 'v0.1.5', outcome, detail: 'ImportError: no module named x', step: 'rebooting',
    ...extra,
  });

  beforeEach(() => {
    resetApiCallMock();
    store = useUpdatesStore();
  });

  it('takes the screen for the leaving release\'s phases and the trial, a banner after', () => {
    // The screen over the dependency set or the satellites would block the
    // whole app for minutes after the new release already held; a banner over
    // the install would let the unit be used while its checkout moves.
    for (const phase of ['download', 'install', 'restart', 'verify']) {
      store.handleProgramUpdateProgress(progress(phase));
      expect([store.miloUpdateScreenPhase, store.miloUpdateBanner]).toEqual([phase, null]);
    }
    for (const phase of ['dependencies', 'satellites']) {
      store.handleProgramUpdateProgress(progress(phase, { item: 'Canapé', index: 1, total: 2 }));
      expect(store.miloUpdateScreenPhase).toBeNull();
      expect(store.miloUpdateBanner?.phase).toBe(phase);
    }
  });

  it('draws no banner while the post-commit phase has nothing to install', () => {
    // Reading nine versions and finding every speaker current is an update
    // going well, which shows nothing.
    for (const phase of ['dependencies', 'satellites']) {
      store.handleProgramUpdateProgress(progress(phase));
      expect(store.miloUpdateBanner).toBeNull();
    }
  });

  it('leaves the screen alone on another program\'s progress', () => {
    // A dependency update carries no phase: read as Milō's, it would clear or
    // replace the screen of an update that is still running.
    store.handleProgramUpdateProgress(progress('install'));

    store.handleProgramUpdateProgress({ program: 'shairport-sync', status: 'updating' });

    expect(store.miloUpdateScreenPhase).toBe('install');
  });

  it('draws the screen for an update this client never saw start', async () => {
    // A kiosk reloading after the reboot learns of the trial from this read
    // alone: no event announced it to the new page.
    apiCall.get.mockResolvedValue(programs({ milo_update: update('verify'), active_updates: ['milo'] }));

    await store.resync();

    expect(store.miloUpdateScreenPhase).toBe('verify');
  });

  it('applies only the last of its two reads sent', async () => {
    // The inventory answers seconds after the read that probes nothing: sent
    // first and answered last, it would put back a step the update has left.
    let inventory;
    apiCall.get.mockReturnValueOnce(new Promise((resolve) => { inventory = resolve; }));
    const load = store.loadLocalPrograms();
    apiCall.get.mockResolvedValueOnce(ok({ milo_update: update('verify'), last_update: null }));
    await store.loadMiloUpdate();

    inventory(programs({ milo_update: update('restart') }));
    await load;

    expect(store.miloUpdateScreenPhase).toBe('verify');
  });

  it('ends the screen on the completion, and on a read that no longer lists the update', async () => {
    // The completion may be missed by a tab in the background; the read after
    // a rollback is the only word of it the origin release gives.
    store.handleProgramUpdateProgress(progress('install'));
    store.handleProgramUpdateComplete({ program: 'milo', success: false });
    expect(store.miloUpdateScreenPhase).toBeNull();

    store.handleProgramUpdateProgress(progress('restart'));
    apiCall.get.mockResolvedValue(programs({ last_update: record('rolled_back') }));
    await store.resync();
    expect(store.miloUpdateScreenPhase).toBeNull();
  });

  it('names a lost backend under the screen a restart, until it answers again', async () => {
    // The release being replaced is the one serving the socket: whatever step
    // it named last, its going away is the restart, and a read that cannot
    // reach it changes nothing — the screen must not fall back to the app.
    store.handleProgramUpdateProgress(progress('install'));

    store.handleBackendLost();
    apiCall.get.mockResolvedValue(fail('Network Error', 0));
    await store.resync();
    expect(store.miloUpdateScreenPhase).toBe('restart');

    apiCall.get.mockResolvedValue(programs({ milo_update: update('verify') }));
    await store.resync();
    expect(store.miloUpdateScreenPhase).toBe('verify');
  });

  it('a lost backend outside the screen changes nothing', () => {
    // During the satellites the server is up and running the push: a dropped
    // socket there is a phone's, and a screen raised over it would never end.
    store.handleBackendLost();
    expect(store.miloUpdateScreenPhase).toBeNull();

    store.handleProgramUpdateProgress(progress('satellites', { item: 'Canapé', index: 1, total: 2 }));
    store.handleBackendLost();
    expect(store.miloUpdateScreenPhase).toBeNull();
  });

  it('does not move the update back on a read sent before a progress event', async () => {
    // The answer was read before the event: applied, it puts the screen back
    // on a step the update already left.
    let answer;
    apiCall.get.mockReturnValueOnce(new Promise((resolve) => { answer = resolve; }));
    const load = store.loadLocalPrograms();

    store.handleProgramUpdateProgress(progress('install'));
    answer(programs({ milo_update: update('download'), active_updates: ['milo'] }));
    await load;

    expect(store.miloUpdateScreenPhase).toBe('install');
  });

  it.each([
    ['rolled_back', true],
    ['rollback_failed', true],
    ['committed', false],
  ])('describes a %s record on the Milō row and in a banner: %s', async (outcome, told) => {
    // Nothing on success; a release put back is said once, in words, and the
    // row keeps the cause until the next update.
    apiCall.get.mockResolvedValue(programs({ last_update: record(outcome) }));

    await store.loadLocalPrograms();

    expect(!!store.miloUpdateFailure).toBe(told);
    expect(!!store.rollbackNotice).toBe(told);
  });

  it('closes the rollback banner for every screen through the server', async () => {
    // Kept in the browser, the kiosk's private profile forgot the close at
    // every restart and told the same rollback again; the server keeps it, and
    // the row keeps the cause.
    apiCall.get.mockResolvedValue(programs({ last_update: record('rolled_back') }));
    await store.loadLocalPrograms();
    apiCall.patch.mockResolvedValue(ok({ status: 'success', last_update: record('rolled_back', { dismissed: true }) }));

    await store.dismissRollbackNotice();

    expect(apiCall.patch).toHaveBeenCalledWith('/api/programs/milo/last-update', { dismissed: true }, expect.anything());
    expect(store.rollbackNotice).toBeNull();
    expect(store.miloUpdateFailure).not.toBeNull();
  });

  it('follows the server: a closed record stays closed, a new one is told', async () => {
    // A screen that starts after the close (the kiosk at boot) reads it
    // closed; the next outcome is a new record, which nothing here may hide.
    apiCall.get.mockResolvedValue(programs({ last_update: record('rolled_back', { dismissed: true }) }));
    await store.loadLocalPrograms();
    expect(store.rollbackNotice).toBeNull();
    expect(store.miloUpdateFailure).not.toBeNull();

    apiCall.get.mockResolvedValue(programs({ last_update: record('rolled_back', { dismissed: false }) }));
    await store.resync();

    expect(store.rollbackNotice).not.toBeNull();
  });

  it('drops a banner another screen closed, past a read sent before', async () => {
    // The phone closed it while this screen's read was in flight: that answer
    // predates the close and would raise the banner again.
    apiCall.get.mockResolvedValue(programs({ last_update: record('rolled_back') }));
    await store.loadLocalPrograms();
    let answer;
    apiCall.get.mockReturnValueOnce(new Promise((resolve) => { answer = resolve; }));
    const load = store.loadLocalPrograms();

    store.markLastUpdateDismissed();
    answer(programs({ last_update: record('rolled_back') }));
    await load;

    expect(store.rollbackNotice).toBeNull();
    expect(store.miloUpdateFailure).not.toBeNull();
  });

  it('lets a close heard during a read end a stale update screen', async () => {
    // The install failed before its reboot and the guard put the previous
    // release back: only a read ends this screen's "Restarting". A close from
    // another screen landing meanwhile is about the banner alone; held as an
    // update move, it dropped that read and left "Do not unplug" up for good.
    store.handleProgramUpdateProgress(progress('restart'));
    let answer;
    apiCall.get.mockReturnValueOnce(new Promise((resolve) => { answer = resolve; }));
    const load = store.loadLocalPrograms();

    store.markLastUpdateDismissed();
    answer(programs({ milo_update: null, active_updates: [], last_update: record('rolled_back') }));
    await load;

    expect(store.miloUpdateScreenPhase).toBeNull();
    expect(store.isLocalUpdating('milo')).toBe(false);
    expect(store.rollbackNotice).toBeNull();
    expect(store.miloUpdateFailure).not.toBeNull();
  });

  it('holds the rollback banner back while an update runs again', async () => {
    // A retry pressed from the row: the banner about the previous attempt
    // would sit over the one in progress.
    apiCall.get.mockResolvedValue(programs({ programs: { milo: {} }, last_update: record('rolled_back') }));
    await store.loadLocalPrograms();
    apiCall.post.mockResolvedValue(ok({ status: 'success' }));

    await store.startLocalUpdate('milo');

    expect(store.rollbackNotice).toBeNull();
  });
});
