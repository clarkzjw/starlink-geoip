import httpx

import pandas as pd

from ipaddress import ip_network
from dataclasses import dataclass

from jobcontext import JobContext


STARLINK_ASN = [14593, 45700]


@dataclass
class Record:
    CIDR: str
    ASN: int
    Hits: int


headers = {
    "User-Agent": "Starlink GeoIP Database (https://github.com/clarkzjw/starlink-geoip)"
}


def get_bgp_list(ctx: JobContext):
    print("Downloading BGP announcement table from bgp.tools")
    table_path = ctx.tmp("table.jsonl")
    with httpx.Client() as client:
        response = client.get("https://bgp.tools/table.jsonl", headers=headers, timeout=300)
        response.raise_for_status()
    table_path.write_bytes(response.content)

    jsonObj = pd.read_json(path_or_buf=table_path, lines=True)
    count = 0
    total = len(jsonObj)

    list = {14593: {"IPv4": [], "IPv6": []}, 45700: {"IPv4": [], "IPv6": []}}

    for line in jsonObj.iterrows():
        ASN = line[1]["ASN"]
        count += 1
        if count % 10000 == 0:
            print(f"Iterating {count} BGP entries, {count/total:.2%} done")

        if ASN in STARLINK_ASN:
            CIDR = line[1]["CIDR"]
            HITS = line[1]["Hits"]
            r = Record(CIDR, ASN, HITS)
            if ip_network(CIDR).version == 4:
                list[ASN]["IPv4"].append(r)
            elif ip_network(CIDR).version == 6:
                list[ASN]["IPv6"].append(r)

    for ASN in STARLINK_ASN:
        list[ASN]["IPv4"] = sorted(list[ASN]["IPv4"], key=lambda x: x.CIDR)
        list[ASN]["IPv6"] = sorted(list[ASN]["IPv6"], key=lambda x: x.CIDR)

    with open(ctx.out("bgp/starlink-bgp.csv"), "w") as f:
        f.write("CIDR,ASN\n")

        for ASN in STARLINK_ASN:
            for r in list[ASN]["IPv4"]:
                f.write(f"{r.CIDR},{r.ASN}\n")
            for r in list[ASN]["IPv6"]:
                f.write(f"{r.CIDR},{r.ASN}\n")


if __name__ == "__main__":
    get_bgp_list(JobContext.legacy())
