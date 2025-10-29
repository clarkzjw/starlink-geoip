import os
import sys
import httpx

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


def update_dns_ptr(df: pd.DataFrame):
    pass


if __name__ == "__main__":

    get_feed()

    df = join_feed()

    update_dns_ptr(df)
