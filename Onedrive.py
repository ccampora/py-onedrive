from Authentication import authenticate
from Operations import sync_onedrive_to_disk
from Config import create_pyonedrive_config_folder
from Config import init_onedrive_database

def start():
    # Authentication 
    authenticate()
    
    
create_pyonedrive_config_folder()
init_onedrive_database()

start()

sync_onedrive_to_disk("", "")