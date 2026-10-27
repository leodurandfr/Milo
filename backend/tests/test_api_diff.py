# backend/tests/test_api_diff.py
"""tools/release/api_diff.py sorts a change by who it can break.

Its output is pasted into the release notes as "API and integration changes",
the section the stability promise points integrators to: a contracted change
filed under Internal is a break announced as nothing, and the companion apps
are not the only readers who would miss it.
"""
import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("api_diff", REPO / "tools" / "release" / "api_diff.py")
api_diff = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(api_diff)

MANIFESTS = [
    {"rest": [{"method": "GET", "path": "/api/audio/state"}],
     "ws": {"events": [{"category": "source", "type": "state"}]}},
]


def _op(response="AudioState", refs=("AudioState",), params=()):
    return {"params": list(params), "request": "", "response": response, "refs": list(refs)}


def _event(**fields):
    refs = [f["type"] for f in fields.values() if f["type"][0].isupper()]
    return {"fields": fields, "refs": refs}


def _command(**fields):
    return {"fields": fields, "refs": []}


def _surface(**overrides):
    surface = {
        "operations": {"GET /api/audio/state": _op(), "GET /api/radio/genres": _op("untyped JSON", ())},
        "schemas": {
            "AudioState": {"fields": {"source": {"type": "string", "required": True},
                                      "session": {"type": "SessionView", "required": True}},
                           "refs": ["SessionView"]},
            "SessionView": {"fields": {"title": {"type": "string", "required": True}}, "refs": []},
            "Genre": {"fields": {"name": {"type": "string", "required": True}}, "refs": []},
        },
        "events": {
            "source/state": _event(source={"type": "string", "required": True},
                                   limits={"type": "Limits", "required": True}),
            "settings/language_changed": _event(language={"type": "string", "required": True}),
        },
        "models": {"Limits": {"fields": {"min_db": {"type": "number", "required": True}},
                                 "refs": []}},
    }
    for key, value in overrides.items():
        surface[key] = {**surface[key], **value}
    return surface


def test_the_same_surface_has_no_change():
    changes = api_diff.diff(_surface(), _surface(), MANIFESTS)
    assert changes == {"contracted": [], "internal": []}
    assert "No change" in api_diff.render("v1", "v2", changes)


def test_a_field_of_a_schema_a_pinned_route_reaches_is_contracted():
    """SessionView is two refs away from the pinned route, and still its answer."""
    new = _surface(schemas={"SessionView": {"fields": {}, "refs": []}})

    changes = api_diff.diff(_surface(), new, MANIFESTS)

    assert changes == {"contracted": ["`SessionView`: field `title` removed"], "internal": []}


def test_an_unpinned_route_and_schema_are_internal():
    old = _surface()
    new = _surface(
        operations={"GET /api/radio/genres": _op("Genre[]", ("Genre",))},
        schemas={"Genre": {"fields": {"name": {"type": "string", "required": False}}, "refs": []}},
    )

    changes = api_diff.diff(old, new, MANIFESTS)

    assert changes["contracted"] == []
    assert changes["internal"] == [
        "`GET /api/radio/genres`: response is now Genre[] (was untyped JSON)",
        "`Genre`: field `name` is now optional",
    ]


def test_events_are_sorted_by_the_manifest_that_pins_them():
    new = _surface(events={
        "source/state": _event(limits={"type": "Limits", "required": True}),
        "settings/language_changed": _event(language={"type": "integer", "required": True}),
    })

    changes = api_diff.diff(_surface(), new, MANIFESTS)

    assert changes["contracted"] == ["WebSocket `source/state`: field `source` removed"]
    assert changes["internal"] == [
        "WebSocket `settings/language_changed`: field `language` is now integer (was string)"
    ]


def test_a_model_only_a_pinned_event_carries_is_contracted():
    """No REST route reaches `Limits`: only the event does, and it is pinned."""
    new = _surface(models={"Limits": {"fields": {}, "refs": []}})

    changes = api_diff.diff(_surface(), new, MANIFESTS)

    assert changes == {"contracted": ["`Limits`: field `min_db` removed"], "internal": []}


def test_a_value_added_to_a_contracted_enum_is_contracted():
    """The apps' decoder refuses a whole state on a `phase` it does not know."""
    old = _surface(schemas={
        "SessionView": {"fields": {"phase": {"type": "Phase", "required": True}}, "refs": ["Phase"]},
        "Phase": {"fields": {}, "values": ["playing", "paused"], "refs": []},
    })
    new = _surface(schemas={
        "SessionView": {"fields": {"phase": {"type": "Phase", "required": True}}, "refs": ["Phase"]},
        "Phase": {"fields": {}, "values": ["playing", "paused", "buffering"], "refs": []},
    })

    changes = api_diff.diff(old, new, MANIFESTS)

    assert changes == {"contracted": ['`Phase`: value "buffering" added'], "internal": []}


