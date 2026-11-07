"""Run the vendored 10ghz-log-analyzer and grid-mapper scripts on behalf of a
web request.

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

APP_DIR = os.path.dirname(os.path.abspath(__file__))
ANALYZER_DIR = os.path.join(APP_DIR, "analyzer")
GRIDMAPPER_DIR = os.path.join(APP_DIR, "gridmapper")
for _dir in (GRIDMAPPER_DIR, ANALYZER_DIR):
    if _dir not in sys.path:
        sys.path.insert(0, _dir)

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

data_source = importlib.import_module("data_source")
contests = importlib.import_module("contests")

# Which site this deployment is: "10ghz" (the ARRL 10 GHz and Up analyzer) or
# "vhf" (the ARRL January/June/September VHF analyzer, which also handles 10
# GHz and Up logs and non-contest logs). Set by the stack's SiteMode parameter.
SITE_MODE = os.environ.get("SITE_MODE", "10ghz").strip().lower()
if SITE_MODE not in ("10ghz", "vhf"):
    SITE_MODE = "10ghz"
SITE_HOSTS = {"10ghz": "10ghz.microwavedx.com", "vhf": "vhf.microwavedx.com"}


def site_credit():
    """The credit line every image and HTML page this site produces carries."""
    return f"Created using {SITE_HOSTS[SITE_MODE]} by K2UA"


# The credit strip added under each PNG: small grey type, right-aligned, on a
# band of the image's own background below the picture, so it never covers
# any of the map or plot (the images are cropped tight to their content).
_CREDIT_FONT = os.path.join(matplotlib.get_data_path(), "fonts", "ttf", "DejaVuSans.ttf")


def credit_png(path, text=None):
    """Add the site credit below a PNG and in its metadata. Idempotent: an
    image already carrying the credit is left alone. Returns True if changed."""
    from PIL import Image, ImageDraw, ImageFont, PngImagePlugin

    text = text or site_credit()
    with Image.open(path) as im:
        if im.info.get("Comment") == text:
            return False
        im.load()
        info, dpi = dict(im.info), im.info.get("dpi")
        base = im.convert("RGBA")
    width, height = base.size
    size = max(14, round(min(width, height) * 0.011))
    font = ImageFont.truetype(_CREDIT_FONT, size)
    pad = round(size * 0.7)
    strip = size + 2 * pad
    ground = base.getpixel((width - 1, height - 1))
    if ground[3] == 0:
        ground = (255, 255, 255, 255)
    out = Image.new("RGBA", (width, height + strip), ground)
    out.paste(base, (0, 0))
    draw = ImageDraw.Draw(out)
    lum = 0.299 * ground[0] + 0.587 * ground[1] + 0.114 * ground[2]
    ink = (120, 126, 134, 255) if lum > 128 else (150, 156, 164, 255)
    right = width - pad * 2
    draw.text((right, height + strip // 2), text, font=font, fill=ink, anchor="rm")
    meta = PngImagePlugin.PngInfo()
    for key, value in info.items():
        if isinstance(value, str) and key not in ("Comment", "Software"):
            meta.add_text(key, value)
    meta.add_text("Software", text)
    meta.add_text("Comment", text)
    out = out.convert("RGB") if base.getextrema()[3][0] == 255 else out
    out.save(path, pnginfo=meta, dpi=dpi) if dpi else out.save(path, pnginfo=meta)
    return True

# Contest choices on the VHF site: AUTO (detect it from the log), a contest
# id, or NONE (no contest -- maps and the movie only, no score).
CONTEST_CHOICES = ("AUTO", "NONE", contests.ARRL_VHF_JAN, contests.ARRL_VHF_JUN,
                   contests.ARRL_VHF_SEP, contests.ARRL_10_GHZ)
CATEGORY_CHOICES = ("AUTO",) + tuple(contests.CATEGORIES)
# Outputs that only make sense for the ARRL 10 GHz and Up contest (they
# score with its distance x band multiplier rules), not offered on the VHF site.
TEN_GHZ_ONLY_OUTPUTS = ("weekend_analysis", "comprehensive_analysis", "directional_viz",
                        "directional_location")

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
# grid-mapper maps: one maidenhead_map.py run per log produces both kinds (it
# always draws density maps; --paths adds path maps), sorted out by file name.
MAP_OUTPUTS = {
    "grid_density": r"_maidenhead_map\.(png|html)$",
    "grid_paths": r"_grid_paths_map\.(png|html)$",
}
# grid-mapper's --animate movie gets a run of its own (--animate-only), so it
# doesn't also draw every per-band map.
MOVIE_OUTPUT = "grid_movie"
MOVIE_PATTERN = r"_contest_animation\.html$"
# The VHF contests' Cabrillo log and summary, from the analyzer's vhf_cabrillo.py.
VHF_PATTERNS = {
    "cabrillo": r"_ARRL_VHF_(JAN|JUN|SEP)\.log$",
    "summary": r"_ARRL_VHF_(JAN|JUN|SEP)_Summary\.txt$",
}
ALL_OUTPUTS = list(SINGLE_LOG_OUTPUTS) + list(MAP_OUTPUTS) + [MOVIE_OUTPUT] + list(MULTI_LOG_OUTPUTS)

OUTPUT_LABELS = {
    "cabrillo": "Cabrillo log",
    "summary": "Contest summary",
    "station_report": "Station activity report",
    "weekend_analysis": "Weekend analysis",
    "comprehensive_analysis": "Comprehensive analysis",
    "directional_viz": "Directional plots (per contest day)",
    "directional_location": "Directional plots (per operating location)",
    "comparison": "Log comparison",
    "grid_density": "Grid maps (contacts per grid square)",
    "grid_paths": "Path maps (lines to each station worked)",
    "grid_movie": "Contest movie (the whole contest as an animated map)",
}

GRID4_RE = re.compile(r"^[A-R]{2}[0-9]{2}$")

_modules = {}


def _module(name):
    if name not in _modules:
        _modules[name] = importlib.import_module(name)
    return _modules[name]


def upstream_version(project_dir=ANALYZER_DIR):
    """Upstream version/commit recorded by scripts/sync-upstream.sh."""
    info = {}
    try:
        with open(os.path.join(project_dir, "UPSTREAM.txt")) as f:
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
    grids: list = field(default_factory=list)  # 4-character operating grids
    header: dict = field(default_factory=dict)  # Cabrillo header / ADIF CONTEST_ID
    detected: str = None                        # detected contest id, or None
    detection: str = ""                         # why
    category: str = "SO"                        # VHF category guessed from the header

    @property
    def log_format(self):
        if data_source.is_adif_path(self.path):
            return "adif"
        return "cabrillo" if self.path.lower().endswith(".log") else "csv"

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
            f"{label}: no QSOs found. Cabrillo logs need QSO: lines; ADIF logs need "
            "<CALL:...> records; CSV logs need columns date, band, sourcegrid, time, call, grid."
        )
    missing = [c for c in data_source.RAW_COLUMNS if c not in df.columns]
    if missing:
        raise InputError(f"{label}: missing column(s): {', '.join(missing)}.")

    if callsign == "UNKNOWN":
        callsign = form_callsign or "UNKNOWN"

    bands = [b for b in data_source.BAND_ORDER if b in set(df["band"])]
    bands += sorted(set(map(str, df["band"])) - set(bands))
    dates = sorted(str(d) for d in df["date"].dropna().unique())
    grids = sorted({g[:4].upper() for g in map(str, df["sourcegrid"].dropna())
                    if GRID4_RE.match(g[:4].upper())})
    if data_source.is_adif_path(path) and not grids:
        raise InputError(f"{label}: this ADIF log has no MY_GRIDSQUARE, so the grid you "
                         "operated from isn't known. Add it in your logger and export again.")
    header = data_source.read_log_header(path)
    found = contests.detect_contest(df, header)
    return LogSource(
        label=label, path=path, callsign=callsign, qsos=len(df), bands=bands,
        first_date=dates[0] if dates else "", last_date=dates[-1] if dates else "",
        grids=grids, header=header, detected=found.contest_id, detection=found.reason,
        category=contests.category_from_header(header, callsign),
    )


def effective_contest(src, contest="AUTO"):
    """The contest a log is scored as: on the 10 GHz site always ARRL 10 GHz
    and Up; on the VHF site the chosen one, or the detected one for AUTO, or
    None for NONE (or AUTO when nothing was detected)."""
    if SITE_MODE != "vhf":
        return contests.ARRL_10_GHZ
    if contest == "AUTO":
        return src.detected
    return None if contest == "NONE" else contest


def contest_label(contest_id):
    found = contests.CONTESTS.get(contest_id)
    return found.name if found else "No contest"


def _vhf_argv(contest_id, category, analog):
    argv = ["--contest", contest_id]
    if category and category != "AUTO":
        argv += ["--category", category]
    if analog:
        argv.append("--analog")
    return argv


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
    for path in files:
        if path.lower().endswith(".png"):
            try:
                credit_png(path)
            except Exception:  # a credit line is never worth losing the image over
                logger.exception("couldn't add the credit to %s", path)
    return files, stdout.getvalue(), error


def _fix_unknown_callsign(path, callsign):
    """CSV/Sheets sources carry no call sign, so the analysis scripts name
    their files UNKNOWN_...; rename to the call sign from the web form."""
    name = os.path.basename(path)
    if callsign and callsign != "UNKNOWN" and name.startswith("UNKNOWN_"):
        new_path = os.path.join(os.path.dirname(path),
                                data_source.file_safe_call(callsign) + name[len("UNKNOWN"):])
        os.replace(path, new_path)
        return new_path
    return path


def run_outputs(sources, outputs, workdir, band_category="AUTO", map_options=None,
                contest="AUTO", entry_category="AUTO", analog=False):
    """Run every requested output against the sources. Returns [RunResult].

    map_options: {"html": bool, "osm": bool} -- grid-mapper's --html and
    --osm-basemap switches, applied to whichever map outputs are requested
    (--osm-basemap also applies to the contest movie).
    contest / entry_category / analog: the VHF site's contest (AUTO, NONE or a
    contest id), entry category (AUTO or a code such as R) and Analog Only.
    """
    results = []
    single = [o for o in outputs if o in SINGLE_LOG_OUTPUTS]
    maps = [o for o in outputs if o in MAP_OUTPUTS]
    map_options = map_options or {}

    for index, src in enumerate(sources):
        category = src.band_category if band_category == "AUTO" else band_category
        runs = {}  # (module, extra argv) -> (files, notes, error): run each script once

        def run_once(module_name, extra):
            key = (module_name, tuple(extra))
            if key not in runs:
                run_dir = os.path.join(workdir, f"src{index}", module_name + "".join(extra))
                files, notes, error = run_script(module_name, [src.path, *extra], run_dir,
                                                 callsign=src.callsign, band_category=category)
                files = [_fix_unknown_callsign(f, src.callsign) for f in files]
                runs[key] = (files, notes, error)
            return runs[key]

        scored_as = effective_contest(src, contest)
        vhf_argv = (_vhf_argv(scored_as, entry_category, analog)
                    if scored_as in contests.VHF_CONTESTS else None)

        for output in single:
            module_name, extra, patterns = SINGLE_LOG_OUTPUTS[output]
            if output in VHF_PATTERNS and SITE_MODE == "vhf":
                if scored_as is None:
                    results.append(RunResult(output, src.label, [], "", (
                        "No contest was detected for this log (" + src.detection + "). "
                        "Choose the contest, or use the maps and the movie on their own.")
                        if contest == "AUTO" else
                        "Choose a contest to make a Cabrillo log or a contest summary."))
                    continue
                if vhf_argv:
                    module_name, extra, patterns = "vhf_cabrillo", vhf_argv, [VHF_PATTERNS[output]]
            files, notes, error = run_once(module_name, extra)
            kept = [f for f in files if any(re.search(p, os.path.basename(f)) for p in patterns)]
            if not kept and not error:
                error = "No output was produced -- see the processing notes."
            results.append(RunResult(output, src.label, kept, notes, error))

        maps_from = (scored_as, vhf_argv)
        if maps:
            results += _run_maps(src, index, maps, map_options, workdir, run_once, maps_from)
        if MOVIE_OUTPUT in outputs:
            results.append(_run_movie(src, index, map_options, workdir, run_once, maps_from))

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


def _plain_cabrillo(src, workdir):
    """A Cabrillo copy of a log with no CONTEST: line, for maps and a movie
    of a log that isn't scored as a contest (the movie shows no score)."""
    vhf = _module("vhf_cabrillo")
    with contextlib.redirect_stdout(io.StringIO()):
        df, _ = data_source.resolve_source(src.path, verbose=False)
    has_mode = "mode" in df.columns
    lines = ["START-OF-LOG: 3.0", f"CALLSIGN: {src.callsign}", "CREATED-BY: 10ghz-web-logger-analyzer"]
    for row in df.itertuples(index=False):
        mode = vhf.cabrillo_mode(getattr(row, "mode", "") if has_mode else "")
        lines.append(f"QSO: {data_source.band_to_cabrillo(row.band)} {mode} {vhf._qso_date(row.date)} "
                     f"{str(row.time).zfill(4)[:4]} {src.callsign} {str(row.sourcegrid).upper()} "
                     f"{str(row.call).upper()} {str(row.grid).upper()}")
    lines.append("END-OF-LOG:")
    os.makedirs(workdir, exist_ok=True)
    path = os.path.join(workdir, data_source.file_safe_call(src.callsign) + "_log.log")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path


