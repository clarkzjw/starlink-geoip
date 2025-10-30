import os
import time
import json
import httpx
import datetime

import pandas as pd
import pycountry

from copy import deepcopy
from pathlib import Path

from util import GEOIP


ASN = [14593, 45700]

DATA_DIR = os.getenv("DATA_DIR", "./starlink-geoip-data")
if not os.path.exists(Path(DATA_DIR).joinpath("atlas")):
    os.makedirs(Path(DATA_DIR).joinpath("atlas"))


def get_date() -> str:
    now = datetime.datetime.now(tz=datetime.timezone.utc)
    return now.strftime("%Y%m%d-%H%M")


date = get_date()
geoip_client = GEOIP()


def new_atlas_client():
    return httpx.Client(base_url="https://atlas.ripe.net/api/v2/")


def get_probes_list():
    list = []
    client = new_atlas_client()
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
        client = new_atlas_client()
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
    return None


def get_dns_ptr(ip) -> str:
    return geoip_client.get_pop_by_ip(ip)


def refresh_atlas_probes():
    probe_list = []
    probe_list_original = []

    active_rows = []
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
            # determine country code/name
            country_code = probe_info.get("country_code") or ""
            country = (
                pycountry.countries.get(alpha_2=country_code) if country_code else None
            )
            country_name = country.name if country else ""

            # determine ptr depending on ASN/AF
            if probe_info.get("asn_v4") in ASN:
                ptr = get_dns_ptr(probe_info.get("address_v4"))
            else:
                ptr = get_dns_ptr(probe_info.get("address_v6"))

            active_rows.append(
                {
                    "probe_id": probe_id,
                    "ptr": ptr,
                    "country_code": country_code,
                    "country_name": country_name,
                }
            )
        time.sleep(0.5)

    active_probe_df = pd.DataFrame(active_rows)
    if not active_probe_df.empty:
        active_probe_df = active_probe_df.sort_values(
            by=["ptr", "country_code", "country_name"]
        )

    with open(Path(DATA_DIR).joinpath("atlas/probes.json"), "w") as f:
        json.dump(probe_list, f, indent=4)
    with open(Path(DATA_DIR).joinpath("atlas/probes-{}.json".format(date)), "w") as f:
        json.dump(probe_list_original, f, indent=4)

    with open(Path(DATA_DIR).joinpath("atlas/active_probes.csv"), "w") as f:
        if not active_rows:
            pass
        else:
            for _, row in active_probe_df.iterrows():
                f.write(
                    f"{row['probe_id']},{row['ptr']},{row['country_code']},{row['country_name']}\n"
                )
