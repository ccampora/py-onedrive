import os
import json
from Item import get_etag_from_local
from Globals import SECRETS_FILE, CONFIG_FOLDER, ONEDRIVE_DB_FOLDER


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
        
#def save_deltalink_to_db(token):
    
    