def _cabrillo_for_maps(src, run_once, maps_from=(contests.ARRL_10_GHZ, None)):
    """grid-mapper reads Cabrillo (with the operator's grid on every QSO
    line). A VHF contest log is mapped from the analyzer's VHF Cabrillo (so
    the movie scores the chosen contest and category); a log scored as no
    contest from a plain Cabrillo copy; and on the 10 GHz site a CSV or ADIF
    log is converted by the 10 GHz converter -- the same file the "cabrillo"
    output produces. Returns (path or None, notes, error)."""
    scored_as, vhf_argv = maps_from
    if vhf_argv:
        files, notes, error = run_once("vhf_cabrillo", vhf_argv)
        cabrillo = next((f for f in files if re.search(VHF_PATTERNS["cabrillo"], f)), None)
        if cabrillo is None:
            error = error or "Couldn't convert this log to Cabrillo for mapping -- see the processing notes."
        return cabrillo, notes, error
    if scored_as is None:
        if src.path.lower().endswith(".log") and not contests.contest_from_name(src.header.get("CONTEST")):
            return src.path, "", ""
        try:
            return _plain_cabrillo(src, os.path.join(os.path.dirname(src.path), "plain")), "", ""
        except Exception as e:  # noqa: BLE001 -- reported as this output's error
            logger.exception("plain Cabrillo conversion failed")
            return None, "", f"Couldn't convert this log for mapping ({e})."
    if src.path.lower().endswith(".log"):
        return src.path, "", ""
    files, notes, error = run_once("arrl_10ghz_cabrillo", [])
    cabrillo = next((f for f in files if f.endswith("_ARRL_10GHZ.log")), None)
    if cabrillo is None:
        error = error or "Couldn't convert this log to Cabrillo for mapping -- see the processing notes."
    return cabrillo, notes, error


