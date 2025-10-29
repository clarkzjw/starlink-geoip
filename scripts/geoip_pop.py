import os
import sys
import httpx
import ipaddress
import threading
import subprocess

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

GEOIP_FEED = "https://geoip.starlinkisp.net/feed.csv"
POP_FEED = "https://geoip.starlinkisp.net/pops.csv"

datetime_now = datetime.now(tz=timezone.utc)
year = datetime_now.year
month = datetime_now.month
dt_string = datetime_now.strftime("%Y%m%d-%H%M")

DATA_DIR = os.getenv("DATA_DIR", "./starlink-geoip-data")
FEED_DATA_DIR = Path(DATA_DIR).joinpath("feed")
POP_FEED_DATA_DIR = Path(DATA_DIR).joinpath("pop")
GEOIP_DATA_DIR = Path(DATA_DIR).joinpath("geoip")

FORCE_PTR_REFRESH = False


def get_feed():
    for dir_path in [FEED_DATA_DIR, GEOIP_DATA_DIR, POP_FEED_DATA_DIR]:
        _dir = dir_path.joinpath(f"{year}")
        if not _dir.exists():
            _dir.mkdir(parents=True, exist_ok=True)

    with httpx.Client() as client:
        feeds_urls = [GEOIP_FEED, POP_FEED]
        for url in feeds_urls:
            geoip_file = client.get(url)
            content = geoip_file.content.decode("utf-8")
            filename = url.split("/")[-1]
            if filename == "feed.csv":
                filename = (
                    Path(FEED_DATA_DIR)
                    .joinpath(f"{year}")
                    .joinpath(f"feed_{dt_string}.csv")
                )
                latest = Path(FEED_DATA_DIR).joinpath("feed-latest.csv")
            elif filename == "pops.csv":
                filename = (
                    Path(POP_FEED_DATA_DIR)
                    .joinpath(f"{year}")
                    .joinpath(f"pops_{dt_string}.csv")
                )
                latest = Path(POP_FEED_DATA_DIR).joinpath("pops-latest.csv")
            else:
                print("Unknown feed filename")
                sys.exit(1)

            with open(filename, "w") as f:
                f.write(content)
            with open(latest, "w") as f:
                f.write(content)


def join_feed():
    geoip_feed_header = "cidr,country,region,city"
    feed_df = pd.read_csv(
        FEED_DATA_DIR.joinpath("feed-latest.csv"),
        header=None,
        names=geoip_feed_header.split(","),
        index_col=False,
    )

    pop_feed_header = "cidr,pop,code"
    pop_df = pd.read_csv(
        POP_FEED_DATA_DIR.joinpath("pops-latest.csv"),
        header=None,
        names=pop_feed_header.split(","),
        index_col=False,
    )

    merged_left = feed_df.merge(pop_df, on="cidr", how="left")

    feed_only_mask = ~feed_df["cidr"].isin(pop_df["cidr"])
    feed_only = feed_df[feed_only_mask]

    if not feed_only.empty:
        feed_only_reindexed = feed_only.reindex(columns=merged_left.columns)
        merged_df = pd.concat([merged_left, feed_only_reindexed], ignore_index=True)
    else:
        merged_df = merged_left

    return merged_df
    # merged_df.to_csv(
    #     GEOIP_DATA_DIR.joinpath("geoip-pops-latest.csv"),
    #     index=False,
    # )
    # merged_df.to_csv(
    #     GEOIP_DATA_DIR.joinpath(f"{year}{month}").joinpath(
    #         f"geoip-pops-{dt_string}.csv"
    #     ),
    #     index=False,
    # )


def parse_subnet(subnet: str) -> None | ipaddress.IPv6Address | ipaddress.IPv4Address:
    try:
        subnet_ip = ipaddress.IPv6Network(subnet).network_address
    except ipaddress.AddressValueError:
        try:
            subnet_ip = ipaddress.IPv4Network(subnet).network_address
        except ipaddress.AddressValueError:
            print("Invalid subnet: {}".format(subnet))
            return None
    return subnet_ip


def dig_ptr(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str:
    print(f"Digging PTR for IP: {ip}")
    try:
        cmd = ["dig", "@8.8.8.8", "-x", str(ip), "+trace", "+all"]

        output = subprocess.check_output(cmd, timeout=5).decode("utf-8")
        for _line in output.splitlines():
            if "PTR" in _line and ".arpa." in _line and (not _line.startswith(";")):
                domain = _line.split("PTR")[1].strip()
                return domain
        return ""
    except subprocess.CalledProcessError as e:
        print(f"Error executing dig command: {e}")
        return ""


def update_dns_ptr(df: pd.DataFrame) -> pd.DataFrame:
    if "ptr" not in df.columns:
        df["ptr"] = ""

    total = len(df)
    if total == 0:
        return df

    cpu_count = os.cpu_count() or 1
    threads = cpu_count * 16

    # Create chunks of index positions
    indices = df.index.tolist()
    chunk_size = (total + threads - 1) // threads
    chunks = [indices[i : i + chunk_size] for i in range(0, total, chunk_size)]

    lock = threading.Lock()
    threads_list = []

    def worker(idx_slice):
        for idx in idx_slice:
            subnet = df.at[idx, "cidr"]
            subnet_ip = parse_subnet(subnet)
            if subnet_ip is None:
                continue
            ptr_rec = dig_ptr(subnet_ip)
            # protect write to shared DataFrame
            with lock:
                df.at[idx, "ptr"] = ptr_rec

    for chunk in chunks:
        t = threading.Thread(target=worker, args=(chunk,), daemon=True)
        threads_list.append(t)
        t.start()

    for t in threads_list:
        t.join()

    return df


if __name__ == "__main__":

    # get_feed()

    df = join_feed()

    update_dns_ptr(df)
