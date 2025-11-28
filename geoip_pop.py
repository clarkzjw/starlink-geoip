import os
import sys
import time
import httpx
import ipaddress
import threading
import subprocess
import json
from pathlib import Path
from typing import Dict, Any
from collections import Counter

from datetime import datetime, timezone

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

FEED_LATEST_FILE = FEED_DATA_DIR.joinpath("feed-latest.csv")
POP_LATEST_FILE = POP_FEED_DATA_DIR.joinpath("pops-latest.csv")
GEOIP_LATEST_FILE = GEOIP_DATA_DIR.joinpath("geoip-pops-ptr-latest.csv")


def read_file(file_path: Path) -> str:
    with open(file_path, "r") as f:
        return f.read()


def get_feed():
    for dir_path in [FEED_DATA_DIR, GEOIP_DATA_DIR, POP_FEED_DATA_DIR]:
        _dir = dir_path.joinpath(f"{year}{month}")
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
                    .joinpath(f"{year}{month}")
                    .joinpath(f"feed-{dt_string}.csv")
                )
                latest = Path(FEED_DATA_DIR).joinpath("feed-latest.csv")
                old_file = read_file(FEED_LATEST_FILE)
                if content == old_file:
                    print("Feed file unchanged; skipping update.")
                    continue
            elif filename == "pops.csv":
                filename = (
                    Path(POP_FEED_DATA_DIR)
                    .joinpath(f"{year}{month}")
                    .joinpath(f"pops-{dt_string}.csv")
                )
                latest = Path(POP_FEED_DATA_DIR).joinpath("pops-latest.csv")
                old_file = read_file(POP_LATEST_FILE)
                if content == old_file:
                    print("POP file unchanged; skipping update.")
                    continue
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

    feed_only_mask = ~merged_left["cidr"].isin(pop_df["cidr"])
    feed_only = merged_left[feed_only_mask]
    common_df = merged_left[~feed_only_mask]

    print(f"Feed only rows: {len(feed_only)}")
    print(f"Common rows: {len(common_df)}")

    if not feed_only.empty:
        merged_df = pd.concat([common_df, feed_only], ignore_index=True)
    else:
        merged_df = merged_left

    print(f"Total merged rows: {len(merged_df)}")

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
    print(f"Digging PTR for IP: {ip}")
    try:
        cmd = ["dig", "-x", str(ip), "+trace", "+all", "+dnssec"]
        output = subprocess.check_output(cmd, timeout=5).decode("utf-8")
        for _line in output.splitlines():
            if (
                "PTR" in _line
                and ".arpa." in _line
                and (not _line.startswith(";"))
                and "RRSIG" not in _line
            ):
                domain = _line.split("PTR")[1].strip()
                return domain
        return ""
    except subprocess.TimeoutExpired:
        print(f"Timeout expired for dig command on IP: {ip}")
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

    to_process = df.index.tolist()

    lock = threading.Lock()
    chunk_lock = threading.Lock()

    while to_process:
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

    df.to_csv(f"/tmp/geoip-pops-ptr-{dt_string}.csv", index=False)
    df_cmp = pd.read_csv(f"/tmp/geoip-pops-ptr-{dt_string}.csv")

    old_df = pd.read_csv(GEOIP_LATEST_FILE)
    if old_df.equals(df_cmp):
        print("No changes in geoip-pops-ptr data; skipping update.")
        return

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


def convert_geoip_to_json(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Convert a dataframe with columns:
      cidr,country,region,city,pop,code,dns_ptr,pop_dns_ptr_match
    into a JSON structure matching:
    {
      "valid": {
        "AD": {
          "AD-07": {
            "Andorra la Vella": {
              "ips": [
                ["2a0d:3344:3f00::/40", "customer.sfiabgr1.pop.starlinkisp.net."]
              ]
            }
          }
        }
      }
    }
    Only rows with a non-empty dns_ptr and pop_dns_ptr_match == True are included.
    """
    # normalize column names if necessary
    expected_cols = [
        "cidr",
        "country",
        "region",
        "city",
        "pop",
        "code",
        "dns_ptr",
        "pop_dns_ptr_match",
    ]
    # If df has a header row duplicated or extra, try to select matching columns
    cols_lower = [c.lower() for c in df.columns]
    col_map = {}
    for ec in expected_cols:
        if ec in cols_lower:
            col_map[ec] = df.columns[cols_lower.index(ec)]
    # fallback: assume columns are already correct
    get = lambda r, k: (
        r[col_map[k]]
        if k in col_map
        else r.get(k, "") if isinstance(r, dict) else r.get(k, "")
    )

    # prepare top-level keys in desired order
    # result: Dict[str, Any] = {"valid": {}, "dns_ptr_pop_not_match": {}}
    result = {
        "countries": {},
        "pop_subnet_count": [],
    }

    # counter for all non-empty dns_ptr values (regardless of match)
    ptr_counter: Counter = Counter()

    for _, row in df.iterrows():
        cidr = get(row, "cidr")
        dns_ptr = (get(row, "dns_ptr") or "").strip()
        if dns_ptr:
            ptr_counter[dns_ptr] += 1

        country = (get(row, "country") or "").strip()
        region = (get(row, "region") or "").strip()
        city = (get(row, "city") or "").strip()

        if not country:
            continue

        target_bucket = "countries"
        country_dict = result[target_bucket].setdefault(country, {})
        region_dict = country_dict.setdefault(region, {})
        city_dict = region_dict.setdefault(city, {})
        ips = city_dict.setdefault("ips", [])
        ips.append([cidr, dns_ptr])

    # build pop_subnet_count as list of [dns_ptr, count] sorted desc
    pop_subnet_count = [[ptr, cnt] for ptr, cnt in ptr_counter.most_common()]
    # sort pop_subnet_count by ptr
    pop_subnet_count.sort(key=lambda x: x[0])
    result["pop_subnet_count"] = pop_subnet_count

    # sort valid = dict(sorted(valid.items()))
    result["countries"] = dict(sorted(result["countries"].items()))

    return result


def convert_to_geoip_json():
    print("Converting geoip-pops-ptr CSV to JSON format")
    CSV_PATH = GEOIP_DATA_DIR.joinpath("geoip-pops-ptr-latest.csv")
    JSON_PATH = GEOIP_DATA_DIR.joinpath("geoip-latest.json")

    if CSV_PATH.exists():
        df = pd.read_csv(CSV_PATH, dtype=str, keep_default_na=False)
        geojson = convert_geoip_to_json(df)
        with open(JSON_PATH, "w") as f:
            json.dump(geojson, f, indent=2)
        print(f"Wrote {JSON_PATH}")


def refresh_geoip_pop():

    get_feed()

    df = join_feed()

    update_dns_ptr(df)

    convert_to_geoip_json()
