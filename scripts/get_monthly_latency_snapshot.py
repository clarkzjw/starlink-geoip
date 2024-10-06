import os
import json
import httpx
import datetime

from pathlib import Path


MARITIME_LATENCY = "https://api.starlink.com/public-files/metrics_maritime.json"
RESIDENTIAL_LATENCY = "https://api.starlink.com/public-files/metrics_residential.json"

DATA_DIR = os.getenv("DATA_DIR", "../starlink-geoip-data")


def get_latency_json():
    with httpx.Client() as client:
        maritime = client.get(MARITIME_LATENCY)
        residential = client.get(RESIDENTIAL_LATENCY)
        with open(Path(DATA_DIR).joinpath("latency/metrics_maritime").joinpath("metrics_maritime-{}.json".format(datetime.datetime.now().strftime("%Y%m"))), 'w') as f:
            json.dump(maritime.json(), f, indent=2)
        with open(Path(DATA_DIR).joinpath("latency/metrics_residential").joinpath("metrics_residential-{}.json".format(datetime.datetime.now().strftime("%Y%m"))), 'w') as f:
            json.dump(residential.json(), f, indent=2)


if __name__ == "__main__":
    get_latency_json()
