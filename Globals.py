import os
import logging

CONFIG_FOLDER = os.path.expanduser('~') + "/.py-onedrive"
SECRETS_FILE = f'{CONFIG_FOLDER}/.py-onedrive-secrets'
ONEDRIVE_DB_FOLDER = f'{CONFIG_FOLDER}/db'
ONEDRIVE_ROOT = os.path.expanduser('~') + "/onedrive"
EXCLUDE_FILE = f'{CONFIG_FOLDER}/.py-onedrive-exclude'
DELTALINK_FILE = f'{CONFIG_FOLDER}/.py-onedrive-deltalink'
LOGGER = logging.getLogger()