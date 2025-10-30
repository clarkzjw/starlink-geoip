from ipaddress import ip_network, ip_address
import pandas as pd


POP_FEED_URL = "https://geoip.starlinkisp.net/pops.csv"


class GEOIP:
    def __init__(self):
        pop_feed_header = "cidr,pop,code"

        self.pop_df = pd.read_csv(
            POP_FEED_URL,
            header=None,
            names=pop_feed_header.split(","),
            index_col=False,
        )
        cidrs = self.pop_df["cidr"].tolist()
        networks = [ip_network(c) for c in cidrs]
        self.networks = list(zip(networks, self.pop_df["pop"].tolist(), cidrs))

    def get_pop_by_ip(self, ip_str: str) -> str:
        try:
            ip = ip_address(ip_str)
        except ValueError:
            print(f"Invalid IP address: {ip_str}")
            return ""

        for network, pop, cidr in self.networks:
            if ip in network:
                print(f"IP {ip} found in CIDR {cidr}, POP: {pop}")
                return str(pop)
        return ""


if __name__ == "__main__":
    geoip = GEOIP()
    print(geoip.get_pop_by_ip("209.198.159.56"))
