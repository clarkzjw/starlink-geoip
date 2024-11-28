import os
import httpx
import datetime

import pandas as pd

from ipaddress import ip_network
from dataclasses import dataclass
from pathlib import Path


STARLINK_ASN = [14593, 45700]

DATA_DIR = os.getenv("DATA_DIR", "./starlink-geoip-data")


@dataclass
class Record:
    CIDR: str
    ASN: int
    Hits: int


def get_date() -> str:
    now = datetime.datetime.now(tz=datetime.timezone.utc)
    return now.strftime("%Y%m%d-%H%M")


date = get_date()


headers={"User-Agent": "Starlink GeoIP Database GitHub Actions CI (https://github.com/clarkzjw/starlink-geoip)"}


def new_client():
    return httpx.Client()


def get_bgp_list():
    client = new_client()

    print("Downloading BGP announcement table from bgp.tools")
    response = client.get("https://bgp.tools/table.jsonl", headers=headers)
    with open(f"./table.jsonl", "wb") as f:
        f.write(response.content)

    jsonObj = pd.read_json(path_or_buf="./table.jsonl", lines=True)
    count = 0
    total = len(jsonObj)

    list = {
        14593: {"IPv4": [], "IPv6": []},
        45700: {"IPv4": [], "IPv6": []}
    }

    for line in jsonObj.iterrows():
        ASN = line[1]['ASN']
        count += 1
        if count % 10000 == 0:
            print(f"Iterating {count} BGP entries, {count/total:.2%} done")

        if ASN in STARLINK_ASN:
            CIDR = line[1]['CIDR']
            HITS = line[1]['Hits']
            r = Record(CIDR, ASN, HITS)
            if ip_network(CIDR).version == 4:
                list[ASN]["IPv4"].append(r)
            elif ip_network(CIDR).version == 6:
                list[ASN]["IPv6"].append(r)

    for ASN in STARLINK_ASN:
        list[ASN]["IPv4"] = sorted(list[ASN]["IPv4"], key=lambda x: x.CIDR)
        list[ASN]["IPv6"] = sorted(list[ASN]["IPv6"], key=lambda x: x.CIDR)

    with open(Path(DATA_DIR).joinpath("bgp/starlink-bgp.csv"), "w") as f:
        f.write("CIDR,ASN\n")

        for ASN in STARLINK_ASN:
            for r in list[ASN]["IPv4"]:
                f.write(f"{r.CIDR},{r.ASN}\n")
            for r in list[ASN]["IPv6"]:
                f.write(f"{r.CIDR},{r.ASN}\n")


if __name__ == '__main__':
    get_bgp_list()
