"""Run the vendored 10ghz-log-analyzer scripts on behalf of a web request.

The upstream scripts are command-line programs: they read a source from
sys.argv, may prompt with input(), print progress to stdout, and write their
output files into the current directory. Rather than re-implementing any of
their logic (which is how the old web version drifted out of date), this
module runs each script's own main() in a sandbox:

  - sys.argv is set to the script name plus the uploaded log path(s)
  - the working directory is an empty per-run temp directory
  - input() is answered from the web form (call sign, band category)
  - stdout is captured and returned as processing notes

Whatever files the script writes are collected and returned. This keeps the
web app in lock-step with the CLI -- updating is just scripts/sync-upstream.sh.
"""

import builtins
import contextlib
import importlib
import io
import logging
import os
import re
import sys
from dataclasses import dataclass, field

os.environ.setdefault("MPLBACKEND", "Agg")
os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")

ANALYZER_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "analyzer")
if ANALYZER_DIR not in sys.path:
    sys.path.insert(0, ANALYZER_DIR)

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

data_source = importlib.import_module("data_source")

logger = logging.getLogger(__name__)

# Output option -> (upstream module, extra argv, filename patterns it keeps).
# Several options come from the same script run (the Cabrillo converter writes
# both the .log and the summary), so each option lists which of that run's
# files belong to it.
SINGLE_LOG_OUTPUTS = {
    "cabrillo": ("arrl_10ghz_cabrillo", [], [r"_ARRL_10GHZ\.log$"]),
    "summary": ("arrl_10ghz_cabrillo", [], [r"_ARRL_10GHZ_Summary\.txt$"]),
    "station_report": ("station_report", [], [r"."]),
    "weekend_analysis": ("weekend_analysis", [], [r"."]),
    "comprehensive_analysis": ("comprehensive_analysis", [], [r"."]),
    "directional_viz": ("directional_visualization", [], [r"."]),
    "directional_location": ("directional_visualization", ["--location-based"], [r"."]),
}
MULTI_LOG_OUTPUTS = {
    "comparison": ("log_comparison", [], [r"."]),
}
ALL_OUTPUTS = list(SINGLE_LOG_OUTPUTS) + list(MULTI_LOG_OUTPUTS)

OUTPUT_LABELS = {
    "cabrillo": "Cabrillo log",
    "summary": "Contest summary",
    "station_report": "Station activity report",
    "weekend_analysis": "Weekend analysis",
    "comprehensive_analysis": "Comprehensive analysis",
    "directional_viz": "Directional plots (per contest day)",
    "directional_location": "Directional plots (per operating location)",
    "comparison": "Log comparison",
}

_modules = {}


def _module(name):
    if name not in _modules:
        _modules[name] = importlib.import_module(name)
    return _modules[name]


def upstream_version():
    """Upstream version/commit recorded by scripts/sync-upstream.sh."""
    info = {}
    try:
        with open(os.path.join(ANALYZER_DIR, "UPSTREAM.txt")) as f:
            for line in f:
                key, _, value = line.partition(":")
                info[key.strip()] = value.strip()
    except OSError:
        pass
    return info


class InputError(ValueError):
    """A problem with the user's log or form input (reported as HTTP 400)."""


@dataclass
class LogSource:
    """One uploaded log (or fetched Google Sheet), saved to a local path."""
    label: str          # original file name, for display
    path: str           # local .log (Cabrillo) or .csv path the scripts read
    callsign: str       # from the Cabrillo header, or the web form
    qsos: int = 0
    bands: list = field(default_factory=list)
    first_date: str = ""
    last_date: str = ""

    @property
    def band_category(self):
        """10G if only 10 GHz was worked, otherwise ALL (the two Cabrillo
        CATEGORY-BAND choices the upstream converter offers)."""
        return "10G" if self.bands and set(self.bands) == {"10 GHz"} else "ALL"


@dataclass
class RunResult:
    output: str
    source: str
    files: list            # absolute paths of files the script produced
    notes: str             # captured stdout
    error: str = ""


