"""Debug metrics of one AURA module as CSV, for experiment reports (parameter ``debug: true``).

The node writes one row per event to ``<metrics_dir>/<module>_<UTC start>.csv`` (flushed per row, so a killed
node keeps its data) and, when it shuts down, ``<module>_<UTC start>_summary.csv`` (metric, value, unit, note).
The same file is in every AURA package so each module stays self-contained.
"""

import csv
import math
import time
from datetime import datetime, timezone
from pathlib import Path


def percentile(values, q):
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    k = (len(values) - 1) * q / 100
    lo, hi = math.floor(k), math.ceil(k)
    return values[lo] + (values[hi] - values[lo]) * (k - lo)


def stats(name, values, unit="ms", digits=1, note=""):
    """Summary rows (count, mean, p50, p95, max) for one measured quantity."""
    values = [v for v in values if v is not None]
    if not values:
        return [(f"{name} count", 0, "", note)]
    r = lambda v: round(v, digits)
    return [(f"{name} count", len(values), "", note), (f"{name} mean", r(sum(values) / len(values)), unit, ""),
            (f"{name} p50", r(percentile(values, 50)), unit, ""), (f"{name} p95", r(percentile(values, 95)), unit, ""),
            (f"{name} max", r(max(values)), unit, "")]


class MetricsLog:
    def __init__(self, directory, module, fields):
        self.dir = Path(directory).expanduser()
        self.dir.mkdir(parents=True, exist_ok=True)
        stem = f"{module}_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
        self.path = self.dir / f"{stem}.csv"
        self.summary_path = self.dir / f"{stem}_summary.csv"
        self.fields = ["time"] + list(fields)
        self.rows = []
        self._file = open(self.path, "w", newline="")
        self._writer = csv.DictWriter(self._file, fieldnames=self.fields, extrasaction="ignore")
        self._writer.writeheader()
        self._file.flush()

    def row(self, **values):
        values["time"] = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        for k, v in values.items():
            if isinstance(v, float):
                values[k] = round(v, 4)
        self._writer.writerow(values)
        self._file.flush()
        self.rows.append(values)

    def column(self, name, where=lambda r: True):
        return [r.get(name) for r in self.rows if where(r) and r.get(name) not in (None, "")]

    def close(self, summary_rows):
        with open(self.summary_path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["metric", "value", "unit", "note"])
            w.writerows(summary_rows)
        self._file.close()
        return self.summary_path
