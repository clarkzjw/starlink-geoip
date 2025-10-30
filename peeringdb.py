import os
import json
import httpx

from pathlib import Path


NETID = [18747, 36005]

DATA_DIR = os.getenv("DATA_DIR", "./starlink-geoip-data")


def new_client():
    return httpx.Client(base_url="https://www.peeringdb.com/")


def retrive_net(netid: int):
    client = new_client()
    response = client.get(f"api/net/{netid}")
    client.close()
    return response.json()


def refresh_peeringdb():
    for netid in NETID:
        data = retrive_net(netid)
        with open(
            Path(DATA_DIR).joinpath("peeringdb/net-{}.json".format(netid)), "w"
        ) as f:
            f.write(json.dumps(data, indent=4))
