"""
Download the three open datasets used in this analysis into data/raw/.

1. Metropolitan Police recorded crime by borough and month (London Datastore, Open Government Licence v2)
   https://data.london.gov.uk/dataset/mps-recorded-crime-geographic-breakdown-exy3m
   - most recent 24 months (Oct 2024 to Sep 2026) and the historical file (Oct 2020 to Sep 2024)
2. English Indices of Deprivation 2025, File 10: local authority district summaries v2 (MHCLG, OGL v3)
   https://www.gov.uk/government/statistics/english-indices-of-deprivation-2025
3. ONS mid-2025 population estimates by local authority, via the Nomis API (OGL v3)
   https://www.nomisweb.co.uk/

The London Datastore publishes a new version of the crime files every month under a new URL. The URLs below are
the versions used for the results in this repository (published 25 Sep 2026). To refresh, replace them with the
current links from the dataset page. All raw files are small and are committed for reproducibility.

Usage:  python src/download_data.py
"""
from pathlib import Path

import requests

RAW = Path(__file__).resolve().parents[1] / "data" / "raw"
FILES = {
    "mps_borough_crime_24m.csv":
        "https://data.london.gov.uk/download/exy3m/byz/MPS%20Borough%20Level%20Crime%20(most%20recent%2024%20months).csv",
    "mps_borough_crime_historical.csv":
        "https://data.london.gov.uk/download/exy3m/pg2/MPS%20Borough%20Level%20Crime%20(Historical).csv",
    "iod2025_file10_lad_summaries_v2.xlsx":
        "https://assets.publishing.service.gov.uk/media/6917412ebc34c86ce4e6e7fc/"
        "File_10_-_IoD2025_Local_Authority_District_Summaries__lower-tier__v2.xlsx",
    "ons_population_mid2025_lad.csv":
        "https://www.nomisweb.co.uk/api/v01/dataset/NM_2002_1.data.csv?geography=TYPE424&date=latest&gender=0"
        "&c_age=200&measures=20100&select=date_name,geography_name,geography_code,obs_value",
}


def main() -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    for name, url in FILES.items():
        r = requests.get(url, timeout=120)
        r.raise_for_status()
        (RAW / name).write_bytes(r.content)
        print(f"{name:<40} {len(r.content)/1e3:>7.0f} kB")


if __name__ == "__main__":
    main()
