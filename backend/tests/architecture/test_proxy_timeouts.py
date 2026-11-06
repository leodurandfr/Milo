"""Structural guardrail: a client that waits longer than nginx must be given the time.

Every `/api` request from the browser goes through the nginx site that
`provisioning/network.sh::write_nginx_site` writes, and a `location` with no
`proxy_read_timeout` cuts the upstream at nginx's default of 60 s. A frontend
call that passes `timeout:` above that is budgeting for an answer the proxy will
never wait for: nginx replies 504 with an HTML body, the caller reads no
`detail`, and the handler carries on to the end on the backend.

That is what the WiFi adoption did. `POST /api/discovery/adopt-speaker` is
synchronous and runs well past 60 s on a path that succeeds (hotspot
association, gateway, push, then the server's WiFi restored in a `finally`); the
store waits 120 s. Replayed against a disposable nginx with the site's `/api/` block and an
upstream answering in 65 s: the client failed after 60.1 s with a 504, the UI
said the speaker refused the configuration, and the upstream's success hit a
broken pipe — the speaker had accepted and was rebooting onto the LAN.

Nothing else catches this class: the config is a heredoc in a shell module that
only an image build runs, and the frontend's budget is a literal three layers up.

Doctrine note (as in the other guardrails here): every extractor asserts its own
output is non-trivial first, so a broken parse fails loudly instead of passing on
an empty surface.
"""
import re
from dataclasses import dataclass
from pathlib import Path

from backend.core.multiroom.wifi_adoption import ADOPTION_DEADLINE, RESTORE_WORST_CASE, STEPS_DEADLINE

REPO_ROOT = Path(__file__).resolve().parents[3]

NETWORK_SH = REPO_ROOT / "provisioning" / "network.sh"
FRONTEND_SRC = REPO_ROOT / "frontend" / "src"

NGINX_DEFAULT_READ_TIMEOUT_S = 60

# What separates the backend's deadline, counted from when the handler starts,
# from the client's, counted from when the request left: nginx, uvicorn, the
# event loop, and the answer's way back.
ANSWER_MARGIN_S = 10

