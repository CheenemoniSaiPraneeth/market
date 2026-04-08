import json
import os
from datetime import datetime

INPUT_FILE = "companies_suii.json"
MASTER_FILE = "empty.json"
TODAY_FILE = "charith.json"

TODAY_DATE = datetime.now().strftime("%Y-%m-%d")

# ---------------------------------
# LOAD HARVESTED
# ---------------------------------
with open(INPUT_FILE, "r", encoding="utf-8") as f:
    harvested = json.load(f)

# ---------------------------------
# LOAD MASTER
# ---------------------------------
if os.path.exists(MASTER_FILE):
    with open(MASTER_FILE, "r", encoding="utf-8") as f:
        master_data = json.load(f)
else:
    master_data = []

master_urls = {item["url"] for item in master_data}

today_new = []

# ---------------------------------
# PROCESS
# ---------------------------------
for company_block in harvested:

    company = company_block.get("company")

    for link in company_block.get("candidate_links", []):

        url = link.get("url")
        score = link.get("score")

        if not url:
            continue

        # ONLY CHECK: if already exists
        if url in master_urls:
            continue

        record = {
            "company": company,
            "url": url,
            "score": score,
            "date_added": TODAY_DATE
        }

        # Add to master
        master_data.append(record)
        master_urls.add(url)

        # Add to today's file
        today_new.append(record)

# ---------------------------------
# SAVE FILES
# ---------------------------------
with open(MASTER_FILE, "w", encoding="utf-8") as f:
    json.dump(master_data, f, indent=4)

with open(TODAY_FILE, "w", encoding="utf-8") as f:
    json.dump(today_new, f, indent=4)

print("\nDelta Engine Complete")
print("New URLs added today:", len(today_new))
print("Total master URLs:", len(master_data))
