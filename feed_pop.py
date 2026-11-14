"""Poll Starlink's GeoIP feed and PoP feed; store new versions.

The feeds are served from Google Cloud Storage, whose ETag is the MD5 of the
content. Sending ``If-None-Match`` with the MD5 of our latest copy gets a
``304 Not Modified`` (no body) when nothing changed. On a ``200`` the body is
still compared byte-for-byte, in case the ETag is not an MD5 (e.g. the host
changes), so a wrong ETag can only cost a download, never a missed update.
"""

from pathlib import Path

import httpx

from jobcontext import JobContext, md5_file

GEOIP_FEED = "https://geoip.starlinkisp.net/feed.csv"
POP_FEED = "https://geoip.starlinkisp.net/pops.csv"

# (url, data-repo folder, file prefix)
FEEDS = [
    (GEOIP_FEED, "feed", "feed"),
    (POP_FEED, "pop", "pops"),
]


def fetch_if_changed(client: httpx.Client, url: str, latest: Path) -> bytes | None:
    headers = {}
    md5 = md5_file(latest)
    if md5:
        headers["If-None-Match"] = f'"{md5}"'
    r = client.get(url, headers=headers, timeout=60)
    if r.status_code == 304:
        return None
    r.raise_for_status()
    if latest.exists() and r.content == latest.read_bytes():
        return None
    return r.content


def refresh_feed_pop(ctx: JobContext) -> None:
    with httpx.Client() as client:
        for url, folder, prefix in FEEDS:
            latest_rel = f"{folder}/{prefix}-latest.csv"
            content = fetch_if_changed(client, url, ctx.inp(latest_rel))
            if content is None:
                print(f"{url} unchanged")
                continue
            print(f"{url} changed ({len(content)} bytes)")
            # Write bytes unchanged so the file's MD5 matches the server ETag.
            ctx.out(f"{folder}/{ctx.year_month}/{prefix}-{ctx.dt_string}.csv").write_bytes(content)
            ctx.out(latest_rel).write_bytes(content)


if __name__ == "__main__":
    refresh_feed_pop(JobContext.legacy())
