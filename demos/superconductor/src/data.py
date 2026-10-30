"""Fixed, read-only data loader for the superconductor task. DO NOT EDIT.

Downloads the REAL UCI Superconductivity dataset (Hamidieh, 2018; data from the NIMS SuperCon
database, CC BY 4.0) once and caches it to ~/.cache/superconductivity. Returns one row per material:

  X — DataFrame: `material` (chemical formula string), the 81 composition descriptors from the
      paper (`number_of_elements`, `mean_atomic_mass`, …, `wtd_std_Valence`), then 86 element
      amounts (`H`, `He`, …, `Rn`) parsed from the formula.
  y — ndarray of critical temperatures in kelvin.
"""

import os
import urllib.request
import zipfile

import pandas as pd

URL = "https://archive.ics.uci.edu/static/public/464/superconductivty+data.zip"
CACHE = os.path.expanduser("~/.cache/superconductivity")


def _ensure():
    train, uniq = os.path.join(CACHE, "train.csv"), os.path.join(CACHE, "unique_m.csv")
    if not (os.path.exists(train) and os.path.exists(uniq)):
        os.makedirs(CACHE, exist_ok=True)
        zpath = os.path.join(CACHE, "data.zip")
        urllib.request.urlretrieve(URL, zpath)
        with zipfile.ZipFile(zpath) as z:
            z.extractall(CACHE)
        os.remove(zpath)
    return train, uniq


def load():
    train, uniq = _ensure()
    t, u = pd.read_csv(train), pd.read_csv(uniq)
    assert len(t) == len(u) and (t.critical_temp.values == u.critical_temp.values).all()
    y = t.pop("critical_temp").to_numpy(dtype=float)
    elements = u.drop(columns=["critical_temp", "material"])
    X = pd.concat([u[["material"]], t, elements], axis=1)
    return X, y
