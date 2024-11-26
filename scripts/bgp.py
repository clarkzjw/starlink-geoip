import os
import httpx
import datetime

import pandas as pd

from pathlib import Path


STARLINK_ASN = [14593, 45700]

DATA_DIR = os.getenv("DATA_DIR", "./starlink-geoip-data")


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

    with open(Path(DATA_DIR).joinpath("bgp/starlink-bgp.csv"), "w") as f1:
        with open(Path(DATA_DIR).joinpath("bgp/starlink-bgp-{}.csv".format(date)), "w") as f2:
            f1.write("CIDR,ASN\n")
            f2.write("CIDR,ASN,Hits\n")

            for line in jsonObj.iterrows():
                ASN = line[1]['ASN']
                count += 1
                if count % 10000 == 0:
                    print(f"Iterating {count} BGP entries, {count/total:.2%} done")

                if ASN in STARLINK_ASN:
                    CIDR = line[1]['CIDR']
                    HITS = line[1]['Hits']
                    f1.write(f"{CIDR}, {ASN}\n")
                    f2.write(f"{CIDR}, {ASN}, {HITS}\n")


if __name__ == '__main__':
    get_bgp_list()
