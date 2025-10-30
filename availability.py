#!/usr/bin/env python3
# flake8: noqa: E501

# npm install geobuf
# npx geobuf2json < availability-cells.pb > availability-cells.geojson

# https://api.starlink.com/public-files/availability-cells.pb

# visualize geojson with https://geojson.io/

import os
import json
import time
import httpx
import subprocess

from pathlib import Path

import geocoder
from shapely.geometry import Polygon


DATA_DIR = os.getenv("DATA_DIR", "./starlink-geoip-data")
token = os.getenv("GEOCODER_TOKEN", "")


def new_client():
    return httpx.Client(base_url="https://api.starlink.com")


def retrive_availability_cells():
    client = new_client()
    response = client.get("/public-files/availability-cells.pb")
    client.close()
    with open(Path(DATA_DIR).joinpath("availability/availability-cells.pb"), "wb") as f:
        f.write(response.content)


def ensure_dir():
    Path(DATA_DIR).joinpath("availability").mkdir(parents=True, exist_ok=True)


def convert():
    subprocess.run(
        [
            "bash",
            "-c",
            "npx geobuf2json < availability-cells.pb > availability-cells.geojson",
        ],
        cwd=Path(DATA_DIR).joinpath("availability"),
        check=True,
    )
    filepath = Path(DATA_DIR).joinpath("availability/availability-cells.geojson")
    with open(filepath, "r") as f:
        data = json.load(f)
    with open(filepath, "w") as f:
        f.write(json.dumps(data, indent=4))


def classify():
    status_dict = {}
    with open(
        Path(DATA_DIR).joinpath("availability/availability-cells.geojson"), "r"
    ) as f:
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
        filepath = Path(DATA_DIR).joinpath(
            "availability/{}.geojson".format(status.replace(" ", "_"))
        )
        with open(filepath, "w") as f:
            obj = {
                "type": "FeatureCollection",
                "features": [feature for feature in status_dict[status]],
            }
            f.write(json.dumps(obj, indent=4))

            csv_filepath = Path(DATA_DIR).joinpath(
                "availability/{}.csv".format(status.replace(" ", "_"))
            )
            with open(csv_filepath, "w") as csv_f:
                print(csv_filepath)
                count = 0
                for feature in obj["features"]:
                    polygonType = feature["geometry"]["type"]
                    if polygonType == "Polygon":
                        coordinates = [feature["geometry"]["coordinates"]]
                    elif polygonType == "MultiPolygon":
                        coordinates = feature["geometry"]["coordinates"]
                    for i in coordinates:
                        for j in i:
                            time.sleep(0.1)
                            count += 1

                            polygon = Polygon(j)
                            g = geocoder.google(
                                "{}, {}".format(polygon.centroid.y, polygon.centroid.x),
                                key=token,
                            )
                            if g.json is None:
                                for k in j:
                                    g = geocoder.google(
                                        "{}, {}".format(k[1], k[0]), key=token
                                    )
                                    if g.json:
                                        country = g.json["country"]
                                        lat = k[1]
                                        lon = k[0]
                                        break
                            else:
                                if "country" not in g.json:
                                    country = g.json["address"]
                                else:
                                    country = g.json["country"]
                                lat = polygon.centroid.y
                                lon = polygon.centroid.x

                            if country == "AQ" and count > 1:
                                continue
                            else:
                                line = "{},{},{},{}\n".format(
                                    country,
                                    lat,
                                    lon,
                                    "http://maps.google.com/maps?z=12&t=m&q=loc:{}+{}".format(
                                        lat, lon
                                    ),
                                )
                                csv_f.write(line)
                                print(line)


def refresh_availability_zone():
    ensure_dir()
    retrive_availability_cells()
    convert()
    classify()
