import logging

from Authentication import authenticate
from Operations import sync_onedrive_to_disk
from Config import create_pyonedrive_config_folder
from Config import init_onedrive_database
from Config import init_config


def start():
    create_pyonedrive_config_folder()
    init_config()
    # Authentication
    authenticate()
    
    sync_onedrive_to_disk("", "")
    
    
if __name__ == "__main__":
    start()
    
