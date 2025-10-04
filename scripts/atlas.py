import os
import time
import json
import httpx
import datetime
import subprocess

import pycountry

from copy import deepcopy
from pathlib import Path


ASN = [14593, 45700]

DATA_DIR = os.getenv("DATA_DIR", "./starlink-geoip-data")


def get_date() -> str:
    now = datetime.datetime.now(tz=datetime.timezone.utc)
    return now.strftime("%Y%m%d-%H%M")


date = get_date()


def new_client():
    return httpx.Client(base_url="https://atlas.ripe.net/api/v2/")


def get_probes_list():
    list = []
    client = new_client()
    for asn in ASN:
        response = client.get("probes", params={"asn": asn, "limit": 200})
        for probe in response.json()["results"]:
            list.append(probe["id"])
        while response.json()["next"]:
            response = client.get(response.json()["next"])
            for probe in response.json()["results"]:
                list.append(probe["id"])
    client.close()
    return list


def get_probe_info(id: str):
    attempts = 5
    for attempt in range(1, attempts + 1):
        client = new_client()
        try:
            response = client.get(f"probes/{id}", timeout=10.0)
            response.raise_for_status()
            return response.json()
        except httpx.ReadTimeout as e:
            print(f"Timeout getting probe {id} (attempt {attempt}/{attempts}): {e}")
        except httpx.HTTPError as e:
            print(f"HTTP error getting probe {id} (attempt {attempt}/{attempts}): {e}")
        finally:
            client.close()
        time.sleep(min(2 ** (attempt - 1), 10))
    # return {"id": id, "error": "failed_to_fetch"}
    return None


def get_dns_ptr(ip):
    try:
        return (
            subprocess.check_output(f"dig @1.1.1.1 -x {ip} +short +retry=3", shell=True)
            .decode("utf-8")
            .strip()
        )
    except subprocess.CalledProcessError:
        return ""


if __name__ == "__main__":
    probe_list = []
    probe_list_original = []

    active_probe = {}
    for probe_id in get_probes_list():
        print(f"Getting info for probe {probe_id}")
        probe_info = get_probe_info(probe_id)
        if not probe_info:
            print(f"Failed to get info for probe {probe_id}, skipping.")
            continue
        probe_info_original = deepcopy(probe_info)
        probe_list_original.append(probe_info_original)

        # remove certain fields
        probe_info.pop("last_connected", None)
        probe_info.pop("total_uptime", None)
        probe_info.pop("tags", None)
        probe_info.pop("status_since", None)

        # insert new fields
        if probe_info["asn_v4"] in ASN:
            probe_info["ipv4_ptr"] = get_dns_ptr(probe_info["address_v4"])
        if probe_info["asn_v6"] in ASN:
            probe_info["ipv6_ptr"] = get_dns_ptr(probe_info["address_v6"])

        probe_list.append(probe_info)

        probe_status = probe_info["status"]["name"]
        is_public_probe = probe_info["is_public"]
        if probe_status == "Connected" and is_public_probe:
            if probe_info["country_code"] is None:
                country_name = ""
                probe_info["country_code"] = ""
            else:
                country = pycountry.countries.get(alpha_2=probe_info["country_code"])
                if country is None:
                    country_name = ""
                else:
                    country_name = country.name
            if probe_info["asn_v4"] in ASN:
                active_probe[probe_id] = [
                    get_dns_ptr(probe_info["address_v4"]),
                    probe_info["country_code"],
                    country_name,
                ]
            else:
                active_probe[probe_id] = [
                    get_dns_ptr(probe_info["address_v6"]),
                    probe_info["country_code"],
                    country_name,
                ]
        time.sleep(0.5)

    active_probe = dict(sorted(active_probe.items(), key=lambda item: item[1]))

    with open(Path(DATA_DIR).joinpath("atlas/probes.json"), "w") as f:
        json.dump(probe_list, f, indent=4)
    with open(Path(DATA_DIR).joinpath("atlas/probes-{}.json".format(date)), "w") as f:
        json.dump(probe_list_original, f, indent=4)

    with open(Path(DATA_DIR).joinpath("atlas/active_probes.csv"), "w") as f:
        for key, value in active_probe.items():
            f.write(f"{key},{value[0]},{value[1]},{value[2]}\n")
