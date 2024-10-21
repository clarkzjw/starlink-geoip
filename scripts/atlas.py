import os
import sys
import time
import json
import httpx
import subprocess

from pprint import pprint
from pathlib import Path

ASN = [14593, 45700]

DATA_DIR = os.getenv("DATA_DIR", "./starlink-geoip-data")


def new_client():
    return httpx.Client(base_url='https://atlas.ripe.net/api/v2/')


def get_probes_list():
    list = []
    client = new_client()
    for asn in ASN:
        response = client.get('probes', params={'asn': asn, 'limit': 200})
        for probe in response.json()['results']:
            list.append(probe['id'])
        while response.json()['next']:
            response = client.get(response.json()['next'])
            for probe in response.json()['results']:
                list.append(probe['id'])
    client.close()
    return list


def get_probe_info(id: str):
    client = new_client()
    response = client.get(f'probes/{id}')
    client.close()
    return response.json()


def get_dns_ptr(ip):
    try:
        return subprocess.check_output(f"dig -x {ip} +short", shell=True).decode().strip()
    except subprocess.CalledProcessError:
        return None


if __name__ == '__main__':
    probe_list = []
    status = []

    active_probe = {}
    for probe_id in get_probes_list():
        print(f"Getting info for probe {probe_id}")
        probe_info = get_probe_info(probe_id)
        probe_status = probe_info['status']['name']
        status.append(probe_status)
        probe_list.append(probe_info)

        public_probe = probe_info["is_public"]
        if probe_status == "Connected" and public_probe:
            if probe_info['asn_v4'] in ASN:
                active_probe[probe_id] = get_dns_ptr(probe_info['address_v4'])
            else:
                active_probe[probe_id] = get_dns_ptr(probe_info['address_v6'])
        time.sleep(0.5)

    active_probe = dict(sorted(active_probe.items(), key=lambda item: item[1]))

    with open(Path(DATA_DIR).joinpath("atlas/probes.json"), "w") as f:
        json.dump(probe_list, f, indent=4)

    with open(Path(DATA_DIR).joinpath("atlas/active_probes.csv"), "w") as f:
        for key, value in active_probe.items():
            f.write(f"{key},{value}\n")
