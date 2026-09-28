import pytest

from app.agent.tools import Toolbox


def said(quote: str, *rider_text: str) -> bool:
    return Toolbox(None, None, rider_text=list(rider_text))._rider_said(quote)


@pytest.mark.parametrize(
    ("quote", "rider_text"),
    [
        ("6", ["When's the next downtown 6 at 86th St?"]),
        ("the 6 train", ["next downtown 6 at 86th"]),
        ("downtown", ["next downtwon 6 at 86th stret"]),  # rider's typo
        ("Queens-bound", ["queens bound 7 at grand centrl"]),
        ("going to Brooklyn", ["next L at Times Square going to Brooklyn"]),
        ("to Canarsie", ["L at 8th Av to Canarsie"]),
        ("uptown", ["which 86 st?", "uptown"]),  # an earlier or later turn counts
        ("shuttle", ["shuttle at Times Square to Grand Central"]),
    ],
)
def test_rider_said(quote, rider_text):
    assert said(quote, *rider_text)


@pytest.mark.parametrize(
    ("quote", "rider_text"),
    [
        ("", ["next 6 at 86th"]),
        ("the", ["next 6 at 86th"]),  # only filler
        ("downtown", ["next 6 at 86th"]),  # invented
        ("6", ["next train at 86th"]),  # "6" is not "86"
        ("uptown", ["downtown 1 at 96"]),
        ("Brooklyn", ["next A at Jay St"]),
    ],
)
def test_rider_did_not_say(quote, rider_text):
    assert not said(quote, *rider_text)


def test_without_transcript_any_quote_counts():
    assert Toolbox(None, None)._rider_said("anything")
    assert not Toolbox(None, None)._rider_said("")
