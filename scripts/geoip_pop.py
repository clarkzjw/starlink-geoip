import os
import httpx

from pathlib import Path

import pandas as pd

GEOIP_FEED = "https://geoip.starlinkisp.net/feed.csv"
POP_FEED = "https://geoip.starlinkisp.net/pops.csv"

DATA_DIR = os.getenv("DATA_DIR", "./starlink-geoip-data")
FEED_DATA_DIR = Path(DATA_DIR).joinpath("feed")
GEOIP_DATA_DIR = Path(DATA_DIR).joinpath("geoip")

FORCE_PTR_REFRESH = False


def get_feed():
    with httpx.Client() as client:
        feeds_urls = [GEOIP_FEED, POP_FEED]
        for url in feeds_urls:
            geoip_file = client.get(url)
            content = geoip_file.content.decode("utf-8")
            filename = url.split("/")[-1]
            with open(FEED_DATA_DIR.joinpath(filename), "w") as f:
                f.write(content)


def init():
    for dir_path in [FEED_DATA_DIR, GEOIP_DATA_DIR]:
        if not dir_path.exists():
            dir_path.mkdir(parents=True, exist_ok=True)


def join_feed():
    geoip_feed_header = "cidr,country,region,city"
    feed_df = pd.read_csv(
        FEED_DATA_DIR.joinpath("feed.csv"),
        header=None,
        names=geoip_feed_header.split(","),
        index_col=False,
    )

    pop_feed_header = "cidr,pop,code"
    pop_df = pd.read_csv(
        FEED_DATA_DIR.joinpath("pops.csv"),
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

    print(merged_df.head())
    merged_df.to_csv(
        FEED_DATA_DIR.joinpath("geoip_with_pops.csv"),
        index=False,
    )


if __name__ == "__main__":
    init()

    get_feed()

    join_feed()
