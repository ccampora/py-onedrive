import logging
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(message)s',
                    datefmt='%Y-%m-%d %H:%M:%S')

from Authentication import authenticate
from Operations import sync_onedrive_to_disk
from Config import create_pyonedrive_config_folder
from Config import init_onedrive_database
from Globals import LOGGER as logger


def start():
    create_pyonedrive_config_folder()
    init_onedrive_database()
    # Authentication
    logger.info("Authenticating...")
    authenticate()
    
    logger.info("Initializing synchronization...")
    sync_onedrive_to_disk("", "")
    
    
if __name__ == "__main__":
    logger = logging.getLogger();
    logger.info('Starting...')
    start()
    
