from datetime import datetime, timezone

import bgp
import peeringdb
import atlas
import map.process_map
import monthly_latency_snapshot
import availability
import geoip_pop


def run_jobs(now: datetime):

    print("Current UTC date and time:", now.strftime("%Y-%m-%d %H:%M:%S"))

    hour = now.hour
    day = now.day

    if hour == 0:
        if day in [1, 7, 14, 21, 28]:
            print("Running monthly latency snapshot and availability zone refresh...")
            monthly_latency_snapshot.get_latency_json()
            print("Refreshing availability zones...")
            availability.refresh_availability_zone()

        print("Refreshing BGP")
        bgp.get_bgp_list()
        print("Refreshing PeeringDB")
        peeringdb.refresh_peeringdb()
        print("Refreshing Atlas probes")
        atlas.refresh_atlas_probes()

    # print("Refreshing GeoIP and POP data")
    # geoip_pop.refresh_geoip_pop()

    print("Refreshing GeoIP map data")
    map.process_map.refresh_map()


if __name__ == "__main__":
    now = datetime.now(tz=timezone.utc)
    run_jobs(now)
