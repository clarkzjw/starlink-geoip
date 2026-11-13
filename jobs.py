from dataclasses import dataclass

HOUR = 3600


@dataclass(frozen=True)
class Job:
    name: str
    target: str  # "module:function", called with a JobContext
    cron: str | None  # None: runs only when triggered by `after`
    owns: tuple[str, ...]
    inputs: tuple[str, ...] = ()
    timeout: int = HOUR
    # Run this job after any of these jobs publishes a commit.
    after: tuple[str, ...] = ()


JOBS: dict[str, Job] = {
    j.name: j
    for j in [
        Job(
            name="feed_pop",
            target="feed_pop:refresh_feed_pop",
            cron="*/5 * * * *",
            owns=("feed/", "pop/"),
            inputs=("feed/feed-latest.csv", "pop/pops-latest.csv"),
            timeout=300,
        ),
        Job(
            name="geoip_ptr",
            target="geoip_ptr:refresh_geoip_ptr",
            cron="0 */6 * * *",
            owns=("geoip/",),
            inputs=(
                "feed/feed-latest.csv",
                "pop/pops-latest.csv",
                "geoip/geoip-pops-ptr-latest.csv",
                "geoip/geoip-pops-ptr-latest.meta.json",
            ),
            timeout=6 * HOUR,
        ),
        Job(
            name="map",
            target="map.process_map:refresh_map",
            cron=None,
            owns=("map/",),
            after=("geoip_ptr", "peeringdb"),
            timeout=4 * HOUR,
        ),
        Job(
            name="bgp",
            target="bgp:get_bgp_list",
            cron="0 * * * *",
            owns=("bgp/",),
            timeout=1200,
        ),
        Job(
            name="peeringdb",
            target="peeringdb:refresh_peeringdb",
            cron="0 3 * * *",
            owns=("peeringdb/",),
            timeout=600,
        ),
        Job(
            name="atlas",
            target="atlas:refresh_atlas_probes",
            cron="30 */6 * * *",
            owns=("atlas/",),
            timeout=3 * HOUR,
        ),
        Job(
            name="latency_snapshot",
            target="monthly_latency_snapshot:get_latency_json",
            cron="0 2 1,7,14,21,28 * *",
            owns=("latency/",),
            timeout=600,
        ),
        Job(
            name="availability",
            target="availability:refresh_availability_zone",
            cron="0 2 1,7,14,21,28 * *",
            owns=("availability/",),
            timeout=12 * HOUR,
        ),
    ]
}


def dependents(job_name: str) -> list[Job]:
    return [j for j in JOBS.values() if job_name in j.after]


def _check() -> None:
    owners: dict[str, str] = {}
    for job in JOBS.values():
        for prefix in job.owns:
            assert prefix.endswith("/"), f"{job.name}: owns {prefix!r} must end with /"
            for other, other_job in owners.items():
                assert not (
                    prefix.startswith(other) or other.startswith(prefix)
                ), f"{job.name} owns {prefix!r}, overlapping {other_job}'s {other!r}"
            owners[prefix] = job.name
        for dep in job.after:
            assert dep in JOBS, f"{job.name}: unknown dependency {dep!r}"


_check()
