import sys
import json
import subprocess
import ipaddress
import requests
from pprint import pprint


url = "https://geoip.starlinkisp.net/feed.csv"
file = requests.get(url)
geoip = file.content.decode("utf-8")

result = {}
num = 0
nxdomain_list = []

for line in geoip.splitlines():
    if line:
        NXDOMAIN = 0
        num += 1
        subnet = line.split(',')[0]
        country_code = line.split(',')[1]
        state_code = line.split(',')[2]
        city = line.split(',')[3]
        try:
            ips = ipaddress.IPv6Network(subnet).hosts()
        except ipaddress.AddressValueError:
            try:
                ips = ipaddress.IPv4Network(subnet).hosts()
            except:
                continue

        for ip in ips:
            ip = str(ip)
            cmd = ["nslookup", ip]
            try:
                output = subprocess.check_output(cmd)
                output = output.decode("utf-8")
                domain = output.splitlines()[0].split('=')[1].strip()
                print(num, ip, domain)
                if country_code not in result.keys():
                    result[country_code] = {}
                if state_code not in result[country_code].keys():
                    result[country_code][state_code] = {}
                if city not in result[country_code][state_code].keys():
                    result[country_code][state_code][city] = {"ips": []}
                result[country_code][state_code][city]["ips"].append((subnet, domain))
                break
            except subprocess.CalledProcessError as e:
                if "NXDOMAIN" in e.output.decode("utf-8"):
                    print(e.output)
                    NXDOMAIN += 1
                    if NXDOMAIN > 100:
                        nxdomain_list.append(line)
                        break
            except subprocess.TimeoutExpired:
                pass

with open('geoip.json', 'w') as f:
    json.dump(result, f, indent=4)

with open('nxdomain.json', 'w') as f:
    json.dump(nxdomain_list, f, indent=4)
