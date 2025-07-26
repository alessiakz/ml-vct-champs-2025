import requests
import json
import os

# Create output directory if it doesn't exist
output_dir = os.path.join("data", "raw", "matches")
os.makedirs(output_dir, exist_ok=True)

# # Set request parameters
# url = "http://127.0.0.1:3001/match"
# params = {
#     "q": "results",
#     "num_pages": 208,  # increase as needed, 208 is start of lock//in, last updated at 7/26/25
#     "max_retries": 3,
#     "request_delay": 1,
#     "timeout": 30
# }

# response = requests.get(url, params=params)

# if response.ok:
#     data = response.json()
    
#     # Optional: Filter for tournaments with "VCT" in the name
#     vct_matches = [
#         match for match in data["data"]["segments"]
#         if (
#             ("VCT" in match["tournament_name"].upper() and "CHINA" not in match["tournament_name"].upper())
#             or "CHAMPIONS TOUR 2023" in match["tournament_name"].upper()
#             or "VALORANT CHAMPIONS 2023" in match["tournament_name"].upper()
#         )
#     ]


#     # # Save full response (raw)
#     # with open(os.path.join(output_dir, "vct_matches_raw.json"), "w", encoding="utf-8") as f:
#     #     json.dump(data, f, indent=2, ensure_ascii=False)

#     # Save filtered VCT matches
#     with open(os.path.join(output_dir, "vct_matches_filtered.json"), "w", encoding="utf-8") as f:
#         json.dump(vct_matches, f, indent=2, ensure_ascii=False)

#     print(f"Saved {len(vct_matches)} VCT matches to data/vct/")
# else:
#     print("Request failed with status:", response.status_code)


# ---- STATS ----
regions = {
    "na": "north-america",
    "eu": "europe",
    "ap": "asia-pacific",
    "sa": "latin-america",
    "jp": "japan"
}

target_orgs = {
    "100T", "C9", "EG", "FUR", "KRÜ", "LEV", "LOUD", "MIBR", "NRG", "SEN", "G2", "2G",
    "DFM", "DRX", "GENG", "GE", "PRX", "RRQ", "T1", "TLN", "TS", "ZETA", "NS", "BME",
    "BBL", "FNC", "FUT", "GX", "KC", "KOI", "NAVI", "TH", "TL", "VIT", "M8", "APK"
}

stats_output_dir = os.path.join("data", "raw", "stats")
os.makedirs(stats_output_dir, exist_ok=True)

base_stats_url = "http://127.0.0.1:3001/stats"

for region_code, region_name in regions.items():
    print(f"Fetching stats for {region_name}...")

    stats_params = {
        "region": region_code,
        "timespan": "all"
    }

    try:
        response = requests.get(base_stats_url, params=stats_params)
        response.raise_for_status()
        stats_data = response.json()

        # # Save full unfiltered stats
        # full_out_path = os.path.join(stats_output_dir, f"{region_code}_stats.json")
        # with open(full_out_path, "w", encoding="utf-8") as f:
        #     json.dump(stats_data, f, indent=2, ensure_ascii=False)
        # print(f"✅ Saved full stats for {region_name} to {full_out_path}")

        # Filter by org
        segments = stats_data.get("data", {}).get("segments", [])
        filtered_segments = [
            entry for entry in segments
            if entry.get("org", "").upper() in target_orgs
        ]

        # Save filtered stats
        filtered_out_path = os.path.join(stats_output_dir, f"{region_code}_stats_filtered.json")
        with open(filtered_out_path, "w", encoding="utf-8") as f:
            json.dump(filtered_segments, f, indent=2, ensure_ascii=False)
        print(f"✅ Saved {len(filtered_segments)} filtered stats for {region_name} to {filtered_out_path}")

    except requests.exceptions.RequestException as e:
        print(f"❌ Failed to fetch stats for {region_name}: {e}")