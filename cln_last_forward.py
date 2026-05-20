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

def get_forward_at(idx):
    if idx < 0: return None
    res = call_rpc("listforwards", "status=settled", f"index=created", f"start={idx}", "limit=1")
    fws = res.get("forwards", [])
    return fws[0] if fws else None

def find_start_index(target_ts):
    # Get the latest forward to find current max index
    # We use a large start value to see where we are
    res = call_rpc("listforwards", "status=settled", "limit=1") # Get the very first
    fws = res.get("forwards", [])
    if not fws: return 0
    first_idx = fws[0]["created_index"]
    
    # To find the max index, we can't easily jump to the end with CLN's API 
    # without knowing the count. But we can assume the max index is at least 
    # the number of forwards.
    # Actually, let's just find an upper bound.
    low = first_idx
    high = first_idx + 1000
    
    # Exponential search for upper bound
    while True:
        f = get_forward_at(high)
        if not f or f["received_time"] > target_ts:
            break
        low = high
        high *= 2
    
    # Binary search for exact start
    start_idx = low
    while low <= high:
        mid = (low + high) // 2
        f = get_forward_at(mid)
        if not f:
            high = mid - 1
            continue
        
        if f["received_time"] < target_ts:
            start_idx = f["created_index"]
            low = mid + 1
        else:
            high = mid - 1
            
    return start_idx

# Optimized node/alias gathering
print("Gathering node info...", file=sys.stderr)
node_info = {}
nodes_res = call_rpc("listnodes")
for n in nodes_res.get("nodes", []):
    node_info[n["nodeid"]] = n.get("alias", n["nodeid"][:20])

all_peers = call_rpc("listpeers").get("peers", [])

channel_liquidity = {}
channel_to_peer = {}

for peer in all_peers:
    peer_id = peer["id"]
    alias = node_info.get(peer_id, peer_id[:20])
    peer["alias"] = alias
    
    pchannels = call_rpc("listpeerchannels", peer_id).get("channels", [])
    for ch in pchannels:
        if "short_channel_id" in ch and ch["state"] == "CHANNELD_NORMAL":
            scid = ch["short_channel_id"]
            channel_liquidity[scid] = [ch["to_us_msat"] / ch["total_msat"], ch["fee_proportional_millionths"]]
            channel_to_peer[scid] = peer

ct = int(time.time())
target_ts = ct - int(86400 * config["daysago"])

def to_alias(scid):
    return (channel_to_peer[scid]["alias"] if scid in channel_to_peer else scid)

def get_liquidity(scid):
    if scid not in channel_liquidity:
        return "?.??"
    l = channel_liquidity[scid][0]
    if l < 0.2:
        return colored("%.2f" % l, "red")
    if l > 0.8:
        return colored("%.2f" % l, "green")
    return "%.2f" % l

# Find the starting index for our time window
print(f"Seeking forwards from {config['daysago']} days ago...", file=sys.stderr)
start_index = find_start_index(target_ts)

last_forwards = []
total_fee = 0
total_volume = 0

# Fetch in pages
current_start = max(0, start_index - 1)
limit = 1000
print(f"Fetching forwards starting from index {start_index}...", file=sys.stderr)
while True:
    res = call_rpc("listforwards", "status=settled", "index=created", f"start={current_start}", f"limit={limit}")
    fws = res.get("forwards", [])
    if not fws:
        break
    
    for fw in fws:
        ts = int(fw["resolved_time"])
        if ts >= target_ts:
            if "out_channel" in fw:
                last_forwards.append(fw)
                total_fee += fw["fee_msat"]
                total_volume += fw["out_msat"]
        
        current_start = fw["created_index"]
    
    # Safety: if we've passed the current time, stop
    if fws[-1]["received_time"] > ct + 60:
        break
        
    if len(fws) < limit:
        break

sort_key = config["sort"]
if sort_key == "fee":
    last_forwards.sort(key=lambda fw: fw["fee_msat"])
elif sort_key == "ppm":
    last_forwards.sort(key=lambda fw: fw["fee_msat"] / fw["out_msat"] if fw["fee_msat"] >= 1000 else 0)
elif sort_key == "volume":
    last_forwards.sort(key=lambda fw: fw["out_msat"])
elif sort_key == "time":
    last_forwards.sort(key=lambda fw: int(fw["resolved_time"]))
else:
    raise (Exception("unsupported sort"))

for fw in last_forwards:
    ppm = str(ceil(fw["fee_msat"] / fw["out_msat"] * 1000000)) if fw["fee_msat"] >= 1000 else "?"
    ts_start = int(fw["received_time"])
    ts_done = int(fw["resolved_time"])

    print("%d\t%.2f\t(%d\t%s)\t%s(%s) -> %s(%s)\t%s" % (
        (ts_done - ts_start),
        (ct - ts_done) / 3600,
        ceil(fw["fee_msat"] / ONE_SAT),
        ppm,
        '{:20s}'.format(to_alias(fw["out_channel"])[-20:]),
        get_liquidity(fw["out_channel"]),
        '{:20s}'.format(to_alias(fw["in_channel"])[-20:]),
        get_liquidity(fw["in_channel"]),
        '{:>15s}'.format(f"{fw['out_msat']:,}")
    ))

print(f"Total fee {total_fee:,} / volume {total_volume:,} / ntx " + str(len(last_forwards)))
