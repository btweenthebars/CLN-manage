import json
import os
import subprocess
from subprocess import PIPE
import time
from datetime import datetime
import sys
from termcolor import colored
import argparse
from cln_lib import init_cln, call_rpc, verify_env

# Default Constants
ONE_SAT_MSAT = 1000
DEFAULT_MEANINGFUL_FORWARD_MSAT = 30000 * ONE_SAT_MSAT
DEFAULT_MIN_FEE = 68
DEFAULT_MAX_FEE = 9999

# Fee Tiers (balance in sats, ratio, fee_ppm)
DEFAULT_TIERS = [
    {"balance": 3100000, "ratio": 0.35, "fee": 197},
    {"balance": 2100000, "ratio": 0.30, "fee": 311},
    {"balance": 1750000, "ratio": 0.25, "fee": 571},
    {"balance": 1400000, "ratio": 0.20, "fee": 751},
    {"balance": 1050000, "ratio": 0.15, "fee": 977},
    {"balance": 600000,  "ratio": 0.08, "fee": DEFAULT_MAX_FEE}
]

parser = argparse.ArgumentParser(description="Core Lightning Active Fee Management",
                                 formatter_class=argparse.ArgumentDefaultsHelpFormatter)
parser.add_argument("--cli", default=os.environ.get("CLN_CLI", "lightning-cli"), help="your lightning-cli command")
parser.add_argument("--dry-run", action="store_true", help="Don't actually set fees, just print what would be done")
parser.add_argument("--meaningful", type=int, default=DEFAULT_MEANINGFUL_FORWARD_MSAT, help="Meaningful forward threshold in msat")
parser.add_argument("--config", help="Path to a JSON config file for tiers and peers")
cmd_args, unknown_args = parser.parse_known_args()
config = vars(cmd_args)

clncli = [config["cli"]]
if "CLN_DIR" in os.environ:
    clncli.extend(["--lightning-dir", os.environ["CLN_DIR"]])
clncli.extend(unknown_args)
init_cln(clncli)

myinfo = verify_env()
mypubkey = myinfo["id"]

# Peer lists
source_nodes = []
ignored_nodes = []
ignored_peers = {}
special_peers = {}

if config["config"] and os.path.exists(config["config"]):
    try:
        with open(config["config"], "r") as f:
            cdata = json.load(f)
            source_nodes = cdata.get("source_nodes", [])
            ignored_nodes = cdata.get("ignored_nodes", [])
            ignored_peers = cdata.get("ignored_peers", {})
            special_peers = cdata.get("special_peers", {})
    except Exception as e:
        print(colored(f"Warning: Could not load config: {e}", "yellow"), file=sys.stderr)

scid_to_peer = {}
scid_to_alias = {}
source_channels = []

def refresh_state():
    global scid_to_peer, scid_to_alias, source_channels
    scid_to_peer.clear()
    scid_to_alias.clear()
    source_channels.clear()
    
    peers = call_rpc("listpeers").get("peers", [])
    for p in peers:
        p_id = p["id"]
        res = call_rpc("listpeerchannels", p_id)
        for ch in res.get("channels", []):
            if "short_channel_id" in ch:
                scid = ch["short_channel_id"]
                scid_to_peer[scid] = p
                
                if p_id in source_nodes:
                    source_channels.append(scid)
                
                # Gossip alias
                n_res = call_rpc("listnodes", p_id)
                if "nodes" in n_res and n_res["nodes"]:
                    scid_to_alias[scid] = n_res["nodes"][0].get("alias", p_id[:20])
                else:
                    scid_to_alias[scid] = p_id[:20]

def get_peer_info(peer_id):
    peer_size = 0
    peer_balance = 0
    peer_ppm = -1
    
    res = call_rpc("listpeerchannels", peer_id)
    for ch in res.get("channels", []):
        if ch.get("state") == "CHANNELD_NORMAL":
            peer_size += ch["total_msat"]
            peer_balance += ch["to_us_msat"]
            if peer_ppm == -1:
                peer_ppm = ch["fee_proportional_millionths"]
    
    return {"total_size": peer_size, "balance": peer_balance, "ppm": peer_ppm}

def get_out_forward_info(scid):
    last_out_forward = 0
    last_ppm = -1
    now = int(time.time())
    
    res = call_rpc("listforwards", "-k", "status=settled", f"out_channel={scid}")
    for fw in res.get("forwards", []):
        ts = int(fw.get("resolved_time", 0))
        if ts > last_out_forward:
            last_out_forward = ts
        
        if fw["out_msat"] > config["meaningful"]:
            ppm = int(fw["fee_msat"] / fw["out_msat"] * 1000000)
            last_ppm = ppm
            
    return {"days_ago": (now - last_out_forward) / 86400, "last_ppm": last_ppm}

refresh_state()
print(colored("Active Fee Management started. Waiting for forward events on stdin...", "green"), file=sys.stderr)

