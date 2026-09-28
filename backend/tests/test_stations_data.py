import csv

from app.stations.loader import DEFAULT_ALIASES, complex_name
from app.stations.lookup import normalize_query


def read_aliases() -> list[dict]:
    with DEFAULT_ALIASES.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_aliases_csv_is_well_formed():
    rows = read_aliases()
    assert {"alias", "complex_id"} <= set(rows[0])
    assert all(r["alias"].strip() and r["complex_id"].isdigit() for r in rows)
    keys = [(normalize_query(r["alias"]), r["complex_id"]) for r in rows]
    assert len(keys) == len(set(keys)), "duplicate alias after normalization"


def test_aliases_cover_common_names():
    names = {normalize_query(r["alias"]) for r in read_aliases()}
    assert len(names) >= 30
    for required in [
        "herald sq", "barclays", "port authority", "grand central", "penn station",
        "wtc", "world trade ctr", "union sq", "columbus circle", "yankee stadium",
        "citi field", "jackson hts", "atlantic terminal",
    ]:  # fmt: skip
        assert required in names


def test_complex_name_orders_by_frequency_then_name():
    assert complex_name(["8 Av", "14 St"]) == "14 St/8 Av"
    assert complex_name(["42 St-Port Authority Bus Terminal"] + ["Times Sq-42 St"] * 4) == (
        "Times Sq-42 St/42 St-Port Authority Bus Terminal"
    )
    assert complex_name(["14 St-Union Sq"] * 3) == "14 St-Union Sq"
