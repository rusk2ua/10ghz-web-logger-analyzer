"""Download the Natural Earth data grid-mapper draws with, at image build time.

Run with CARTOPY_DATA_DIR unset; files land in /opt/cartopy (or argv[1]). At
run time the Lambda sets CARTOPY_DATA_DIR to that folder, so cartopy finds
them there and never downloads anything during a job.
"""
import sys

import cartopy
from cartopy.io import shapereader

cartopy.config["data_dir"] = sys.argv[1] if len(sys.argv) > 1 else "/opt/cartopy"

# The datasets maidenhead_map.py uses (coastlines, land, ocean, lakes, borders,
# and state/province lines for the --animate contest movie).
for category, name in [
    ("physical", "coastline"),
    ("physical", "land"),
    ("physical", "ocean"),
    ("physical", "lakes"),
    ("cultural", "admin_0_boundary_lines_land"),
    ("cultural", "admin_1_states_provinces_lines"),
]:
    print(shapereader.natural_earth(resolution="10m", category=category, name=name))
