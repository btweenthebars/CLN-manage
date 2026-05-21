# CLN-manage
Utility scripts for Core Lightning (CLN) management.

## Environment Variables
- `CLN_CLI`: Path to `lightning-cli` (default: `lightning-cli`)
- `CLN_DIR`: Path to the lightning directory (default: `~/.lightning`)

All scripts support passing extra arguments directly to `lightning-cli` (e.g., `--rpc-file`, `--net`).

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

### `cln_active_fee.py`
Automated liquidity-based fee management. Listens to forwards on stdin and adjusts fees based on tiered rules.
- `--dry-run`: Preview changes without applying them.
- `--config path/to/config.json`: Load custom fee tiers and peer lists.

### `cln_list_outputs.py`
Display wallet UTXOs and total balance.

### `cln_alias_to_id.py`
Lookup node IDs and SCIDs by alias or substring.
