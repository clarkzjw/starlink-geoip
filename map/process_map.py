import re
import os
import sys
import time
import json
import httpx
import requests
import geocoder
import pycountry
from pathlib import Path

import pandas as pd


GEOIP_JSON_URL = "https://raw.githubusercontent.com/clarkzjw/starlink-geoip-data/refs/heads/master/geoip/geoip-latest.json"
NETFAC_JSON_TEMPLATE_URL = "https://raw.githubusercontent.com/clarkzjw/starlink-geoip-data/refs/heads/master/peeringdb/net-{}.json"
POPS_CSV_URL = "https://raw.githubusercontent.com/clarkzjw/starlink-geoip-data/refs/heads/master/geoip/geoip-pops-ptr-latest.csv"
PEERINGDB_NET_ID = [18747, 36005]
DATA_DIR = os.getenv("DATA_DIR", "./starlink-geoip-data")
GEOIP_MAP_DIR = Path(DATA_DIR).joinpath("map")


def load_pops_csv() -> pd.DataFrame:
    # cidr,country,region,city,pop,code,dns_ptr,pop_dns_ptr_match
    # 14.1.64.0/24,PH,PH-00,Manila,mnlaphl1,mnl,customer.mnlaphl1.pop.starlinkisp.net.,True
    # 14.1.65.0/24,PH,PH-00,Manila,mnlaphl1,mnl,customer.mnlaphl1.pop.starlinkisp.net.,True
    df = pd.read_csv(POPS_CSV_URL)
    return df


def get_pop(subnet: str, df: pd.DataFrame) -> str:
    match = df[df["cidr"] == subnet]
    if not match.empty:
        # if "pop_dns_ptr_match" is True
        if match.iloc[0]["pop_dns_ptr_match"]:
            return match.iloc[0]["pop"]
        else:
            ptr = match.iloc[0]["dns_ptr"]
            pop_from_ptr = ptr.split(".")[1]
            return pop_from_ptr
    return ""


def get_pop_from_csv(subnet: str, df: pd.DataFrame) -> str:
    match = df[df["subnet"] == subnet]
    if not match.empty:
        return match.iloc[0]["pop"]
    return ""


def get_geoip_json() -> dict:
    geoipJson = requests.get(GEOIP_JSON_URL)
    return json.loads(geoipJson.content)


def get_pop_list(geoipJson: dict):
    with open("./data/pop.json", "r+") as f:
        pops = json.load(f)
        pop_code_list = [x["code"] for x in pops]

        for pop in geoipJson["pop_subnet_count"]:
            if re.match(r"customer\.[a-z0-9]+\.pop\.starlinkisp\.net\.", pop[0]):
                pop_code = pop[0].split(".")[1]
                if pop_code not in pop_code_list:
                    print(pop_code)
                    pops.append(
                        {
                            "code": pop_code,
                            "dns": pop[0],
                            "city": "",
                            "country": "",
                            "lat": 0,
                            "lon": 0,
                            "show": False,
                        }
                    )
        pops.sort(key=lambda x: x["code"])

        with open(Path(GEOIP_MAP_DIR).joinpath("pop.json"), "w") as f_out:
            json.dump(pops, f_out, indent=4)


def convert_country_code(two_letter_code: str) -> str:
    country = pycountry.countries.get(alpha_2=two_letter_code)
    if country is not None:
        return country.name
    print("Country code {} not found".format(two_letter_code))
    return two_letter_code


def get_netfac_list():
    netfac_geojson = {"type": "FeatureCollection", "features": []}

    netfac_ids = []
    peeringdb_client = httpx.Client(base_url="https://www.peeringdb.com/")
    for netid in PEERINGDB_NET_ID:
        netfac_json = requests.get(NETFAC_JSON_TEMPLATE_URL.format(netid))
        netfac_json = json.loads(netfac_json.content)
        netfac_json = netfac_json["data"][0]["netfac_set"]
        for netfac in netfac_json:
            time.sleep(10)
            netfac_ids.append(netfac["id"])
            print("Processing PeeringDB netfac ID: {}".format(netfac["id"]))
            response = peeringdb_client.get(f'api/netfac/{netfac["id"]}', timeout=10.0)
            netfac_detail = response.json()
            if "data" not in netfac_detail:
                print(netfac_detail)
                sys.exit(1)
            name = netfac_detail["data"][0]["name"]
            address = ""
            fac_id = netfac_detail["data"][0]["fac_id"]
            lat, lon = (
                netfac_detail["data"][0]["fac"]["latitude"],
                netfac_detail["data"][0]["fac"]["longitude"],
            )

            if lat is None or lon is None:
                address += ",".join(
                    [
                        netfac_detail["data"][0]["fac"]["address1"],
                        netfac_detail["data"][0]["fac"]["city"],
                        netfac_detail["data"][0]["fac"]["country"],
                    ]
                )
                print("Geocoding address: {}".format(address))
                g = geocoder.arcgis(address)
                lat, lon = g.json["lat"], g.json["lng"]
                print("Geocoded to: {}, {}".format(lat, lon))

            netfac_geojson["features"].append(
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [float(lon), float(lat)],
                    },
                    "properties": {
                        "title": name,
                        "name": name,
                        "description": "https://www.peeringdb.com/fac/{}".format(
                            fac_id
                        ),
                    },
                }
            )

    peeringdb_client.close()
    json.dump(
        netfac_geojson, open(Path(GEOIP_MAP_DIR).joinpath("netfac.json"), "w"), indent=4
    )


def get_city_list(geoipJson: dict):
    city_json = {"type": "FeatureCollection", "features": []}
    df_geoip = load_pops_csv()

    geoipJson = geoipJson["valid"]
    for country in geoipJson:
        for state in geoipJson[country]:
            for city in geoipJson[country][state]:
                # international waters:
                # https://en.wikipedia.org/wiki/ISO_3166-1_alpha-2#User-assigned_code_elements
                if country == "XZ":
                    continue
                # country_full = convert_country_code(country)
                print("{}, {}, {}".format(country, state, city))

                if country == "US":
                    # US has too many cities with the same name, add state to improve accuracy
                    g = geocoder.arcgis("{}, {}, {}".format(city, state, country))
                else:
                    g = geocoder.arcgis("{}, {}".format(city, country))

                if g.json is None:
                    g = geocoder.arcgis("{}, {}, {}".format(city, state, country))
                    if g.json is None:
                        g = geocoder.arcgis("{}".format(city))
                source_gps = g.json
                print(source_gps)
                description = "<i>{},{},{}</i><br>".format(country, state, city)
                for ip in geoipJson[country][state][city]["ips"]:
                    pop = get_pop(ip[0], df_geoip)
                    description += f"Subnet: {ip[0]}: PoP: {pop}<br>(PTR: {ip[1]})<br>"

                city_json["features"].append(
                    {
                        "type": "Feature",
                        "geometry": {
                            "type": "Point",
                            "coordinates": [
                                float(source_gps["lng"]),
                                float(source_gps["lat"]),
                            ],
                        },
                        "properties": {
                            "title": city,
                            "name": "{}, {}, {}".format(country, state, city),
                            "description": description,
                        },
                    }
                )

    json.dump(city_json, open(Path(GEOIP_MAP_DIR).joinpath("city.json"), "w"), indent=4)


def refresh_map():
    # get_netfac_list()

    geoipJson = get_geoip_json()

    get_pop_list(geoipJson)
    get_city_list(geoipJson)


if __name__ == "__main__":
    load_pops_csv()