def test_a_model_dropped_from_a_contracted_answer_is_contracted():
    """A variant leaving the `details` union disappears from the schemas whole."""
    old = _surface(schemas={
        "AudioState": {"fields": {"details": {"type": "SpotifyDetails | null", "required": True}},
                       "refs": ["SpotifyDetails"]},
        "SpotifyDetails": {"fields": {}, "refs": []},
    })
    new = _surface(schemas={
        "AudioState": {"fields": {"details": {"type": "null", "required": True}}, "refs": []},
    })

    changes = api_diff.diff(old, new, MANIFESTS)

    assert "`SpotifyDetails` removed" in changes["contracted"]


def test_a_literal_is_compared_by_its_values():
    assert api_diff._ref_name({"enum": ["off", "track"]}) == '"off" | "track"'
    assert api_diff._ref_name({"oneOf": [{"$ref": "#/A"}, {"$ref": "#/B"}]}) == "A | B"


def test_two_classes_of_one_pair_are_read_as_one_payload():
    """radio's and podcast's `source/favorite_added`: a change in either is a change."""
    radio = {"source": {"type": "string", "required": True},
             "station_id": {"type": "string", "required": True}}
    podcast = {"source": {"type": "string", "required": True},
               "podcast": {"type": "object", "required": True}}
    podcast_changed = {**podcast, "source": {"type": "integer", "required": True}}

    before = api_diff._merge_variants([radio, podcast])
    after = api_diff._merge_variants([radio, podcast_changed])

    assert before["station_id"]["required"] is False
    assert api_diff._field_changes("e", before, after) == [
        "e: field `source` is now integer | string (was string)"
    ]


def test_a_route_a_manifest_pins_through_a_literal_segment_is_contracted():
    """Manifests write a literal where the route has a parameter (`local` for `{target}`)."""
    ops = {"PUT /api/equalizer/target/{target}/enabled": _op()}
    manifests = [{"rest": [{"method": "PUT", "path": "/api/equalizer/target/local/enabled"}]}]

    assert api_diff.contracted_operations(ops, manifests) == set(ops)


def test_a_parameter_the_apps_send_is_contracted_and_another_is_not():
    """iOS sends `seek {position_ms}`; nobody sends `play_station`'s `station`."""
    def surface(commands):
        return {**_surface(), "commands": commands,
                "sent_commands": ["seek"], "sent_params": ["position_ms"]}

    old = surface({"cd/seek": _command(position_ms={"type": "number", "required": True}),
                   "radio/play_station": _command(station={"type": "object", "required": False})})
    new = surface({"cd/seek": _command(position_ms={"type": "number", "required": True},
                                       hint={"type": "string", "required": False}),
                   "radio/play_station": _command()})

    changes = api_diff.diff(old, new, MANIFESTS)

    assert changes["contracted"] == []
    assert changes["internal"] == [
        "command `cd/seek`: field `hint` added (string)",
        "command `radio/play_station`: field `station` removed",
    ]


def test_a_rename_of_a_parameter_the_apps_send_is_contracted_both_ways():
    """The old name is gone, and the new one is required: every press would 400."""
    def surface(commands):
        return {**_surface(), "commands": commands,
                "sent_commands": ["seek"], "sent_params": ["position_ms"]}

    old = surface({"cd/seek": _command(position_ms={"type": "number", "required": True})})
    new = surface({"cd/seek": _command(ms={"type": "number", "required": True})})

    changes = api_diff.diff(old, new, MANIFESTS)

    assert changes["contracted"] == [
        "command `cd/seek`: field `ms` added (number, required)",
        "command `cd/seek`: field `position_ms` removed",
    ]


def test_a_renamed_command_the_apps_send_reads_as_a_contracted_removal():
    old = {**_surface(), "commands": {"radio/next": _command()}, "sent_commands": ["next"], "sent_params": []}
    new = {**_surface(), "commands": {"radio/step": _command()}, "sent_commands": [], "sent_params": []}

    changes = api_diff.diff(old, new, MANIFESTS)

    assert changes["contracted"] == ["command `radio/next` removed"]
    assert changes["internal"] == ["command `radio/step` added"]


def test_the_apps_send_commands_every_source_declares():
    """Read from the vendored Swift: a parse that finds nothing marks nothing contracted."""
    commands, params = api_diff.commands_the_apps_send(REPO)

    assert {"pause", "seek", "play_context"} <= commands
    assert "position_ms" in params
