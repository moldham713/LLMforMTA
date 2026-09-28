"""Cut the checked-in test fixture out of a real subway GTFS feed.

Keeps every stop (so realtime destinations have names), but trips only for a handful
of stations: one trip per (route, station, direction) calling there, and only those
trips' stop_times at those stations. Routes serving each kept station match the real feed.

    python scripts/build_gtfs_fixture.py GTFS_DIR STATIONS_CSV OUT_DIR
"""

import csv
import sys
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
    chosen: dict[tuple, str] = {}
    with st_path.open(newline="", encoding="utf-8-sig") as f:
        st_h = csv.DictReader(f).fieldnames
    with st_path.open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if row["stop_id"] in parent_of:
                t = trip_by_id[row["trip_id"]]
                key = (t["route_id"], parent_of[row["stop_id"]], t["direction_id"])
                chosen.setdefault(key, row["trip_id"])
    trip_ids = set(chosen.values())

    with st_path.open(newline="", encoding="utf-8-sig") as f:
        stop_times = [
            r for r in csv.DictReader(f) if r["trip_id"] in trip_ids and r["stop_id"] in parent_of
        ]

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
    write(
        out / "transfers.txt",
        tr_h,
        [t for t in tr if t["from_stop_id"] in parent_of and t["to_stop_id"] in parent_of],
    )
    write(out / "feed_info.txt", fi_h, fi)

    st_header, stations = read(stations_csv)
    write(
        out.parent / "stations.csv",
        st_header,
        [s for s in stations if s["GTFS Stop ID"] in PARENTS],
    )


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]))
