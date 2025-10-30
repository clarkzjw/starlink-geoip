from ipaddress import ip_network, IPv4Address, IPv6Address
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

    def get_pop_by_ip(self, ip_str: str):
        print("Fetching POP for IP:", ip_str)
        for _, row in self.pop_df.iterrows():
            network = ip_network(row["cidr"])
            try:
                ip = IPv4Address(ip_str)
            except ValueError:
                try:
                    ip = IPv6Address(ip_str)
                except ValueError:
                    print(f"Invalid IP address: {ip_str}")
                    return None
            except Exception:
                print(f"Invalid IP address: {ip_str}")
                return None
            if ip in network:
                print(f"IP {ip} found in CIDR {row['cidr']}, POP: {row['pop']}")
                return row["pop"]


if __name__ == "__main__":
    geoip = GEOIP()
    print(geoip.get_pop_by_ip("14.1.66.2"))
