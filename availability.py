#!/usr/bin/env python3
# flake8: noqa: E501

# npm install geobuf
# npx geobuf2json < availability-cells.pb > availability-cells.geojson

# https://api.starlink.com/public-files/availability-cells.pb

# visualize geojson with https://geojson.io/

import json
import time
import httpx
import subprocess

from pathlib import Path

import geocoder
from shapely.geometry import Polygon

from jobcontext import JobContext


def new_client():
    return httpx.Client(base_url="https://api.starlink.com")


def retrive_availability_cells(out_dir: Path):
    with new_client() as client:
        response = client.get("/public-files/availability-cells.pb", timeout=120)
        response.raise_for_status()
    with open(out_dir.joinpath("availability-cells.pb"), "wb") as f:
        f.write(response.content)


def convert(out_dir: Path):
    subprocess.run(
        [
            "bash",
            "-c",
            "npx geobuf2json < availability-cells.pb > availability-cells.geojson",
        ],
        cwd=out_dir,
        check=True,
    )
    filepath = out_dir.joinpath("availability-cells.geojson")
    with open(filepath, "r") as f:
        data = json.load(f)
    with open(filepath, "w") as f:
        f.write(json.dumps(data, indent=4))


def classify(out_dir: Path):
    status_dict = {}
    with open(out_dir.joinpath("availability-cells.geojson"), "r") as f:
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
        filepath = out_dir.joinpath("{}.geojson".format(status.replace(" ", "_")))
        with open(filepath, "w") as f:
            obj = {
                "type": "FeatureCollection",
                "features": [feature for feature in status_dict[status]],
            }
            f.write(json.dumps(obj, indent=4))

            csv_filepath = out_dir.joinpath("{}.csv".format(status.replace(" ", "_")))
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
                            g = geocoder.arcgis(
                                "{}, {}".format(polygon.centroid.y, polygon.centroid.x)
                            )
                            if g.json is None:
                                for k in j:
                                    g = geocoder.arcgis("{}, {}".format(k[1], k[0]))
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


def refresh_availability_zone(ctx: JobContext):
    out_dir = ctx.out_folder("availability")
    retrive_availability_cells(out_dir)
    convert(out_dir)
    classify(out_dir)


if __name__ == "__main__":
    refresh_availability_zone(JobContext.legacy())
