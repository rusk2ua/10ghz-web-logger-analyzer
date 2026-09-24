# Running the analyzer locally

These steps run the whole web app on your own computer, in a Python virtual environment.
Nothing touches AWS. The steps are for macOS and Linux; see [Windows](#windows) for the
differences.

## Before you start

You need **Git** and **Python 3.11 or newer**. Check with:

```bash
python3 --version
```

If that shows 3.10 or older (macOS ships 3.9), install a newer Python first. The simplest
way on a Mac is the **macOS 64-bit universal2 installer** for Python 3.12 from
[python.org/downloads/macos](https://www.python.org/downloads/macos/). After it finishes,
double-click **Install Certificates.command** in the Finder window it opens, then open a
new terminal. (`brew install python@3.12` also works if your Homebrew is healthy.) Then use
`python3.12` wherever these steps say `python3`. **Check this before Step 2**: a venv
keeps the Python it was created with, so a 3.9 venv can't install the dependencies.

## Step 1: Get the code

**First time (no copy on your machine yet):**

Change `~/Projects` to wherever you keep repos:

```bash
cd ~/Projects
git clone https://github.com/rusk2ua/10ghz-web-logger-analyzer.git
cd 10ghz-web-logger-analyzer
```

**If you already have a clone:**

```bash
cd ~/Projects/10ghz-web-logger-analyzer
git pull
```

To confirm you're on the right version, run `ls`. You should see `backend/`,
`frontend/`, `template.yaml` and `deploy.sh`, and no `infrastructure/` or `cdk.json`.

## Step 2: Create and activate the virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Your prompt should now start with `(.venv)`. **Every step below assumes the venv is
active.** If you open a new terminal later, `cd` into the project and run
`source .venv/bin/activate` again.

## Step 3: Install the dependencies

```bash
pip install --upgrade pip
pip install -r requirements-dev.txt
```

This installs pandas, numpy, matplotlib, requests, boto3, pytest and cfn-lint into
`.venv` only, not your system Python. It takes a minute or two the first time.

## Step 4: Run the tests

```bash
pytest
```

All tests should pass in about 10 seconds. That confirms the vendored analyzer scripts,
the web handler, and the web-vs-CLI parity check all work on your machine.

Optionally, lint the CloudFormation templates. No output means they're clean:

```bash
cfn-lint template.yaml certificate.yaml
```

## Step 5: Start the local server

```bash
python dev/local_server.py
```

You'll see:

```
ARRL 10 GHz log analyzer running at http://127.0.0.1:8000  (Ctrl+C to stop)
```

Leave this terminal running.

## Step 6: Use the app in your browser

1. Open **http://localhost:8000**.
2. **Try a sample first:** under *Your log*, click the **CSV log** sample link. It loads
   the sample and fills in K2UA as the call sign.
3. Pick outputs under *What to generate*. Cabrillo, Summary and per-day Directional plots
   are checked by default.
4. Click **Analyze log**. After a second or two you'll see:
   - a summary card with the call sign, QSO count, bands, dates and category
   - download links for each report
   - thumbnails of the polar plots (click one to open full size)
   - **Download everything (.zip)**
   - **Processing notes**: the scoring breakdown and duplicate check the CLI prints
5. **Try your own log:** click **Start over**, then drag in a real Cabrillo `.log` or CSV.
   For CSVs, enter your call sign.
6. **Try the comparison:** load two logs (for example, two years of yours), check
   **Log comparison**, and click Analyze.

Google Sheets works locally as long as your machine can reach docs.google.com. Share the
sheet as "Anyone with the link can view" and paste the link.

## Step 7: Find the generated files

Everything the app produces is also saved in the project folder under:

```
local-output/results/<random-id>/
```

That folder is git-ignored. Delete it whenever you like with `rm -rf local-output`.

## Step 8: Stop and clean up

- Stop the server: press **Ctrl+C** in the server terminal.
- Leave the venv: `deactivate`.

## Next time

```bash
cd ~/Projects/10ghz-web-logger-analyzer
source .venv/bin/activate
git pull
python dev/local_server.py
```

Re-run `pip install -r requirements-dev.txt` only if `backend/requirements.txt` changed.

## Troubleshooting

| Problem | Fix |
|---|---|
| `Address already in use` | Something else has port 8000. Run `python dev/local_server.py --port 8001` and open that port. |
| `Requires-Python >=3.10` or `No matching distribution found for numpy==2.2.6` during Step 3, often with `cp39` in the file names | The venv was made with an old Python (macOS ships 3.9). Rebuild it with a newer one: `deactivate; rm -rf .venv; brew install python@3.12; python3.12 -m venv .venv; source .venv/bin/activate`, then redo Step 3. |
| `brew` fails with `Bad CPU type in executable` (and possibly `'git' must be installed`) | An Intel-only Homebrew in `/usr/local` on an Apple Silicon Mac, usually carried over by Migration Assistant. Use the python.org installer instead (see [Before you start](#before-you-start)). To fix Homebrew, install the Apple Silicon version, which goes to `/opt/homebrew`; see [AWS_DEPLOY.md](AWS_DEPLOY.md#step-1-install-the-tools-one-time). |
| `python3: command not found` or version below 3.11 | Install a newer Python (see [Before you start](#before-you-start)), then delete and recreate `.venv` with it. |
| `No module named pandas` (or similar) | The venv isn't active. Run `source .venv/bin/activate`. |
| pip install fails compiling numpy or pandas | Your pip is too old to find prebuilt wheels. Run `pip install --upgrade pip`, then retry. |
| The page loads but Analyze does nothing | Open the browser dev tools console (Cmd+Option+J on Mac, Ctrl+Shift+J on Windows) and check for errors. |
| Code changes don't show up | Python changes need a server restart (Ctrl+C, rerun). Frontend changes just need a browser refresh (Cmd+Shift+R). |

## Windows

- Use `py -3.12 -m venv .venv` to create the venv.
- Activate with `.venv\Scripts\Activate.ps1` in PowerShell, or `.venv\Scripts\activate.bat`
  in cmd.
- If PowerShell blocks activation, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`
  once.
- Everything else is the same.

Once this works locally, [AWS_DEPLOY.md](AWS_DEPLOY.md) puts the same app on AWS.
