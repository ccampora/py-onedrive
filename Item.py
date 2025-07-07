import os
import json
from Globals import ONEDRIVE_DB_FOLDER, EXCLUDE_FILE
from Globals import LOGGER as logger

def get_etag_from_local(id):
    path = f'{ONEDRIVE_DB_FOLDER}/{id}'

    if os.path.isfile(path) is True: #file exists
        with open(path, "r") as itemfile:
            r = json.load(itemfile)
        return r["eTag"]
    else:
        return ""
    
def is_excluded(path):
    for e in EXCLUDE_LIST:
        if path.find(e["path"]) != -1:
            return True
    return False

"""
Returns the EXCLUDE_LIST
"""
def get_exclude_list():
    """Get the list of excluded folders/files"""
    # Check if exclude file exists, if not create it with empty list
    if not os.path.exists(EXCLUDE_FILE):
        # Create the directory if it doesn't exist
        os.makedirs(os.path.dirname(EXCLUDE_FILE), exist_ok=True)
        # Create empty exclude file
        with open(EXCLUDE_FILE, "w") as excludefile:
            json.dump([], excludefile)
        return []
    
    try:
        with open(EXCLUDE_FILE, "r") as excludefile:
            return json.load(excludefile)
    except (json.JSONDecodeError, FileNotFoundError):
        # If file is corrupted or doesn't exist, return empty list
        return []

EXCLUDE_LIST = get_exclude_list()