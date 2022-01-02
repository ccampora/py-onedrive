from Authentication import authenticate
from Operations import sync_onedrive_to_disk

def start():
    # Authentication 
    authenticate()
    
start()

sync_onedrive_to_disk("", "")

