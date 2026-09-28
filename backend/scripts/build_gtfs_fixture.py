"""Cut the checked-in test fixture out of a real subway GTFS feed.

Keeps every stop and the whole stations dataset, plus two kinds of trips: one trip per
(route, station, direction) at a handful of chosen stations (stop_times at those stations
only), so routes there match the real feed including reroutes; and one full trip per
(route, direction) on its usual stop pattern, so every line's stop order is known.

    python scripts/build_gtfs_fixture.py GTFS_DIR STATIONS_CSV OUT_DIR
"""

import csv
import sys
from collections import Counter, defaultdict
from pathlib import Path

PARENTS = {
    # Times Sq-42 St complex (611)
    "127", "725", "902", "R16", "A27",
    # Grand Central-42 St (610)
    "631", "723", "901",
    # 86 St on six different lines
    "121", "626", "Q04", "A20", "R44", "N10",
    # 14 St-Union Sq (602) and neighbours
    "L03", "R20", "635", "634", "R21", "L05",
    # 14 St/8 Av (618) and 14 St/6 Av (601)
    "A31", "L01", "132", "D19", "L02",
    # 8 Av in Brooklyn: a same-name decoy for cross-street queries
    "N02",
    # W 4 St: two GTFS stops sharing one MTA Station ID
    "A32", "D20",
    # 34 St-Penn Station, two separate complexes sharing an alias
    "128", "A28",
}  # fmt: skip


def read(path: Path) -> tuple[list[str], list[dict]]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        return list(reader.fieldnames), list(reader)


def write(path: Path, header: list[str], rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, header, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    print(f"{path.name}: {len(rows)} rows")


def main(gtfs_dir: Path, stations_csv: Path, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)

    stops_h, stops = read(gtfs_dir / "stops.txt")
    chosen_stops = [s for s in stops if s["stop_id"] in PARENTS or s["parent_station"] in PARENTS]
    parent_of = {s["stop_id"]: s["parent_station"] or s["stop_id"] for s in chosen_stops}

    trips_h, trips = read(gtfs_dir / "trips.txt")
    trip_by_id = {t["trip_id"]: t for t in trips}

    st_path = gtfs_dir / "stop_times.txt"
    with st_path.open(newline="", encoding="utf-8-sig") as f:
        st_h = csv.DictReader(f).fieldnames
    chosen: dict[tuple, str] = {}
    patterns: dict[str, list[str]] = defaultdict(list)
    with st_path.open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            patterns[row["trip_id"]].append(row["stop_id"])
            if row["stop_id"] in parent_of:
                t = trip_by_id[row["trip_id"]]
                key = (t["route_id"], parent_of[row["stop_id"]], t["direction_id"])
                chosen.setdefault(key, row["trip_id"])
    trip_ids = set(chosen.values())

    # One full trip per (route, direction) on its most common stop pattern, so the fixture
    # knows every line's stop order (for direction-by-destination) without whole schedules.
    by_pattern: dict[tuple, Counter] = defaultdict(Counter)
    example: dict[tuple, str] = {}
    for trip_id, stop_ids in patterns.items():
        t = trip_by_id[trip_id]
        key = (t["route_id"], t["direction_id"])
        by_pattern[key][tuple(stop_ids)] += 1
        example.setdefault((key, tuple(stop_ids)), trip_id)
    full_trips = {example[(key, c.most_common(1)[0][0])] for key, c in by_pattern.items()}
    del patterns

    with st_path.open(newline="", encoding="utf-8-sig") as f:
        stop_times = [
            r
            for r in csv.DictReader(f)
            if r["trip_id"] in full_trips
            or (r["trip_id"] in trip_ids and r["stop_id"] in parent_of)
        ]
    trip_ids |= full_trips

    kept_trips = [t for t in trips if t["trip_id"] in trip_ids]
    route_ids = {t["route_id"] for t in kept_trips}
    service_ids = {t["service_id"] for t in kept_trips}

    routes_h, routes = read(gtfs_dir / "routes.txt")
    cal_h, cal = read(gtfs_dir / "calendar.txt")
    cd_h, cd = read(gtfs_dir / "calendar_dates.txt")
    tr_h, tr = read(gtfs_dir / "transfers.txt")
    fi_h, fi = read(gtfs_dir / "feed_info.txt")

    # Every stop, so realtime destinations anywhere on the network have names.
    write(out / "stops.txt", stops_h, stops)
    write(out / "routes.txt", routes_h, [r for r in routes if r["route_id"] in route_ids])
    write(out / "trips.txt", trips_h, kept_trips)
    write(out / "stop_times.txt", st_h, stop_times)
    write(out / "calendar.txt", cal_h, [c for c in cal if c["service_id"] in service_ids])
    write(out / "calendar_dates.txt", cd_h, [c for c in cd if c["service_id"] in service_ids])
    write(out / "transfers.txt", tr_h, tr)
    write(out / "feed_info.txt", fi_h, fi)

    # The whole stations dataset (it is small), so every complex is searchable.
    st_header, stations = read(stations_csv)
    write(out.parent / "stations.csv", st_header, stations)


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]))
