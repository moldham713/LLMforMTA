import pytest

from app.stations.lookup import normalize_query, query_parts


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("14th street and 8th ave", "14 st / 8 av"),
        ("14th and 8th", "14 / 8"),
        ("Eighth Avenue & W. 14th St.", "8 av / w 14 st"),
        ("14 St/8 Av", "14 st / 8 av"),
        ("Times Sq-42 St", "times sq / 42 st"),
        ("Times Square", "times sq"),
        ("St. Mark's Place", "st marks pl"),
        ("Jackson Heights–Roosevelt Avenue", "jackson hts / roosevelt av"),
        ("second avenue", "2 av"),
        ("1st Ave at 14th Street", "1 av / 14 st"),
        ("Cathedral Pkwy (110 St)", "cathedral pkwy 110 st"),
        ("  86   ST  ", "86 st"),
        ("", ""),
        ("& and /", ""),
    ],
)
def test_normalize_query(raw, expected):
    assert normalize_query(raw) == expected


def test_normalization_is_idempotent():
    once = normalize_query("14th Street & Eighth Avenue")
    assert normalize_query(once) == once


def test_query_parts():
    assert query_parts("14 st / 8 av") == ["14 st", "8 av"]
    assert query_parts("times sq") == ["times sq"]
    assert query_parts("") == []
