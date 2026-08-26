# CLN-manage
Utility scripts for Core Lightning (CLN) management.

## Environment Variables
- `CLN_CLI`: Path to `lightning-cli` (default: `lightning-cli`)
- `CLN_DIR`: Path to the lightning directory (default: `~/.lightning`)
- `CLN_PEER_FEES_CACHE_FILE`: Custom path to write/read the peer fee cache JSON file (default: `peer_fees_cache.json` in script directory)
- `CLN_REBALANCE_RECORDS_FILE`: Custom path to write/read the rebalance records JSON file (default: `rebalance_records` in script directory)

All scripts support passing extra arguments directly to `lightning-cli` (e.g., `--rpc-file`, `--net`).

## Interactive Agent

### `cln_agent.py`
An interactive AI agent powered by the Google Antigravity SDK. It wraps all scripts below as tools, allowing you to manage and query your Core Lightning node using plain English.

To use the agent:
1. Activate the virtual environment:
   ```bash
   source .venv/bin/activate
   ```
2. Run the agent:
   ```bash
   python cln_agent.py
   ```

## Scripts

### `cln_list_channels.py`
List channels with liquidity ratios, colored status, and SCIDs.
- `--sort [time|ratio]`: Sort channels.
- `--all`: Show additional info (remote policy).

### `cln_last_forward.py`
Show recent settled forwards with fee and volume stats.
- `--daysago N`: Show forwards from the last N days.
- `--sort [fee|ppm|volume|time]`: Sort forwards.

### `cln_channel_review.py`
In-depth review of channel performance with interactive fee adjustment.
- `[aliases...]`: Filter by node alias or ID.
- `--xdays 1 7 30`: Stats for specific day ranges.
- `--non-interactive`: Skip the fee update prompt.

### `cln_list_outputs.py`
Display wallet UTXOs and total balance.

### `cln_alias_to_id.py`
Lookup node IDs and SCIDs by alias or substring.

### `cln_cache_fees.py`
Cache remote peer fee PPM distributions to a JSON file (`peer_fees_cache.json`) to speed up channel reviews.

### `cln_hop_circular_rebal_alias.py`
Perform circular rebalancing on channels by alias or SCID and record execution results and average PPM costs to `rebalance_records`.
- `alias_or_scid`: Target inbound channel alias or SCID.
- `hop`: Maximum number of hops (`maxhops`).
- `ppm`: Maximum fee PPM to pay (default: 100).
- `maxoutppm`: Maximum out fee PPM (default: 60).
- `amount`: Total amount to rebalance in satoshis (default: 400,000).
- `splitsize`: Split size in satoshis (default: 100,000).

## Libraries

### `cln_lib.py`
A shared helper module exposing:
* `init_cln(clncli_list)`: Initializes global CLI connection parameters.
* `call_rpc(*args)`: Executes commands against `lightning-cli` with robust error parsing from stdout/stderr.
* `verify_env(cli_path)`: Verifies node connectivity and prints user-facing connection troubleshooting hints.
* `get_peer_fees(peer_id, call_rpc_func)`: Resolves remote peer fee distributions from `peer_fees_cache.json` with lazy loading and dynamic RPC lookup fallback. The cache is automatically reloaded if it is older than 3 hours.



