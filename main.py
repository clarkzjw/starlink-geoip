import os
import csv
import sys
import json
import httpx
import datetime
import geocoder
import jsondiff
import ipaddress
import subprocess
import threading

from pprint import pprint
from pathlib import Path
from collections import defaultdict


GEOIP_FEED = "https://geoip.starlinkisp.net/feed.csv"
BGP_FEED = "https://raw.githubusercontent.com/clarkzjw/starlink-geoip-data/refs/heads/master/bgp/starlink-bgp.csv"

DATA_DIR = os.getenv("DATA_DIR", "./starlink-geoip-data")
GEOIP_FEED_DIR = Path(DATA_DIR).joinpath("feed")
GEOIP_DATA_DIR = Path(DATA_DIR).joinpath("geoip")
FORCE_PTR_REFRESH = False


def run(func, *args, **kwargs):
    job_thread = threading.Thread(target=func)
    job_thread.start()


def get_date() -> str:
    now = datetime.datetime.now(tz=datetime.timezone.utc)
    return now.strftime("%Y%m%d-%H%M")


date = get_date()


def get_feed() -> str:
    with httpx.Client() as client:
        file = client.get(GEOIP_FEED)
        return file.content.decode("utf-8")


def get_bgp_feed() -> str:
    with httpx.Client() as client:
        file = client.get(BGP_FEED)
        return file.content.decode("utf-8")


def get_bgp_list() -> list:
    bgp_list = []
    bgp_feed = get_bgp_feed()
    for line in bgp_feed.splitlines()[1:]:
        bgp_list.append(line.split(",")[0])
    return bgp_list


def subnet_in_bgp(subnet: str, bgp_list: list) -> bool:
    for bgp_subnet in bgp_list:
        if ipaddress.ip_network(subnet).overlaps(ipaddress.ip_network(bgp_subnet)):
            return True
    return False


def save_feed(feed: str):
    filename = GEOIP_FEED_DIR.joinpath("feed-{}.csv".format(date))
    with open(filename, 'w') as f:
        f.write(feed)
    with open(GEOIP_FEED_DIR.joinpath("feed-latest.csv"), 'w') as f:
        f.write(feed)
    with open(GEOIP_FEED_DIR.joinpath("latest"), 'w') as f:
        f.write(str(filename))


def str_diff(last: str, now: str) -> bool:
    if last == now:
        return False
    return True


def json_diff(last, now) -> bool:
    diff = jsondiff.diff(last, now)
    print("json diff")
    pprint(diff)
    return len(diff) > 0


def get_latest() -> bool:
    first_run = False
    if not GEOIP_FEED_DIR.joinpath("latest").exists():
        first_run = True

    last_feedname, last_feed = get_last_feed()
    feed_now = get_feed()

    if str_diff(last_feed, feed_now) or first_run:
        print("Feed has been updated since {}".format(last_feedname))
        save_feed(feed_now)
        return True

    print("Feed has not been updated since {}".format(last_feedname))
    return False


def get_last_feed() -> tuple:
    if not GEOIP_FEED_DIR.joinpath("latest").exists():
        print("No latest feed found, downloading latest feed")
        save_feed(get_feed())
        return get_last_feed()

    with open(GEOIP_FEED_DIR.joinpath("latest"), 'r') as f:
        last_feedname = f.read().strip("\n")
        with open(last_feedname, 'r') as f:
            last_feed = f.read()
            return last_feedname, last_feed