def _run_maps(src, index, maps, map_options, workdir, run_once, maps_from=(contests.ARRL_10_GHZ, None)):
    """Run grid-mapper once for this log and split its files into the
    requested map outputs."""
    cabrillo, notes, error = _cabrillo_for_maps(src, run_once, maps_from)
    if cabrillo is None:
        return [RunResult(output, src.label, [], notes, error) for output in maps]

    argv = [cabrillo]
    if "grid_paths" in maps:
        argv.append("--paths")
    if map_options.get("html"):
        argv.append("--html")
    if map_options.get("osm"):
        argv.append("--osm-basemap")
    argv += ["--credit", site_credit()]
    files, notes, error = run_script("maidenhead_map", argv,
                                     os.path.join(workdir, f"src{index}", "maidenhead_map"))

    results = []
    for output in maps:
        kept = [f for f in files if re.search(MAP_OUTPUTS[output], os.path.basename(f))]
        out_error = error
        if not kept and not error:
            out_error = "No map was produced -- see the processing notes."
        results.append(RunResult(output, src.label, kept, notes, out_error))
    return results


def _run_movie(src, index, map_options, workdir, run_once, maps_from=(contests.ARRL_10_GHZ, None)):
    """grid-mapper --animate-only: the whole log as one animated HTML page."""
    cabrillo, notes, error = _cabrillo_for_maps(src, run_once, maps_from)
    if cabrillo is None:
        return RunResult(MOVIE_OUTPUT, src.label, [], notes, error)
    argv = [cabrillo, "--animate-only"]
    if map_options.get("osm"):
        argv.append("--osm-basemap")
    argv += ["--credit", site_credit()]
    files, notes, error = run_script("maidenhead_map", argv,
                                     os.path.join(workdir, f"src{index}", "maidenhead_map_movie"))
    kept = [f for f in files if re.search(MOVIE_PATTERN, os.path.basename(f))]
    if not kept and not error:
        error = "No movie was produced -- the log needs a time and both grid squares on each QSO."
    return RunResult(MOVIE_OUTPUT, src.label, kept, notes, error)
