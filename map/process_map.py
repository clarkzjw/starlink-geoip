import re
import os
import sys
import csv
import time
import json
import requests
import geocoder
import pycountry
from pprint import pprint


GEOIP_JSON_URL = "https://raw.githubusercontent.com/clarkzjw/starlink-geoip-data/refs/heads/master/geoip/geoip-latest.json"


def get_geoip_json() -> dict:
    geoipJson = requests.get(GEOIP_JSON_URL)
    return json.loads(geoipJson.content)


def get_pop_list(geoipJson: dict):
    with open("./data/pop.json", 'r+') as f:
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
    return pycountry.countries.get(alpha_2=two_letter_code).name


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

    json.dump(city_json, open("./data/city.json", "w"), indent=4)



if __name__ == "__main__":
    geoipJson = get_geoip_json()

    get_pop_list(geoipJson)
    get_city_list(geoipJson)
