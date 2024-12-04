import re
import os
import sys
import csv
import time
import json
import httpx
import requests
import geocoder
import pycountry
from pprint import pprint
from pathlib import Path


GEOIP_JSON_URL = "https://raw.githubusercontent.com/clarkzjw/starlink-geoip-data/refs/heads/master/geoip/geoip-latest.json"
NETFAC_JSON_TEMPLATE_URL = "https://raw.githubusercontent.com/clarkzjw/starlink-geoip-data/refs/heads/master/peeringdb/net-{}.json"
PEERINGDB_NET_ID = [18747, 36005]
DATA_DIR = os.getenv("DATA_DIR", "../starlink-geoip-data")
GEOIP_MAP_DIR = Path(DATA_DIR).joinpath("map")


def get_geoip_json() -> dict:
    geoipJson = requests.get(GEOIP_JSON_URL)
    return json.loads(geoipJson.content)


def get_pop_list(geoipJson: dict):
    with open(Path(GEOIP_MAP_DIR).joinpath("pop.json"), 'r+') as f:
        pops = json.load(f)
        pop_code_list = [x["code"] for x in pops]

        for pop in geoipJson["pop_subnet_count"]:
            if re.match(r"customer\.[a-z0-9]+\.pop\.starlinkisp\.net\.", pop[0]):
                pop_code = pop[0].split('.')[1]
                if pop_code not in pop_code_list:
                    print(pop_code)
                    pops.append({
                        "code": pop_code,
                        "dns": pop[0],
                        "city": "",
                        "country": "",
                        "lat": 0,
                        "lon": 0,
                        "show": False
                    })
        pops.sort(key=lambda x: x["code"])

        f.seek(0)
        json.dump(pops, f, indent=4)
        f.truncate()

def convert_country_code(two_letter_code: str) -> str:
    country = pycountry.countries.get(alpha_2=two_letter_code)
    if country is not None:
        return country.name
    print("Country code {} not found".format(two_letter_code))
    return two_letter_code


def get_netfac_list():
    token = os.getenv("GEOCODER_TOKEN", "")
    if len(token) == 0:
        print("Please set GEOCODER_TOKEN in environment variable")
        sys.exit(1)

    netfac_geojson = {
        "type": "FeatureCollection",
        "features": []
    }

    netfac_ids = []
    peeringdb_client = httpx.Client(base_url='https://www.peeringdb.com/')
    for netid in PEERINGDB_NET_ID:
        netfac_json = requests.get(NETFAC_JSON_TEMPLATE_URL.format(netid))
        netfac_json = json.loads(netfac_json.content)
        netfac_json = netfac_json["data"][0]["netfac_set"]
        for netfac in netfac_json:
            time.sleep(10)
            netfac_ids.append(netfac["id"])
            response = peeringdb_client.get(f'api/netfac/{netfac["id"]}')
            netfac_detail = response.json()
            name = netfac_detail["data"][0]["name"]
            address = ""
            fac_id = netfac_detail["data"][0]["fac_id"]
            lat, lon = netfac_detail["data"][0]["fac"]["latitude"], netfac_detail["data"][0]["fac"]["longitude"]

            if lat is None or lon is None:
                address += ",".join([netfac_detail["data"][0]["fac"]["address1"], netfac_detail["data"][0]["fac"]["city"], netfac_detail["data"][0]["fac"]["country"]])
                g = geocoder.google(address, key=token)
                lat, lon = g.json["lat"], g.json["lng"]

            netfac_geojson["features"].append({
                "type": "Feature",
                "geometry": {
                        "type": "Point",
                        "coordinates": [float(lon), float(lat)]
                    },
                "properties": {
                    "title": name,
                    "name": name,
                    "description": "https://www.peeringdb.com/fac/{}".format(fac_id),
                }
            })

    peeringdb_client.close()
    json.dump(netfac_geojson, open(Path(GEOIP_MAP_DIR).joinpath("netfac.json"), "w"), indent=4)


def get_city_list(geoipJson: dict):
    token = os.getenv("GEOCODER_TOKEN", "")
    if len(token) == 0:
        print("Please set GEOCODER_TOKEN in environment variable")
        sys.exit(1)

    city_json = {
        "type": "FeatureCollection",
        "features": []
    }

    geoipJson = geoipJson["valid"]
    for country in geoipJson:
        for state in geoipJson[country]:
            for city in geoipJson[country][state]:
                # international waters:
                # https://en.wikipedia.org/wiki/ISO_3166-1_alpha-2#User-assigned_code_elements
                if country == "XZ":
                    continue
                country_full = convert_country_code(country)
                print("{}, {}, {}".format(country, state, city))

                g = geocoder.google("{}, {}, {}".format(city, state, country_full), key=token)
                if g.json is None:
                    g = geocoder.google("{}, {}".format(city, country_full), key=token)
                    if g.json is None:
                        g = geocoder.google("{}".format(city), key=token)
                source_gps = g.json

                description = "<i>{},{},{}</i><br>".format(country, state, city)
                for ip in geoipJson[country][state][city]["ips"]:
                    description += "{}<br>({})<br>".format(ip[0], ip[1])

                city_json["features"].append({
                    "type": "Feature",
                    "geometry": {
                            "type": "Point",
                            "coordinates": [float(source_gps["lng"]), float(source_gps["lat"])]
                        },
                    "properties": {
                        "title": city,
                        "name": "{}, {}, {}".format(country, state, city),
                        "description": description,
                    }
                })

    json.dump(city_json, open(Path(GEOIP_MAP_DIR).joinpath("city.json"), "w"), indent=4)



if __name__ == "__main__":
    geoipJson = get_geoip_json()

    get_pop_list(geoipJson)
    get_city_list(geoipJson)
    get_netfac_list()
