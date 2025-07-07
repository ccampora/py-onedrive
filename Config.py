import os
import json
from Globals import CONFIG_FOLDER, SECRETS_FILE, ONEDRIVE_DB_FOLDER, EXCLUDE_FILE, DELTALINK_FILE

def initialize_config():
    """Initialize configuration directories and files"""
    # Create main config directory
    os.makedirs(CONFIG_FOLDER, exist_ok=True)
    
    # Create database directory
    os.makedirs(ONEDRIVE_DB_FOLDER, exist_ok=True)
    
    # Create exclude file if it doesn't exist
    if not os.path.exists(EXCLUDE_FILE):
        with open(EXCLUDE_FILE, "w") as f:
            json.dump([], f)
    
    # Create deltalink file if it doesn't exist
    if not os.path.exists(DELTALINK_FILE):
        with open(DELTALINK_FILE, "w") as f:
            f.write("")

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
