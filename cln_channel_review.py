import json
import os
import subprocess
from subprocess import PIPE
import time
from statistics import mean, median
from math import ceil
import sys
from termcolor import colored
import argparse

parser = argparse.ArgumentParser(description="c-lightning channel review",
                                 formatter_class=argparse.ArgumentDefaultsHelpFormatter)
parser.add_argument("--cli", default=os.environ.get("CLN_CLI", "lightning-cli"), help="your lightning-cli command")
parser.add_argument("--xdays", nargs="*", default=[1,7,30], type=int, help="last forward in xdays(can be list)")
parser.add_argument("--peer-id", help="peer pubkey that you want to review otherwise it will review all your peers")
parser.add_argument("--recent-forward", help="review peers that forwards from i to j days (e.g. 0,7)")
parser.add_argument("--absent-forward", default=-1, type=int, help="review peers that have no forwards in the last n days")
parser.add_argument("--ratio-min", default=0, type=float, help="review peers that have we have liquidity ratio >= x")
parser.add_argument("--ratio-max", default=1, type=float, help="review peers that have we have liquidity ratio <= y")
parser.add_argument("--non-interactive", action="store_true", help="skip interactive ppm update")
cmd_args, unknown_args = parser.parse_known_args()
config = vars(cmd_args)

# Separate unknown_args into those for CLN and those for the script
cln_options = []
aliases = []
i = 0
while i < len(unknown_args):
    arg = unknown_args[i]
    if arg.startswith("-"):
        cln_options.append(arg)
        if "=" not in arg and i + 1 < len(unknown_args) and not unknown_args[i+1].startswith("-"):
            cln_options.append(unknown_args[i+1])
            i += 1
    else:
        aliases.append(arg)
    i += 1

ONE_M = 1000000000
ONE_SAT = 1000

clncli = [config["cli"]]
if "CLN_DIR" in os.environ:
    clncli.extend(["--lightning-dir", os.environ["CLN_DIR"]])
clncli.extend(cln_options)

def call_rpc(*args):
    args = clncli + list(args)
    try:
        j = subprocess.run(args, stdout=PIPE, stderr=PIPE)
        if j.returncode != 0:
            err_msg = j.stderr.decode().strip()
            if not err_msg and j.stdout:
                try:
                    err_json = json.loads(j.stdout)
                    err_msg = err_json.get("message", err_json.get("error", j.stdout.decode().strip()))
                except:
                    err_msg = j.stdout.decode().strip()
            return {"error": err_msg or f"Exit code {j.returncode}"}
        return json.loads(j.stdout)
    except Exception as e:
        return {"error": str(e)}

def verify_env():
    info = call_rpc("getinfo")
    if "id" not in info:
        print(colored("Error: Could not connect to Core Lightning.", "red"), file=sys.stderr)
        sys.exit(1)
    return info

def get_forward_at(idx):
    if idx < 0: return None
    res = call_rpc("listforwards", "status=settled", "index=created", f"start={idx}", "limit=1")
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
        if not f: break
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

# 1. Verify environment and gathering node info
info = verify_env()
mypubkey = info["id"]
print("Gathering node/channel info...", file=sys.stderr)

# Batch fetch metadata
node_aliases = {}
nodes_res = call_rpc("listnodes")
for n in nodes_res.get("nodes", []):
    node_aliases[n["nodeid"]] = n.get("alias", n["nodeid"][:20])

peer_connection_map = {}
peers_res_info = call_rpc("listpeers")
for p in peers_res_info.get("peers", []):
    peer_connection_map[p["id"]] = p.get("connected", False)

all_channels = []
chan_info_map = {}
peers_res = call_rpc("listpeerchannels")
for ch in peers_res.get("channels", []):
    if "short_channel_id" in ch:
        scid = ch["short_channel_id"]
        ch["peer_connected"] = peer_connection_map.get(ch["peer_id"], False)
        all_channels.append(ch)
        chan_info_map[scid] = ch

# 2. Filtering Logic (Initial)
xdays = sorted(config["xdays"])
max_xday = max(xdays) if xdays else 0
if config["absent_forward"] != -1:
    max_xday = max(max_xday, config["absent_forward"])
if config["recent_forward"]:
    tmp = config["recent_forward"].split(",", 1)
    dto = int(tmp[0]) if len(tmp) == 1 else int(tmp[1])
    max_xday = max(max_xday, dto)

selected_chans = [c for c in all_channels if config["ratio_min"] <= (c["to_us_msat"] / c["total_msat"]) <= config["ratio_max"]]

if config["peer_id"]:
    selected_chans = [c for c in selected_chans if c["peer_id"] == config["peer_id"]]

if aliases:
    filtered = []
    for ch in selected_chans:
        alias_name = node_aliases.get(ch["peer_id"], "node not exist in gossip").lower()
        peer_id = ch["peer_id"].lower()
        if any(a.lower() in alias_name or a.lower() in peer_id for a in aliases):
            filtered.append(ch)
    selected_chans = filtered

