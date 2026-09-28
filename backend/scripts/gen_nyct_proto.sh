#!/bin/sh
# Regenerate app/realtime/nyct_subway_pb2.py from MTA's nyct-subway.proto.
# Run inside the api container: sh scripts/gen_nyct_proto.sh
# grpcio-tools is only needed here, so it is installed ad hoc rather than pinned.
set -eu
pip install -q grpcio-tools
cd app/realtime
python -m grpc_tools.protoc -Iproto --python_out=. proto/nyct-subway.proto
# protoc emits a top-level import; the bindings live under google.transit.
sed -i 's/^import gtfs_realtime_pb2 as/from google.transit import gtfs_realtime_pb2 as/' nyct_subway_pb2.py
grep -m1 'Protobuf Python Version' nyct_subway_pb2.py
