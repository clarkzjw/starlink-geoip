import sys
import json
import subprocess
import ipaddress
import requests
import datetime
import httpx
import schedule
import threading
import time
from pathlib import Path
from pprint import pprint


GEOIP_FEED = "https://geoip.starlinkisp.net/feed.csv"
DEBUG = True


def run(func, *args, **kwargs):
    job_thread = threading.Thread(target=func)
    job_thread.start()


def get_date() -> str:
    # return: "20240129-2100"
    now = datetime.datetime.now(tz=datetime.timezone.utc)
    return now.strftime("%Y%m%d-%H%M")


def get_feed() -> str:
    with httpx.Client() as client:
        file = client.get(GEOIP_FEED)
        return file.content.decode("utf-8")


def save_feed(feed: str):
    feed_dir = Path("data").joinpath("feed")
    filename = feed_dir.joinpath("feed-{}.csv".format(get_date()))
    with open(filename, 'w') as f:
        f.write(feed)
    if not DEBUG:
        with open(feed_dir.joinpath("latest"), 'w') as f:
            f.write(str(filename))


def check_diff(last: str, now: str) -> bool:
    if last == now:
        return False
    return True


def get_latest() -> bool:
    last_feedname, last_feed = get_last_feed()

    feed_now = get_feed()
    if check_diff(last_feed, feed_now):
        print("Feed has been updated since {}".format(last_feedname))
        save_feed(feed_now)
        return True
    print("Feed has not been updated since {}".format(last_feedname))
    return False


def get_last_feed() -> tuple:
    feed_dir = Path("data").joinpath("feed")
    with open(feed_dir.joinpath("latest"), 'r') as f:
        last_feedname = f.read()
        with open(last_feedname, 'r') as f:
            last_feed = f.read()
            return last_feedname, last_feed


def process_geoip():
    _, last_feed = get_last_feed()

    result = {}
    num = 0

    for line in last_feed.splitlines():
        if line:
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

            for ip in subnet_ips:
                ip = str(ip)
                cmd = ["dig", "@1.1.1.1", "+short", "-x", ip]
                try:
                    domain = subprocess.check_output(cmd).decode("utf-8").strip("\n")
                    print(num, ip, domain)
                    if country_code not in result.keys():
                        result[country_code] = {}
                    if state_code not in result[country_code].keys():
                        result[country_code][state_code] = {}
                    if city not in result[country_code][state_code].keys():
                        result[country_code][state_code][city] = {"ips": []}
                    result[country_code][state_code][city]["ips"].append((subnet, domain))
                    break
                except subprocess.TimeoutExpired:
                    pass

    geoip_filename = Path("data").joinpath("geoip").joinpath("{}.json".format(get_date()))
    with open(geoip_filename, 'w') as f:
        json.dump(result, f, indent=2)
    with open(Path("data").joinpath("geoip").joinpath("latest"), 'w') as f:
        f.write(str(geoip_filename))


def run_once():
    if get_latest():
        process_geoip()


schedule.every(1).hours.at(":00").do(run, run_once)


if __name__ == "__main__":
    run_once()
    while True:
        schedule.run_pending()
        time.sleep(0.5)
