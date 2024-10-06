import re
import json
import requests


GEOIP_FEED_URL = "https://raw.githubusercontent.com/clarkzjw/starlink-geoip-data/refs/heads/master/geoip/geoip-latest.json"


def get_latency_geoip_feed() -> dict:
    feed = requests.get(GEOIP_FEED_URL)
    return json.loads(feed.content)


def get_pop_list():
    feed = get_latency_geoip_feed()

    with open("./data/pop.json", 'r+') as f:
        pops = json.load(f)
        pop_code_list = [x["code"] for x in pops]

        for pop in feed["pop_subnet_count"]:
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


if __name__ == "__main__":
    get_pop_list()