# npm install geobuf
# npx geobuf2json < availability-cells.pb > availability-cells.geojson

# https://api.starlink.com/public-files/availability-cells.pb

# visualize geojson with https://geojson.io/

import os
import json
import httpx
import subprocess

from pathlib import Path


DATA_DIR = os.getenv("DATA_DIR", "./starlink-geoip-data")


def new_client():
    return httpx.Client(base_url='https://api.starlink.com')


def retrive_availability_cells():
    client = new_client()
    response = client.get('/public-files/availability-cells.pb')
    client.close()
    with open(Path(DATA_DIR).joinpath("availability/availability-cells.pb"), "wb") as f:
        f.write(response.content)


def ensure_dir():
    Path(DATA_DIR).joinpath("availability").mkdir(parents=True, exist_ok=True)


def convert():
    subprocess.run(["bash", "-c", "npx geobuf2json < availability-cells.pb > availability-cells.geojson"], cwd=Path(DATA_DIR).joinpath("availability"), check=True)
    filepath = Path(DATA_DIR).joinpath("availability/availability-cells.geojson")
    with open(filepath, "r") as f:
        data = json.load(f)
    with open(filepath, "w") as f:
        f.write(json.dumps(data, indent=4))


def classify():
    status_dict = {}
    with open(Path(DATA_DIR).joinpath("availability/availability-cells.geojson"), "r") as f:
        data = json.load(f)
        for feature in data["features"]:
            status = feature["properties"]["status"]
            if "expected" in feature["properties"]:
                status += " " + feature["properties"]["expected"]
            if status not in status_dict:
                status_dict[status] = []
            status_dict[status].append(feature)

    for status in status_dict.keys():
        # faq
        # test
        # blacklisted
        # waitlisted Expanding in 2025
        # waitlisted Sold Out
        # waitlisted Expanding in 2025
        # waitlisted Service date is unknown at this time
        filepath = Path(DATA_DIR).joinpath("availability/{}.geojson".format(status.replace(" ", "_")))
        with open(filepath, "w") as f:
            f.write(json.dumps({
                "type": "FeatureCollection",
                "features": [feature for feature in status_dict[status]]
            }, indent=4))


if __name__ == "__main__":
    ensure_dir()
    retrive_availability_cells()
    convert()
    classify()
