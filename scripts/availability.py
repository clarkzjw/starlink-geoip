# npm install geobuf
# npx geobuf2json < availability-cells.pb > availability-cells.geojson

# https://api.starlink.com/public-files/availability-cells.pb

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


if __name__ == "__main__":
    ensure_dir()
    retrive_availability_cells()
    convert()
