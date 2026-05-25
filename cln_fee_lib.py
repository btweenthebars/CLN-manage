import json
import os
import sys
from termcolor import colored

_peer_fees_cache = None

def _load_cache():
    global _peer_fees_cache
    if _peer_fees_cache is not None:
        return
    
    _peer_fees_cache = {}
    script_dir = os.path.dirname(os.path.abspath(__file__))
    cache_path = os.path.join(script_dir, "peer_fees_cache.json")
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r") as f:
                _peer_fees_cache = json.load(f)
        except Exception as e:
            print(colored(f"Warning: Failed to load peer fees cache: {e}", "yellow"), file=sys.stderr)

def get_peer_fees(peer_id, call_rpc):
    """Get sorted list of remote peer fee PPMs.
    Loads from peer_fees_cache.json if available, or queries live via CLN RPC.
    
    Args:
        peer_id (str): Node ID of the peer.
        call_rpc (callable): Function to perform CLN RPC calls.
        
    Returns:
        list[int]: Sorted list of remote peer fee PPMs.
    """
    _load_cache()
    
    if peer_id not in _peer_fees_cache:
        peer_chans = call_rpc("listchannels", "-k", f"source={peer_id}").get("channels", [])
        _peer_fees_cache[peer_id] = sorted([c["fee_per_millionth"] for c in peer_chans])
        
    return _peer_fees_cache[peer_id]
