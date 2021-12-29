import os
import json

SECRETS_FILE = os.path.expanduser('~') + "/.py-onedrive-secrets"

def check_secrets_file_exist():
    
    # Is not exists then create stub file
    if os.path.isfile(SECRETS_FILE) is False:
        stub_secrets = {
            "access_token": ""
        }
        
        with open(SECRETS_FILE, "w") as outfile:
            json.dump(stub_secrets, outfile)
    return True

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
