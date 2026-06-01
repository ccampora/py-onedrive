import os
import logging

CONFIG_FOLDER = os.path.expanduser('~') + "/.py-onedrive"
SECRETS_FILE = f'{CONFIG_FOLDER}/.py-onedrive-secrets'
ONEDRIVE_DB_FOLDER = f'{CONFIG_FOLDER}/db'
DELTALINK_FILE = f'{CONFIG_FOLDER}/.py-onedrive-deltalink'
INODE_FILE = f'{CONFIG_FOLDER}/.py-onedrive-inodes'
CACHE_FOLDER = f'{CONFIG_FOLDER}/cache'
LOGGER = logging.getLogger()