HEREDOC_RE = re.compile(r"write_nginx_site\(\)\s*\{.*?<< 'EOF'\n(.*?)\nEOF\n", re.DOTALL)
LOCATION_RE = re.compile(r"^\s*location\s+(?:(=|\^~|~\*|~)\s+)?(\S+)\s*\{", re.MULTILINE)
READ_TIMEOUT_RE = re.compile(r"^\s*proxy_read_timeout\s+(\d+)(ms|s|m|h|d|w)?\s*;", re.MULTILINE)
SET_HEADER_RE = re.compile(r"^\s*(proxy_set_header\s+[^;]+;)", re.MULTILINE)
UNIT_S = {"ms": 0.001, "s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}

TIMEOUT_RE = re.compile(r"\btimeout\s*:\s*([A-Za-z_$][\w$]*|\d[\d_]*)")
CALL_RE = re.compile(r"\bapiCall\.(?:get|post|put|patch|delete)\(")
URL_RE = re.compile(r"""['"`](/api/[^'"`]*)['"`]""")


@dataclass(frozen=True)
class Location:
    modifier: str
    path: str
    body: str
    inherited_read_timeout_s: float

    @property
    def read_timeout_s(self) -> float:
        m = READ_TIMEOUT_RE.search(self.body)
        if not m:
            return self.inherited_read_timeout_s
        return _seconds(m)

    @property
    def proxies(self) -> bool:
        return "proxy_pass http://milo_backend;" in self.body

    @property
    def headers(self) -> list[str]:
        return [" ".join(h.split()) for h in SET_HEADER_RE.findall(self.body)]


@dataclass(frozen=True)
class ClientTimeout:
    file: str
    url: str
    ms: int


def _seconds(m: re.Match) -> float:
    return int(m.group(1)) * UNIT_S[m.group(2) or "s"]


def _locations() -> list[Location]:
    m = HEREDOC_RE.search(NETWORK_SH.read_text())
    assert m, f"no `<< 'EOF'` heredoc found in {NETWORK_SH.name}::write_nginx_site"
    site = m.group(1)
    spans = []
    for loc in LOCATION_RE.finditer(site):
        depth, i = 1, loc.end()
        while depth:
            assert i < len(site), f"unbalanced braces after `location {loc.group(2)}`"
            depth += {"{": 1, "}": -1}.get(site[i], 0)
            i += 1
        spans.append((loc, i))
    # A server-level directive is what a location without its own inherits.
    outside, cursor = [], 0
    for loc, end in spans:
        outside.append(site[cursor:loc.start()])
        cursor = end
    outside.append(site[cursor:])
    server = READ_TIMEOUT_RE.search("".join(outside))
    inherited = _seconds(server) if server else NGINX_DEFAULT_READ_TIMEOUT_S
    return [Location(loc.group(1) or "", loc.group(2), site[loc.end():end - 1], inherited) for loc, end in spans]


def _serving(locations: list[Location], uri: str) -> Location:
    """The location nginx picks for `uri`: exact, then longest prefix, then regex."""
    for loc in locations:
        if loc.modifier == "=" and loc.path == uri:
            return loc
    prefixes = [loc for loc in locations if loc.modifier in ("", "^~") and uri.startswith(loc.path)]
    longest = max(prefixes, key=lambda loc: len(loc.path), default=None)
    if longest is not None and longest.modifier == "^~":
        return longest
    for loc in locations:
        if loc.modifier in ("~", "~*"):
            flags = re.IGNORECASE if loc.modifier == "~*" else 0
            if re.search(loc.path, uri, flags):
                return loc
    assert longest is not None, f"no nginx location serves {uri}"
    return longest


def _client_timeouts() -> list[ClientTimeout]:
    found, resolved = 0, []
    for path in sorted([*FRONTEND_SRC.rglob("*.js"), *FRONTEND_SRC.rglob("*.vue")]):
        text = path.read_text()
        rel = str(path.relative_to(REPO_ROOT))
        for t in TIMEOUT_RE.finditer(text):
            found += 1
            value = t.group(1)
            if value[0].isdigit():
                ms = int(value.replace("_", ""))
            else:
                const = re.search(rf"\bconst\s+{re.escape(value)}\s*=\s*(\d[\d_]*)\s*;", text)
                if not const:
                    continue
                ms = int(const.group(1).replace("_", ""))
            calls = [c for c in CALL_RE.finditer(text, 0, t.start())]
            if not calls:
                continue
            call = calls[-1]
            between = text[call.end():t.start()]
            if between.count("(") - between.count(")") < 0:
                continue  # the option sits after that call closed
            url = URL_RE.search(text, call.end(), t.start())
            if not url:
                continue
            uri = re.sub(r"\$\{[^}]*\}", "x", url.group(1)).split("?", 1)[0]  # nginx matches the path alone
            resolved.append(ClientTimeout(rel, uri, ms))
    assert found == len(resolved), (
        f"{found - len(resolved)} frontend `timeout:` option(s) could not be tied to an "
        f"apiCall URL and a millisecond value; resolved: {resolved}"
    )
    return resolved


def test_extractors_read_a_real_surface():
    """Without this, a moved heredoc or a renamed constant reads as 'nothing to check'."""
    locations = _locations()
    assert len(locations) >= 5, locations
    by_path = {loc.path: loc for loc in locations}
    assert "/api/" in by_path
    assert by_path["/ws"].read_timeout_s == 86400

    timeouts = _client_timeouts()
    assert len(timeouts) >= 4, timeouts
    assert any(
        t.file.endswith("stores/discoveryStore.js") and t.url == "/api/discovery/adopt-speaker"
        and t.ms == 120000
        for t in timeouts
    ), timeouts


def test_client_timeouts_fit_the_proxy():
    """A client timeout past 60 s needs a location that waits at least as long.

    Fails when a frontend call raises its `timeout:` above what the nginx location
    serving it allows: the user is then told the request failed while the backend
    finishes it.
    """
    locations = _locations()
    short = []
    for t in _client_timeouts():
        if t.ms <= NGINX_DEFAULT_READ_TIMEOUT_S * 1000:
            continue
        loc = _serving(locations, t.url)
        if not loc.proxies or loc.read_timeout_s * 1000 < t.ms:
            short.append(
                f"{t.file}: {t.url} waits {t.ms} ms, but `location {loc.modifier} {loc.path}` "
                f"{'does not proxy it' if not loc.proxies else f'cuts it at {loc.read_timeout_s:g} s'}"
            )
    assert not short, "\n".join(short)


def test_the_client_outwaits_the_adoption_deadline():
    """The adoption answers once the server's WiFi is restored, by ADOPTION_DEADLINE;
    the wizard must still be listening.

    Fails when `ADOPT_TIMEOUT_MS` (stores/discoveryStore.js) comes down to, or
    the backend's answer goes up to, within ANSWER_MARGIN_S of the other. The
    answer is due at the later of ADOPTION_DEADLINE and the steps' deadline plus
    the restore at its worst: on a WiFi-only server it travels only once wlan0
    is home again. Past the client's budget, the wizard gives up on its own and
    shows the generic message, while the backend's answer — a success, or the
    step that failed — reaches nobody. Nginx waiting longer than the client is
    `test_client_timeouts_fit_the_proxy`'s.
    """
    adopt = [t for t in _client_timeouts() if t.url == "/api/discovery/adopt-speaker"]
    assert len(adopt) == 1, adopt
    answered_by = max(ADOPTION_DEADLINE, STEPS_DEADLINE + RESTORE_WORST_CASE)
    assert answered_by + ANSWER_MARGIN_S <= adopt[0].ms / 1000, (
        f"{adopt[0].file} waits {adopt[0].ms} ms for an adoption the backend may "
        f"answer at {answered_by:g} s"
    )


def test_api_locations_forward_the_same_headers():
    """Every backend-bound `/api` location forwards what `/api/` forwards.

    Fails when a location carved out of `/api/` (for its own timeout, say) drops a
    `proxy_set_header`: without `Host $host` nginx sends the upstream's name and
    the backend's request gate refuses the call 403.
    """
    locations = _locations()
    reference = next(loc for loc in locations if loc.modifier == "" and loc.path == "/api/")
    assert len(reference.headers) >= 4, reference.headers
    carved = [loc for loc in locations if loc.path.startswith("/api/") and loc is not reference]
    drift = [
        f"location {loc.modifier} {loc.path}: missing {sorted(set(reference.headers) - set(loc.headers))}, "
        f"extra {sorted(set(loc.headers) - set(reference.headers))}"
        for loc in carved
        if loc.proxies and sorted(loc.headers) != sorted(reference.headers)
    ]
    assert not drift, "\n".join(drift)
