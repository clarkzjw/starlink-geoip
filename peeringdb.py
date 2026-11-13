import json
import httpx

from jobcontext import JobContext


NETID = [18747, 36005]


def new_client():
    return httpx.Client(base_url="https://www.peeringdb.com/")


def retrive_net(netid: int):
    with new_client() as client:
        response = client.get(f"api/net/{netid}", timeout=60)
        response.raise_for_status()
    return response.json()


def refresh_peeringdb(ctx: JobContext):
    for netid in NETID:
        data = retrive_net(netid)
        with open(ctx.out("peeringdb/net-{}.json".format(netid)), "w") as f:
            f.write(json.dumps(data, indent=4))


if __name__ == "__main__":
    refresh_peeringdb(JobContext.legacy())
