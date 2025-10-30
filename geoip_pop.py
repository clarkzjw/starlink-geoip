import os
import sys
import time
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


def dig_ptr(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str | None:
    """
    Return domain string when PTR found, empty string when no PTR,
    and None when a TimeoutExpired occurred (to indicate a retry is needed).
    """
    print(f"Digging PTR for IP: {ip}")
    try:
        cmd = ["dig", "-x", str(ip), "+trace", "+all"]
        output = subprocess.check_output(cmd, timeout=5).decode("utf-8")
        for _line in output.splitlines():
            if "PTR" in _line and ".arpa." in _line and (not _line.startswith(";")):
                domain = _line.split("PTR")[1].strip()
                return domain
        return ""
    except subprocess.TimeoutExpired:
        print(f"Timeout expired for dig command on IP: {ip}")
        # signal to caller that a timeout occurred and this row should be retried
        return None
    except subprocess.CalledProcessError as e:
        print(f"Error executing dig command: {e}")
        return ""


def update_dns_ptr(df: pd.DataFrame, max_attempts: int = 100):
    if "dns_ptr" not in df.columns:
        df["dns_ptr"] = ""
    if "processed" not in df.columns:
        df["processed"] = False
    if "attempts" not in df.columns:
        df["attempts"] = 0

    total = len(df)
    if total == 0:
        return df

    cpu_count = os.cpu_count() or 1
    threads = max(8, cpu_count * 8)

    # indices to process in the current round
    to_process = df.index.tolist()

    lock = threading.Lock()
    chunk_lock = threading.Lock()

    while to_process:
        # build chunks for this round
        chunk_size = (len(to_process) + threads - 1) // threads
        chunks = [
            to_process[i : i + chunk_size]
            for i in range(0, len(to_process), chunk_size)
        ]

        total_chunks = len(chunks)
        processed_chunks = 0
        threads_list = []
        retries: list[int] = []

        def worker(idx_slice):
            nonlocal processed_chunks
            for idx in idx_slice:
                # skip if already processed by other thread/round
                with lock:
                    if df.at[idx, "processed"]:
                        continue
                    df.at[idx, "attempts"] = int(df.at[idx, "attempts"]) + 1
                subnet = df.at[idx, "cidr"]
                time.sleep(0.1)
                subnet_ip = parse_subnet(subnet)
                if subnet_ip is None:
                    with lock:
                        df.at[idx, "processed"] = True
                    continue
                ptr_rec = dig_ptr(subnet_ip)
                if ptr_rec is None:
                    # timeout: schedule retry (but do not mark processed)
                    with lock:
                        retries.append(idx)
                else:
                    with lock:
                        df.at[idx, "dns_ptr"] = ptr_rec
                        df.at[idx, "processed"] = True
            # chunk finished: update and print progress
            with chunk_lock:
                processed_chunks += 1
                print(
                    f"Processed {processed_chunks}/{total_chunks} chunks (round size {len(to_process)})"
                )

        for chunk in chunks:
            t = threading.Thread(target=worker, args=(chunk,), daemon=True)
            threads_list.append(t)
            t.start()

        for t in threads_list:
            t.join()

        # prepare next round: keep only retries that haven't exhausted attempts
        next_round = []
        with lock:
            for idx in set(retries):
                if int(df.at[idx, "attempts"]) < max_attempts:
                    next_round.append(idx)
                else:
                    # give up after max_attempts
                    df.at[idx, "processed"] = True
        to_process = sorted(next_round)

        if to_process:
            print(f"Retrying {len(to_process)} rows (attempts < {max_attempts})")

    def check_ptr_pop_match(row) -> bool:
        ptr = row["dns_ptr"]
        pop = row["pop"]
        # e.g., undefined.hostname.localhost., 0-179-184-103.host.net.id.
        if not ptr.startswith("customer."):
            return False
        try:
            parts = ptr.split(".")
            if len(parts) < 6:
                return False
            ptr_pop = parts[1]
            return ptr_pop == pop
        except Exception:
            return False

    df["pop_dns_ptr_match"] = df.apply(check_ptr_pop_match, axis=1)
    df = df.drop(columns=["processed", "attempts"])

    df_with_pop = df[df["pop"].notna()]
    df_without_pop = df[df["pop"].isna()]
    df = pd.concat([df_with_pop, df_without_pop], ignore_index=True)

    df.to_csv(
        GEOIP_DATA_DIR.joinpath("geoip-pops-ptr-latest.csv"),
        index=False,
    )
    df.to_csv(
        GEOIP_DATA_DIR.joinpath(f"{year}{month}").joinpath(
            f"geoip-pops-ptr-{dt_string}.csv"
        ),
        index=False,
    )


if __name__ == "__main__":

    get_feed()

    df = join_feed()

    update_dns_ptr(df)
