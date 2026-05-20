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
    try:
        j = subprocess.run(args, stdout=PIPE, stderr=PIPE)
        if j.returncode != 0:
            # Fallback for older CLN that might not support certain filters
            return {"error": j.stderr.decode()}
        return json.loads(j.stdout)
    except Exception as e:
        return {"error": str(e)}

def get_forward_at(idx):
    if idx < 0: return None
    res = call_rpc("listforwards", "status=settled", "index=created", f"start={idx}", "limit=1")
    if "error" in res:
        # Retry without status filter for older CLN
        res = call_rpc("listforwards", "index=created", f"start={idx}", "limit=1")
    fws = res.get("forwards", [])
    return fws[0] if fws else None

def find_max_index():
    low = 0
    high = 1000
    last_valid_idx = -1
    
    f0 = get_forward_at(0)
    if not f0: return -1
    last_valid_idx = f0.get("created_index", 0)

    while True:
        f = get_forward_at(high)
        if not f:
            break
        last_valid_idx = f.get("created_index", high)
        low = high
        high *= 2
    
    search_high = high
    while low <= search_high:
        mid = (low + search_high) // 2
        f = get_forward_at(mid)
        if f:
            last_valid_idx = f.get("created_index", mid)
            low = mid + 1
        else:
            search_high = mid - 1
    return last_valid_idx

# 1. Batch get metadata
print("Gathering node/channel info...", file=sys.stderr)
node_info = {}
nodes_res = call_rpc("listnodes")
if "nodes" in nodes_res:
    for n in nodes_res.get("nodes", []):
        node_info[n["nodeid"]] = n.get("alias", n["nodeid"][:20])

channel_liquidity = {}
channel_to_alias = {}
peers_res = call_rpc("listpeerchannels")
all_channels = peers_res.get("channels", [])

for ch in all_channels:
    if "short_channel_id" in ch:
        scid = ch["short_channel_id"]
        peer_id = ch["peer_id"]
        alias = node_info.get(peer_id, peer_id[:20])
        
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

# 2. Fetch forwards backwards from the end
ct = int(time.time())
target_ts = ct - int(86400 * config["daysago"])
print(f"Seeking forwards from {config['daysago']} days ago...", file=sys.stderr)

max_idx = find_max_index()
if max_idx == -1:
    print("No settled forwards found on this node.", file=sys.stderr)
    sys.exit(0)

print(f"Found max forward index: {max_idx}. Fetching forwards...", file=sys.stderr)

last_forwards = []
total_fee = 0
total_volume = 0
limit = 1000
current_end = max_idx

while current_end >= 0:
    start = max(0, current_end - limit + 1)
    res = call_rpc("listforwards", "status=settled", "index=created", f"start={start}", f"limit={limit}")
    if "error" in res:
        res = call_rpc("listforwards", "index=created", f"start={start}", f"limit={limit}")
        
    fws = res.get("forwards", [])
    if not fws:
        break
    
    chunk_hit_target = False
    for fw in reversed(fws):
        if fw.get("status") != "settled" and "status" in fw:
            continue
            
        # Use resolved_time if available, otherwise received_time
        ts = int(fw.get("resolved_time", fw.get("received_time", 0)))
        if ts < target_ts:
            chunk_hit_target = True
            break
        
        if "out_channel" in fw:
            last_forwards.append(fw)
            total_fee += fw["fee_msat"]
            total_volume += fw["out_msat"]
    
    if chunk_hit_target:
        break
        
    current_end = start - 1

# Sort the final list
sort_key = config["sort"]
if sort_key == "fee":
    last_forwards.sort(key=lambda fw: fw["fee_msat"])
elif sort_key == "ppm":
    last_forwards.sort(key=lambda fw: fw["fee_msat"] / fw["out_msat"] if fw["out_msat"] > 0 else 0)
elif sort_key == "volume":
    last_forwards.sort(key=lambda fw: fw["out_msat"])
elif sort_key == "time":
    last_forwards.sort(key=lambda fw: int(fw.get("resolved_time", fw.get("received_time", 0))))

for fw in last_forwards:
    ppm = str(ceil(fw["fee_msat"] / fw["out_msat"] * 1000000)) if fw["out_msat"] > 0 else "?"
    ts_start = int(fw["received_time"])
    ts_done = int(fw.get("resolved_time", ts_start))

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
