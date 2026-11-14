# Starlink Map

A map visualizing Starlink's [GeoIP feed](https://geoip.starlinkisp.net/feed.csv).

Live: [https://pan.uvic.ca/~clarkzjw/starlink/index.html](https://pan.uvic.ca/~clarkzjw/starlink/index.html)

Data: [https://github.com/clarkzjw/starlink-geoip-data](https://github.com/clarkzjw/starlink-geoip-data)

## Local scheduler

Jobs and schedules (UTC) are defined in `jobs.py`; `uv run python scheduler.py list` shows them.

```
uv run python scheduler.py serve                       # run the scheduler (see deploy/ for systemd)
uv run python scheduler.py run <job>                   # one run now, then publish
uv run python scheduler.py run <job> --no-publish      # one run, keep output under work/runs/
```
