# Idena Wallet Balance Timeline

Build a human-readable balance timeline for any Idena address.

- Pages the list endpoint with `continuationToken`
- Resolves each transaction to get reliable `blockHeight` and `timestamp`
- Classifies incoming vs outgoing and reconstructs the wallet balance
- Saves JSONL, CSV, a tail CSV with ISO timestamps, and two plots with grouped thousands

## Features
- Robust pagination with `continuationToken`
- Per-tx detail lookup via `/Transaction/{hash}`
- Balance reconstruction with fees and tips
- Optional calibration to current on-chain balance for an absolute curve
- Human-friendly plots with thousands separators on both axes
- Caching of tx detail JSON to speed up reruns

## Requirements
- Python 3.9+
- pip
- Packages: `requests`, `matplotlib`

### Install on Linux or macOS
```bash
python3 -m venv .venv
. .venv/bin/activate
pip install --upgrade pip setuptools wheel
pip install requests matplotlib
```

### Install on Windows PowerShell
```powershell
py -m venv .venv
. .venv\Scripts\Activate.ps1
pip install --upgrade pip setuptools wheel
pip install requests matplotlib
```

## Files
- `idena_balance_timeline.py` – main script that fetches, reconstructs, and plots
- `tx_cache/` – cached transaction details (created on first run)

### Outputs for `--out-prefix my_wallet`
- `my_wallet.timeline.jsonl`
- `my_wallet.timeline.csv`
- `my_wallet.tail_25.csv`
- `my_wallet.balance_all.png`
- `my_wallet.balance_last_1000000.png`

## Quickstart

Example address from Idena explorer:

### Linux or macOS
```bash
. .venv/bin/activate
export MPLBACKEND=Agg
python idena_balance_timeline.py \
  --address 0x98D16d7021930b788135dD834983394fF2De9869 \
  --out-prefix idna_bridge \
  --title "idna-bsc bridge balance" \
  --limit 100 \
  --concurrency 8 \
  --verbose
```

### Windows PowerShell
```powershell
. .venv\Scripts\Activate.ps1
$env:MPLBACKEND = "Agg"
python idena_balance_timeline.py `
  --address 0x98D16d7021930b788135dD834983394fF2De9869 `
  --out-prefix idna_bridge `
  --title "idna-bsc bridge balance" `
  --limit 100 `
  --concurrency 8 `
  --verbose
```

## Usage

Basic run:

```bash
python idena_balance_timeline.py \
  --address 0xYourIdenaAddress \
  --out-prefix my_wallet \
  --title "my wallet balance"
```

### Important options
- `--limit 100` – page size for the list endpoint
- `--sleep 0.25` – delay between list pages
- `--max-pages 0` – 0 means no page cap
- `--concurrency 8` – parallel requests for `/Transaction/{hash}`
- `--cache-dir tx_cache` – directory for cached tx details
- `--force-refresh` – ignore cache and refetch all details
- `--no-calibrate` – do not query `/Address/{addr}` for current balance. Curve starts at 0
- `--tail 25` – number of final rows to export in `...tail_25.csv`
- `--title` – plot title shown in the PNGs
- `--base-url https://api.idena.io/api` – override API base if needed
- `--verbose` – print progress

## What the script does
1. Calls `GET /api/Address/{address}/Txs?limit=100&continuationToken=...` until the token ends.
2. Extracts each tx hash and resolves details via `GET /api/Transaction/{hash}` to obtain `blockHeight` and `timestamp`.
3. Classifies directions:
   - outgoing: subtract amount + fee + tips
   - incoming: add amount
4. Reconstructs the balance in chronological order.
5. By default calibrates to the current chain balance so the series is absolute. Add `--no-calibrate` for a relative curve starting at 0.
6. Writes JSONL, CSV, tail CSV with ISO timestamps, and plots.

## Example – alternative address and title
```bash
python idena_balance_timeline.py \
  --address 0x403a9f6219E8Aa493EcD40e342d5a886e901F8e9 \
  --out-prefix bitmart \
  --title "bitmart balance" \
  --limit 100 \
  --concurrency 8
```

## Downloading results from a remote server

Replace the placeholders before running. The first block copies all generated files at once.

### Linux or macOS Terminal
```bash
# copy all outputs that start with prefix
scp [user]@[host]:/path/to/repo/bitmart.* ./ 
scp [user]@[host]:/path/to/repo/bitmart.balance_* ./

# copy specific files
scp [user]@[host]:/path/to/repo/bitmart.balance_all.png ./
scp [user]@[host]:/path/to/repo/bitmart.balance_last_1000000.png ./
scp [user]@[host]:/path/to/repo/bitmart.timeline.csv ./
scp [user]@[host]:/path/to/repo/bitmart.tail_25.csv ./
```

### Windows PowerShell
```powershell
# optionally add -i C:\path\to\private_key if you use a key file
scp [user]@[host]:/path/to/repo/bitmart.balance_all.png "C:\\Users\\YourUser\\Downloads\\"
scp [user]@[host]:/path/to/repo/bitmart.balance_last_1000000.png "C:\\Users\\YourUser\\Downloads\\"
scp [user]@[host]:/path/to/repo/bitmart.timeline.csv "C:\\Users\\YourUser\\Downloads\\"
scp [user]@[host]:/path/to/repo/bitmart.tail_25.csv "C:\\Users\\YourUser\\Downloads\\"
```

**Tip:** To fetch everything with the same prefix in one go on Windows, use:

```powershell
scp [user]@[host]:/path/to/repo/bitmart.* "C:\\Users\\YourUser\\Downloads\\"
scp [user]@[host]:/path/to/repo/bitmart.balance_* "C:\\Users\\YourUser\\Downloads\\"
```

## Troubleshooting
- **ModuleNotFoundError: No module named `matplotlib`**

  Install dependencies inside a venv:

  ```bash
  python3 -m venv .venv
  . .venv/bin/activate
  pip install requests matplotlib
  ```
- **Plots do not render on headless servers**

  Set the non-interactive backend:

  ```bash
  export MPLBACKEND=Agg
  ```
- **Rate limits or timeouts**

  Lower `--concurrency` and increase `--sleep`.

- **Rows with block=0**

  The script skips them by design and enforces that none survive.

- **Missing balances in JSONL**

  The script reconstructs balances even if the JSONL lacks them.

## License
MIT – see [LICENSE](LICENSE) in this repository.
