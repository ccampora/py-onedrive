import os
import json
from Globals import ONEDRIVE_DB_FOLDER

def get_etag_from_local(id):
    path = f'{ONEDRIVE_DB_FOLDER}/{id}'

    if os.path.isfile(path) is True: #file exists
        with open(path, "r") as itemfile:
            r = json.load(itemfile)
        return r["eTag"]
    else:
        return ""