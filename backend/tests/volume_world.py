"""The registry as the volume state reads it.

VolumeStateStore keeps levels and mutes only; who is online and what the zones
are it reads from ClientRegistryService when it answers. A volume test that
needs a speaker online, or a zone, sets it here — on the outside world — the
way a snapclient connecting or an operator linking two rooms does.
"""
from typing import Dict, Iterable, List, Optional
from backend.core.multiroom.models import Client, Zone


class Registry:
    """ClientRegistryService, reduced to what the volume code asks of it.

    `known` are the clients the registry holds offline (an online one is
    known too);
    `dac` are the ones whose volume an external amp owns.
    """

    def __init__(self, online: Iterable[str] = (), known: Iterable[str] = (),
                 dac: Iterable[str] = ()):
        self.online = set(online)
        self.known = set(known)
        self.dac = set(dac)
        self.zones: Dict[str, Zone] = {}
        self.subscribers: List = []

    def zone(self, zone_id: str, members: Iterable[str], name: Optional[str] = None) -> Zone:
        zone = Zone(name=name or zone_id, id=zone_id, client_ids=list(members))
        self.zones[zone_id] = zone
        return zone

    def subscribe(self, callback) -> None:
        self.subscribers.append(callback)

    def is_client_online(self, mac_id: str) -> bool:
        return mac_id in self.online

    def get_online_client_ids(self) -> List[str]:
        return sorted(self.online)

    def get_client(self, mac_id: str):
        if mac_id not in self.known | self.online:
            return None
        return Client(mac_id=mac_id, name=mac_id, ip="192.168.1.10",
                      online=mac_id in self.online, volume_control=mac_id not in self.dac)

    def get_zone(self, zone_id: str) -> Optional[Zone]:
        return self.zones.get(zone_id)

    def get_all_zones(self) -> Dict[str, Zone]:
        return dict(self.zones)


def world(store) -> Registry:
    """The Registry `store` reads, attached on first use.

    Refuses a store already reading another registry: replacing it would drop
    whatever that one was set up to answer, and the test would go on passing.
    """
    if store._registry is None:
        store.set_registry(Registry())
    assert isinstance(store._registry, Registry), "the store already reads another registry"
    return store._registry
