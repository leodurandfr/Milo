"""The playlist formats a station's site hands out, read for the stream they list.

What breaks when these fail: a custom station added by its `.pls`/`.m3u` link
ends LOAD_FAILED ("playlist gave no stream") although the file lists a stream,
or is handed to mpv as a playlist, which mpv fetches again at every EOF.
"""
import pytest

from backend.sources.radio.playlist import is_playlist_url, parse_playlist

STREAM = "http://ice1.somafm.com/groovesalad-128-mp3"


@pytest.mark.parametrize("text", [
    f"[playlist]\nnumberofentries=2\nFile1={STREAM}\nTitle1=Groove Salad\n"
    "File2=http://ice2.somafm.com/groovesalad-128-mp3\nVersion=2\n",
    f"[playlist]\r\nNumberOfEntries=1\r\nfile1 = {STREAM}\r\nLength1=-1\r\n",
    f"[Playlist]\nTitle1=Live at http://somafm.com\nFILE1={STREAM}\n",
    f"#EXTM3U\n#EXTINF:-1,Groove Salad\n{STREAM}\nhttp://ice2.somafm.com/x\n",
    f'<asx version="3.0"><entry><title>Groove</title><REF HREF="{STREAM}"/></entry></asx>',
    '<?xml version="1.0"?><playlist xmlns="http://xspf.org/ns/0/"><info>https://somafm.com</info>'
    f"<trackList><track><location>{STREAM}</location></track></trackList></playlist>",
], ids=["pls", "pls-crlf-spaced", "pls-url-in-title", "m3u", "asx", "xspf"])
def test_the_first_stream_listed_is_found(text):
    assert parse_playlist(text) == STREAM


def test_a_nested_playlist_is_passed_over_for_the_stream_after_it():
    text = f"[playlist]\nFile1=http://mirror.example/groovesalad.pls\nFile2={STREAM}\n"
    assert parse_playlist(text) == STREAM


def test_an_entity_in_an_xml_playlist_is_decoded():
    assert parse_playlist('<asx><entry><ref href="http://s.example/live?a=1&amp;b=2"/></entry></asx>') \
        == "http://s.example/live?a=1&b=2"


@pytest.mark.parametrize("text", [
    "",
    "[playlist]\nnumberofentries=0\nVersion=2\n",
    "#EXTM3U\n#EXTINF:-1,Nothing\n",
    "[playlist]\nFile1=mms://old.example/live\n",
    "relative/stream.mp3\n",
    "<html><body>Not found</body></html>",
    "[playlist]\nFile1=http://mirror.example/groovesalad.pls\n",
    "#EXTM3U\n#EXT-X-TARGETDURATION:10\n#EXTINF:10,\nhttp://cdn.example/seg123.aac\n",
], ids=["empty", "pls-no-entry", "m3u-comments-only", "pls-not-http", "m3u-relative", "html-page",
        "only-a-playlist", "hls-under-m3u"])
def test_a_playlist_with_no_http_stream_gives_none(text):
    assert parse_playlist(text) is None


@pytest.mark.parametrize("url, expected", [
    ("https://somafm.com/groovesalad.pls", True),
    ("http://radio.example/LIVE.M3U?token=1", True),
    ("http://radio.example/live.asx#x", True),
    ("http://radio.example/live.xspf", True),
    ("http://radio.example/live.m3u8", False),
    ("http://radio.example/live.mp3", False),
    ("http://radio.example/listen?format=.pls", False),
], ids=["pls", "m3u-query-case", "asx-fragment", "xspf", "hls", "mp3", "suffix-in-query"])
def test_a_playlist_url_is_told_by_its_path(url, expected):
    assert is_playlist_url(url) is expected