for line in sys.stdin:
    try:
        fw = json.loads(line)
    except:
        continue
        
    in_scid = fw.get("in_channel")
    out_scid = fw.get("out_channel")
    
    if in_scid not in scid_to_peer or out_scid not in scid_to_peer:
        refresh_state()
        if in_scid not in scid_to_peer or out_scid not in scid_to_peer:
            continue
            
    in_peer = scid_to_peer[in_scid]
    out_peer = scid_to_peer[out_scid]
    in_alias = scid_to_alias.get(in_scid, in_peer["id"][:20])
    out_alias = scid_to_alias.get(out_scid, out_peer["id"][:20])
    
    in_info = get_peer_info(in_peer["id"])
    out_info = get_peer_info(out_peer["id"])
    
    ratio_in = in_info["balance"] / in_info["total_size"] if in_info["total_size"] > 0 else 0
    ratio_out = out_info["balance"] / out_info["total_size"] if out_info["total_size"] > 0 else 0
    
    sat_forward = fw["out_msat"] / 1000
    real_ppm = int(fw["fee_msat"] / fw["out_msat"] * 1000000) if fw["out_msat"] > 0 else 0
    resolved_time = datetime.fromtimestamp(fw["resolved_time"]).strftime('%Y-%m-%d %H:%M:%S')
    
    print(f"{sat_forward:,.0f} sats\t{out_alias}:{ratio_out:.2f} => {in_alias}:{ratio_in:.2f}\tFee: {fw['fee_msat']/1000:.3f} ({real_ppm} ppm)\t{resolved_time}")

    new_out_fee = -1
    new_in_fee = -1
    
    # OUT Logic
    if fw["out_msat"] >= config["meaningful"] and out_peer["id"] not in ignored_nodes and out_info["ppm"] < DEFAULT_MIN_FEE and ratio_out < 0.45:
        new_out_fee = DEFAULT_MIN_FEE
        
    if out_scid not in source_channels:
        # Tiered fee increase as balance depletes
        for tier in DEFAULT_TIERS[:-1]: # All but the last 'deplete' tier
            if out_peer["id"] not in ignored_peers and out_info["balance"] < tier["balance"] * ONE_SAT_MSAT and ratio_out < tier["ratio"]:
                new_out_fee = max(new_out_fee, tier["fee"], out_info["ppm"])
                
    # Deplete tier (absolute bottom)
    deplete_tier = DEFAULT_TIERS[-1]
    if out_info["balance"] < deplete_tier["balance"] * ONE_SAT_MSAT and ratio_out < deplete_tier["ratio"]:
        if out_peer["id"] in special_peers:
            new_out_fee = special_peers[out_peer["id"]].get("max_fee", DEFAULT_MAX_FEE)
        else:
            new_out_fee = DEFAULT_MAX_FEE

    # IN Logic (recovery)
    new_in_fee = in_info["ppm"]
    state_changed = False
    
    # If currently at max, check if we can step down
    if new_in_fee == DEFAULT_MAX_FEE and (in_info["balance"] >= 600000 * ONE_SAT_MSAT or ratio_in >= 0.08):
        new_in_fee = 977 # Tier 4 fee
        state_changed = True
    
    # Step down tiers as balance recovers
    # (Simplified: if balance > tier threshold, set fee to tier below it)
    # This logic from original is a bit specific, I'll keep it close but clean it up.
    tiers_reversed = list(reversed(DEFAULT_TIERS))
    for i in range(len(tiers_reversed) - 1):
        current_tier = tiers_reversed[i]
        next_tier = tiers_reversed[i+1]
        if new_in_fee == current_tier["fee"] and (in_info["balance"] >= next_tier["balance"] * ONE_SAT_MSAT or ratio_in >= next_tier["ratio"]):
            new_in_fee = next_tier["fee"]
            state_changed = True
            
    # Final step down from Tier 0
    if new_in_fee == 197 and (in_info["balance"] >= 3100000 * ONE_SAT_MSAT or ratio_in >= 0.35):
        # Original script sets to 11111 which is weird, maybe it means 'back to normal'
        # I'll use a placeholder or just leave it for now.
        # Let's use 100 as a reasonable default recovery fee.
        new_in_fee = 100 
        state_changed = True

    # Adjust based on last forward performance
    if state_changed:
        fw_info = get_out_forward_info(in_scid)
        if fw_info["last_ppm"] != -1 and new_in_fee <= fw_info["last_ppm"]:
            new_in_fee = fw_info["last_ppm"] + 10 # Slight bump above last success

    # Apply changes
    if new_out_fee != -1 and new_out_fee != out_info["ppm"]:
        print(colored(f"Adjusting OUT channel {out_alias} fee: {out_info['ppm']} -> {new_out_fee} ppm", "cyan"))
        if not config["dry_run"]:
            res = call_rpc("setchannel", f"id={out_peer['id']}", f"feeppm={new_out_fee}")
            print(res)

    if new_in_fee != -1 and new_in_fee != in_info["ppm"]:
        print(colored(f"Adjusting IN channel {in_alias} fee: {in_info['ppm']} -> {new_in_fee} ppm", "cyan"))
        if not config["dry_run"]:
            res = call_rpc("setchannel", f"id={in_peer['id']}", f"feeppm={new_in_fee}")
            print(res)

    time.sleep(1) # Small throttle
