import os
import json
from Item import get_etag_from_local
from Globals import DELTALINK_FILE, SECRETS_FILE, CONFIG_FOLDER, ONEDRIVE_DB_FOLDER, EXCLUDE_FILE


def check_secrets_file_exist():    
    # Is not exists then create stub file
    if os.path.isfile(SECRETS_FILE) is False:
        stub_secrets = {
            "access_token": ""
        }
        
        with open(SECRETS_FILE, "w") as outfile:
            json.dump(stub_secrets, outfile)
    return True

def create_pyonedrive_config_folder():
    if os.path.exists(CONFIG_FOLDER) is False:
        os.mkdir(CONFIG_FOLDER)

# Read secrets file and return content as String
def read_secrets_file():
    secrets = ""
    with open(SECRETS_FILE, "r") as infile:
        secrets = json.load(infile)
    return secrets

def save_bearer_response(bearer_response):
    # Check if file exists, if not creates a placeholder
    check_secrets_file_exist()

    # dump secrets back to file
    with open(SECRETS_FILE, "w") as outfile:
        json.dump(bearer_response, outfile)
        
def get_current_bearer(): 
    check_secrets_file_exist()
    return read_secrets_file()["access_token"]

def get_current_refresh_token():
    check_secrets_file_exist()
    return read_secrets_file()["refresh_token"]

def init_onedrive_database():     
     if os.path.exists(ONEDRIVE_DB_FOLDER) is False:
         os.mkdir(ONEDRIVE_DB_FOLDER)
         
def save_item_remoteinfo_to_db(id, jsonInfo):
    path = f'{ONEDRIVE_DB_FOLDER}/{id}'
    
    with open(path, "w") as outfile:
        json.dump(jsonInfo, outfile)
        
    print(get_etag_from_local(id))

def get_exclude_list():
    with open(EXCLUDE_FILE, "r") as excludefile:
        r = json.load(excludefile)
    return r["exclude"]    

EXCLUDE_LIST = get_exclude_list()
for e in EXCLUDE_LIST:
    print(e["path"])
    
    """
    Checks if the deltalink file described in DELTALINK_FILE parameters exists. If not, then creates a stub. 
    """
def check_deltalink_file_exist():    
    # Is not exists then create stub file
    if os.path.isfile(DELTALINK_FILE) is False:
        stub_deltalink = {
            "deltalink": ""
        }
        
        with open(DELTALINK_FILE, "w") as outfile:
            json.dump(stub_deltalink, outfile)    


    """
    Saves the deltalink to the db for later usage
    """
def save_deltalink_to_db(deltaToken):
    
    check_deltalink_file_exist()
    
    with open(DELTALINK_FILE, "w") as file:
        json.dump({ "deltalink" : deltaToken}, file)


    """
    Returns the deltalink that was previously save after the last delta call
    """
def get_deltalink_from_db():
    
    check_deltalink_file_exist()
    
    with open(DELTALINK_FILE, "r") as file:
        r = json.load(file)
    
    return r["deltalink"]
        
    
    
