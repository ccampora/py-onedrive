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
    with open(EXCLUDE_FILE, "r") as excludefile:
        r = json.load(excludefile)
    
    ex_list = r["exclude"]

    logger.info("Exclude List: ")
    for e in ex_list:
        logger.info(e["path"])
    
    return ex_list
        
EXCLUDE_LIST = get_exclude_list()