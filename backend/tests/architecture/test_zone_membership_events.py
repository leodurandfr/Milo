"""Structural guardrail: every zone whose membership moved is announced.

The registry is a store with three subscribers — `VolumeStateStore` (which keeps
its own zone→members map and applies zone deltas from it), `CrossoverService`
(which recomputes the band split of a zone whose members changed) and
`SnapcastWebSocketService` (which turns the event into the `multiroom/zone_changed`
the frontend *and* Milo-Mac read zone membership from). None of them polls. A zone
that changed and was not announced therefore stays wrong in all three at once, and
it is silent in every direction: the registry's own state is self-consistent, no
route fails, no existing test goes red.

That is not hypothetical. `add_client_to_zone` and `create_zone` both take a client
out of the zone it was in, and both announced the old zone only when the departure
*destroyed* it. Moving a member out of a three-member zone left:

  * the volume store still listing the mover in the zone it left — and the volume
    ownership rule commits a zone delta to an absent client unconditionally, so
    adjusting that zone moved a speaker that is no longer in it;
  * the crossover service never recalculating it — if the mover was the subwoofer,
    the remaining members kept a highpass with nothing under it;
  * the frontend and Milo-Mac still drawing the mover inside it.

And `create_zone` did not detach at all, so one client could sit in two zones'
`client_ids` while `client.zone_id` named only one — a state the UI offers no way
back out of.

The rule below is derived, never typed: each scenario diffs the registry's own
`{zone_id: client_ids}` map across the mutation and requires the emitted zone
events to name exactly the ids that moved. A sixth membership path added later is
covered without anyone thinking to write a test for it.

Doctrine note (as in the other architecture guardrails): the extractor asserts its
own output is non-trivial first, so a scenario that mutates nothing fails loudly
instead of passing on an empty diff.
"""
from unittest.mock import AsyncMock

import pytest

from backend.core.multiroom.client_registry import ClientRegistryService
from backend.core.multiroom.models import RegistryEventType

ZONE_EVENTS = frozenset({
    RegistryEventType.ZONE_CREATED,
    RegistryEventType.ZONE_UPDATED,
    RegistryEventType.ZONE_DELETED,
    RegistryEventType.ZONE_CLIENT_REMOVED,
})


async def _registry() -> ClientRegistryService:
    settings = AsyncMock()
    settings.get_setting = AsyncMock(return_value=None)
    settings.set_settings = AsyncMock()
    registry = ClientRegistryService(settings_service=settings)
    await registry.initialize()
    for i in range(6):
        await registry.register_client(f"c{i}", f"Client {i}", f"192.168.1.1{i}")
    return registry


def _membership(registry) -> dict:
    """The registry's own view of who is in which zone."""
    return {
        zone_id: tuple(zone.client_ids)
        for zone_id, zone in registry.get_all_zones().items()
    }


async def _run(mutation):
    """Apply one membership mutation, returning (changed zone ids, announced ids)."""
    registry = await _registry()
    await registry.create_zone("z1", "Salon", ["c0", "c1", "c2"])
    await registry.create_zone("z2", "Bureau", ["c3", "c4"])

    announced = set()

    async def spy(event_type, data):
        if event_type in ZONE_EVENTS:
            announced.add(data["zone_id"])

    registry.subscribe(spy)

    before = _membership(registry)
    await mutation(registry)
    after = _membership(registry)

    changed = {
        zone_id
        for zone_id in before.keys() | after.keys()
        if before.get(zone_id) != after.get(zone_id)
    }
    return changed, announced


# Every way a zone's member list can move. Both tests below run the whole set,
# and they catch different halves: the announcement rule is what
# `move_between_surviving_zones` broke, while `create_from_a_client_already_in_a_zone`
# passes it vacuously — a zone that should have lost a member and did not shows no
# diff to announce — and is caught by the one-zone-per-client rule instead. The
# rest are here so the rule is stated over the whole surface, not over the bug.
SCENARIOS = {
    "move_between_surviving_zones":
        lambda r: r.add_client_to_zone("z2", "c2"),
    "create_from_a_client_already_in_a_zone":
        lambda r: r.create_zone("z3", "Cuisine", ["c0", "c5"]),
    "move_that_dissolves_the_old_zone":
        lambda r: r.add_client_to_zone("z1", "c3"),
    "remove_from_a_surviving_zone":
        lambda r: r.remove_client_from_zone("z1", "c2"),
    "remove_that_dissolves_the_zone":
        lambda r: r.remove_client_from_zone("z2", "c4"),
    "unregister_a_member":
        lambda r: r.unregister_client("c2"),
    "delete_a_zone":
        lambda r: r.delete_zone("z1"),
    "create_from_free_clients":
        lambda r: r.create_zone("z3", "Cuisine", ["c5", "c0"]),
}


@pytest.mark.parametrize("name", sorted(SCENARIOS))
@pytest.mark.asyncio
async def test_every_zone_whose_membership_moved_is_announced(name):
    changed, announced = await _run(SCENARIOS[name])

    # Not vacuous: a scenario that stopped mutating anything would otherwise
    # satisfy the rule by announcing nothing.
    assert changed, f"scenario {name!r} moved no zone — the guardrail is testing nothing"

    assert changed <= announced, (
        f"{name}: zone(s) {sorted(changed - announced)} changed membership and no "
        f"event named them. VolumeStateStore, CrossoverService and the "
        f"multiroom/zone_changed feed all keep the stale membership, with nothing "
        f"to repair it."
    )


@pytest.mark.asyncio
async def test_no_client_is_ever_listed_by_two_zones():
    """`client.zone_id` names one zone; `Zone.client_ids` must agree with it.

    The two are separate records, so nothing makes them agree by construction —
    and a mac in two `client_ids` is not reachable through the UI any more: both
    zones act on it and `get_zone_for_client` can only ever answer one of them.
    """
    for name, mutation in sorted(SCENARIOS.items()):
        registry = await _registry()
        await registry.create_zone("z1", "Salon", ["c0", "c1", "c2"])
        await registry.create_zone("z2", "Bureau", ["c3", "c4"])
        await mutation(registry)

        zones = registry.get_all_zones().values()
        assert zones, f"scenario {name!r} left no zone — nothing is being checked"
        for mac_id in registry.get_client_ids():
            listing = [z.id for z in zones if mac_id in z.client_ids]
            assert len(listing) <= 1, f"{name}: {mac_id} is listed by zones {listing}"
            client = registry.get_client(mac_id)
            assert client.zone_id == (listing[0] if listing else None), (
                f"{name}: {mac_id}.zone_id is {client.zone_id!r} while the zones "
                f"listing it are {listing}"
            )