# 3. Fetch Forward Statistics
ct = int(time.time())
target_ts = ct - int(86400 * max_xday)
chan_stats = {}

def init_chan_stats(scid):
    if scid not in chan_stats:
        chan_stats[scid] = {
            "total_in": 0, "total_out": 0,
            "last_in": 0, "last_out": 0, "last_ppm": 0,
            "xdays": {d: {"c_in": 0, "c_out": 0, "fee": 0, "v_in": 0, "v_out": 0, "ppms": []} for d in xdays}
        }

def process_fw(fw):
    ts = int(fw.get("resolved_time", fw.get("received_time", 0)))
    if ts < target_ts: return False
    
    in_ch = fw.get("in_channel")
    out_ch = fw.get("out_channel")
    fee = fw.get("fee_msat", 0)
    vol_in = fw.get("in_msat", 0)
    vol_out = fw.get("out_msat", 0)
    ppm = ceil(fee / vol_out * 1000000) if vol_out > 0 else 0
    
    if in_ch:
        init_chan_stats(in_ch)
        chan_stats[in_ch]["total_in"] += vol_in
        chan_stats[in_ch]["last_in"] = max(chan_stats[in_ch]["last_in"], ts)
        for d in xdays:
            if ct - ts < 86400 * d:
                chan_stats[in_ch]["xdays"][d]["c_in"] += 1
                chan_stats[in_ch]["xdays"][d]["v_in"] += vol_in

    if out_ch:
        init_chan_stats(out_ch)
        chan_stats[out_ch]["total_out"] += vol_out
        chan_stats[out_ch]["last_out"] = max(chan_stats[out_ch]["last_out"], ts)
        if fee >= 1000: chan_stats[out_ch]["last_ppm"] = ppm
        for d in xdays:
            if ct - ts < 86400 * d:
                chan_stats[out_ch]["xdays"][d]["c_out"] += 1
                chan_stats[out_ch]["xdays"][d]["v_out"] += vol_out
                chan_stats[out_ch]["xdays"][d]["fee"] += fee
                chan_stats[out_ch]["xdays"][d]["ppms"].append(ppm)
    return True

use_global_scan = (config["recent_forward"] or config["absent_forward"] != -1 or len(selected_chans) > 10)

if max_xday > 0:
    if use_global_scan:
        print(f"Scanning recent forwards (backward paging)...", file=sys.stderr)
        max_idx = find_max_index()
        if max_idx != -1:
            limit = 1000
            current_end = max_idx
            while current_end >= 0:
                start = max(0, current_end - limit + 1)
                res = call_rpc("listforwards", "status=settled", "index=created", f"start={start}", f"limit={limit}")
                fws = res.get("forwards", [])
                if not fws: break
                chunk_hit_target = False
                for fw in reversed(fws):
                    ts = int(fw.get("resolved_time", fw.get("received_time", 0)))
                    if ts < target_ts:
                        chunk_hit_target = True
                        break
                    process_fw(fw)
                if chunk_hit_target: break
                current_end = start - 1
    else:
        print(f"Fetching forwards for {len(selected_chans)} channels...", file=sys.stderr)
        for ch in selected_chans:
            scid = ch["short_channel_id"]
            for dir in ["in_channel", "out_channel"]:
                res = call_rpc("listforwards", "status=settled", f"{dir}={scid}")
                for fw in res.get("forwards", []):
                    process_fw(fw)

# 4. Final Filtering (Forward activity)
if config["recent_forward"] or config["absent_forward"] != -1:
    dfrom = 0
    dto = config["absent_forward"]
    if config["recent_forward"]:
        tmp = config["recent_forward"].split(",", 1)
        dfrom = int(tmp[0]) if len(tmp) > 1 else 0
        dto = int(tmp[0]) if len(tmp) == 1 else int(tmp[1])

    want_peers = set()
    exclude_peers = set()
    for scid, stats in chan_stats.items():
        last_act = max(stats["last_in"], stats["last_out"])
        days_ago = (ct - last_act) / 86400
        p_id = chan_info_map.get(scid, {}).get("peer_id")
        if not p_id: continue
        if days_ago < dto:
            if days_ago >= dfrom: want_peers.add(p_id)
            else: exclude_peers.add(p_id)
    if config["recent_forward"]:
        target_peers = want_peers - exclude_peers
        selected_chans = [c for c in selected_chans if c["peer_id"] in target_peers]
    else:
        selected_chans = [c for c in selected_chans if c["peer_id"] not in want_peers]

# 5. Main Display Loop
peer_fees_cache = {}
script_dir = os.path.dirname(os.path.abspath(__file__))
cache_path = os.path.join(script_dir, "peer_fees_cache.json")
if os.path.exists(cache_path):
    try:
        with open(cache_path, "r") as f:
            peer_fees_cache = json.load(f)
    except Exception as e:
        print(colored(f"Warning: Failed to load peer fees cache: {e}", "yellow"), file=sys.stderr)
