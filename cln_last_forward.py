import json
import os
import subprocess
from subprocess import PIPE
import time
from math import ceil
import sys
from termcolor import colored
import argparse

parser = argparse.ArgumentParser(description="c-lightning channel review",
                                 formatter_class=argparse.ArgumentDefaultsHelpFormatter)
parser.add_argument("--cli", default=os.environ.get("CLN_CLI", "lightning-cli"), help="your lightning-cli command")
parser.add_argument("--cli-args", default=[], nargs='+', help="lightning-cli arguments ommitting --")
parser.add_argument("--daysago", default=1.0, type=float, help="last forward N days ago, can be float")
parser.add_argument("--sort", default="time", type=str, help="sorted by, fee|ppm|volume|time")
cmd_args = parser.parse_args()
config = vars(cmd_args)

ONE_SAT = 1000

clncli = [config["cli"]]
if "CLN_DIR" in os.environ:
    clncli.extend(["--lightning-dir", os.environ["CLN_DIR"]])
clncli.extend(list(map((lambda a: "--" + a), config["cli_args"])))

def call_rpc(*args):
    args = clncli + list(args)
    j = subprocess.run(args, stdout=PIPE, stderr=subprocess.DEVNULL)
    try:
        return json.loads(j.stdout)
    except:
        return {}

# 1. Batch get node aliases (1 RPC call)
print("Gathering node/channel info...", file=sys.stderr)
node_info = {}
nodes_res = call_rpc("listnodes")
for n in nodes_res.get("nodes", []):
    node_info[n["nodeid"]] = n.get("alias", n["nodeid"][:20])

# 2. Batch get ALL channel liquidity (1 RPC call)
# Passing no peer ID to listpeerchannels returns all channels on the node
channel_liquidity = {}
channel_to_alias = {}
all_channels = call_rpc("listpeerchannels").get("channels", [])

for ch in all_channels:
    if "short_channel_id" in ch:
        scid = ch["short_channel_id"]
        peer_id = ch["peer_id"]
        alias = node_info.get(peer_id, peer_id[:20])
        
        # Store liquidity and alias
        liq = 0.0
        if "total_msat" in ch and ch["total_msat"] > 0:
            liq = ch["to_us_msat"] / ch["total_msat"]
            
        channel_liquidity[scid] = liq
        channel_to_alias[scid] = alias

def get_liquidity_str(scid):
    if scid not in channel_liquidity:
        return "?.??"
    l = channel_liquidity[scid]
    if l < 0.2:
        return colored("%.2f" % l, "red")
    if l > 0.8:
        return colored("%.2f" % l, "green")
    return "%.2f" % l

def to_alias(scid):
    return channel_to_alias.get(scid, scid)

# 3. Fetch forwards in efficient pages
ct = int(time.time())
target_ts = ct - int(86400 * config["daysago"])
print(f"Fetching forwards from {config['daysago']} days ago...", file=sys.stderr)

last_forwards = []
total_fee = 0
total_volume = 0

# Start from the end and work backwards if possible, or just paginate forward
# Most CLN nodes perform well with index=created and a large start offset
# We'll just fetch the most recent 10,000 for simplicity and speed
res = call_rpc("listforwards", "status=settled", "index=created", "limit=10000")
fws = res.get("forwards", [])

for fw in reversed(fws):
    ts = int(fw["resolved_time"])
    if ts < target_ts:
        break
    
    if "out_channel" in fw:
        last_forwards.append(fw)
        total_fee += fw["fee_msat"]
        total_volume += fw["out_msat"]

# Sort the final list
sort_key = config["sort"]
if sort_key == "fee":
    last_forwards.sort(key=lambda fw: fw["fee_msat"])
elif sort_key == "ppm":
    last_forwards.sort(key=lambda fw: fw["fee_msat"] / fw["out_msat"] if fw["out_msat"] > 0 else 0)
elif sort_key == "volume":
    last_forwards.sort(key=lambda fw: fw["out_msat"])
elif sort_key == "time":
    last_forwards.sort(key=lambda fw: int(fw["resolved_time"]))

for fw in last_forwards:
    ppm = str(ceil(fw["fee_msat"] / fw["out_msat"] * 1000000)) if fw["out_msat"] > 0 else "?"
    ts_start = int(fw["received_time"])
    ts_done = int(fw["resolved_time"])

    print("%d\t%.2f\t(%d\t%s)\t%s(%s) -> %s(%s)\t%s" % (
        (ts_done - ts_start),
        (ct - ts_done) / 3600,
        ceil(fw["fee_msat"] / ONE_SAT),
        ppm,
        '{:20s}'.format(to_alias(fw["out_channel"])[-20:]),
        get_liquidity_str(fw["out_channel"]),
        '{:20s}'.format(to_alias(fw["in_channel"])[-20:]),
        get_liquidity_str(fw["in_channel"]),
        '{:>15s}'.format(f"{fw['out_msat']:,}")
    ))

print(f"Total fee {total_fee:,} / volume {total_volume:,} / ntx " + str(len(last_forwards)))
