import json
import os
import ipaddress
import subprocess
import threading
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

import pandas as pd

from jobcontext import JobContext, md5_file

FEED_LATEST = "feed/feed-latest.csv"
POP_LATEST = "pop/pops-latest.csv"
GEOIP_LATEST_CSV = "geoip/geoip-pops-ptr-latest.csv"
GEOIP_LATEST_JSON = "geoip/geoip-latest.json"
GEOIP_META = "geoip/geoip-pops-ptr-latest.meta.json"

PTR_MAX_AGE = timedelta(hours=24)
# Cron fires at :00, but start times drift by seconds; without slack a run
# started at 00:00:03 would make the next day's 00:00:01 check see 23:59:58.
GATE_SLACK = timedelta(minutes=10)

# IPv6 subnets with prefix length >= this value (e.g., /64, /128) are not
# PTR-looked-up; their dns_ptr is left empty.
SKIP_PTR_V6_MIN_PREFIXLEN = 64


def join_feed(ctx: JobContext) -> pd.DataFrame:
    geoip_feed_header = "cidr,country,region,city"
    feed_df = pd.read_csv(
        ctx.inp(FEED_LATEST),
        header=None,
        names=geoip_feed_header.split(","),
        index_col=False,
    )

    pop_feed_header = "cidr,pop,code"
    pop_df = pd.read_csv(
        ctx.inp(POP_LATEST),
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
        return ipaddress.ip_network(subnet, strict=False).network_address
    except ValueError:
        print("Invalid subnet: {}".format(subnet))
        return None


def should_skip_ptr(subnet: str) -> bool:
    try:
        net = ipaddress.ip_network(subnet, strict=False)
    except ValueError:
        return True
    return net.version == 6 and net.prefixlen >= SKIP_PTR_V6_MIN_PREFIXLEN


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


def update_dns_ptr(ctx: JobContext, df: pd.DataFrame, max_attempts: int = 100) -> dict:
    """Fill dns_ptr, write the CSVs if anything changed, return run stats."""
    if "dns_ptr" not in df.columns:
        df["dns_ptr"] = ""
    if "processed" not in df.columns:
        df["processed"] = False
    if "attempts" not in df.columns:
        df["attempts"] = 0

    total = len(df)
    stats = {"rows": total, "skipped": 0, "looked_up": 0, "changed": False}
    if total == 0:
        return stats

    cpu_count = os.cpu_count() or 1
    threads = max(8, cpu_count * 8)

    skip_mask = df["cidr"].map(should_skip_ptr).astype(bool)
    df.loc[skip_mask, "dns_ptr"] = ""
    df.loc[skip_mask, "processed"] = True
    stats["skipped"] = int(skip_mask.sum())
    stats["looked_up"] = total - stats["skipped"]
    print(
        f"Skipping PTR lookup for {stats['skipped']}/{total} rows "
        f"(invalid or IPv6 prefix >= /{SKIP_PTR_V6_MIN_PREFIXLEN})"
    )

    to_process = df.index[~skip_mask].tolist()

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

    tmp_csv = ctx.tmp(f"geoip-pops-ptr-{ctx.dt_string}.csv")
    df.to_csv(tmp_csv, index=False)
    df_cmp = pd.read_csv(tmp_csv)

    old_path = ctx.inp(GEOIP_LATEST_CSV)
    if old_path.exists() and pd.read_csv(old_path).equals(df_cmp):
        print("No changes in geoip-pops-ptr data; skipping update.")
        return stats

    stats["changed"] = True
    df.to_csv(ctx.out(GEOIP_LATEST_CSV), index=False)
    df.to_csv(
        ctx.out(f"geoip/{ctx.year_month}/geoip-pops-ptr-{ctx.dt_string}.csv"),
        index=False,
    )
    return stats


def convert_geoip_to_json(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Convert a dataframe with columns:
      cidr,country,region,city,pop,code,dns_ptr,pop_dns_ptr_match
    into a JSON structure matching:
    {
      "countries": {
        "AD": {
          "AD-07": {
            "Andorra la Vella": {
              "ips": [
                ["2a0d:3344:3f00::/40", "customer.sfiabgr1.pop.starlinkisp.net."]
              ]
            }
          }
        }
      },
      "pop_subnet_count": [["customer.sfiabgr1.pop.starlinkisp.net.", 1]]
    }
    """
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

        country_dict = result["countries"].setdefault(country, {})
        region_dict = country_dict.setdefault(region, {})
        city_dict = region_dict.setdefault(city, {})
        ips = city_dict.setdefault("ips", [])
        ips.append([cidr, dns_ptr])

    pop_subnet_count = [[ptr, cnt] for ptr, cnt in ptr_counter.most_common()]
    pop_subnet_count.sort(key=lambda x: x[0])
    result["pop_subnet_count"] = pop_subnet_count

    result["countries"] = dict(sorted(result["countries"].items()))

    return result


def convert_to_geoip_json(ctx: JobContext) -> None:
    print("Converting geoip-pops-ptr CSV to JSON format")
    csv_path = ctx.output_dir / GEOIP_LATEST_CSV
    if not csv_path.exists():
        csv_path = ctx.inp(GEOIP_LATEST_CSV)
    if not csv_path.exists():
        return
    df = pd.read_csv(csv_path, dtype=str, keep_default_na=False)
    json_path = ctx.out(GEOIP_LATEST_JSON)
    with open(json_path, "w") as f:
        json.dump(convert_geoip_to_json(df), f, indent=2)
    print(f"Wrote {json_path}")


def load_meta(ctx: JobContext) -> dict:
    path = ctx.inp(GEOIP_META)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError) as e:
        print(f"Ignoring unreadable {GEOIP_META}: {e}")
        return {}


def gate(
    ctx: JobContext,
    meta: dict,
    feed_md5: str,
    pop_md5: str,
    max_age: timedelta = PTR_MAX_AGE,
) -> str | None:
    """Return why the job should run, or None to skip."""
    if meta.get("feed_md5") != feed_md5 or meta.get("pop_md5") != pop_md5:
        return "inputs changed"
    try:
        last = datetime.fromisoformat(meta["started_at"])
    except (KeyError, TypeError, ValueError):
        return "no valid previous run time"
    age = ctx.now - last
    if age + GATE_SLACK >= max_age:
        return f"last run {age} ago (max age {max_age})"
    return None


def refresh_geoip_ptr(ctx: JobContext, force: bool = False) -> None:
    feed_md5 = md5_file(ctx.inp(FEED_LATEST))
    pop_md5 = md5_file(ctx.inp(POP_LATEST))
    if feed_md5 is None or pop_md5 is None:
        raise FileNotFoundError(
            f"Missing {FEED_LATEST} or {POP_LATEST} in {ctx.input_dir}"
        )

    reason = "forced" if force else gate(ctx, load_meta(ctx), feed_md5, pop_md5)
    if reason is None:
        print("Inputs unchanged and last PTR run is recent; skipping.")
        return
    print(f"Running PTR refresh: {reason}")

    df = join_feed(ctx)
    stats = update_dns_ptr(ctx, df)
    convert_to_geoip_json(ctx)

    meta = {
        "feed_md5": feed_md5,
        "pop_md5": pop_md5,
        "started_at": ctx.now.isoformat(),
        "finished_at": datetime.now(tz=timezone.utc).isoformat(),
        **stats,
    }
    with open(ctx.out(GEOIP_META), "w") as f:
        json.dump(meta, f, indent=2)


if __name__ == "__main__":
    refresh_geoip_ptr(JobContext.legacy())