def process_geoip():
    _, last_feed = get_last_feed()

    num = 0
    valid = {}
    nxdomain_list = []
    servfail_list = []
    bgp_not_active_list = []
    geoip_json = {}
    pop_subnet_count = defaultdict(int)

    bgp_list = get_bgp_list()

    for line in last_feed.splitlines():
        if line:
            NXDOMAIN = 0
            SERVFAIL = 0
            num += 1
            subnet = line.split(',')[0]
            country_code = line.split(',')[1]
            state_code = line.split(',')[2]
            city = line.split(',')[3]
            try:
                subnet_ips = ipaddress.IPv6Network(subnet).hosts()
            except ipaddress.AddressValueError:
                try:
                    subnet_ips = ipaddress.IPv4Network(subnet).hosts()
                except:
                    print("Invalid subnet: {}".format(subnet))
                    continue

            if not subnet_in_bgp(subnet, bgp_list):
                bgp_not_active_list.append(line)

            for ip in subnet_ips:
                ip = str(ip)
                cmd = ["nslookup", ip, "1.1.1.1"]
                try:
                    output = subprocess.check_output(cmd).decode("utf-8")
                    if "Truncated" in output.splitlines()[0]:
                        domain = output.splitlines()[1].split('=')[1].strip()
                    else:
                        domain = output.splitlines()[0].split('=')[1].strip()
                    print(num, ip, domain)
                    if country_code not in valid.keys():
                        valid[country_code] = {}
                    if state_code not in valid[country_code].keys():
                        valid[country_code][state_code] = {}
                    if city not in valid[country_code][state_code].keys():
                        valid[country_code][state_code][city] = {"ips": []}
                    valid[country_code][state_code][city]["ips"].append((subnet, domain))
                    pop_subnet_count[domain] += 1
                    break
                except subprocess.CalledProcessError as e:
                    if "NXDOMAIN" in e.output.decode("utf-8"):
                        print(e.output)
                        NXDOMAIN += 1
                        if NXDOMAIN > 5:
                            nxdomain_list.append(line)
                            break
                    elif "SERVFAIL" in e.output.decode("utf-8"):
                        print(e.output)
                        SERVFAIL +=1
                        if SERVFAIL > 5:
                            servfail_list.append(line)
                            break
                except:
                    continue

    geoip_json = {
        "valid": valid,
        "nxdomain": nxdomain_list,
        "servfail": servfail_list,
        "pop_subnet_count": sorted(pop_subnet_count.items()),
        "bgp_not_active": bgp_not_active_list
    }

    should_update = True
    if FORCE_PTR_REFRESH:
        tmp_geoip_filename = GEOIP_DATA_DIR.joinpath("tmp_geoip.json")
        with open(tmp_geoip_filename, 'w') as f:
            json.dump(geoip_json, f, indent=2)

        with open(GEOIP_DATA_DIR.joinpath("tmp_geoip.json"), 'r') as f:
            now_geoip = f.read()
            now_geoip = json.loads(now_geoip)

        with open(GEOIP_DATA_DIR.joinpath("geoip-latest.json"), 'r') as f:
            last_geoip = f.read()
            last_geoip = json.loads(last_geoip)

        if json_diff(now_geoip, last_geoip):
            print("GEOIP has been updated")
        else:
            should_update = False

        os.remove(tmp_geoip_filename)

    if should_update:
        geoip_filename = GEOIP_DATA_DIR.joinpath("geoip-{}.json".format(date))
        with open(geoip_filename, 'w') as f:
            json.dump(geoip_json, f, indent=2)
        with open(GEOIP_DATA_DIR.joinpath("geoip-latest.json"), 'w') as f:
            json.dump(geoip_json, f, indent=2)
        with open(GEOIP_DATA_DIR.joinpath("latest"), 'w') as f:
            f.write(str(geoip_filename))


def create_map_data():
    with open(".passwd/geocoder", "r") as f:
        token = f.read()

    with open("./geoip/geoip.json", "r") as f:
        geoip = f.read()
        geoip = json.loads(geoip)

    pop_list = []
    city_list = []
    for country in geoip:
        for state in geoip[country]:
            for city in geoip[country][state]:
                city_list.append(city)
                for pop in geoip[country][state][city]["ips"]:
                    if pop[1] != "undefined.hostname.localhost.":
                        location_code = pop[1].split('.')[1]
                        pop_list.append(location_code)

    pop_list = sorted(list(set(pop_list)))
    city_list = sorted(list(set(city_list)))

    with open("./geoip/pop.csv", newline='') as csvfile:
        reader = csv.reader(csvfile, delimiter=',')

        with open("./geoip/pop_location.csv", 'w', newline='') as output:
            writer = csv.writer(output, delimiter=',')
            for row in reader:
                print(row[1])
                location = geocoder.bing("{},{}".format(row[1], row[2]), key=token)
                location = location.json
                writer.writerow([row[0], row[1], location["lat"], location["lng"]])


def run_once():
    GEOIP_FEED_DIR.mkdir(parents=True, exist_ok=True)
    GEOIP_DATA_DIR.mkdir(parents=True, exist_ok=True)

    if get_latest():
        process_geoip()


if __name__ == "__main__":
    if len(sys.argv) > 1:
        if sys.argv[1] == "ptr-refresh":
            FORCE_PTR_REFRESH = True
            process_geoip()
    else:
        run_once()
