"""Legacy entry point for the GitHub Action: run every job in place on one
DATA_DIR checkout (default ./starlink-geoip-data); the Action pushes it.

The local scheduler (scheduler.py) replaces this; see README.
"""

from datetime import datetime, timezone

import atlas
import availability
import bgp
import feed_pop
import geoip_ptr
import monthly_latency_snapshot
import peeringdb
from jobcontext import JobContext
from map import process_map


def run_jobs(now: datetime):
    print("Current UTC date and time:", now.strftime("%Y-%m-%d %H:%M:%S"))
    ctx = JobContext.legacy(now=now)

    if now.day in [1, 7, 14, 21, 28]:
        print("Running monthly latency snapshot and availability zone refresh...")
        monthly_latency_snapshot.get_latency_json(ctx)
        print("Refreshing availability zones...")
        availability.refresh_availability_zone(ctx)

    print("Refreshing BGP")
    bgp.get_bgp_list(ctx)
    print("Refreshing PeeringDB")
    peeringdb.refresh_peeringdb(ctx)
    print("Refreshing Atlas probes")
    atlas.refresh_atlas_probes(ctx)

    print("Refreshing GeoIP feed and POP feed")
    feed_pop.refresh_feed_pop(ctx)
    print("Refreshing GeoIP DNS PTR data")
    geoip_ptr.refresh_geoip_ptr(ctx)

    print("Refreshing GeoIP map data")
    process_map.refresh_map(ctx)


if __name__ == "__main__":
    run_jobs(datetime.now(tz=timezone.utc))
