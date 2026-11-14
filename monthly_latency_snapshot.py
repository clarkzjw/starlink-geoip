import json
import httpx

from jobcontext import JobContext


MARITIME_LATENCY = "https://api.starlink.com/public-files/metrics_maritime.json"
RESIDENTIAL_LATENCY = "https://api.starlink.com/public-files/metrics_residential.json"


def get_latency_json(ctx: JobContext):
    with httpx.Client() as client:
        for url, name in [
            (MARITIME_LATENCY, "metrics_maritime"),
            (RESIDENTIAL_LATENCY, "metrics_residential"),
        ]:
            response = client.get(url, timeout=60)
            response.raise_for_status()
            path = ctx.out(f"latency/{name}/{name}-{ctx.year_month}.json")
            with open(path, "w") as f:
                json.dump(response.json(), f, indent=2)


if __name__ == "__main__":
    get_latency_json(JobContext.legacy())