progress_total = len(selected_chans)
for idx, ch in enumerate(selected_chans):
    scid = ch["short_channel_id"]
    peer_id = ch["peer_id"]
    alias = node_aliases.get(peer_id, "node not exist in gossip")
    peer_connected = ch["peer_connected"]
    ratio = ch["to_us_msat"] / ch["total_msat"]
    local_fee_base = ch.get("fee_base_msat", 0)
    local_fee_ppm = ch.get("fee_proportional_millionths", 0)
    remote_fee_base = 0
    remote_fee_ppm = 0
    if "updates" in ch and "remote" in ch["updates"]:
        remote_fee_base = ch["updates"]["remote"].get("fee_base_msat", 0)
        remote_fee_ppm = ch["updates"]["remote"].get("fee_proportional_millionths", 0)

    stats = chan_stats.get(scid, {
        "total_in": 0, "total_out": 0, "last_in": 0, "last_out": 0, "last_ppm": 0,
        "xdays": {d: {"c_in": 0, "c_out": 0, "fee": 0, "v_in": 0, "v_out": 0, "ppms": []} for d in xdays}
    })

    colored_alias = colored(alias, "green" if peer_connected else "red")
    colored_ratio = colored("%.2f" % ratio, "red" if ratio <= 0.2 else "yellow" if ratio >= 0.8 else "green")
    
    print(f"{peer_id}({colored_alias}) {scid} - {idx + 1} out of {progress_total}")
    print("")
    print("channel size: %.2fM, to_us %.4fM, ratio %s" % (ch["total_msat"]/ONE_M/1000, ch["to_us_msat"]/ONE_M/1000, colored_ratio))
    print("local_fee(%d,%d) remote_fee(%d,%d)" % (local_fee_base, local_fee_ppm, remote_fee_base, remote_fee_ppm))
    
    in_days_ago = (ct - stats["last_in"]) / 86400 if stats["last_in"] > 0 else 999
    out_days_ago = (ct - stats["last_out"]) / 86400 if stats["last_out"] > 0 else 999
    color_days = lambda d: colored("%d" % d, "green" if d <= 7 else "yellow" if d <= 20 else "red")
    
    print("last ppm %s, in forward %s days ago, out forward %s days ago" % (
        colored(str(stats["last_ppm"]), "green", attrs=["bold"]),
        color_days(in_days_ago), color_days(out_days_ago)
    ))

    def format_msat_pair(in_v, out_v):
        c_in = "blue" if in_v > out_v else "white"
        c_out = "blue" if out_v > in_v else "white"
        if in_v == out_v: c_in = c_out = "blue"
        return "(" + colored("%.3f" % (in_v/ONE_M/1000), c_in) + "," + colored("%.3f" % (out_v/ONE_M/1000), c_out) + ")"

    print("Total Msat in/out forwards %s" % format_msat_pair(stats["total_in"], stats["total_out"]))
    for d in xdays:
        d_stats = stats["xdays"][d]
        print("")
        print("Msat in/out forwards %d days ago %s" % (d, format_msat_pair(d_stats["v_in"], d_stats["v_out"])))
        color_count = lambda c: colored(str(c), "green" if c >= 5 else "yellow" if c >= 2 else "red")
        print("last %d days num_forward(in %s, out %s)" % (d, color_count(d_stats["c_in"]), color_count(d_stats["c_out"])))
        print("last %d days fee earned %.3f" % (d, d_stats["fee"]/ONE_SAT/1000))
        if d_stats["ppms"]:
            ppms = d_stats["ppms"]
            import numpy as np
            print("last %d days ppm min %d, avg %d, median %d, max %d" % (
                d, min(ppms), int(mean(ppms)), int(median(ppms)), max(ppms)
            ))

    print("")
    if peer_id not in peer_fees_cache:
        peer_chans = call_rpc("listchannels", "-k", f"source={peer_id}").get("channels", [])
        peer_fees_cache[peer_id] = sorted([c["fee_per_millionth"] for c in peer_chans])
    
    peer_ppms = peer_fees_cache[peer_id]
    if peer_ppms:
        import numpy as np
        dist = [20, 30, 40, 50, 60, 70, 80]
        dist_strs = []
        for p in dist:
            val = int(np.percentile(peer_ppms, p))
            dist_strs.append(f"{p}:{colored(str(val), 'yellow')}")
        print("remote peer's ppms distribution: " + " ".join(dist_strs))

    if not config["non_interactive"]:
        print("\nChange PPM to [default=no change; base,ppm; ppm]: ", end='')
        sys.stdout.flush()
        line = sys.stdin.readline().rstrip()
        if line:
            try:
                if "," in line: new_base, new_ppm = map(int, line.split(","))
                else: new_base, new_ppm = local_fee_base, int(line)
                print(call_rpc("setchannel", scid, str(new_base), str(new_ppm)))
            except Exception as e: print(colored(f"Error updating PPM: {e}", "red"))

    print("-" * 40)