def inspect_source(path, label, form_callsign=""):
    """Load a saved log with the upstream loader to validate it and pull out
    the call sign, bands and dates. Raises InputError if it isn't usable."""
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            df, callsign = data_source.resolve_source(path, verbose=False)
    except Exception as e:  # malformed CSV, bad encoding, etc.
        raise InputError(f"{label}: could not read this log ({e}).") from e

    if df.empty:
        raise InputError(
            f"{label}: no QSOs found. Cabrillo logs need QSO: lines; CSV logs need "
            "columns date, band, sourcegrid, time, call, grid."
        )
    missing = [c for c in data_source.RAW_COLUMNS if c not in df.columns]
    if missing:
        raise InputError(f"{label}: missing column(s): {', '.join(missing)}.")

    if callsign == "UNKNOWN":
        callsign = form_callsign or "UNKNOWN"

    bands = [b for b in data_source.BAND_ORDER if b in set(df["band"])]
    bands += sorted(set(map(str, df["band"])) - set(bands))
    dates = sorted(str(d) for d in df["date"].dropna().unique())
    return LogSource(
        label=label, path=path, callsign=callsign, qsos=len(df), bands=bands,
        first_date=dates[0] if dates else "", last_date=dates[-1] if dates else "",
    )


def _answer_prompt(prompt, callsign, band_category):
    """Answer an upstream input() prompt from the web form values."""
    text = str(prompt).lower()
    if "band" in text:
        return band_category
    if "call" in text:
        return callsign
    return ""


def run_script(module_name, argv, workdir, callsign="", band_category="ALL"):
    """Run an upstream script's main() in workdir. Returns (files, stdout, error)."""
    module = _module(module_name)
    os.makedirs(workdir, exist_ok=True)
    before = set(os.listdir(workdir))
    stdout = io.StringIO()
    error = ""

    old_argv, old_cwd, old_input = sys.argv, os.getcwd(), builtins.input
    sys.argv = [f"{module_name}.py", *argv]
    builtins.input = lambda prompt="": _answer_prompt(prompt, callsign, band_category)
    try:
        os.chdir(workdir)
        with contextlib.redirect_stdout(stdout):
            module.main()
    except SystemExit as e:
        if e.code not in (None, 0):
            lines = stdout.getvalue().strip().splitlines()
            error = lines[-1] if lines else f"{module_name} exited with {e.code}"
    except Exception as e:
        logger.exception("%s failed", module_name)
        error = f"{type(e).__name__}: {e}"
    finally:
        os.chdir(old_cwd)
        sys.argv, builtins.input = old_argv, old_input
        plt.close("all")

    produced = sorted(set(os.listdir(workdir)) - before)
    files = [os.path.join(workdir, f) for f in produced]
    return files, stdout.getvalue(), error


def _fix_unknown_callsign(path, callsign):
    """CSV/Sheets sources carry no call sign, so the analysis scripts name
    their files UNKNOWN_...; rename to the call sign from the web form."""
    name = os.path.basename(path)
    if callsign and callsign != "UNKNOWN" and name.startswith("UNKNOWN_"):
        new_path = os.path.join(os.path.dirname(path), callsign + name[len("UNKNOWN"):])
        os.replace(path, new_path)
        return new_path
    return path


def run_outputs(sources, outputs, workdir, band_category="AUTO"):
    """Run every requested output against the sources. Returns [RunResult]."""
    results = []
    single = [o for o in outputs if o in SINGLE_LOG_OUTPUTS]

    for index, src in enumerate(sources):
        category = src.band_category if band_category == "AUTO" else band_category
        runs = {}  # (module, extra argv) -> (files, notes, error): run each script once
        for output in single:
            module_name, extra, patterns = SINGLE_LOG_OUTPUTS[output]
            key = (module_name, tuple(extra))
            if key not in runs:
                run_dir = os.path.join(workdir, f"src{index}", module_name + "".join(extra))
                files, notes, error = run_script(module_name, [src.path, *extra], run_dir,
                                                 callsign=src.callsign, band_category=category)
                files = [_fix_unknown_callsign(f, src.callsign) for f in files]
                runs[key] = (files, notes, error)
            files, notes, error = runs[key]
            kept = [f for f in files if any(re.search(p, os.path.basename(f)) for p in patterns)]
            if not kept and not error:
                error = "No output was produced -- see the processing notes."
            results.append(RunResult(output, src.label, kept, notes, error))

    if "comparison" in outputs:
        if len(sources) < 2:
            results.append(RunResult("comparison", "all logs", [], "",
                                     "Log comparison needs 2 to 4 logs."))
        else:
            module_name, extra, _ = MULTI_LOG_OUTPUTS["comparison"]
            files, notes, error = run_script(
                module_name, [s.path for s in sources[:4]] + extra,
                os.path.join(workdir, "comparison"))
            results.append(RunResult("comparison", "all logs", files, notes, error))

    return results

